"""订阅/代扣测试（P-09）：两笔68元、取消仅本人、不退款不增余额。"""
import pytest

from app.services import money, subscription as sub_svc
from tests.conftest import login


def test_identify_recurring_bob(db):
    """bob 的同商户同金额周期扣费（外卖平台 45 元 ×2）被识别。"""
    r = sub_svc.identify_recurring(db, user_id=2)
    merchants = {x["merchant"] for x in r}
    assert "外卖平台" in merchants


def test_two_68_distinct_subscriptions(db):
    """两笔 68 元来自两个不同订阅（撤回疑似重复判断的依据）。"""
    subs = sub_svc.list_subscriptions(db, 2)
    amounts = [s for s in subs if s["amount_cents"] == 6800]
    assert len(amounts) == 2
    assert amounts[0]["id"] != amounts[1]["id"]


def test_cancel_only_holder(client, db):
    """只有持有人能取消订阅。"""
    alice = login(client, "alice")
    # bob 的订阅（音乐会员 id 需要查询）
    subs = sub_svc.list_subscriptions(db, 2)
    bob_sub = subs[0]["id"]
    r = client.post("/api/tools/run", json={"tool_name": "subscribe_cancel",
                                            "params": {"sub_id": bob_sub}, "confirmed": True},
                    headers=alice["headers"])
    assert r.status_code == 400  # alice 非持有人


def test_cancel_does_not_refund_or_increase_balance(client, db):
    """取消不退回历史费用、不增加余额。"""
    bob = login(client, "bob")
    before = money.balance(db, 2)
    subs = sub_svc.list_subscriptions(db, 2)
    target = [s for s in subs if s["merchant"] == "XX音乐会员A"][0]
    r = client.post("/api/tools/run", json={"tool_name": "subscribe_cancel",
                                            "params": {"sub_id": target["id"]}, "confirmed": True},
                    headers=bob["headers"])
    data = r.json()
    assert data["status"] == "success"
    after = money.balance(db, 2)
    assert after == before  # 余额不变


def test_cancel_effective_status(client, db):
    bob = login(client, "bob")
    subs = sub_svc.list_subscriptions(db, 2)
    target = [s for s in subs if s["merchant"] == "XX音乐会员B"][0]
    client.post("/api/tools/run", json={"tool_name": "subscribe_cancel",
                                        "params": {"sub_id": target["id"]}, "confirmed": True},
                headers=bob["headers"])
    st = sub_svc.cancel_effect_status(db, user_id=2, sub_id=target["id"])
    assert st["status"] == "cancelled"
    assert st["effective_change_at"] is not None


def test_cancel_keeps_historical_fee(client, db):
    """取消后已发生的 68 元扣费流水保留。"""
    from app.models import Account, Transaction
    bob = login(client, "bob")
    subs = sub_svc.list_subscriptions(db, 2)
    target = [s for s in subs if s["merchant"] == "XX音乐会员A"][0]
    client.post("/api/tools/run", json={"tool_name": "subscribe_cancel",
                                        "params": {"sub_id": target["id"]}, "confirmed": True},
                headers=bob["headers"])
    acct = db.query(Account).filter(Account.user_id == 2).first()
    txs = db.query(Transaction).filter(Transaction.account_id == acct.id,
                                       Transaction.merchant == "XX音乐会员A").all()
    assert len(txs) == 1  # 历史扣费仍在


def test_query_subscription_is_yellow(client, db):
    """订阅查询是黄色操作（官网列明）。"""
    alice = login(client, "alice")
    r = client.post("/api/tools/run", json={"tool_name": "subscribe_query",
                                            "params": {"sub_id": 1}}, headers=alice["headers"])
    assert r.json()["level"] == "yellow"


def test_subscription_list_only_own(db):
    subs = sub_svc.list_subscriptions(db, 1)
    assert all(s["id"] in (1,) for s in subs)  # alice 只有视频会员
