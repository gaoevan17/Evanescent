"""LLM 统一入口：根据 APP_MODE 选择离线解析器或真实模型。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..config import settings


def get_planner(db: Session):
    if settings.APP_MODE == "llm" and settings.LLM_API_KEY:
        from .provider import LLMPlanner
        return LLMPlanner()
    from .offline import OfflinePlanner
    return OfflinePlanner(db)


def is_offline_mode() -> bool:
    return not (settings.APP_MODE == "llm" and settings.LLM_API_KEY)
