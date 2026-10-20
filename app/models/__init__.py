"""SQLAlchemy 数据模型。金额一律使用整数分（Integer）。"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Text, JSON, UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship

from ..db import Base


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# ---------------------------------------------------------------------------
# 用户、账户、共同空间
# ---------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    display_name = Column(String(64), nullable=False)
    phone = Column(String(32), unique=True, nullable=True)
    password_hash = Column(String(128), nullable=False)
    is_demo = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=_utcnow)


class Account(Base):
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    balance_cents = Column(Integer, nullable=False, default=0)  # 可用余额（分）
    currency = Column(String(8), default="CNY", nullable=False)
    status = Column(String(16), default="active", nullable=False)  # active|frozen|locked

    user = relationship("User")


class SharedSpace(Base):
    __tablename__ = "shared_spaces"
    id = Column(Integer, primary_key=True)
    name = Column(String(64), nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=_utcnow)


class SpaceMember(Base):
    __tablename__ = "space_members"
    id = Column(Integer, primary_key=True)
    space_id = Column(Integer, ForeignKey("shared_spaces.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    role = Column(String(16), default="member", nullable=False)  # owner|member
    joined_at = Column(DateTime, default=_utcnow)
    active = Column(Boolean, default=True, nullable=False)  # 临时退出用

    __table_args__ = (UniqueConstraint("space_id", "user_id", name="uq_space_user"),)


# ---------------------------------------------------------------------------
# 共识账本：费用项目、规则版本、分摊、确认
# ---------------------------------------------------------------------------
class ExpenseItem(Base):
    __tablename__ = "expense_items"
    id = Column(Integer, primary_key=True)
    space_id = Column(Integer, ForeignKey("shared_spaces.id"), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    category = Column(String(32), nullable=False)  # 电费|宽带|订阅|其他
    total_cents = Column(Integer, nullable=False)
    split_method = Column(String(32), nullable=False)  # days|equal|weight
    status = Column(String(16), default="draft", nullable=False)  # draft|pending|confirmed|disputed
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=_utcnow)


class RuleVersion(Base):
    """有版本的共识规则。每次变更产生新版本，旧版本只读。"""
    __tablename__ = "rule_versions"
    id = Column(Integer, primary_key=True)
    expense_item_id = Column(Integer, ForeignKey("expense_items.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    split_config = Column(JSON, nullable=False)  # 分摊配置：{user_id: {weight, days, note}}
    effective_from = Column(DateTime, default=_utcnow)
    status = Column(String(16), default="pending", nullable=False)  # pending|confirmed|superseded|disputed
    superseded_by = Column(Integer, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"))
    reason = Column(Text, default="")

    __table_args__ = (UniqueConstraint("expense_item_id", "version", name="uq_item_version"),)


class RuleConfirmation(Base):
    __tablename__ = "rule_confirmations"
    id = Column(Integer, primary_key=True)
    rule_version_id = Column(Integer, ForeignKey("rule_versions.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    confirmed = Column(Boolean, default=False, nullable=False)
    confirmed_at = Column(DateTime, nullable=True)
    note = Column(Text, default="")

    __table_args__ = (UniqueConstraint("rule_version_id", "user_id", name="uq_confirm_user"),)


# ---------------------------------------------------------------------------
# 付款任务与授权
# ---------------------------------------------------------------------------
class PaymentTask(Base):
    __tablename__ = "payment_tasks"
    id = Column(Integer, primary_key=True)
    space_id = Column(Integer, ForeignKey("shared_spaces.id"), nullable=True, index=True)
    expense_item_id = Column(Integer, ForeignKey("expense_items.id"), nullable=True)
    rule_version_id = Column(Integer, ForeignKey("rule_versions.id"), nullable=True)
    payer_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    payee_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # 收款人（可空=商户/共同账户模拟）
    amount_cents = Column(Integer, nullable=False)
    status = Column(String(24), default="pending", nullable=False)
    # pending|confirmed|authorized|executing|success|failed|unknown|cancelled|frozen
    idempotency_key = Column(String(64), unique=True, nullable=False)
    due_at = Column(DateTime, nullable=True)  # 定时任务执行时间
    business_no = Column(String(64), nullable=True)
    frozen_reason = Column(Text, default="")
    created_at = Column(DateTime, default=_utcnow)


class Authorization(Base):
    __tablename__ = "authorizations"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    action = Column(String(32), nullable=False)  # transfer|subscribe_query|subscribe_cancel|...
    amount_cents = Column(Integer, nullable=True)
    target_resource = Column(String(128), nullable=True)  # 收款人/资源标识
    plan_version = Column(String(64), nullable=True)
    rule_version_id = Column(Integer, nullable=True)
    operation_id = Column(String(64), unique=True, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    status = Column(String(16), default="active", nullable=False)  # active|used|expired|revoked
    challenge_id = Column(Integer, nullable=True)  # 强验证关联
    created_at = Column(DateTime, default=_utcnow)


# ---------------------------------------------------------------------------
# 证据卡（异常核验）
# ---------------------------------------------------------------------------
class EvidenceCard(Base):
    __tablename__ = "evidence_cards"
    id = Column(Integer, primary_key=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)
    merchant_alias = Column(String(128), nullable=True)
    subscription_auth_ref = Column(String(128), nullable=True)
    amount_cents = Column(Integer, nullable=False)
    occurred_at = Column(DateTime, nullable=True)
    status = Column(String(24), default="pending", nullable=False)
    # pending(待核验)|confirmed_normal|confirmed_duplicate|confirmed_subscription|fraud_suspected
    user_feedback = Column(Text, default="")
    resolution = Column(Text, default="")
    created_at = Column(DateTime, default=_utcnow)


# ---------------------------------------------------------------------------
# 交易流水
# ---------------------------------------------------------------------------
class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)
    tx_type = Column(String(24), nullable=False)  # debit|credit|transfer_out|transfer_in
    amount_cents = Column(Integer, nullable=False)  # 正数；方向由 tx_type 决定
    counterparty = Column(String(128), nullable=True)
    merchant = Column(String(128), nullable=True)
    memo = Column(Text, default="")  # 不可信数据
    category = Column(String(32), default="其他")
    status = Column(String(24), default="success", nullable=False)  # pending|success|failed|unknown
    bank_status = Column(String(24), default="success", nullable=False)  # 银行侧真实状态（适配层回读）
    idempotency_key = Column(String(64), unique=True, nullable=False)
    business_no = Column(String(64), nullable=True)
    ref_tx_id = Column(Integer, nullable=True)  # 退款/补偿关联
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (Index("ix_tx_account_time", "account_id", "created_at"),)


class Contact(Base):
    """联系人/收款人。按姓名、手机号或备注检索；同名由用户澄清。"""
    __tablename__ = "contacts"
    id = Column(Integer, primary_key=True)
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(64), nullable=False, index=True)
    phone = Column(String(32), nullable=True, index=True)
    memo = Column(String(128), default="")
    is_member = Column(Boolean, default=False, nullable=False)  # 是否为系统内成员
    member_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=_utcnow)


# ---------------------------------------------------------------------------
# 卡片
# ---------------------------------------------------------------------------
class Card(Base):
    __tablename__ = "cards"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    card_type = Column(String(16), default="virtual", nullable=False)  # virtual
    full_number = Column(String(32), nullable=False)  # 仅本人会话可用，共享空间不得返回
    masked_number = Column(String(32), nullable=False)
    status = Column(String(24), default="active", nullable=False)
    # active|processing|restricted|lost|unlocked
    limit_cents = Column(Integer, default=0, nullable=False)
    apply_no = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=_utcnow)


# ---------------------------------------------------------------------------
# 订阅 / 代扣
# ---------------------------------------------------------------------------
class Subscription(Base):
    __tablename__ = "subscriptions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    merchant = Column(String(128), nullable=False)
    alias = Column(String(128), nullable=True)  # 商户别名（不可信）
    amount_cents = Column(Integer, nullable=False)
    cycle = Column(String(16), default="monthly", nullable=False)
    status = Column(String(24), default="active", nullable=False)  # active|cancelled|cancel_pending
    authorized = Column(Boolean, default=True, nullable=False)
    card_id = Column(Integer, ForeignKey("cards.id"), nullable=True)
    next_charge_at = Column(DateTime, nullable=True)
    effective_change_at = Column(DateTime, nullable=True)  # 取消生效时间
    created_at = Column(DateTime, default=_utcnow)


# ---------------------------------------------------------------------------
# 理财
# ---------------------------------------------------------------------------
class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    risk_level = Column(String(16), nullable=False)  # R1..R5
    min_cents = Column(Integer, nullable=False)
    term_days = Column(Integer, nullable=False)
    fee_rate = Column(Integer, default=0, nullable=False)  # 万分数
    redemption_rule = Column(String(128), default="T+1 到账", nullable=False)
    is_synthetic = Column(Boolean, default=True, nullable=False)  # 均为合成模拟产品


class RiskProfile(Base):
    __tablename__ = "risk_profiles"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    answers = Column(JSON, nullable=True)
    risk_level = Column(String(16), nullable=False)  # 保守|稳健|平衡|进取|激进


class Holding(Base):
    __tablename__ = "holdings"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    amount_cents = Column(Integer, nullable=False, default=0)


class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    order_type = Column(String(16), nullable=False)  # subscribe|redeem
    amount_cents = Column(Integer, nullable=False)
    status = Column(String(24), default="pending", nullable=False)
    # pending|processing|settled|failed|cancelled|credited(赎回到账)
    idempotency_key = Column(String(64), unique=True, nullable=False)
    business_no = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=_utcnow)


# ---------------------------------------------------------------------------
# DAG 计划与节点
# ---------------------------------------------------------------------------
class DagPlan(Base):
    __tablename__ = "dag_plans"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    space_id = Column(Integer, ForeignKey("shared_spaces.id"), nullable=True)
    intent_summary = Column(Text, default="")
    plan_version = Column(String(64), nullable=False)
    status = Column(String(24), default="running", nullable=False)
    # running|paused|waiting_auth|completed|failed|cancelled|superseded
    original_text = Column(Text, default="")
    created_at = Column(DateTime, default=_utcnow)


class DagNode(Base):
    __tablename__ = "dag_nodes"
    id = Column(Integer, primary_key=True)
    plan_id = Column(Integer, ForeignKey("dag_plans.id"), nullable=False, index=True)
    node_key = Column(String(64), nullable=False)
    tool_name = Column(String(64), nullable=False)
    params = Column(JSON, default=dict)
    depends_on = Column(JSON, default=list)  # 依赖的 node_key 列表
    status = Column(String(24), default="pending", nullable=False)
    # pending|running|waiting_confirm|waiting_auth|success|failed|unknown|frozen|skipped|cancelled
    result = Column(JSON, nullable=True)
    error = Column(Text, default="")
    idempotency_key = Column(String(64), nullable=True)
    retries = Column(Integer, default=0)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


# ---------------------------------------------------------------------------
# 会话与认证挑战
# ---------------------------------------------------------------------------
class Session(Base):
    __tablename__ = "sessions"
    id = Column(Integer, primary_key=True)
    token_hash = Column(String(128), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=_utcnow)


class AuthChallenge(Base):
    __tablename__ = "auth_challenges"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    purpose = Column(String(64), nullable=False)  # strong_verify
    challenge_type = Column(String(16), default="sms_otp", nullable=False)  # 模拟短信 OTP
    code_hash = Column(String(128), nullable=False)
    operation_id = Column(String(64), nullable=False)  # 绑定具体操作
    expires_at = Column(DateTime, nullable=False)
    attempts = Column(Integer, default=0)
    consumed = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=_utcnow)


# ---------------------------------------------------------------------------
# 审计
# ---------------------------------------------------------------------------
class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=True, index=True)
    action = Column(String(64), nullable=False)
    intent_summary = Column(Text, default="")
    plan_version = Column(String(64), nullable=True)
    decision_reason = Column(Text, default="")
    evidence_ref = Column(JSON, default=list)
    tool_params = Column(JSON, nullable=True)
    auth_ref = Column(String(128), nullable=True)
    auth_result = Column(String(24), nullable=True)
    business_receipt = Column(JSON, nullable=True)
    before_state = Column(JSON, nullable=True)
    after_state = Column(JSON, nullable=True)
    success = Column(Boolean, default=False)
    created_at = Column(DateTime, default=_utcnow)


# ---------------------------------------------------------------------------
# 熔断 / 安全锁定
# ---------------------------------------------------------------------------
class SecurityLock(Base):
    __tablename__ = "security_locks"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    lock_type = Column(String(32), default="circuit_breaker", nullable=False)
    reason = Column(Text, default="")
    unlock_condition = Column(Text, default="")
    locked_until = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
