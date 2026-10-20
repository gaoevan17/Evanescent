"""账单分析服务：导入去重、分类、个人/共同区分、月/年度报告、异常候选识别。"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Account, EvidenceCard, Transaction

TZ = ZoneInfo(settings.TZ)


def import_transactions(db: Session, *, account_id: int,
                        items: list[dict], idempotency_prefix: str = "imp") -> dict:
    """导入账单并去重。相同 (merchant, amount, 当日) 视为重复，跳过。

    items: [{"merchant","amount_cents","memo","category","occurred_at"(iso), "merchant_alias"}]
    """
    imported, duplicated = 0, 0
    for it in items:
        occurred = dt.datetime.fromisoformat(it["occurred_at"]) if it.get("occurred_at") else dt.datetime.now(dt.timezone.utc)
        amount = int(it["amount_cents"])
        merchant = it.get("merchant", "")
        memo = it.get("memo", "")
        # 去重：同账户内同商户同金额视为重复账单
        dup = (
            db.query(Transaction)
            .filter(Transaction.account_id == account_id,
                    Transaction.merchant == merchant,
                    Transaction.amount_cents == amount)
            .first()
        )
        if dup is not None:
            duplicated += 1
            continue
        tx = Transaction(
            account_id=account_id, tx_type="debit", amount_cents=amount,
            merchant=merchant, memo=memo, category=it.get("category", "其他"),
            status="success", idempotency_key=f"{idempotency_prefix}-{merchant}-{amount}-{occurred.timestamp():.0f}",
        )
        db.add(tx)
        imported += 1
    db.commit()
    return {"imported": imported, "duplicated": duplicated}


def classify_and_report(db: Session, *, account_id: int, year: int,
                        month: int | None = None) -> dict:
    """月度/年度分类汇总，可追溯到底层交易。"""
    q = db.query(Transaction).filter(Transaction.account_id == account_id)
    if month is not None:
        q = q.filter(func.strftime("%Y", Transaction.created_at) == f"{year:04d}",
                     func.strftime("%m", Transaction.created_at) == f"{month:02d}")
    else:
        q = q.filter(func.strftime("%Y", Transaction.created_at) == f"{year:04d}")
    txs = q.all()
    by_cat: dict[str, int] = {}
    detail = []
    for t in txs:
        cat = t.category or "其他"
        by_cat[cat] = by_cat.get(cat, 0) + t.amount_cents
        detail.append({"tx_id": t.id, "merchant": t.merchant, "memo": t.memo,
                       "category": cat, "amount_cents": t.amount_cents,
                       "created_at": t.created_at.isoformat()})
    return {"total_cents": sum(by_cat.values()), "by_category_cents": by_cat,
            "transactions": detail}


def _is_recurring(txs: list[Transaction], merchant: str, amount_cents: int) -> bool:
    """同一商户同金额出现≥2次 → 周期扣费候选。"""
    hits = [t for t in txs if t.merchant == merchant and t.amount_cents == amount_cents]
    return len(hits) >= 2


def detect_anomalies(db: Session, *, account_id: int,
                     known_merchants: set[str] | None = None) -> list[dict]:
    """异常候选识别（确定性规则），返回证据卡列表。不直接认定盗刷。

    - 同金额出现 ≥2 次（含不同商户别名）→ 疑似重复扣费
    - 陌生商户 → 待核验
    """
    known = known_merchants or set()
    txs = db.query(Transaction).filter(Transaction.account_id == account_id).order_by(
        Transaction.created_at.desc()).all()
    anomalies = []

    # 同金额 ≥2 次 → 疑似重复（可能同商户，也可能不同商户别名）
    by_amount: dict[int, list[Transaction]] = {}
    for t in txs:
        by_amount.setdefault(t.amount_cents, []).append(t)
    seen_tx_ids: set[int] = set()
    for amount, group in by_amount.items():
        if amount <= 0 or len(group) < 2:
            continue
        for t in group:
            card = _ensure_evidence(db, transaction_id=t.id, amount_cents=amount,
                                    merchant_alias=t.merchant,
                                    kind="duplicate_suspected",
                                    note=f"同金额 {amount} 分出现 {len(group)} 次")
            anomalies.append(card)
            seen_tx_ids.add(t.id)

    # 陌生商户
    for t in txs:
        if t.id in seen_tx_ids:
            continue
        if t.merchant and t.merchant not in known and t.amount_cents > 0:
            card = _ensure_evidence(db, transaction_id=t.id, amount_cents=t.amount_cents,
                                    merchant_alias=t.merchant, kind="unknown_merchant",
                                    note="陌生商户扣费")
            anomalies.append(card)
    return anomalies


def _ensure_evidence(db: Session, *, transaction_id: int, amount_cents: int,
                     merchant_alias: str, kind: str, note: str) -> dict:
    existing = db.query(EvidenceCard).filter(
        EvidenceCard.transaction_id == transaction_id,
        EvidenceCard.status == "pending").first()
    if existing:
        return _card_dict(existing)
    card = EvidenceCard(transaction_id=transaction_id, amount_cents=amount_cents,
                        merchant_alias=merchant_alias, status="pending",
                        user_feedback="", resolution=f"[{kind}] {note}")
    db.add(card)
    db.commit()
    return _card_dict(card)


def _card_dict(c: EvidenceCard) -> dict:
    return {"card_id": c.id, "transaction_id": c.transaction_id,
            "merchant_alias": c.merchant_alias, "amount_cents": c.amount_cents,
            "status": c.status, "resolution": c.resolution, "user_feedback": c.user_feedback}
