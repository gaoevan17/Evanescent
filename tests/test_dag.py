"""DAG 调度测试：依赖约束、部分完成（P-11）、取消、改口、暂停恢复、熔断、持久化。"""
import pytest

from app.services import dag, money
from app.models import DagNode
from tests.conftest import login


def test_create_and_run_green_plan(db):
    plan = dag.create_plan(db, user_id=1, space_id=None, intent_summary="查余额",
                           original_text="查余额",
                           nodes=[{"node_key": "n1", "tool_name": "balance_query",
                                   "params": {}, "depends_on": []}])
    r = dag.run_plan(db, plan.id, 1)
    assert r["plan_status"] == "completed"
    node = db.query(DagNode).filter(DagNode.node_key == "n1").first()
    assert node.status == "success"


def test_dependency_constraint(db):
    """前一步失败，后一步跳过（前一步真实状态约束后一步）。"""
    plan = dag.create_plan(db, user_id=1, space_id=None, intent_summary="test",
                           original_text="t",
                           nodes=[
                               {"node_key": "bad", "tool_name": "unknown_tool",
                                "params": {}, "depends_on": []},
                               {"node_key": "good", "tool_name": "balance_query",
                                "params": {}, "depends_on": ["bad"]},
                           ])
    r = dag.run_plan(db, plan.id, 1)
    bad = db.query(DagNode).filter(DagNode.node_key == "bad").first()
    good = db.query(DagNode).filter(DagNode.node_key == "good").first()
    assert bad.status == "failed"
    assert good.status == "skipped"


def test_partial_completion_p11(db):
    """P-11：部分完成，不标整体成功。"""
    # 模拟：A 付款成功、B 待赎回到账、C 未授权
    plan = dag.create_plan(db, user_id=1, space_id=1, intent_summary="结算",
                           original_text="结算",
                           nodes=[
                               {"node_key": "a_pay", "tool_name": "balance_query",
                                "params": {}, "depends_on": []},  # 用查询模拟 A 已完成
                               {"node_key": "b_redeem", "tool_name": "wealth_redeem",
                                "params": {"holding_id": 1, "amount_cents": 1000},
                                "depends_on": []},  # 需强验证
                           ])
    r = dag.run_plan(db, plan.id, 1)
    # A 节点成功，B 节点等待验证 → 计划不是 completed
    assert r["plan_status"] in ("waiting", "failed")
    assert r["plan_status"] != "completed"
    statuses = {n["node_key"]: n["status"] for n in r["nodes"]}
    assert statuses["a_pay"] == "success"
    assert statuses["b_redeem"] == "waiting_auth"


def test_cancel_only_pending_nodes(db):
    plan = dag.create_plan(db, user_id=1, space_id=None, intent_summary="t",
                           original_text="t",
                           nodes=[{"node_key": "n1", "tool_name": "balance_query",
                                   "params": {}, "depends_on": []}])
    dag.run_plan(db, plan.id, 1)
    # 已成功节点不可取消，但取消计划会保留已执行交易
    r = dag.cancel_plan(db, plan.id)
    assert r["plan_status"] == "cancelled"


def test_modify_node_invalidates_auth(db):
    from app.models import Authorization
    plan = dag.create_plan(db, user_id=1, space_id=None, intent_summary="t",
                           original_text="t",
                           nodes=[{"node_key": "n1", "tool_name": "balance_query",
                                   "params": {}, "depends_on": []}])
    dag.run_plan(db, plan.id, 1)
    # 给用户一个 active 授权
    db.add(Authorization(user_id=1, action="transfer", operation_id="op-x",
                         expires_at=__import__("datetime").datetime.now(
                             __import__("datetime").timezone.utc) +
                         __import__("datetime").timedelta(minutes=30), status="active"))
    db.commit()
    # 改口一个未执行节点
    plan2 = dag.create_plan(db, user_id=1, space_id=None, intent_summary="t2",
                            original_text="t2",
                            nodes=[{"node_key": "m1", "tool_name": "transfer",
                                    "params": {"amount_cents": 100}, "depends_on": []}])
    r = dag.modify_node(db, plan_id=plan2.id, node_key="m1",
                        new_params={"amount_cents": 200}, user_id=1)
    assert "旧授权失效" in r["note"]
    # 旧授权被 revoke
    active = db.query(Authorization).filter(Authorization.status == "active").count()
    assert active == 0


def test_pause_and_resume(db):
    plan = dag.create_plan(db, user_id=1, space_id=None, intent_summary="t",
                           original_text="t",
                           nodes=[{"node_key": "n1", "tool_name": "balance_query",
                                   "params": {}, "depends_on": []}])
    dag.pause_plan(db, plan.id)
    assert dag.get_plan(db, plan.id).status == "paused"
    r = dag.resume_plan(db, plan.id, 1)
    assert r["plan_status"] == "completed"


def test_plan_persisted_across_session(db):
    """重启后不丢失待处理任务（重新开 session 读取）。"""
    plan = dag.create_plan(db, user_id=1, space_id=None, intent_summary="持久化",
                           original_text="t",
                           nodes=[{"node_key": "n1", "tool_name": "wealth_redeem",
                                   "params": {"holding_id": 1, "amount_cents": 100},
                                   "depends_on": []}])
    dag.run_plan(db, plan.id, 1)
    # 模拟重启：新 session 读取
    from app.db import SessionLocal
    db2 = SessionLocal()
    try:
        snap = dag.plan_snapshot(db2, plan.id)
        assert snap["status"] in ("waiting", "failed")
        assert any(n["status"] == "waiting_auth" for n in snap["nodes"])
    finally:
        db2.close()


def test_revalidate_scheduled_tasks(db):
    """定时任务执行前重新校验。"""
    import datetime as dt
    from app.models import PaymentTask
    db.add(PaymentTask(space_id=1, expense_item_id=1, rule_version_id=1,
                       payer_user_id=2, amount_cents=999_999_999, status="pending",
                       idempotency_key="sched-big",
                       due_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)))
    db.commit()
    results = dag.revalidate_scheduled_tasks(db)
    # 余额不足 → 冻结
    assert any(r["payment_task_id"] and r["action"] == "frozen" for r in results)
