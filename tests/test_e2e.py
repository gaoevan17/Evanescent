"""端到端交互流程测试：通过 HTTP API 完成完整业务闭环。"""
from tests.conftest import login


def test_chat_settlement_flow(client, db):
    """结算共同开销：对话生成任务链并执行。"""
    alice = login(client, "alice")
    r = client.post("/api/chat", json={"message": "帮我们结算本月共同开销", "space_id": 1},
                    headers=alice["headers"])
    data = r.json()
    assert r.status_code == 200
    # 结算计划包含账单分析、异常核验、共享进度
    assert data["plan"] is not None
    tools = {n["tool_name"] for n in data["plan"]["nodes"]}
    assert "bill_analysis" in tools


def test_chat_transfer_flow(client, db):
    """转账：对话→计划→等待确认。"""
    alice = login(client, "alice")
    r = client.post("/api/chat", json={"message": "转给李伟 100 元"}, headers=alice["headers"])
    data = r.json()
    assert data["plan"] is not None
    assert data["plan"]["plan_status"] == "waiting"  # 黄色待确认


def test_chat_duplicate_name_clarification(client, db):
    """同名澄清：转给张伟（bob 有两个同名联系人）。"""
    bob = login(client, "bob")
    r = client.post("/api/chat", json={"message": "转给张伟 100 元"}, headers=bob["headers"])
    data = r.json()
    assert data["clarification"] is not None
    assert len(data["clarification"]["options"]) == 2


def test_full_transfer_with_confirm(client, db):
    """完整转账：确认后执行成功，余额守恒。"""
    alice = login(client, "alice")
    me = client.get("/api/me", headers=alice["headers"]).json()
    before = me["balance_cents"]
    r = client.post("/api/chat", json={"message": "转给李伟 50 元"}, headers=alice["headers"])
    data = r.json()
    plan_id = data["plan"]["plan_id"]
    assert data["plan"]["plan_status"] == "waiting"  # 黄色待确认
    # 确认执行
    r2 = client.post(f"/api/plans/{plan_id}/continue",
                     json={"node_key": "t1", "confirmed": True}, headers=alice["headers"])
    data2 = r2.json()
    assert data2["plan_status"] == "completed"
    me2 = client.get("/api/me", headers=alice["headers"]).json()
    assert me2["balance_cents"] == before - 5000


def test_strong_verify_full_flow(client, db):
    """强验证完整流程：红色操作签发挑战→验证→执行。"""
    from app.services import wealth
    wealth.submit_risk_profile(db, user_id=1, answers=[4, 4, 4, 4, 4])
    alice = login(client, "alice")
    r = client.post("/api/tools/run", json={"tool_name": "wealth_subscribe",
                                            "params": {"product_id": 1, "amount_cents": 20000}},
                    headers=alice["headers"])
    data = r.json()
    assert data["status"] == "waiting_auth"
    chal = data["challenge"]
    r2 = client.post("/api/tools/run", json={"tool_name": "wealth_subscribe",
                                             "params": {"product_id": 1, "amount_cents": 20000},
                                             "challenge_id": chal["challenge_id"],
                                             "challenge_code": chal["mock_code"],
                                             "operation_id": data["operation_id"],
                                             "authorization_id": data["authorization_id"]},
                     headers=alice["headers"])
    assert r2.json()["status"] == "success"


def test_health_endpoint(client, db):
    r = client.get("/api/health")
    assert r.json()["status"] == "ok"


def test_login_wrong_password(client, db):
    r = client.post("/api/auth/login", json={"username": "alice", "password": "wrong"})
    assert r.status_code == 401
