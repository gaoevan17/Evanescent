"""服务端强验证挑战（模拟短信 OTP）。

实现真实的服务端挑战机制：过期、一次性消费、防重放、限次、结果校验。
挑战码为随机一次性 OTP，由服务端生成并以「模拟短信」形式返回（仅演示环境），
**醒目标记为模拟认证**，不冒充正式强验证（人脸/短信/U盾）。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import secrets

from sqlalchemy.orm import Session

from ..config import settings
from ..models import AuthChallenge


class ChallengeError(Exception):
    pass


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def issue_challenge(db: Session, user_id: int, purpose: str, operation_id: str) -> dict:
    """签发一次强验证挑战。返回 {challenge_id, mock_code, expires_at}。

    mock_code 仅在「模拟认证环境」返回，用于演示；生产环境应由短信/人脸通道下发，
    不会回传给调用方。
    """
    code = f"{secrets.randbelow(1_000_000):06d}"
    c = AuthChallenge(
        user_id=user_id,
        purpose=purpose,
        challenge_type="sms_otp",
        code_hash=_hash(code),
        operation_id=operation_id,
        expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=settings.CHALLENGE_TTL),
    )
    db.add(c)
    db.commit()
    return {
        "challenge_id": c.id,
        "challenge_type": "sms_otp",
        "simulated": True,
        "mock_code": code,  # 仅模拟环境
        "expires_at": c.expires_at.isoformat(),
        "ttl_seconds": settings.CHALLENGE_TTL,
        "operation_id": operation_id,
    }


def verify_challenge(db: Session, challenge_id: int, user_id: int,
                     operation_id: str, code: str) -> bool:
    """校验挑战码：一次性消费、过期、限次、绑定用户与操作。"""
    c = db.query(AuthChallenge).filter(AuthChallenge.id == challenge_id).first()
    if c is None:
        raise ChallengeError("挑战不存在")
    from ..db import ensure_aware
    if c.user_id != user_id:
        raise ChallengeError("挑战不属于当前用户")
    if c.operation_id != operation_id:
        raise ChallengeError("挑战与当前操作不匹配")
    if c.consumed:
        raise ChallengeError("挑战已使用（防重放）")
    if ensure_aware(c.expires_at) < dt.datetime.now(dt.timezone.utc):
        raise ChallengeError("挑战已过期")
    if c.attempts >= settings.CHALLENGE_MAX_ATTEMPTS:
        raise ChallengeError("挑战尝试次数已达上限")
    c.attempts += 1
    db.commit()
    if not secrets.compare_digest(_hash(code), c.code_hash):
        raise ChallengeError("验证码错误")
    c.consumed = True
    db.commit()
    return True
