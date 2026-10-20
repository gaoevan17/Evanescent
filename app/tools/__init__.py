"""受控工具白名单 + 权限门（统一执行入口）。

- 工具名 → 处理器映射（白名单）
- 参数校验（结构化工具契约）
- 身份来自服务端会话（由调用方传入 user_id），不信任请求体自报
- 权限门：绿/黄/红分级执行，确认/强验证由确定性程序控制
- 授权绑定：关键参数（金额/收款人/规则版本）变更使旧授权失效
"""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy.orm import Session

from ..auth import challenge as challenge_mod
from ..security import policy
from ..services import audit, bill, card, evidence, ledger, money, subscription, transfer, wealth
from ..models import Authorization, User


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# ---------------------------------------------------------------------------
# 工具处理器（每个 handler 接收 db, user_id, params）
# ---------------------------------------------------------------------------
def _h_balance(db, user_id, p):
    return {"balance_cents": money.balance(db, user_id)}


def _h_transactions(db, user_id, p):
    acct = money.get_account(db, user_id)
    from ..models import Transaction
    limit = int(p.get("limit", 50))
    txs = (db.query(Transaction).filter(Transaction.account_id == acct.id)
           .order_by(Transaction.id.desc()).limit(limit).all())
    return {"transactions": [{"id": t.id, "tx_type": t.tx_type, "amount_cents": t.amount_cents,
                              "merchant": t.merchant, "memo": t.memo, "status": t.status,
                              "created_at": t.created_at.isoformat()} for t in txs]}


def _h_bill_analysis(db, user_id, p):
    acct = money.get_account(db, user_id)
    month = p.get("month")
    year = int(p.get("year", dt.datetime.now().year))
    return bill.classify_and_report(db, account_id=acct.id, year=year, month=month)


def _h_bill_report(db, user_id, p):
    return _h_bill_analysis(db, user_id, p)


def _h_products(db, user_id, p):
    return {"products": wealth.list_products(db)}


def _h_holdings(db, user_id, p):
    return {"holdings": wealth.list_holdings(db, user_id)}


def _h_risk_profile(db, user_id, p):
    return {"risk_profile": wealth.get_risk_profile(db, user_id)}


def _h_wealth_income(db, user_id, p):
    return {"holdings": wealth.list_holdings(db, user_id), "note": "合成模拟收益，非真实承诺"}


def _h_cards(db, user_id, p):
    return {"cards": card.list_cards(db, user_id)}


def _h_space_progress(db, user_id, p):
    space_id = int(p["space_id"])
    if not ledger.is_space_member(db, space_id, user_id):
        raise PermissionError("非共同空间成员")
    return ledger.member_due_summary(db, space_id)


def _h_transfer(db, user_id, p):
    amount = int(p["amount_cents"])
    payee_contact_id = p.get("payee_contact_id")
    scheduled_at = None
    if p.get("scheduled_at"):
        scheduled_at = dt.datetime.fromisoformat(p["scheduled_at"])
    return transfer.transfer(
        db, payer_user_id=user_id, payee_contact_id=payee_contact_id,
        payee_external=p.get("payee_external"), amount_cents=amount,
        memo=p.get("memo", ""), idempotency_key=p.get("idempotency_key"),
        scheduled_at=scheduled_at, force_timeout=bool(p.get("force_timeout")),
    )


def _h_aa_request(db, user_id, p):
    return transfer.request_aa(db, requester_user_id=user_id, space_id=p.get("space_id"),
                               title=p.get("title", ""), payers=[int(x) for x in p.get("payers", [])],
                               amount_cents=int(p["amount_cents"]),
                               idempotency_key=p.get("idempotency_key"))


def _h_subscribe_query(db, user_id, p):
    return subscription.query_subscription_authorization(db, user_id=user_id, sub_id=int(p["sub_id"]))


def _h_subscribe_cancel(db, user_id, p):
    return subscription.cancel_subscription(db, user_id=user_id, sub_id=int(p["sub_id"]))


def _h_virtual_card_apply(db, user_id, p):
    return card.apply_virtual_card(db, user_id=user_id, limit_cents=int(p.get("limit_cents", 0)))


def _h_card_lost(db, user_id, p):
    return card.report_lost(db, user_id=user_id, card_id=int(p["card_id"]))


def _h_card_unlost(db, user_id, p):
    return card.report_unlost(db, user_id=user_id, card_id=int(p["card_id"]))


def _h_card_limit_adjust(db, user_id, p):
    return card.adjust_limit(db, user_id=user_id, card_id=int(p["card_id"]),
                             limit_cents=int(p["limit_cents"]))


def _h_card_restrict(db, user_id, p):
    return card.restrict_card(db, user_id=user_id, card_id=int(p["card_id"]),
                              reason=p.get("reason", ""))


def _h_card_unrestrict(db, user_id, p):
    return card.unrestrict_card(db, user_id=user_id, card_id=int(p["card_id"]))


def _h_wealth_subscribe(db, user_id, p):
    return wealth.subscribe_product(db, user_id=user_id, product_id=int(p["product_id"]),
                                    amount_cents=int(p["amount_cents"]),
                                    idempotency_key=p.get("idempotency_key"))


def _h_wealth_redeem(db, user_id, p):
    return wealth.redeem_product(db, user_id=user_id, holding_id=int(p["holding_id"]),
                                 amount_cents=int(p["amount_cents"]),
                                 idempotency_key=p.get("idempotency_key"))


def _h_password_change(db, user_id, p):
    from ..auth import hash_password
    u = db.query(User).filter(User.id == user_id).first()
    u.password_hash = hash_password(str(p["new_password"]))
    db.commit()
    return {"status": "changed"}


def _h_list_evidence(db, user_id, p):
    return {"evidence": evidence.list_evidence(db, user_id, p.get("status"))}


def _h_update_evidence(db, user_id, p):
    return evidence.update_evidence(db, card_id=int(p["card_id"]), user_id=user_id,
                                    feedback=p.get("feedback", ""),
                                    new_status=p["new_status"])


def _h_list_rule_versions(db, user_id, p):
    from ..models import RuleVersion
    item_id = int(p["expense_item_id"])
    rvs = (db.query(RuleVersion).filter(RuleVersion.expense_item_id == item_id)
           .order_by(RuleVersion.version.desc()).all())
    return {"rule_versions": [{"id": r.id, "version": r.version, "status": r.status,
                               "split_config": r.split_config, "reason": r.reason} for r in rvs]}


def _h_propose_split_rule(db, user_id, p):
    item_id = int(p["expense_item_id"])
    split_config = {"method": p.get("method", "equal"), "weights": p.get("weights", {})}
    rv = ledger.create_rule_version(db, expense_item_id=item_id, split_config=split_config,
                                    created_by=user_id, reason=p.get("reason", ""))
    split = ledger.compute_split(p.get("total_cents", 0) or 0, split_config) if p.get("total_cents") else None
    return {"rule_version_id": rv.id, "version": rv.version, "split_cents": split}


def _h_confirm_split_rule(db, user_id, p):
    rv_id = int(p["rule_version_id"])
    confirmed = bool(p.get("confirmed", True))
    ledger.confirm_rule(db, rule_version_id=rv_id, user_id=user_id, confirmed=confirmed,
                        note=p.get("note", ""))
    return {"rule_version_id": rv_id, "user_id": user_id, "confirmed": confirmed}


def _h_list_subscriptions(db, user_id, p):
    return {"subscriptions": subscription.list_subscriptions(db, user_id)}


# 工具白名单注册表
TOOL_HANDLERS = {
    "balance_query": _h_balance,
    "transaction_list": _h_transactions,
    "bill_analysis": _h_bill_analysis,
    "bill_report": _h_bill_report,
    "wealth_income_query": _h_wealth_income,
    "holding_query": _h_holdings,
    "product_list": _h_products,
    "risk_profile_query": _h_risk_profile,
    "card_list": _h_cards,
    "space_progress_query": _h_space_progress,
    "transfer": _h_transfer,
    "subscribe_query": _h_subscribe_query,
    "subscribe_cancel": _h_subscribe_cancel,
    "virtual_card_apply": _h_virtual_card_apply,
    "aa_request": _h_aa_request,
    "password_change": _h_password_change,
    "card_lost": _h_card_lost,
    "card_unlost": _h_card_unlost,
    "wealth_subscribe": _h_wealth_subscribe,
    "wealth_redeem": _h_wealth_redeem,
    "card_limit_adjust": _h_card_limit_adjust,
    "card_restrict": _h_card_restrict,
    "card_unrestrict": _h_card_unrestrict,
    "list_evidence": _h_list_evidence,
    "update_evidence": _h_update_evidence,
    "list_rule_versions": _h_list_rule_versions,
    "propose_split_rule": _h_propose_split_rule,
    "confirm_split_rule": _h_confirm_split_rule,
    "list_subscriptions": _h_list_subscriptions,
}

# 参数校验契约（必需/可选）
TOOL_SCHEMA = {
    "transfer": {"required": ["amount_cents"],
                 "optional": ["payee_contact_id", "payee_external", "memo",
                              "idempotency_key", "scheduled_at", "force_timeout"]},
    "space_progress_query": {"required": ["space_id"]},
    "subscribe_query": {"required": ["sub_id"]},
    "subscribe_cancel": {"required": ["sub_id"]},
    "virtual_card_apply": {"optional": ["limit_cents"]},
    "card_lost": {"required": ["card_id"]},
    "card_unlost": {"required": ["card_id"]},
    "card_limit_adjust": {"required": ["card_id", "limit_cents"]},
    "card_restrict": {"required": ["card_id"]},
    "card_unrestrict": {"required": ["card_id"]},
    "wealth_subscribe": {"required": ["product_id", "amount_cents"]},
    "wealth_redeem": {"required": ["holding_id", "amount_cents"]},
    "password_change": {"required": ["new_password"]},
    "aa_request": {"required": ["amount_cents", "payers"]},
    "bill_analysis": {"optional": ["year", "month"]},
    "bill_report": {"optional": ["year", "month"]},
    "update_evidence": {"required": ["card_id", "new_status"]},
    "list_rule_versions": {"required": ["expense_item_id"]},
    "propose_split_rule": {"required": ["expense_item_id", "weights"],
                           "optional": ["method", "reason", "total_cents"]},
    "confirm_split_rule": {"required": ["rule_version_id"]},
}


def validate_params(tool_name: str, params: dict) -> dict:
    if tool_name not in TOOL_HANDLERS:
        raise ValueError(f"未注册工具：{tool_name}")
    schema = TOOL_SCHEMA.get(tool_name, {})
    for req in schema.get("required", []):
        if req not in params:
            raise ValueError(f"工具 {tool_name} 缺少参数 {req}")
    return {k: v for k, v in params.items()}


def execute(db: Session, tool_name: str, user_id: int, params: dict) -> dict:
    """直接执行工具（假定权限已通过权限门）。"""
    validate_params(tool_name, params)
    handler = TOOL_HANDLERS[tool_name]
    result = handler(db, user_id, params)
    return result


# ---------------------------------------------------------------------------
# 授权绑定校验
# ---------------------------------------------------------------------------
def _bind_params(tool_name: str, params: dict) -> dict:
    """提取授权需绑定的关键参数。"""
    if tool_name == "transfer":
        return {"amount_cents": params.get("amount_cents"),
                "payee": params.get("payee_contact_id") or params.get("payee_external")}
    return {"amount_cents": params.get("amount_cents"),
            "resource": params.get("sub_id") or params.get("card_id") or
                       params.get("product_id") or params.get("holding_id")}


def authorize(db: Session, *, user_id: int, tool_name: str, params: dict,
              rule_version_id: int | None = None, plan_version: str | None = None) -> Authorization:
    """创建授权（黄色确认或红色强验证通过后）。绑定关键参数。"""
    bound = _bind_params(tool_name, params)
    a = Authorization(
        user_id=user_id, action=tool_name, amount_cents=bound.get("amount_cents"),
        target_resource=str(bound.get("resource")) if bound.get("resource") is not None
        else str(bound.get("payee")),
        rule_version_id=rule_version_id, plan_version=plan_version,
        operation_id=f"op-{uuid.uuid4().hex}", expires_at=_now() + dt.timedelta(minutes=30),
        status="active",
    )
    db.add(a)
    db.commit()
    return a


def _verify_binding(auth: Authorization, tool_name: str, params: dict) -> bool:
    """校验授权与当前参数是否一致；不一致返回 False（旧授权失效）。"""
    bound = _bind_params(tool_name, params)
    if auth.amount_cents is not None and bound.get("amount_cents") is not None:
        if int(auth.amount_cents) != int(bound["amount_cents"]):
            return False
    return True


# ---------------------------------------------------------------------------
# 权限门
# ---------------------------------------------------------------------------
def run_guarded(db: Session, *, tool_name: str, user_id: int, params: dict,
                confirmed: bool = False, authorization_id: int | None = None,
                challenge_id: int | None = None, challenge_code: str | None = None,
                operation_id: str | None = None,
                rule_version_id: int | None = None, plan_version: str | None = None) -> dict:
    """权限门：绿/黄/红分级执行。返回结构化结果。

    - green：自动执行
    - yellow：confirmed=False 时返回 waiting_confirm；True 时创建授权并执行
    - red：未提供 challenge 时签发挑战并返回 waiting_auth（含 challenge_id/
      operation_id/authorization_id）；提供 challenge_id+code 时校验挑战、
      校验授权绑定（参数变更使旧授权失效），通过后执行。
    """
    params = dict(validate_params(tool_name, params))
    amount = int(params.get("amount_cents", 0))
    decision = policy.classify(tool_name, db, user_id, amount)

    def _finish(auth_ref=None, auth_result=None):
        result = execute(db, tool_name, user_id, params)
        audit.record(db, user_id=user_id, action=tool_name,
                     decision_reason=decision.reason,
                     tool_params=params, auth_ref=auth_ref, auth_result=auth_result,
                     business_receipt=result, success=True)
        return {"status": "success", "level": decision.level, "result": result,
                "decision": {"level": decision.level, "reason": decision.reason}}

    if decision.level == "green":
        return _finish()

    if decision.level == "yellow":
        if not confirmed:
            return {"status": "waiting_confirm", "level": "yellow",
                    "detail": params, "reason": decision.reason,
                    "requires_confirm": True}
        auth = authorize(db, user_id=user_id, tool_name=tool_name, params=params,
                         rule_version_id=rule_version_id, plan_version=plan_version)
        return _finish(auth_ref=auth.operation_id, auth_result="confirmed")

    # 红色：强验证
    if challenge_id is None or challenge_code is None:
        # 先创建授权（绑定参数，验证通过后使用），再签发挑战
        auth = authorize(db, user_id=user_id, tool_name=tool_name, params=params,
                         rule_version_id=rule_version_id, plan_version=plan_version)
        chal = challenge_mod.issue_challenge(db, user_id, purpose="strong_verify",
                                             operation_id=auth.operation_id)
        return {"status": "waiting_auth", "level": "red", "reason": decision.reason,
                "challenge": chal, "detail": params, "authorization_id": auth.id,
                "operation_id": auth.operation_id}

    # 校验挑战（防重放、过期、限次、绑定用户与操作）
    challenge_mod.verify_challenge(db, challenge_id, user_id, operation_id or "", challenge_code)
    # 校验授权绑定：参数变更使旧授权失效
    auth = None
    if authorization_id is not None:
        auth = db.query(Authorization).filter(Authorization.id == authorization_id).first()
    if auth is None or auth.status != "active":
        raise PermissionError("授权无效或已过期，请重新授权")
    if not _verify_binding(auth, tool_name, params):
        auth.status = "revoked"
        db.commit()
        raise PermissionError("关键参数已变更，旧授权失效，请重新授权")
    return _finish(auth_ref=auth.operation_id, auth_result="strong_verified")
