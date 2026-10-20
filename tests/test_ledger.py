"""共识账本测试：最大余数法、金额守恒、规则版本、规则变更（P-07/P-08）。"""
import pytest

from app.services import ledger


def test_largest_remainder_exact():
    # 240 元按 30/20/10 无尾差
    r = ledger.largest_remainder(24000, {1: 30, 2: 20, 3: 10})
    assert r == {1: 12000, 2: 8000, 3: 4000}
    assert sum(r.values()) == 24000


def test_largest_remainder_conserves_cents():
    # 100 分三人均分，尾差 1 分给最大余数者
    r = ledger.largest_remainder(100, {1: 1, 2: 1, 3: 1})
    assert sum(r.values()) == 100
    assert sorted(r.values()) == [33, 33, 34]


def test_largest_remainder_conserves_arbitrary():
    # 随机总额守恒
    r = ledger.largest_remainder(10001, {1: 7, 2: 3, 3: 2})
    assert sum(r.values()) == 10001


def test_compute_split_days(db):
    cfg = {"method": "days", "weights": {"1": 30, "2": 20, "3": 10}}
    r = ledger.compute_split(24000, cfg)
    assert r == {1: 12000, 2: 8000, 3: 4000}


def test_compute_split_equal(db):
    cfg = {"method": "equal", "weights": {"1": 1, "2": 1, "3": 1}}
    r = ledger.compute_split(9000, cfg)
    assert r == {1: 3000, 2: 3000, 3: 3000}


def test_acceptance_case_1_due(db):
    """P-07：电费240+宽带90 → 150/110/70 元，合计 330 元。"""
    summary = ledger.member_due_summary(db, space_id=1)
    assert summary["total_cents"] == 33000
    assert summary["members"][1]["due_cents"] == 15000
    assert summary["members"][2]["due_cents"] == 11000
    assert summary["members"][3]["due_cents"] == 7000
    assert sum(m["due_cents"] for m in summary["members"].values()) == 33000


def test_acceptance_case_2_rule_change(db):
    """P-08：A 已付款后 C 改入住天数为 5 天。"""
    from app.models import Authorization, PaymentTask
    from app.tools import run_guarded

    # A 付款 150 元（电费部分，简化：直接创建成功付款任务）
    # 先创建 A 的付款任务并标记成功（模拟已付款）
    from app.models import ExpenseItem
    electric = db.query(ExpenseItem).filter(ExpenseItem.name == "本月电费").first()
    db.add(PaymentTask(space_id=1, expense_item_id=electric.id, rule_version_id=1,
                       payer_user_id=1, amount_cents=15000, status="success",
                       idempotency_key="paid-a-1"))
    db.commit()

    # C 改入住天数 30/20/5
    new_cfg = {"method": "days", "weights": {"1": 30, "2": 20, "3": 5}}
    result = ledger.apply_rule_change(db, expense_item_id=electric.id,
                                      new_split_config=new_cfg, changed_by=3,
                                      reason="C 入住天数改为 5 天")
    # 新分摊：24000 * 30/55=13090(余0.9), *20/55=8727(余0.27), *5/55=2181(余0.8)
    # 尾差 2 分给余数最大者（A=0.909, C=0.818, B=0.273）→ A 补1, C 补1
    new_split = result["new_split_cents"]
    assert sum(new_split.values()) == 24000
    assert new_split[3] == 2182  # C 从 4000 降到 2182
    assert result["new_version"] == 2
    # 旧授权失效
    # 已付款记录保留
    paid = db.query(PaymentTask).filter(PaymentTask.status == "success").all()
    assert any(t.idempotency_key == "paid-a-1" for t in paid)


def test_rule_change_freezes_open_tasks(db):
    from app.models import ExpenseItem, PaymentTask
    electric = db.query(ExpenseItem).filter(ExpenseItem.name == "本月电费").first()
    db.add(PaymentTask(space_id=1, expense_item_id=electric.id, rule_version_id=1,
                       payer_user_id=2, amount_cents=11000, status="pending",
                       idempotency_key="open-b-1"))
    db.commit()
    new_cfg = {"method": "days", "weights": {"1": 30, "2": 20, "3": 5}}
    result = ledger.apply_rule_change(db, expense_item_id=electric.id,
                                      new_split_config=new_cfg, changed_by=3,
                                      reason="改天数")
    assert result["frozen_tasks"] == 1
    t = db.query(PaymentTask).filter(PaymentTask.idempotency_key == "open-b-1").first()
    assert t.status == "frozen"


def test_rule_change_generates_difference_drafts(db):
    from app.models import ExpenseItem, PaymentTask
    electric = db.query(ExpenseItem).filter(ExpenseItem.name == "本月电费").first()
    # A 已付 12000 分，C 已付 4000 分（旧规则下的应付）
    db.add(PaymentTask(space_id=1, expense_item_id=electric.id, rule_version_id=1,
                       payer_user_id=1, amount_cents=12000, status="success",
                       idempotency_key="paid-a-12000"))
    db.add(PaymentTask(space_id=1, expense_item_id=electric.id, rule_version_id=1,
                       payer_user_id=3, amount_cents=4000, status="success",
                       idempotency_key="paid-c-4000"))
    db.commit()
    new_cfg = {"method": "days", "weights": {"1": 30, "2": 20, "3": 5}}
    result = ledger.apply_rule_change(db, expense_item_id=electric.id,
                                      new_split_config=new_cfg, changed_by=3,
                                      reason="改天数")
    drafts = result["difference_drafts"]
    # 新分摊：A=13091, B=8727, C=2182
    # A 已付 12000 → 补付 1091；C 已付 4000 → 退款 1818；B 未付 → 补付 8727
    by_user = {d["user_id"]: d for d in drafts}
    assert by_user[1]["type"] == "补付"
    assert by_user[1]["amount_cents"] == 13091 - 12000
    assert by_user[3]["type"] == "退款"
    assert by_user[3]["amount_cents"] == 4000 - 2182


def test_member_due_isolated(db):
    """共享汇总不泄露私人余额。"""
    summary = ledger.member_due_summary(db, space_id=1)
    for m in summary["members"].values():
        assert "balance" not in m  # 不含私人余额字段


def test_largest_remainder_zero_weight_raises():
    with pytest.raises(ValueError):
        ledger.largest_remainder(100, {1: 0, 2: 0})


def test_space_membership(db):
    assert ledger.is_space_member(db, 1, 1)
    assert ledger.is_space_member(db, 1, 2)
    assert ledger.is_space_member(db, 1, 3)
    assert not ledger.is_space_member(db, 1, 999)
