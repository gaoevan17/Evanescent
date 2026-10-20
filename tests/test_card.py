"""卡片管理测试：申请、进度、额度、限制/解锁、挂失/解挂、仅本人。"""
import pytest

from app.services import card as card_svc
from tests.conftest import login


def test_apply_virtual_card(client, db):
    alice = login(client, "alice")
    r = client.post("/api/tools/run", json={"tool_name": "virtual_card_apply",
                                            "params": {"limit_cents": 50000}, "confirmed": True},
                    headers=alice["headers"])
    data = r.json()
    assert data["status"] == "success"
    assert data["result"]["status"] == "processing"
    assert data["result"]["apply_no"].startswith("AP")


def test_apply_card_requires_confirm(client, db):
    alice = login(client, "alice")
    r = client.post("/api/tools/run", json={"tool_name": "virtual_card_apply",
                                            "params": {}}, headers=alice["headers"])
    assert r.json()["status"] == "waiting_confirm"


def test_card_apply_progress(db):
    r = card_svc.apply_virtual_card(db, user_id=1)
    p = card_svc.card_apply_progress(db, user_id=1, card_id=r["card_id"])
    assert p["status"] == "processing"


def test_card_approve_transitions(db):
    r = card_svc.apply_virtual_card(db, user_id=1)
    card_svc.approve_card(db, card_id=r["card_id"])
    p = card_svc.card_apply_progress(db, user_id=1, card_id=r["card_id"])
    assert p["status"] == "active"


def test_adjust_limit_own(db):
    cards = card_svc.list_cards(db, 1)
    cid = cards[0]["id"]
    r = card_svc.adjust_limit(db, user_id=1, card_id=cid, limit_cents=80000)
    assert r["limit_cents"] == 80000


def test_adjust_limit_other_rejected(db):
    cards = card_svc.list_cards(db, 2)
    cid = cards[0]["id"]
    with pytest.raises(PermissionError):
        card_svc.adjust_limit(db, user_id=1, card_id=cid, limit_cents=1)


def test_restrict_and_unrestrict(db):
    cards = card_svc.list_cards(db, 1)
    cid = cards[0]["id"]
    card_svc.restrict_card(db, user_id=1, card_id=cid, reason="可疑交易")
    assert card_svc.list_cards(db, 1)[0]["status"] == "restricted"
    card_svc.unrestrict_card(db, user_id=1, card_id=cid)
    assert card_svc.list_cards(db, 1)[0]["status"] == "active"


def test_report_lost_and_unlost(db):
    cards = card_svc.list_cards(db, 2)
    cid = cards[0]["id"]
    card_svc.report_lost(db, user_id=2, card_id=cid)
    assert card_svc.list_cards(db, 2)[0]["status"] == "lost"
    card_svc.report_unlost(db, user_id=2, card_id=cid)
    assert card_svc.list_cards(db, 2)[0]["status"] == "active"


def test_lost_card_is_red(client, db):
    alice = login(client, "alice")
    r = client.post("/api/tools/run", json={"tool_name": "card_lost",
                                            "params": {"card_id": 1}}, headers=alice["headers"])
    assert r.json()["status"] == "waiting_auth"


def test_unlost_non_lost_rejected(db):
    cards = card_svc.list_cards(db, 1)
    cid = cards[0]["id"]
    with pytest.raises(ValueError):
        card_svc.report_unlost(db, user_id=1, card_id=cid)


def test_restrict_card_is_red(client, db):
    alice = login(client, "alice")
    r = client.post("/api/tools/run", json={"tool_name": "card_restrict",
                                            "params": {"card_id": 1}}, headers=alice["headers"])
    assert r.json()["status"] == "waiting_auth"
