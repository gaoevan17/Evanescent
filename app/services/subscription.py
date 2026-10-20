"""订阅 / 代扣管理服务：识别周期扣费候选、查询授权、续费提醒、取消与生效回读。

只能由持有人取消；取消不自动退回历史费用，也不增加当前余额。
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from ..models import Subscription
from . import audit


def identify_recurring(db: Session, *, user_id: int) -> list[dict]:
    """从流水识别周期扣费候选（同商户同金额 ≥2 次）。"""
    from ..models import Account, Transaction
    from sqlalchemy import func
    acct = db.query(Account).filter(Account.user_id == user_id).first()
    if acct is None:
        return []
    rows = (
        db.query(Transaction.merchant, Transaction.amount_cents, func.count(Transaction.id))
        .filter(Transaction.account_id == acct.id, Transaction.tx_type == "debit")
        .group_by(Transaction.merchant, Transaction.amount_cents)
        .having(func.count(Transaction.id) >= 2)
        .all()
    )
    return [{"merchant": m, "amount_cents": a, "count": c} for m, a, c in rows]


def list_subscriptions(db: Session, user_id: int) -> list[dict]:
    subs = db.query(Subscription).filter(Subscription.user_id == user_id).all()
    return [{"id": s.id, "merchant": s.merchant, "alias": s.alias,
             "amount_cents": s.amount_cents, "cycle": s.cycle, "status": s.status,
             "authorized": s.authorized,
             "next_charge_at": s.next_charge_at.isoformat() if s.next_charge_at else None}
            for s in subs]


def _own_sub(db: Session, user_id: int, sub_id: int) -> Subscription:
    s = db.query(Subscription).filter(Subscription.id == sub_id,
                                      Subscription.user_id == user_id).first()
    if s is None:
        raise PermissionError("订阅不存在或非本人订阅")
    return s


def query_subscription_authorization(db: Session, *, user_id: int, sub_id: int) -> dict:
    """查询订阅授权（官网列明的黄色操作）。"""
    s = _own_sub(db, user_id, sub_id)
    return {"id": s.id, "merchant": s.merchant, "authorized": s.authorized,
            "status": s.status, "card_id": s.card_id, "amount_cents": s.amount_cents,
            "cycle": s.cycle}


def cancel_subscription(db: Session, *, user_id: int, sub_id: int) -> dict:
    """取消订阅（黄色，需确认）。仅持有人可取消；不退回历史费用、不增加余额。"""
    s = _own_sub(db, user_id, sub_id)
    if s.status != "active":
        raise ValueError(f"订阅状态 {s.status}，不可取消")
    effective = dt.datetime.now(dt.timezone.utc)
    s.status = "cancelled"
    s.authorized = False
    s.effective_change_at = effective
    db.commit()
    audit.record(db, user_id=user_id, action="subscribe_cancel",
                 intent_summary=f"取消订阅 {s.merchant}",
                 tool_params={"sub_id": sub_id},
                 business_receipt={"effective_at": effective.isoformat(),
                                   "note": "取消不退回历史费用，不增加当前余额"},
                 success=True)
    return {"id": s.id, "merchant": s.merchant, "status": s.status,
            "effective_at": effective.isoformat(),
            "note": "取消已生效；历史费用不退回，当前余额不变"}


def cancel_effect_status(db: Session, *, user_id: int, sub_id: int) -> dict:
    """回读取消生效状态。"""
    s = _own_sub(db, user_id, sub_id)
    return {"id": s.id, "merchant": s.merchant, "status": s.status,
            "effective_change_at": s.effective_change_at.isoformat() if s.effective_change_at else None}
