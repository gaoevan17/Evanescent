"""离线解析器测试：主场景、各意图、同名澄清、参数补全。"""
from app.llm.offline import OfflinePlanner


def _p(db):
    return OfflinePlanner(db)


def test_settlement_main_scene(db):
    """主场景：结算共同开销 → 生成跨场景联动计划。"""
    res = _p(db).plan("帮我们结算本月共同开销，电费按入住天数分", user_id=1, space_id=1)
    assert res["intent"] == "settlement"
    assert len(res["nodes"]) >= 3
    tools = {n["tool_name"] for n in res["nodes"]}
    assert "bill_analysis" in tools
    assert "list_evidence" in tools


def test_transfer_intent(db):
    res = _p(db).plan("转给王淑芬 100 元", user_id=2)
    assert res["intent"] == "transfer"
    assert res["nodes"][0]["tool_name"] == "transfer"
    assert res["nodes"][0]["params"]["amount_cents"] == 10000


def test_transfer_duplicate_clarification(db):
    res = _p(db).plan("转给张伟 100 元", user_id=2)
    assert res["clarification"] is not None
    assert len(res["clarification"]["options"]) == 2


def test_transfer_missing_amount(db):
    res = _p(db).plan("转给李伟", user_id=1)
    assert res["clarification"] is not None
    assert "金额" in res["clarification"]["question"]


def test_bill_intent(db):
    res = _p(db).plan("看看我这个月的账单", user_id=1)
    assert res["intent"] == "bill"
    assert any(n["tool_name"] == "bill_analysis" for n in res["nodes"])


def test_wealth_intent(db):
    res = _p(db).plan("帮我看看理财产品", user_id=1)
    assert res["intent"].startswith("wealth")
    assert any(n["tool_name"] == "product_list" for n in res["nodes"])


def test_subscription_cancel_intent(db):
    res = _p(db).plan("取消我的视频会员", user_id=1)
    assert res["intent"] == "subscribe_cancel"


def test_card_intent(db):
    res = _p(db).plan("申请一张虚拟卡", user_id=1)
    assert res["intent"] == "card"
    assert any(n["tool_name"] == "virtual_card_apply" for n in res["nodes"])


def test_balance_query(db):
    res = _p(db).plan("查询我的余额", user_id=1)
    assert any(n["tool_name"] == "balance_query" for n in res["nodes"])


def test_offline_mode_flag(db):
    res = _p(db).plan("查余额", user_id=1)
    assert res["offline_mode"] is True
