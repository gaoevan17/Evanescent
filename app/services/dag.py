"""有状态、可持久化的 DAG 调度服务。

- 节点按依赖拓扑执行，前一步真实状态约束后一步
- 支持暂停、恢复、取消、改口、人工接管
- 重启后不丢失待处理任务（持久化于数据库）
- 连续失败触发熔断（阈值团队配置）
"""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy.orm import Session

from ..config import settings
from ..models import DagNode, DagPlan, SecurityLock
from ..tools import run_guarded

PENDING = "pending"
RUNNING = "running"
WAITING_CONFIRM = "waiting_confirm"
WAITING_AUTH = "waiting_auth"
SUCCESS = "success"
FAILED = "failed"
UNKNOWN = "unknown"
FROZEN = "frozen"
SKIPPED = "skipped"
CANCELLED = "cancelled"

READY_FOR_EXEC = {PENDING, FROZEN}


def create_plan(db: Session, *, user_id: int, space_id: int | None,
                intent_summary: str, original_text: str,
                nodes: list[dict]) -> DagPlan:
    """nodes: [{"node_key","tool_name","params","depends_on":[..]}]. 返回计划。"""
    plan = DagPlan(user_id=user_id, space_id=space_id, intent_summary=intent_summary,
                   plan_version=f"plan-{uuid.uuid4().hex[:12]}",
                   status="running", original_text=original_text)
    db.add(plan)
    db.flush()
    for n in nodes:
        node = DagNode(plan_id=plan.id, node_key=n["node_key"], tool_name=n["tool_name"],
                       params=n.get("params", {}), depends_on=n.get("depends_on", []),
                       status=PENDING, idempotency_key=n.get("idempotency_key"))
        db.add(node)
    db.commit()
    return plan


def get_plan(db: Session, plan_id: int) -> DagPlan | None:
    return db.query(DagPlan).filter(DagPlan.id == plan_id).first()


def _deps_satisfied(db: Session, node: DagNode) -> bool:
    deps = node.depends_on or []
    if not deps:
        return True
    dep_nodes = db.query(DagNode).filter(DagNode.plan_id == node.plan_id,
                                         DagNode.node_key.in_(deps)).all()
    return all(d.status == SUCCESS for d in dep_nodes)


def _dep_failed(db: Session, node: DagNode) -> bool:
    deps = node.depends_on or []
    dep_nodes = db.query(DagNode).filter(DagNode.plan_id == node.plan_id,
                                         DagNode.node_key.in_(deps)).all()
    return any(d.status in (FAILED, CANCELLED) for d in dep_nodes)


def _is_locked(db: Session, user_id: int) -> SecurityLock | None:
    from ..db import ensure_aware
    now = dt.datetime.now(dt.timezone.utc)
    locks = db.query(SecurityLock).filter(SecurityLock.user_id == user_id).all()
    for l in locks:
        if l.locked_until and ensure_aware(l.locked_until) > now:
            return l
    return None


def run_plan(db: Session, plan_id: int, user_id: int) -> dict:
    """推进计划：执行所有依赖已满足且无需交互的节点。

    遇到 yellow/red 节点停在等待态，由前端/测试提供确认与验证后续跑。
    """
    plan = get_plan(db, plan_id)
    if plan is None:
        raise ValueError("计划不存在")
    if plan.status in ("completed", "failed", "cancelled"):
        return {"plan_status": plan.status}

    progressed = True
    waits = []
    while progressed:
        progressed = False
        nodes = db.query(DagNode).filter(DagNode.plan_id == plan_id).all()
        for node in nodes:
            if node.status not in READY_FOR_EXEC:
                continue
            if not _deps_satisfied(db, node):
                if _dep_failed(db, node):
                    node.status = SKIPPED
                    db.commit()
                continue
            if _is_locked(db, user_id):
                plan.status = "paused"
                db.commit()
                return {"plan_status": "paused", "reason": "安全锁定（熔断）"}

            res = _execute_node(db, node, user_id)
            st = res.get("status")
            if st == "success":
                node.status = SUCCESS
                node.result = res.get("result")
                db.commit()
                progressed = True
            elif st == "waiting_confirm":
                node.status = WAITING_CONFIRM
                node.result = {"detail": res.get("detail"), "reason": res.get("reason")}
                db.commit()
                waits.append({"node_key": node.node_key, "type": "confirm",
                              "detail": res.get("detail")})
            elif st == "waiting_auth":
                node.status = WAITING_AUTH
                node.result = {"challenge": res.get("challenge"),
                               "authorization_id": res.get("authorization_id"),
                               "operation_id": res.get("operation_id"),
                               "detail": res.get("detail")}
                db.commit()
                waits.append({"node_key": node.node_key, "type": "auth",
                              "challenge": res.get("challenge"),
                              "authorization_id": res.get("authorization_id"),
                              "operation_id": res.get("operation_id")})
            else:
                node.status = FAILED
                node.error = str(res.get("error", ""))
                db.commit()
                _record_failure(db, plan, user_id)

    # 汇总计划状态
    all_nodes = db.query(DagNode).filter(DagNode.plan_id == plan_id).all()
    if all(n.status in (SUCCESS, SKIPPED) for n in all_nodes) and not waits:
        plan.status = "completed"
    elif any(n.status == FAILED for n in all_nodes):
        plan.status = "failed"
    elif waits:
        plan.status = "waiting"
    db.commit()
    return {"plan_id": plan_id, "plan_status": plan.status, "waits": waits,
            "nodes": [_node_dict(n) for n in all_nodes]}


def _execute_node(db: Session, node: DagNode, user_id: int) -> dict:
    node.status = RUNNING
    db.commit()
    try:
        return run_guarded(db, tool_name=node.tool_name, user_id=user_id,
                           params=node.params or {}, rule_version_id=None,
                           plan_version=None)
    except Exception as e:  # noqa: BLE001
        return {"status": "failed", "error": str(e)}


def _record_failure(db: Session, plan: DagPlan, user_id: int) -> None:
    # 连续失败计数：以近期失败节点数近似，达到阈值则锁定
    fail_count = db.query(DagNode).filter(DagNode.plan_id == plan.id,
                                          DagNode.status == FAILED).count()
    if fail_count >= settings.CIRCUIT_BREAKER_FAILURES:
        until = dt.datetime.now(dt.timezone.utc) + dt.timedelta(
            seconds=settings.CIRCUIT_BREAKER_WINDOW_SECONDS)
        lock = SecurityLock(user_id=user_id, lock_type="circuit_breaker",
                            reason="连续失败触发熔断",
                            unlock_condition="等待恢复窗口或人工解除",
                            locked_until=until)
        db.add(lock)
        db.commit()


def continue_node(db: Session, *, plan_id: int, node_key: str, user_id: int,
                  confirmed: bool = False, challenge_id: int | None = None,
                  challenge_code: str | None = None, operation_id: str | None = None,
                  authorization_id: int | None = None) -> dict:
    """对等待中的节点提供确认或强验证后继续执行，随后继续推进计划。"""
    node = db.query(DagNode).filter(DagNode.plan_id == plan_id,
                                    DagNode.node_key == node_key).first()
    if node is None:
        raise ValueError("节点不存在")
    if node.status not in (WAITING_CONFIRM, WAITING_AUTH):
        raise ValueError(f"节点状态 {node.status}，无需确认/验证")
    try:
        res = run_guarded(db, tool_name=node.tool_name, user_id=user_id,
                          params=node.params or {}, confirmed=confirmed,
                          challenge_id=challenge_id, challenge_code=challenge_code,
                          operation_id=operation_id, authorization_id=authorization_id)
    except Exception as e:  # noqa: BLE001
        node.status = FAILED
        node.error = str(e)
        db.commit()
        return {"status": "failed", "error": str(e)}
    if res.get("status") == "success":
        node.status = SUCCESS
        node.result = res.get("result")
        node.result = node.result or {}
    else:
        node.status = WAITING_AUTH if res.get("status") == "waiting_auth" else WAITING_CONFIRM
        node.result = res
    db.commit()
    # 继续推进其余节点
    return run_plan(db, plan_id, user_id)


def pause_plan(db: Session, plan_id: int) -> dict:
    plan = get_plan(db, plan_id)
    plan.status = "paused"
    db.commit()
    return {"plan_status": "paused"}


def resume_plan(db: Session, plan_id: int, user_id: int) -> dict:
    plan = get_plan(db, plan_id)
    if plan.status != "paused":
        raise ValueError("计划未处于暂停状态")
    plan.status = "running"
    db.commit()
    return run_plan(db, plan_id, user_id)


def cancel_plan(db: Session, plan_id: int) -> dict:
    """取消计划：仅未执行节点置为取消；已执行交易不可删除。"""
    plan = get_plan(db, plan_id)
    if plan is None:
        raise ValueError("计划不存在")
    for n in db.query(DagNode).filter(DagNode.plan_id == plan_id).all():
        if n.status in (PENDING, FROZEN, WAITING_CONFIRM, WAITING_AUTH):
            n.status = CANCELLED
    plan.status = "cancelled"
    db.commit()
    return {"plan_status": "cancelled"}


def modify_node(db: Session, *, plan_id: int, node_key: str, new_params: dict,
                user_id: int) -> dict:
    """改口：修改未执行节点的参数，重置其状态并使其旧授权失效。"""
    from ..models import Authorization
    node = db.query(DagNode).filter(DagNode.plan_id == plan_id,
                                    DagNode.node_key == node_key).first()
    if node is None:
        raise ValueError("节点不存在")
    if node.status == SUCCESS:
        raise ValueError("已执行节点不可改口，只能生成补偿（补付/退款）")
    old_params = node.params
    node.params = new_params
    node.status = PENDING
    node.result = None
    # 使该节点关联的旧授权失效
    for a in db.query(Authorization).filter(Authorization.user_id == user_id,
                                            Authorization.status == "active").all():
        a.status = "revoked"
    db.commit()
    return {"node_key": node_key, "old_params": old_params, "new_params": new_params,
            "note": "参数已变更，旧授权失效，需重新确认/验证"}


def take_over(db: Session, *, plan_id: int, node_key: str, actor_user_id: int,
              decision: str, new_params: dict | None = None) -> dict:
    """人工接管：接管者仍受本人权限约束，仅能对本人的节点操作。"""
    node = db.query(DagNode).filter(DagNode.plan_id == plan_id,
                                    DagNode.node_key == node_key).first()
    if node is None:
        raise ValueError("节点不存在")
    if decision == "cancel":
        if node.status == SUCCESS:
            raise ValueError("已执行节点不可取消")
        node.status = CANCELLED
    elif decision == "retry":
        node.status = PENDING
        node.retries += 1
    elif decision == "modify" and new_params is not None:
        return modify_node(db, plan_id=plan_id, node_key=node_key,
                           new_params=new_params, user_id=actor_user_id)
    else:
        raise ValueError("非法接管指令")
    db.commit()
    return {"node_key": node_key, "status": node.status}


def _node_dict(n: DagNode) -> dict:
    return {"node_key": n.node_key, "tool_name": n.tool_name, "status": n.status,
            "depends_on": n.depends_on, "result": n.result, "error": n.error}


def plan_snapshot(db: Session, plan_id: int) -> dict:
    plan = get_plan(db, plan_id)
    nodes = db.query(DagNode).filter(DagNode.plan_id == plan_id).order_by(DagNode.id).all()
    return {"plan_id": plan.id, "plan_version": plan.plan_version,
            "status": plan.status, "intent_summary": plan.intent_summary,
            "nodes": [_node_dict(n) for n in nodes]}


def revalidate_scheduled_tasks(db: Session) -> list[dict]:
    """定时任务执行前重新校验（规则变更/余额/授权过期/日累计升级）。"""
    from ..db import ensure_aware
    from ..models import Authorization, PaymentTask
    now = dt.datetime.now(dt.timezone.utc)
    results = []
    due_tasks = db.query(PaymentTask).filter(PaymentTask.status == "pending").all()
    for t in due_tasks:
        if t.due_at and ensure_aware(t.due_at) > now:
            continue
        # 余额校验
        from . import money
        bal = money.balance(db, t.payer_user_id)
        # 授权有效性
        auth = db.query(Authorization).filter(Authorization.operation_id == t.idempotency_key,
                                              Authorization.status == "active").first()
        if bal < t.amount_cents or auth is None:
            t.status = "frozen"
            t.frozen_reason = "定时执行前校验失败：余额不足或授权过期"
            results.append({"payment_task_id": t.id, "action": "frozen",
                            "reason": t.frozen_reason})
        else:
            results.append({"payment_task_id": t.id, "action": "ready"})
    db.commit()
    return results
