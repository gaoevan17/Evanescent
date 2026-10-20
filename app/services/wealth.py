"""理财操作服务：产品推荐/对比、风险问卷与适配校验、申购、赎回、订单与持仓。

赎回申请成功 ≠ 资金到账；未到账资金不得用于付款。
所有产品均为合成模拟数据，不提供真实收益承诺。
"""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy.orm import Session

from ..models import Holding, Order, Product, RiskProfile
from . import audit, money


def list_products(db: Session) -> list[dict]:
    return [{"id": p.id, "name": p.name, "risk_level": p.risk_level,
             "min_cents": p.min_cents, "term_days": p.term_days,
             "fee_rate": p.fee_rate, "redemption_rule": p.redemption_rule,
             "is_synthetic": p.is_synthetic} for p in db.query(Product).all()]


def compare_products(db: Session, product_ids: list[int]) -> list[dict]:
    prods = db.query(Product).filter(Product.id.in_(product_ids)).all()
    return [{"id": p.id, "name": p.name, "risk_level": p.risk_level,
             "term_days": p.term_days, "fee_rate": p.fee_rate,
             "redemption_rule": p.redemption_rule, "min_cents": p.min_cents} for p in prods]


# 风险问卷评分：把选项映射为分值，累加后分级
_RISK_LEVELS = ["保守", "稳健", "平衡", "进取", "激进"]


def submit_risk_profile(db: Session, *, user_id: int, answers: list[int]) -> dict:
    """提交风险问卷，计算风险等级。answers: 每题分值列表。"""
    score = sum(answers)
    if score <= 8:
        level = "保守"
    elif score <= 14:
        level = "稳健"
    elif score <= 20:
        level = "平衡"
    elif score <= 26:
        level = "进取"
    else:
        level = "激进"
    p = db.query(RiskProfile).filter(RiskProfile.user_id == user_id).first()
    if p is None:
        p = RiskProfile(user_id=user_id, answers=answers, risk_level=level)
        db.add(p)
    else:
        p.answers = answers
        p.risk_level = level
    db.commit()
    return {"risk_level": level, "score": score}


def get_risk_profile(db: Session, user_id: int) -> dict | None:
    p = db.query(RiskProfile).filter(RiskProfile.user_id == user_id).first()
    if p is None:
        return None
    return {"risk_level": p.risk_level, "answers": p.answers}


_RISK_ORDER = {"R1": 0, "R2": 1, "R3": 2, "R4": 3, "R5": 4}
_LEVEL_MAX_RISK = {"保守": "R2", "稳健": "R3", "平衡": "R4", "进取": "R5", "激进": "R5"}


def check_adaptation(db: Session, user_id: int, product_id: int) -> dict:
    """风险适配校验：产品风险不得超过用户风险承受等级。"""
    prod = db.query(Product).filter(Product.id == product_id).first()
    if prod is None:
        raise ValueError("产品不存在")
    prof = get_risk_profile(db, user_id)
    if prof is None:
        return {"adapted": False, "reason": "未完成风险问卷，请先评估"}
    max_risk = _LEVEL_MAX_RISK[prof["risk_level"]]
    ok = _RISK_ORDER[prod.risk_level] <= _RISK_ORDER[max_risk]
    return {"adapted": ok, "product_risk": prod.risk_level,
            "user_risk": prof["risk_level"],
            "reason": "适配" if ok else f"产品风险 {prod.risk_level} 超出用户 {prof['risk_level']} 承受能力"}


def subscribe_product(db: Session, *, user_id: int, product_id: int,
                      amount_cents: int, idempotency_key: str | None = None) -> dict:
    """申购（需在权限层完成红色强验证后调用）。扣余额、增持仓。"""
    idem = idempotency_key or f"sub-{uuid.uuid4().hex}"
    prod = db.query(Product).filter(Product.id == product_id).first()
    if prod is None:
        raise ValueError("产品不存在")
    if amount_cents < prod.min_cents:
        raise ValueError(f"低于起购金额 {prod.min_cents} 分")
    adapt = check_adaptation(db, user_id, product_id)
    if not adapt["adapted"]:
        raise ValueError(adapt["reason"])
    acct = money.get_account(db, user_id)
    before = money.balance(db, user_id)
    res = money.debit(db, account_id=acct.id, amount_cents=amount_cents,
                      idempotency_key=idem, tx_type="invest_out",
                      counterparty=prod.name, memo=f"申购 {prod.name}",
                      category="理财")
    holding = db.query(Holding).filter(Holding.user_id == user_id,
                                       Holding.product_id == product_id).first()
    if holding is None:
        holding = Holding(user_id=user_id, product_id=product_id, amount_cents=0)
        db.add(holding)
    holding.amount_cents += amount_cents
    order = Order(user_id=user_id, product_id=product_id, order_type="subscribe",
                  amount_cents=amount_cents, status="settled",
                  idempotency_key=f"{idem}-o", business_no=res.business_no or f"O{uuid.uuid4().hex[:8]}")
    db.add(order)
    db.commit()
    audit.record(db, user_id=user_id, action="wealth_subscribe",
                 intent_summary=f"申购 {prod.name} {amount_cents} 分",
                 tool_params={"product_id": product_id, "amount_cents": amount_cents},
                 business_receipt={"order_id": order.id, "status": "settled"},
                 before_state={"balance": before}, after_state={"balance": money.balance(db, user_id)},
                 success=True)
    return {"order_id": order.id, "status": "settled", "holding_cents": holding.amount_cents}


def redeem_product(db: Session, *, user_id: int, holding_id: int,
                   amount_cents: int, idempotency_key: str | None = None) -> dict:
    """赎回（团队策略按红色强验证）。申请成功≠到账；资金在途，不入余额。"""
    idem = idempotency_key or f"red-{uuid.uuid4().hex}"
    holding = db.query(Holding).filter(Holding.id == holding_id,
                                       Holding.user_id == user_id).first()
    if holding is None:
        raise ValueError("持仓不存在")
    if amount_cents > holding.amount_cents:
        raise ValueError("赎回金额超过持仓")
    prod = db.query(Product).filter(Product.id == holding.product_id).first()
    holding.amount_cents -= amount_cents
    order = Order(user_id=user_id, product_id=holding.product_id, order_type="redeem",
                  amount_cents=amount_cents, status="processing",
                  idempotency_key=f"{idem}-o")
    db.add(order)
    db.commit()
    audit.record(db, user_id=user_id, action="wealth_redeem",
                 intent_summary=f"赎回 {amount_cents} 分",
                 tool_params={"holding_id": holding_id, "amount_cents": amount_cents},
                 business_receipt={"order_id": order.id, "status": "processing",
                                   "redemption_rule": prod.redemption_rule if prod else "T+1"},
                 success=True)
    return {"order_id": order.id, "status": "processing",
            "redemption_rule": prod.redemption_rule if prod else "T+1",
            "note": "赎回申请已受理，资金在途，尚未到账"}


def settle_redemption(db: Session, *, order_id: int, user_id: int) -> dict:
    """赎回到账（模拟 T+1）。到账后才入余额，此时方可用于付款。"""
    order = db.query(Order).filter(Order.id == order_id,
                                   Order.user_id == user_id,
                                   Order.order_type == "redeem").first()
    if order is None:
        raise ValueError("赎回订单不存在")
    if order.status != "processing":
        raise ValueError(f"订单状态 {order.status}，不可到账")
    acct = money.get_account(db, user_id)
    money.credit(db, account_id=acct.id, amount_cents=order.amount_cents,
                 idempotency_key=f"red-credit-{order.id}", tx_type="invest_in",
                 counterparty="理财赎回到账", category="理财")
    order.status = "credited"
    db.commit()
    return {"order_id": order.id, "status": "credited", "amount_cents": order.amount_cents}


def list_orders(db: Session, user_id: int) -> list[dict]:
    return [{"id": o.id, "product_id": o.product_id, "order_type": o.order_type,
             "amount_cents": o.amount_cents, "status": o.status,
             "created_at": o.created_at.isoformat()} for o in
            db.query(Order).filter(Order.user_id == user_id).order_by(Order.id.desc()).all()]


def list_holdings(db: Session, user_id: int) -> list[dict]:
    return [{"id": h.id, "product_id": h.product_id, "amount_cents": h.amount_cents}
            for h in db.query(Holding).filter(Holding.user_id == user_id).all()]
