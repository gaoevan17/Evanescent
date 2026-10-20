"""证据卡驱动核验服务（方案二核心机制之二）。

证据卡关联交易号、商户别名、订阅授权、时间与用户反馈；
证据不足时保持「待核验」，不直接认定盗刷。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import EvidenceCard, Transaction
from . import audit


def list_evidence(db: Session, user_id: int, status: str | None = None) -> list[dict]:
    from ..models import Account
    # 通过交易关联到本人账户，确保只看到自己的证据卡
    q = (
        db.query(EvidenceCard)
        .join(Transaction, Transaction.id == EvidenceCard.transaction_id)
        .join(Account, Account.id == Transaction.account_id)
        .filter(Account.user_id == user_id)
    )
    if status:
        q = q.filter(EvidenceCard.status == status)
    cards = q.all()
    return [{
        "card_id": c.id, "transaction_id": c.transaction_id,
        "merchant_alias": c.merchant_alias, "amount_cents": c.amount_cents,
        "status": c.status, "resolution": c.resolution, "user_feedback": c.user_feedback,
    } for c in cards]


def update_evidence(db: Session, *, card_id: int, user_id: int, feedback: str,
                    new_status: str) -> dict:
    """用户反馈更新证据卡。撤回疑似重复判断、确认订阅等。"""
    from ..models import Account
    card = (
        db.query(EvidenceCard)
        .join(Transaction, Transaction.id == EvidenceCard.transaction_id)
        .join(Account, Account.id == Transaction.account_id)
        .filter(EvidenceCard.id == card_id, Account.user_id == user_id)
        .first()
    )
    if card is None:
        raise PermissionError("证据卡不存在或不属于当前用户")
    allowed = {"pending", "confirmed_normal", "confirmed_duplicate",
               "confirmed_subscription", "fraud_suspected"}
    if new_status not in allowed:
        raise ValueError("非法证据卡状态")
    card.user_feedback = feedback
    card.status = new_status
    card.resolution = (card.resolution or "") + f" | 用户反馈：{feedback} → {new_status}"
    db.commit()
    audit.record(db, user_id=user_id, action="evidence_update",
                 intent_summary=f"证据卡 {card_id} 核验",
                 decision_reason=f"反馈：{feedback}；新状态：{new_status}",
                 tool_params={"card_id": card_id, "feedback": feedback, "new_status": new_status},
                 success=True)
    return {"card_id": card_id, "status": new_status, "merchant_alias": card.merchant_alias,
            "amount_cents": card.amount_cents}
