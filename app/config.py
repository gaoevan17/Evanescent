"""应用配置。所有配置从环境变量读取，提供默认值。"""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # dotenv 可选
    pass

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings:
    def __init__(self) -> None:
        self.APP_MODE = os.getenv("APP_MODE", "offline").strip().lower()  # offline | llm
        self.LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
        self.LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
        self.LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")

        self.HOST = os.getenv("HOST", "127.0.0.1")
        self.PORT = int(os.getenv("PORT", "8000"))

        db_url = os.getenv("DATABASE_URL", "sqlite:///./data/consensus_guardian.db")
        # 相对路径转为项目根目录下的绝对路径
        if db_url.startswith("sqlite:///./"):
            rel = db_url[len("sqlite:///./"):]
            db_url = f"sqlite:///{(BASE_DIR / rel).as_posix()}"
        self.DATABASE_URL = db_url

        self.SESSION_TTL = int(os.getenv("SESSION_TTL", "86400"))
        self.CHALLENGE_TTL = int(os.getenv("CHALLENGE_TTL", "300"))
        self.CHALLENGE_MAX_ATTEMPTS = int(os.getenv("CHALLENGE_MAX_ATTEMPTS", "5"))

        self.CIRCUIT_BREAKER_FAILURES = int(os.getenv("CIRCUIT_BREAKER_FAILURES", "5"))
        self.CIRCUIT_BREAKER_WINDOW_SECONDS = int(os.getenv("CIRCUIT_BREAKER_WINDOW_SECONDS", "300"))

        self.TZ = os.getenv("TZ", "Asia/Shanghai")

        self.DEMO_ALICE_PASSWORD = os.getenv("DEMO_ALICE_PASSWORD", "demo123456")
        self.DEMO_BOB_PASSWORD = os.getenv("DEMO_BOB_PASSWORD", "demo123456")
        self.DEMO_CAROL_PASSWORD = os.getenv("DEMO_CAROL_PASSWORD", "demo123456")


settings = Settings()
