"""认证：密码哈希 + 服务端会话。身份始终来自服务端会话，不信任请求体自报的 user_id。"""
from __future__ import annotations

import hashlib
import secrets

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Session as SessionModel
from ..models import User


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return f"pbkdf2${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, _hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
        return secrets.compare_digest(dk.hex(), _hex)
    except Exception:
        return False


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(db: Session, user_id: int) -> str:
    """创建会话，返回明文 token（仅此一次）。"""
    import datetime as dt
    token = secrets.token_urlsafe(32)
    s = SessionModel(
        token_hash=_hash_token(token),
        user_id=user_id,
        expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=settings.SESSION_TTL),
    )
    db.add(s)
    db.commit()
    return token


def resolve_session(db: Session, token: str) -> User | None:
    """根据 token 解析用户；过期或无效返回 None。"""
    import datetime as dt
    from ..db import ensure_aware
    s = db.query(SessionModel).filter(SessionModel.token_hash == _hash_token(token)).first()
    if s is None:
        return None
    if ensure_aware(s.expires_at) < dt.datetime.now(dt.timezone.utc):
        return None
    return db.query(User).filter(User.id == s.user_id).first()


def revoke_session(db: Session, token: str) -> None:
    db.query(SessionModel).filter(SessionModel.token_hash == _hash_token(token)).delete()
    db.commit()
