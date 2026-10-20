"""证据卡核验测试（P-09）：撤回疑似重复判断、证据不足保持待核验。"""
from app.services import bill, evidence
from app.models import Account


def _bob_acct(db):
    return db.query(Account).filter(Account.user_id == 2).first()


def test_two_68_withdraw_duplicate(db):
    """两笔 68 元：用户确认来自两个会员 → 撤回疑似重复判断。"""
    acct = _bob_acct(db)
    cards = bill.detect_anomalies(db, account_id=acct.id)
    dup_cards = [c for c in cards if "duplicate" in c["resolution"]]
    assert len(dup_cards) > 0
    for c in dup_cards:
        r = evidence.update_evidence(db, card_id=c["card_id"], user_id=2,
                                     feedback="我有两个会员账号，是正常订阅",
                                     new_status="confirmed_subscription")
        assert r["status"] == "confirmed_subscription"
    remaining = evidence.list_evidence(db, 2, status="pending")
    assert not any("duplicate" in c["resolution"] for c in remaining)


def test_evidence_stays_pending_without_feedback(db):
    """证据不足时保持待核验，不直接认定盗刷。"""
    acct = db.query(Account).filter(Account.user_id == 1).first()
    cards = bill.detect_anomalies(db, account_id=acct.id)
    assert all(c["status"] == "pending" for c in cards)


def test_evidence_isolated_by_user(db):
    acct = _bob_acct(db)
    bill.detect_anomalies(db, account_id=acct.id)
    import pytest
    with pytest.raises(PermissionError):
        evidence.update_evidence(db, card_id=1, user_id=1, feedback="x",
                                 new_status="confirmed_normal")


def test_update_evidence_invalid_status(db):
    acct = _bob_acct(db)
    cards = bill.detect_anomalies(db, account_id=acct.id)
    if not cards:
        return
    import pytest
    with pytest.raises(ValueError):
        evidence.update_evidence(db, card_id=cards[0]["card_id"], user_id=2,
                                 feedback="x", new_status="hacked")
