"""资金原子操作核心：金额守恒、幂等、并发安全、日累计校验。

所有资金变动（扣款/入账/预留占用）必须经过本模块；禁止直接改余额。
金额一律整数分。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..models import Account, Transaction

# 进程内资金操作锁：串行化关键资金变更，配合事务内重读校验，
# 防止两笔并发转账通过「占用 + 执行」间隙绕过日累计/余额校验。
_money_lock = threading.RLock()


class MoneyError(Exception):
    pass


@dataclass
class MoneyResult:
    ok: bool
    tx_id: int | None
    business_no: str | None
    duplicate: bool = False
    message: str = ""


def _find_by_idempotency(db: Session, idempotency_key: str) -> Transaction | None:
    return db.query(Transaction).filter(Transaction.idempotency_key == idempotency_key).first()


def debit(db: Session, *, account_id: int, amount_cents: int,
          idempotency_key: str, tx_type: str, counterparty: str | None = None,
          merchant: str | None = None, memo: str = "", business_no: str | None = None,
          category: str = "其他", allow_negative: bool = False) -> MoneyResult:
    """原子扣款。返回 MoneyResult。幂等：同 key 重复调用返回原交易不重复扣款。

    使用原子 UPDATE（balance = balance - amount WHERE balance >= amount）避免并发丢失更新。
    """
    if amount_cents <= 0:
        raise MoneyError("扣款金额必须为正整数分")
    with _money_lock:
        existing = _find_by_idempotency(db, idempotency_key)
        if existing is not None:
            return MoneyResult(ok=existing.status == "success", tx_id=existing.id,
                               business_no=existing.business_no, duplicate=True,
                               message="幂等命中：返回原交易，不重复扣款")
        from sqlalchemy import update
        cond = (Account.balance_cents >= amount_cents) if not allow_negative else None
        stmt = update(Account).where(Account.id == account_id)
        if cond is not None:
            stmt = stmt.where(Account.balance_cents >= amount_cents)
        stmt = stmt.values(balance_cents=Account.balance_cents - amount_cents)
        res = db.execute(stmt)
        if res.rowcount == 0:
            acct = db.query(Account).filter(Account.id == account_id).first()
            if acct is None:
                raise MoneyError("账户不存在")
            raise MoneyError(f"余额不足：当前 {acct.balance_cents} 分，需 {amount_cents} 分")
        tx = Transaction(
            account_id=account_id, tx_type=tx_type, amount_cents=amount_cents,
            counterparty=counterparty, merchant=merchant, memo=memo,
            category=category, status="success", idempotency_key=idempotency_key,
            business_no=business_no,
        )
        db.add(tx)
        db.commit()
        return MoneyResult(ok=True, tx_id=tx.id, business_no=tx.business_no, message="扣款成功")


def credit(db: Session, *, account_id: int, amount_cents: int,
           idempotency_key: str, tx_type: str, counterparty: str | None = None,
           merchant: str | None = None, memo: str = "", business_no: str | None = None,
           category: str = "其他") -> MoneyResult:
    """原子入账。"""
    if amount_cents <= 0:
        raise MoneyError("入账金额必须为正整数分")
    with _money_lock:
        existing = _find_by_idempotency(db, idempotency_key)
        if existing is not None:
            return MoneyResult(ok=True, tx_id=existing.id, business_no=existing.business_no,
                               duplicate=True, message="幂等命中")
        from sqlalchemy import update
        stmt = (update(Account).where(Account.id == account_id)
                .values(balance_cents=Account.balance_cents + amount_cents))
        db.execute(stmt)
        tx = Transaction(
            account_id=account_id, tx_type=tx_type, amount_cents=amount_cents,
            counterparty=counterparty, merchant=merchant, memo=memo,
            category=category, status="success", idempotency_key=idempotency_key,
            business_no=business_no,
        )
        db.add(tx)
        db.commit()
        return MoneyResult(ok=True, tx_id=tx.id, business_no=tx.business_no, message="入账成功")


def mark_tx_unknown(db: Session, *, account_id: int, amount_cents: int,
                    idempotency_key: str, tx_type: str, memo: str = "") -> Transaction:
    """登记一笔「结果未知」的交易（如响应超时），金额先不落账，待回读确认。"""
    tx = Transaction(
        account_id=account_id, tx_type=tx_type, amount_cents=amount_cents,
        memo=memo, status="unknown", idempotency_key=idempotency_key,
    )
    db.add(tx)
    db.commit()
    return tx


def get_account(db: Session, user_id: int) -> Account:
    # populate_existing 强制重新读，避免 identity map 返回原子 UPDATE 后的旧值
    return db.query(Account).filter(Account.user_id == user_id).populate_existing().first()


def balance(db: Session, user_id: int) -> int:
    acct = get_account(db, user_id)
    return acct.balance_cents if acct else 0
