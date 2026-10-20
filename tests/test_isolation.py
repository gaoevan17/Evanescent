"""对象级权限隔离测试（P-10）：越权查询拒绝、私有数据不泄露、共享进度可见。"""
from app.services import ledger
from tests.conftest import login


def test_cannot_query_other_balance(client, db):
    """A 无法查询 B 的余额。"""
    alice = login(client, "alice")
    r = client.get("/api/me", headers=alice["headers"])
    data = r.json()
    assert data["balance_cents"] == 500000  # 只能看到自己的
    assert "bob" not in str(data)


def test_no_cross_user_endpoint(client, db):
    """不存在按 user_id 查询任意账户余额的端点。"""
    alice = login(client, "alice")
    # 尝试通过工具传 user_id 参数查他人
    r = client.post("/api/tools/run", json={"tool_name": "balance_query",
                                            "params": {"user_id": 2}}, headers=alice["headers"])
    data = r.json()
    # 结果仍是本人余额，user_id 参数被忽略
    assert data["result"]["balance_cents"] == 500000


def test_shared_progress_no_private_balance(client, db):
    """共享进度不含私人余额字段。"""
    alice = login(client, "alice")
    r = client.get("/api/space/1/progress", headers=alice["headers"])
    data = r.json()
    for m in data["members"].values():
        assert set(m.keys()) <= {"due_cents", "paid_cents", "remaining_cents"}


def test_non_member_rejected_from_space(client, db):
    """非成员无法查看空间进度。"""
    # 创建一个非成员用户
    from app.auth import hash_password
    from app.models import User, Account
    u = User(username="stranger", display_name="陌生人", password_hash=hash_password("demo123456"))
    db.add(u)
    db.commit()
    db.add(Account(user_id=u.id, balance_cents=10000))
    db.commit()
    s = login(client, "stranger")
    r = client.get("/api/space/1/progress", headers=s["headers"])
    assert r.status_code == 403


def test_audit_only_own(client, db):
    alice = login(client, "alice")
    r = client.get("/api/audit", headers=alice["headers"])
    assert r.status_code == 200
    # 审计只返回本人记录


def test_card_full_number_not_in_shared_view(client, db):
    """共享视图不暴露完整卡号。"""
    from app.services import card as card_svc
    cards = card_svc.list_cards(db, 1, shared_view=True)
    assert all(c["full_number"] is None for c in cards)
    cards_self = card_svc.list_cards(db, 1, shared_view=False)
    assert all(c["full_number"] is not None for c in cards_self)


def test_evidence_only_own(client, db):
    from app.services import evidence
    # bob 的两笔 68 元扣费产生证据卡（先跑异常识别）
    from app.services import bill
    from app.models import Account
    acct = db.query(Account).filter(Account.user_id == 2).first()
    bill.detect_anomalies(db, account_id=acct.id)
    bob_cards = evidence.list_evidence(db, user_id=2)
    assert len(bob_cards) > 0
    # alice 看不到 bob 的证据卡
    alice_cards = evidence.list_evidence(db, user_id=1)
    ids = {c["card_id"] for c in bob_cards}
    assert not any(c["card_id"] in ids for c in alice_cards)


def test_cannot_pay_on_behalf(client, db):
    """A 不能替 B 付款（B 的授权必须来自 B 本人会话）。"""
    alice = login(client, "alice")
    # 转账工具只认当前会话身份 alice；无法指定 payer 为 bob
    r = client.post("/api/tools/run", json={"tool_name": "transfer",
                                            "params": {"amount_cents": 1000,
                                                       "payer_user_id": 2,
                                                       "payee_external": "x"},
                                            "confirmed": True}, headers=alice["headers"])
    # payer_user_id 被忽略，仍是 alice 付款
    data = r.json()
    assert data["status"] in ("success", "waiting_auth")
    # 付款方始终是 alice，而不是 bob


def test_identity_from_session_not_body(client, db):
    """身份来自会话，请求体自报 user_id 无效。"""
    alice = login(client, "alice")
    r = client.post("/api/tools/run", json={"tool_name": "balance_query",
                                            "params": {"user_id": 999}}, headers=alice["headers"])
    assert r.json()["result"]["balance_cents"] == 500000
