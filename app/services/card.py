"""卡片管理服务：虚拟卡申请/进度、额度调整、交易限制/解锁、挂失/解挂。

均由持卡人操作；共享空间不暴露完整卡号。状态来自业务接口（模拟）。
"""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy.orm import Session

from ..models import Card
from . import audit


def _mask(number: str) -> str:
    if len(number) <= 4:
        return "****"
    return number[:4] + " **** **** " + number[-4:]


def apply_virtual_card(db: Session, *, user_id: int, limit_cents: int = 0) -> dict:
    """虚拟卡申请（黄色，需确认）。生成申请单，状态 processing → active。"""
    number = "62" + "".join(str(uuid.uuid4().int)[:14]).zfill(14)
    apply_no = f"AP{uuid.uuid4().hex[:10]}"
    card = Card(user_id=user_id, card_type="virtual", full_number=number,
                masked_number=_mask(number), status="processing",
                limit_cents=limit_cents, apply_no=apply_no)
    db.add(card)
    db.commit()
    audit.record(db, user_id=user_id, action="virtual_card_apply",
                 intent_summary="申请虚拟卡", business_receipt={"apply_no": apply_no},
                 success=True)
    return {"card_id": card.id, "apply_no": apply_no, "status": "processing",
            "masked_number": card.masked_number}


def card_apply_progress(db: Session, *, user_id: int, card_id: int) -> dict:
    card = _own_card(db, user_id, card_id)
    return {"card_id": card.id, "apply_no": card.apply_no, "status": card.status,
            "masked_number": card.masked_number}


def approve_card(db: Session, *, card_id: int) -> dict:
    """模拟业务接口：申请进度推进（测试/演示用，代表银行侧状态变化）。"""
    card = db.query(Card).filter(Card.id == card_id).first()
    if card is None:
        raise ValueError("卡片不存在")
    if card.status == "processing":
        card.status = "active"
        db.commit()
    return {"card_id": card.id, "status": card.status}


def _own_card(db: Session, user_id: int, card_id: int) -> Card:
    card = db.query(Card).filter(Card.id == card_id, Card.user_id == user_id).first()
    if card is None:
        raise PermissionError("卡片不存在或非本人卡片")
    return card


def adjust_limit(db: Session, *, user_id: int, card_id: int, limit_cents: int) -> dict:
    """额度调整（团队保守策略：红色强验证）。"""
    card = _own_card(db, user_id, card_id)
    card.limit_cents = limit_cents
    db.commit()
    audit.record(db, user_id=user_id, action="card_limit_adjust",
                 tool_params={"card_id": card_id, "limit_cents": limit_cents}, success=True)
    return {"card_id": card.id, "limit_cents": limit_cents, "masked_number": card.masked_number}


def restrict_card(db: Session, *, user_id: int, card_id: int, reason: str = "") -> dict:
    """交易限制（团队保守策略：红色）。"""
    card = _own_card(db, user_id, card_id)
    card.status = "restricted"
    db.commit()
    audit.record(db, user_id=user_id, action="card_restrict",
                 tool_params={"card_id": card_id, "reason": reason}, success=True)
    return {"card_id": card.id, "status": card.status}


def unrestrict_card(db: Session, *, user_id: int, card_id: int) -> dict:
    """交易解锁（团队保守策略：红色）。"""
    card = _own_card(db, user_id, card_id)
    if card.status != "restricted":
        raise ValueError("卡片未处于限制状态")
    card.status = "active"
    db.commit()
    audit.record(db, user_id=user_id, action="card_unrestrict",
                 tool_params={"card_id": card_id}, success=True)
    return {"card_id": card.id, "status": card.status}


def report_lost(db: Session, *, user_id: int, card_id: int) -> dict:
    """挂失（红色强验证）。"""
    card = _own_card(db, user_id, card_id)
    card.status = "lost"
    db.commit()
    audit.record(db, user_id=user_id, action="card_lost",
                 tool_params={"card_id": card_id}, success=True)
    return {"card_id": card.id, "status": card.status}


def report_unlost(db: Session, *, user_id: int, card_id: int) -> dict:
    """解挂（红色强验证）。"""
    card = _own_card(db, user_id, card_id)
    if card.status != "lost":
        raise ValueError("卡片未处于挂失状态")
    card.status = "active"
    db.commit()
    audit.record(db, user_id=user_id, action="card_unlost",
                 tool_params={"card_id": card_id}, success=True)
    return {"card_id": card.id, "status": card.status}


def list_cards(db: Session, user_id: int, *, shared_view: bool = False) -> list[dict]:
    """本人卡片列表。shared_view=True 时脱敏更严（不返回完整卡号）。"""
    cards = db.query(Card).filter(Card.user_id == user_id).all()
    return [{"id": c.id, "masked_number": c.masked_number, "status": c.status,
             "limit_cents": c.limit_cents, "apply_no": c.apply_no,
             "full_number": (None if shared_view else c.full_number)}
            for c in cards]
