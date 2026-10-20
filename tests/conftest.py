"""pytest 测试夹具：独立测试数据库 + 演示数据。"""
import os
import sys
from pathlib import Path

# 在导入 app 前设置测试环境
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TEST_DB = str(ROOT / "data" / "test.db")
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["APP_MODE"] = "offline"

import pytest  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="function")
def db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    from scripts.init_db import seed
    seed(session)
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def client(db):
    with TestClient(app) as c:
        yield c


def login(client, username: str) -> dict:
    resp = client.post("/api/auth/login", json={"username": username,
                                                "password": "demo123456"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    return {"token": data["token"], "user": data["user"],
            "headers": {"Authorization": f"Bearer {data['token']}"}}
