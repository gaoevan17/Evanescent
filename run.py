"""启动入口：初始化数据库（若为空）并启动服务。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import User  # noqa: E402


def main() -> None:
    Base.metadata.create_all(bind=engine)
    # 若数据库为空则灌入演示数据
    db = SessionLocal()
    try:
        if db.query(User).count() == 0:
            from scripts.init_db import seed
            seed(db)
            print("[init] 已写入演示数据")
    finally:
        db.close()
    print(f"[start] 服务监听 http://{settings.HOST}:{settings.PORT}  (APP_MODE={settings.APP_MODE})")
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=False)


if __name__ == "__main__":
    main()
