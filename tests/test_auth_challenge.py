"""强验证挑战测试：过期、一次性消费、防重放、限次、绑定用户。"""
import datetime as dt
import pytest

from app.auth import challenge as challenge_mod
from app.auth.challenge import ChallengeError
from app.models import AuthChallenge


def _issue(db, user_id=1, op="op-test"):
    return challenge_mod.issue_challenge(db, user_id, "strong_verify", op)


def test_issue_and_verify(db):
    c = _issue(db)
    assert c["simulated"] is True
    assert challenge_mod.verify_challenge(db, c["challenge_id"], 1, "op-test", c["mock_code"]) is True


def test_wrong_code_rejected(db):
    c = _issue(db)
    with pytest.raises(ChallengeError):
        challenge_mod.verify_challenge(db, c["challenge_id"], 1, "op-test", "000000")


def test_one_time_consumption(db):
    """一次性消费：验证成功后不可再用（防重放）。"""
    c = _issue(db)
    challenge_mod.verify_challenge(db, c["challenge_id"], 1, "op-test", c["mock_code"])
    with pytest.raises(ChallengeError):
        challenge_mod.verify_challenge(db, c["challenge_id"], 1, "op-test", c["mock_code"])


def test_expiry(db):
    c = _issue(db)
    ch = db.query(AuthChallenge).filter(AuthChallenge.id == c["challenge_id"]).first()
    ch.expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
    db.commit()
    with pytest.raises(ChallengeError):
        challenge_mod.verify_challenge(db, c["challenge_id"], 1, "op-test", c["mock_code"])


def test_wrong_user_rejected(db):
    c = _issue(db, user_id=1)
    with pytest.raises(ChallengeError):
        challenge_mod.verify_challenge(db, c["challenge_id"], 2, "op-test", c["mock_code"])


def test_wrong_operation_rejected(db):
    c = _issue(db, op="op-a")
    with pytest.raises(ChallengeError):
        challenge_mod.verify_challenge(db, c["challenge_id"], 1, "op-b", c["mock_code"])


def test_max_attempts(db):
    c = _issue(db)
    for _ in range(5):
        try:
            challenge_mod.verify_challenge(db, c["challenge_id"], 1, "op-test", "wrong")
        except ChallengeError:
            pass
    with pytest.raises(ChallengeError):
        challenge_mod.verify_challenge(db, c["challenge_id"], 1, "op-test", c["mock_code"])
