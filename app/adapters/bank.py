"""银行适配层：可替换的模拟银行后端。

SimulatedBankAdapter 是模拟实现，用于演示与测试。它直接操作本地数据库
以模拟「银行侧」的真实状态，支持注入「扣款成功但响应超时」的故障。
正式环境可替换为真实银行接口；模拟接口是否符合主办方验收口径为待确认事项。
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..models import Card, Subscription, Transaction
from ..services import money


@dataclass
class BankResult:
    known: bool          # 系统是否已确知结果
    status: str          # 银行侧真实状态
    business_no: str
    message: str = ""


class BankAdapter:
    """抽象接口。"""

    def transfer_out(self, db: Session, *, account_id: int, amount_cents: int,
                     payee: str, memo: str, idempotency_key: str,
                     business_no: str, force_timeout: bool = False) -> BankResult:
        raise NotImplementedError

    def query_transfer(self, db: Session, business_no: str) -> BankResult:
        raise NotImplementedError


class SimulatedBankAdapter(BankAdapter):
    """模拟银行：真实扣款落账，支持超时注入。"""

    def transfer_out(self, db: Session, *, account_id: int, amount_cents: int,
                     payee: str, memo: str, idempotency_key: str,
                     business_no: str, force_timeout: bool = False) -> BankResult:
        # 先幂等检查
        existing = db.query(Transaction).filter(
            Transaction.idempotency_key == idempotency_key).first()
        if existing is not None:
            return BankResult(known=True, status=existing.bank_status,
                              business_no=existing.business_no, message="幂等命中")

        # 银行侧真实扣款
        res = money.debit(db, account_id=account_id, amount_cents=amount_cents,
                          idempotency_key=idempotency_key, tx_type="transfer_out",
                          counterparty=payee, memo=memo, business_no=business_no,
                          category="转账")
        tx = db.query(Transaction).filter(Transaction.id == res.tx_id).first()
        if force_timeout:
            # 银行实际成功，但系统未收到回执 → 系统侧标 unknown
            tx.bank_status = "success"
            tx.status = "unknown"
            db.commit()
            return BankResult(known=False, status="unknown", business_no=business_no,
                              message="扣款已成功但响应超时（结果未知）")
        tx.bank_status = "success"
        tx.status = "success"
        db.commit()
        return BankResult(known=True, status="success", business_no=business_no,
                          message="转账成功")

    def query_transfer(self, db: Session, business_no: str) -> BankResult:
        tx = db.query(Transaction).filter(Transaction.business_no == business_no).first()
        if tx is None:
            return BankResult(known=False, status="unknown", business_no=business_no,
                              message="未找到交易")
        if tx.bank_status == "success" and tx.status == "unknown":
            # 回读恢复：确认真实成功，补回执
            tx.status = "success"
            db.commit()
        return BankResult(known=True, status=tx.bank_status, business_no=business_no,
                          message=f"银行侧状态：{tx.bank_status}")


def get_bank_adapter() -> BankAdapter:
    return SimulatedBankAdapter()
