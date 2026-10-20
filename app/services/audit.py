"""审计日志。不记录验证码、密钥或模型内部思维链。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import AuditLog


def record(db: Session, *, user_id: int | None, action: str, intent_summary: str = "",
           plan_version: str | None = None, decision_reason: str = "",
           evidence_ref: list | None = None, tool_params: dict | None = None,
           auth_ref: str | None = None, auth_result: str | None = None,
           business_receipt: dict | None = None, before_state: dict | None = None,
           after_state: dict | None = None, success: bool = False) -> AuditLog:
    entry = AuditLog(
        user_id=user_id, action=action, intent_summary=intent_summary,
        plan_version=plan_version, decision_reason=decision_reason,
        evidence_ref=evidence_ref or [], tool_params=tool_params,
        auth_ref=auth_ref, auth_result=auth_result, business_receipt=business_receipt,
        before_state=before_state, after_state=after_state, success=success,
    )
    db.add(entry)
    db.commit()
    return entry
