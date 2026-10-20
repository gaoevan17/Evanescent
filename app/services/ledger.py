"""共识账本服务：有版本的共识规则 + 最大余数法分摊 + 规则变更影响传播。

金额一律整数分。分摊算法使用公开、稳定的「最大余数法（Hamilton 法）」，
以分为单位守恒（总和恰好等于总额）。
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy.orm import Session

from ..models import (
    Authorization, ExpenseItem, PaymentTask, RuleConfirmation, RuleVersion,
    SharedSpace, SpaceMember,
)
from . import audit


def largest_remainder(total_cents: int, weights: dict[int, int]) -> dict[int, int]:
    """最大余数法分摊。weights: {user_id: 权重}。

    返回 {user_id: amount_cents}，满足 sum == total_cents。
    """
    assert total_cents >= 0
    if not weights:
        return {}
    total_weight = sum(weights.values())
    if total_weight <= 0:
        raise ValueError("分摊权重总和必须为正")
    quotas: dict[int, float] = {u: total_cents * w / total_weight for u, w in weights.items()}
    floors: dict[int, int] = {u: int(q) for u, q in quotas.items()}
    remain = total_cents - sum(floors.values())
    # 按小数余数降序，逐个补 1 分
    order = sorted(weights.keys(), key=lambda u: (quotas[u] - floors[u]), reverse=True)
    for i in range(remain):
        floors[order[i % len(order)]] += 1
    assert sum(floors.values()) == total_cents, "最大余数法守恒校验失败"
    return floors


def _split_method_from_config(split_config: dict[str, Any]) -> str:
    return split_config.get("method", "equal")


def compute_split(total_cents: int, split_config: dict[str, Any]) -> dict[int, int]:
    """根据分摊配置计算 {user_id: amount_cents}。

    split_config: {"method": "days"|"equal"|"weight", "weights": {str(user_id): int}}
    """
    method = _split_method_from_config(split_config)
    raw_weights = split_config.get("weights", {})
    weights = {int(k): int(v) for k, v in raw_weights.items()}
    if method == "equal":
        # 均分：每人权重 1
        weights = {u: 1 for u in weights}
    return largest_remainder(total_cents, weights)


# ---------------------------------------------------------------------------
# 规则版本与确认
# ---------------------------------------------------------------------------
def _next_version(db: Session, expense_item_id: int) -> int:
    cur = (
        db.query(RuleVersion)
        .filter(RuleVersion.expense_item_id == expense_item_id)
        .order_by(RuleVersion.version.desc())
        .first()
    )
    return (cur.version + 1) if cur else 1


def create_rule_version(db: Session, *, expense_item_id: int, split_config: dict[str, Any],
                        created_by: int, reason: str = "") -> RuleVersion:
    v = _next_version(db, expense_item_id)
    rv = RuleVersion(
        expense_item_id=expense_item_id, version=v, split_config=split_config,
        created_by=created_by, reason=reason, status="pending",
    )
    db.add(rv)
    db.commit()
    return rv


def confirm_rule(db: Session, *, rule_version_id: int, user_id: int, confirmed: bool,
                 note: str = "") -> RuleConfirmation:
    c = (
        db.query(RuleConfirmation)
        .filter(RuleConfirmation.rule_version_id == rule_version_id,
                RuleConfirmation.user_id == user_id)
        .first()
    )
    if c is None:
        c = RuleConfirmation(rule_version_id=rule_version_id, user_id=user_id,
                             confirmed=confirmed, note=note)
        db.add(c)
    else:
        c.confirmed = confirmed
        c.note = note
    if confirmed:
        c.confirmed_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    return c


def all_confirmed(db: Session, rule_version_id: int, member_user_ids: list[int]) -> bool:
    confirmed = {
        r.user_id for r in db.query(RuleConfirmation)
        .filter(RuleConfirmation.rule_version_id == rule_version_id,
                RuleConfirmation.confirmed.is_(True))
    }
    return all(u in confirmed for u in member_user_ids)


def is_space_member(db: Session, space_id: int, user_id: int) -> bool:
    m = (
        db.query(SpaceMember)
        .filter(SpaceMember.space_id == space_id, SpaceMember.user_id == user_id,
                SpaceMember.active.is_(True))
        .first()
    )
    return m is not None


def space_member_ids(db: Session, space_id: int) -> list[int]:
    return [
        m.user_id for m in db.query(SpaceMember)
        .filter(SpaceMember.space_id == space_id, SpaceMember.active.is_(True))
    ]


# ---------------------------------------------------------------------------
# 规则变更：冻结未付款任务 + 旧授权失效 + 补付/退款草稿
# ---------------------------------------------------------------------------
def apply_rule_change(db: Session, *, expense_item_id: int, new_split_config: dict[str, Any],
                      changed_by: int, reason: str = "") -> dict[str, Any]:
    """应用规则变更（验收案例 P-08 的核心）。

    1. 保留历史付款（success 的任务不动）
    2. 旧版本 superseded、旧授权 revoked
    3. 冻结受影响（未付款）的任务
    4. 生成新版本并计算差额补付/退款草稿
    """
    item = db.query(ExpenseItem).filter(ExpenseItem.id == expense_item_id).first()
    if item is None:
        raise ValueError("费用项目不存在")

    # 1. 标记旧版本 superseded
    old_versions = (
        db.query(RuleVersion)
        .filter(RuleVersion.expense_item_id == expense_item_id,
                RuleVersion.status.in_(["pending", "confirmed"]))
        .all()
    )
    for ov in old_versions:
        ov.status = "superseded"

    # 2. 旧授权失效（绑定该项目的 rule_version 授权）
    affected_auths = (
        db.query(Authorization)
        .filter(Authorization.rule_version_id.in_([ov.id for ov in old_versions]),
                Authorization.status == "active")
        .all()
    )
    for a in affected_auths:
        a.status = "revoked"

    # 3. 冻结未付款任务
    old_total_cents = item.total_cents
    old_config = old_versions[-1].split_config if old_versions else None
    frozen_tasks = 0
    if old_config is not None:
        old_split = compute_split(old_total_cents, old_config)
        open_tasks = (
            db.query(PaymentTask)
            .filter(PaymentTask.expense_item_id == expense_item_id,
                    PaymentTask.status.in_(["pending", "confirmed", "authorized"]))
            .all()
        )
        for t in open_tasks:
            t.status = "frozen"
            t.frozen_reason = f"规则版本变更：{reason}"
            frozen_tasks += 1

    # 4. 新版本
    rv = create_rule_version(db, expense_item_id=expense_item_id,
                             split_config=new_split_config, created_by=changed_by, reason=reason)
    item.status = "disputed"
    db.commit()

    # 5. 计算差额草稿（基于新旧分摊 + 已完成付款）
    new_split = compute_split(old_total_cents, new_split_config)
    drafts = _build_difference_drafts(db, expense_item_id=expense_item_id, new_split=new_split)
    return {
        "expense_item_id": expense_item_id,
        "new_version": rv.version,
        "new_split_cents": new_split,
        "frozen_tasks": frozen_tasks,
        "revoked_authorizations": len(affected_auths),
        "difference_drafts": drafts,
    }


def _paid_amount_by_user(db: Session, expense_item_id: int) -> dict[int, int]:
    """每个成员已成功付款金额。"""
    paid: dict[int, int] = {}
    for t in db.query(PaymentTask).filter(
            PaymentTask.expense_item_id == expense_item_id,
            PaymentTask.status == "success").all():
        paid[t.payer_user_id] = paid.get(t.payer_user_id, 0) + t.amount_cents
    return paid


def _build_difference_drafts(db: Session, expense_item_id: int,
                             new_split: dict[int, int]) -> list[dict[str, Any]]:
    """根据新分摊与已付款，生成补付/退款草稿。"""
    paid = _paid_amount_by_user(db, expense_item_id)
    drafts = []
    for user_id, due in sorted(new_split.items()):
        delta = due - paid.get(user_id, 0)
        if delta > 0:
            drafts.append({"user_id": user_id, "type": "补付", "amount_cents": delta})
        elif delta < 0:
            drafts.append({"user_id": user_id, "type": "退款", "amount_cents": -delta})
    return drafts


def member_due_summary(db: Session, space_id: int) -> dict[str, Any]:
    """共同空间各成员应付汇总（共享视图数据，不含私人余额）。"""
    items = db.query(ExpenseItem).filter(ExpenseItem.space_id == space_id).all()
    members = space_member_ids(db, space_id)
    due: dict[int, int] = {m: 0 for m in members}
    paid: dict[int, int] = {m: 0 for m in members}
    for item in items:
        latest = (
            db.query(RuleVersion)
            .filter(RuleVersion.expense_item_id == item.id)
            .order_by(RuleVersion.version.desc())
            .first()
        )
        if latest is None:
            continue
        split = compute_split(item.total_cents, latest.split_config)
        for u, amt in split.items():
            if u in due:
                due[u] += amt
    for t in db.query(PaymentTask).filter(PaymentTask.space_id == space_id,
                                          PaymentTask.status == "success").all():
        if t.payer_user_id in paid:
            paid[t.payer_user_id] += t.amount_cents
    return {
        "members": {m: {"due_cents": due.get(m, 0), "paid_cents": paid.get(m, 0),
                        "remaining_cents": max(0, due.get(m, 0) - paid.get(m, 0))}
                    for m in members},
        "total_cents": sum(item.total_cents for item in items),
    }
