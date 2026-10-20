"""智能转账服务：收款人检索/同名澄清、即时/定时转账、AA 收款、幂等与回读。"""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..adapters.bank import get_bank_adapter
from ..models import Account, Contact, PaymentTask, User
from . import audit, money


def search_contacts(db: Session, owner_user_id: int, query: str) -> list[dict]:
    """按姓名/手机号/备注检索收款人，返回候选（可能多个同名）。"""
    q = query.strip()
    contacts = (
        db.query(Contact)
        .filter(Contact.owner_user_id == owner_user_id)
        .filter(or_(
            Contact.name.like(f"%{q}%"),
            Contact.phone.like(f"%{q}%"),
            Contact.memo.like(f"%{q}%"),
        ))
        .all()
    )
    return [{"contact_id": c.id, "name": c.name, "phone": c.phone, "memo": c.memo,
             "is_member": c.is_member, "member_user_id": c.member_user_id} for c in contacts]


def resolve_unique_contact(db: Session, owner_user_id: int, query: str) -> dict | None:
    cands = search_contacts(db, owner_user_id, query)
    if len(cands) == 1:
        return cands[0]
    return None


def _acct(db: Session, user_id: int) -> Account:
    return db.query(Account).filter(Account.user_id == user_id).first()


def transfer(db: Session, *, payer_user_id: int, payee_contact_id: int | None,
             payee_external: str | None = None, amount_cents: int,
             memo: str = "", idempotency_key: str | None = None,
             scheduled_at: dt.datetime | None = None,
             force_timeout: bool = False) -> dict:
    """执行转账。即时或定时（scheduled_at 非空时生成付款任务，执行时重校验）。

    幂等：idempotency_key 命中已存在交易则返回原结果。
    """
    idem = idempotency_key or f"trf-{uuid.uuid4().hex}"
    contact = None
    if payee_contact_id is not None:
        contact = db.query(Contact).filter(Contact.id == payee_contact_id).first()
        if contact is None:
            raise ValueError("收款人不存在")
        if contact.owner_user_id != payer_user_id:
            raise PermissionError("收款人联系人不在本人通讯录")
    payee_name = contact.name if contact else (payee_external or "外部收款人")

    if scheduled_at is not None:
        # 定时转账：生成付款任务，实际执行时重新校验
        from ..models import PaymentTask
        pt = PaymentTask(
            payer_user_id=payer_user_id, payee_user_id=contact.member_user_id if contact else None,
            amount_cents=amount_cents, status="pending",
            idempotency_key=idem, due_at=scheduled_at,
        )
        db.add(pt)
        db.commit()
        return {"status": "scheduled", "payment_task_id": pt.id, "idempotency_key": idem,
                "scheduled_at": scheduled_at.isoformat()}

    adapter = get_bank_adapter()
    payer_acct = _acct(db, payer_user_id)
    if payer_acct is None:
        raise ValueError("付款账户不存在")

    before = money.balance(db, payer_user_id)
    biz_no = f"T{dt.datetime.now().timestamp():.0f}{uuid.uuid4().hex[:6]}"
    res = adapter.transfer_out(
        db, account_id=payer_acct.id, amount_cents=amount_cents, payee=payee_name,
        memo=memo, idempotency_key=idem, business_no=biz_no, force_timeout=force_timeout,
    )
    # 若收款人是系统内成员，入账到对方账户（模拟资金流转）
    if contact and contact.is_member and contact.member_user_id and res.known and res.status == "success":
        payee_acct = _acct(db, contact.member_user_id)
        if payee_acct:
            money.credit(db, account_id=payee_acct.id, amount_cents=amount_cents,
                         idempotency_key=f"{idem}-in", tx_type="transfer_in",
                         counterparty=f"来自成员", memo=memo, business_no=biz_no)

    after = money.balance(db, payer_user_id)
    audit.record(db, user_id=payer_user_id, action="transfer",
                 intent_summary=f"转账 {amount_cents} 分给 {payee_name}",
                 decision_reason=f"收款人：{payee_name}",
                 tool_params={"amount_cents": amount_cents, "payee": payee_name,
                              "memo": memo, "idempotency_key": idem},
                 business_receipt={"business_no": biz_no, "status": res.status},
                 before_state={"balance": before}, after_state={"balance": after},
                 success=res.known and res.status == "success")
    return {"status": res.status, "known": res.known, "business_no": biz_no,
            "idempotency_key": idem, "amount_cents": amount_cents, "payee": payee_name}


def query_transfer(db: Session, business_no: str) -> dict:
    """回读转账最终状态（用于超时恢复）。"""
    adapter = get_bank_adapter()
    res = adapter.query_transfer(db, business_no)
    return {"business_no": business_no, "status": res.status, "known": res.known,
            "message": res.message}


def request_aa(db: Session, *, requester_user_id: int, space_id: int | None,
               title: str, payers: list[int], amount_cents: int,
               idempotency_key: str | None = None) -> dict:
    """AA 收款请求：生成各付款人的付款草稿。"""
    idem = idempotency_key or f"aa-{uuid.uuid4().hex}"
    drafts = []
    n = len(payers) if payers else 1
    from .ledger import largest_remainder
    amounts = largest_remainder(amount_cents, {u: 1 for u in payers}) if payers else {requester_user_id: amount_cents}
    for u, amt in amounts.items():
        pt = PaymentTask(
            space_id=space_id, payee_user_id=requester_user_id, amount_cents=amt,
            status="pending", idempotency_key=f"{idem}-{u}",
        )
        db.add(pt)
        drafts.append({"payer_user_id": u, "amount_cents": amt, "payment_task_id": pt.id})
    db.commit()
    return {"status": "draft_created", "idempotency_key": idem, "drafts": drafts}
