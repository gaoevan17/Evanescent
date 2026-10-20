"""确定性权限策略引擎。

权限等级由本模块（确定性程序）判定，模型、前端或用户文本均不能自行降级。
等级定义以官网为准；团队保守策略（如赎回按红色）在此显式标注。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import settings

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo(settings.TZ)
except Exception:  # 无 tzdata 时回退固定 UTC+8
    TZ = dt.timezone(dt.timedelta(hours=8))

# 日累计转账限额（分）：超过 1000 元进入红色强验证
DAILY_TRANSFER_LIMIT_CENTS = 100_000  # 1000 元 = 100000 分


@dataclass(frozen=True)
class PolicyDecision:
    action: str
    level: str  # green | yellow | red
    reason: str
    requires_confirm: bool
    requires_strong_verify: bool
    daily_total_cents: int = 0


# 绿色：纯查询，本人身份/访问范围合法且意图明确后自动执行
GREEN_ACTIONS = {
    "balance_query": "余额查询",
    "transaction_list": "交易记录查询",
    "bill_analysis": "账单分析",
    "bill_report": "月度/年度账单报告",
    "wealth_income_query": "理财收益查询",
    "holding_query": "持仓查询",
    "product_list": "理财产品列表",
    "risk_profile_query": "风险问卷查询",
    "card_list": "本人卡片列表（脱敏）",
    "space_progress_query": "共同空间结算进度（共享视图）",
    "list_evidence": "证据卡列表",
    "list_rule_versions": "规则版本列表",
    "list_subscriptions": "本人订阅列表",
}

# 黄色：先展示详情并取得明确确认
YELLOW_ACTIONS = {
    "transfer": "转账（日累计≤1000元）",
    "subscribe_query": "订阅授权查询",
    "subscribe_cancel": "取消订阅",
    "virtual_card_apply": "虚拟卡申请",
    "aa_request": "AA 收款请求草稿",
    "update_evidence": "证据卡反馈核验",
    "propose_split_rule": "提出分摊规则",
    "confirm_split_rule": "确认分摊规则",
}

# 红色：需要强验证
RED_ACTIONS = {
    "password_change": "密码修改",
    "card_lost": "卡片挂失",
    "card_unlost": "卡片解挂",
    "wealth_subscribe": "理财产品申购",
    # --- 团队保守策略（E-01）：官网未逐项明确，保守按红色处理 ---
    "wealth_redeem": "理财产品赎回（团队保守策略）",
    "card_limit_adjust": "额度调整（团队保守策略）",
    "card_restrict": "交易限制（团队保守策略）",
    "card_unrestrict": "交易解锁（团队保守策略）",
}

# 工具白名单：所有可被 DAG 调用的业务动作
TOOL_WHITELIST = sorted(set(GREEN_ACTIONS) | set(YELLOW_ACTIONS) | set(RED_ACTIONS) | {"transfer"})


def _now_shanghai() -> dt.datetime:
    return dt.datetime.now(TZ)


def _day_start_end() -> tuple[dt.datetime, dt.datetime]:
    """返回北京时间的当日 [起, 止) 的 UTC 时间。"""
    now_sh = _now_shanghai()
    start_sh = now_sh.replace(hour=0, minute=0, second=0, microsecond=0)
    end_sh = start_sh + dt.timedelta(days=1)
    return start_sh.astimezone(dt.timezone.utc), end_sh.astimezone(dt.timezone.utc)


def daily_transfer_total(db: Session, user_id: int) -> int:
    """当日该用户已成功转出的金额（分）。"""
    from ..models import Account, Transaction
    start, end = _day_start_end()
    acct = db.query(Account).filter(Account.user_id == user_id).first()
    if acct is None:
        return 0
    total = (
        db.query(func.coalesce(func.sum(Transaction.amount_cents), 0))
        .filter(
            Transaction.account_id == acct.id,
            Transaction.tx_type == "transfer_out",
            Transaction.status.in_(["success", "pending"]),  # 成功 + 执行中占用
            Transaction.created_at >= start,
            Transaction.created_at < end,
        )
        .scalar()
    )
    return int(total)


def classify_transfer(db: Session, user_id: int, amount_cents: int) -> PolicyDecision:
    """转账等级：按当日累计（含本次）判定黄/红。"""
    already = daily_transfer_total(db, user_id)
    total = already + amount_cents
    if total > DAILY_TRANSFER_LIMIT_CENTS:
        return PolicyDecision(
            action="transfer", level="red",
            reason=f"当日累计转账 {total} 分(含本次) 超过 {DAILY_TRANSFER_LIMIT_CENTS} 分(1000元)上限，需强验证",
            requires_confirm=True, requires_strong_verify=True, daily_total_cents=total,
        )
    return PolicyDecision(
        action="transfer", level="yellow",
        reason=f"当日累计转账 {total} 分(含本次) 未超 1000 元上限，展示详情后确认即可",
        requires_confirm=True, requires_strong_verify=False, daily_total_cents=total,
    )


def classify(action: str, db: Session | None = None, user_id: int | None = None,
             amount_cents: int = 0) -> PolicyDecision:
    """动作 → 权限等级。转账需 db + user_id 计算日累计。"""
    if action == "transfer":
        if db is None or user_id is None:
            raise ValueError("转账权限判定需要数据库会话与用户身份")
        return classify_transfer(db, user_id, amount_cents)

    if action in GREEN_ACTIONS:
        return PolicyDecision(action=action, level="green", reason=GREEN_ACTIONS[action],
                              requires_confirm=False, requires_strong_verify=False)
    if action in YELLOW_ACTIONS:
        return PolicyDecision(action=action, level="yellow", reason=YELLOW_ACTIONS[action],
                              requires_confirm=True, requires_strong_verify=False)
    if action in RED_ACTIONS:
        return PolicyDecision(action=action, level="red", reason=RED_ACTIONS[action],
                              requires_confirm=True, requires_strong_verify=True)
    raise ValueError(f"未注册的动作：{action}")


def is_known_action(action: str) -> bool:
    return action in TOOL_WHITELIST
