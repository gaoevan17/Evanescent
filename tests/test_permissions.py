"""权限分级测试：绿/黄/红、日累计升级、订阅查询黄色、团队保守策略。"""
from app.security import policy
from app.tools import run_guarded
from tests.conftest import login


def test_green_auto_execute(client, db):
    alice = login(client, "alice")
    r = client.post("/api/tools/run", json={"tool_name": "balance_query", "params": {}},
                    headers=alice["headers"])
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "success"
    assert data["level"] == "green"


def test_yellow_requires_confirm(client, db):
    alice = login(client, "alice")
    r = client.post("/api/tools/run", json={"tool_name": "subscribe_query",
                                            "params": {"sub_id": 1}},
                    headers=alice["headers"])
    data = r.json()
    assert data["status"] == "waiting_confirm"
    assert data["level"] == "yellow"


def test_yellow_after_confirm_executes(client, db):
    alice = login(client, "alice")
    r = client.post("/api/tools/run", json={"tool_name": "subscribe_query",
                                            "params": {"sub_id": 1}, "confirmed": True},
                    headers=alice["headers"])
    data = r.json()
    assert data["status"] == "success"


def test_red_requires_strong_verify(client, db):
    alice = login(client, "alice")
    r = client.post("/api/tools/run", json={"tool_name": "wealth_subscribe",
                                            "params": {"product_id": 1, "amount_cents": 10000}},
                    headers=alice["headers"])
    data = r.json()
    assert data["status"] == "waiting_auth"
    assert data["level"] == "red"
    assert data["challenge"]["simulated"] is True


def test_red_after_verify_executes(client, db):
    from app.services import wealth
    # 先给 alice 建风险问卷
    wealth.submit_risk_profile(db, user_id=1, answers=[3, 3, 3, 3, 3])
    alice = login(client, "alice")
    r = client.post("/api/tools/run", json={"tool_name": "wealth_subscribe",
                                            "params": {"product_id": 1, "amount_cents": 10000}},
                    headers=alice["headers"])
    data = r.json()
    chal = data["challenge"]
    r2 = client.post("/api/tools/run", json={"tool_name": "wealth_subscribe",
                                             "params": {"product_id": 1, "amount_cents": 10000},
                                             "challenge_id": chal["challenge_id"],
                                             "challenge_code": chal["mock_code"],
                                             "operation_id": data["operation_id"],
                                             "authorization_id": data["authorization_id"]},
                     headers=alice["headers"])
    data2 = r2.json()
    assert data2["status"] == "success"


def test_daily_limit_upgrade_800_then_300(client, db):
    """先转 800 再转 300：第二笔日累计 1100 → 红色。"""
    from app.services import transfer as transfer_svc
    # 给 alice 建一个成员联系人（李伟 bob）
    cands = transfer_svc.search_contacts(db, 1, "李伟")
    cid = cands[0]["contact_id"]
    alice = login(client, "alice")
    # 第一笔 800
    r1 = client.post("/api/tools/run", json={"tool_name": "transfer",
                                             "params": {"amount_cents": 80000,
                                                        "payee_contact_id": cid},
                                             "confirmed": True}, headers=alice["headers"])
    assert r1.json()["level"] == "yellow"
    assert r1.json()["status"] == "success"
    # 第二笔 300：日累计 1100 → 红色
    r2 = client.post("/api/tools/run", json={"tool_name": "transfer",
                                             "params": {"amount_cents": 30000,
                                                        "payee_contact_id": cid},
                                             "confirmed": True}, headers=alice["headers"])
    data2 = r2.json()
    assert data2["status"] == "waiting_auth"
    assert data2["level"] == "red"


def test_redeem_is_red_team_policy(client, db):
    """赎回按团队保守策略为红色。"""
    alice = login(client, "alice")
    r = client.post("/api/tools/run", json={"tool_name": "wealth_redeem",
                                            "params": {"holding_id": 1, "amount_cents": 1000}},
                    headers=alice["headers"])
    data = r.json()
    assert data["status"] == "waiting_auth"
    assert data["level"] == "red"


def test_card_limit_is_red(client, db):
    alice = login(client, "alice")
    r = client.post("/api/tools/run", json={"tool_name": "card_limit_adjust",
                                            "params": {"card_id": 1, "limit_cents": 50000}},
                    headers=alice["headers"])
    assert r.json()["status"] == "waiting_auth"


def test_unknown_action_rejected(client, db):
    alice = login(client, "alice")
    r = client.post("/api/tools/run", json={"tool_name": "hack_balance",
                                            "params": {}}, headers=alice["headers"])
    assert r.status_code == 400


def test_policy_whitelist_complete():
    for a in ["transfer", "subscribe_query", "wealth_subscribe", "balance_query",
              "card_lost", "subscribe_cancel", "virtual_card_apply"]:
        assert policy.is_known_action(a)


def test_transfer_classify_under_limit(db):
    d = policy.classify_transfer(db, 1, 50000)
    assert d.level == "yellow"


def test_transfer_classify_over_limit(db):
    d = policy.classify_transfer(db, 1, 150000)
    assert d.level == "red"


def test_transfer_classify_boundary(db):
    # 日累计恰好 1000 元 → 黄色；超 1 分 → 红色
    assert policy.classify_transfer(db, 1, 100000).level == "yellow"
    assert policy.classify_transfer(db, 1, 100001).level == "red"
