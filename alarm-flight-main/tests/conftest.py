import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# backend 모듈을 import 할 수 있게 경로 추가
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

# 테스트는 임시 DB 사용 (backend/app.db 를 건드리지 않음)
TEST_DB = Path(tempfile.mkdtemp()) / "test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

import database  # noqa: E402
import main  # noqa: E402


@pytest.fixture
def client():
    # 테스트마다 빈 DB 로 시작
    SQLModel.metadata.drop_all(database.engine)

    with TestClient(main.app) as c:
        yield c


def signup(client, email, ip=None, device_id=None):
    headers = {"X-Forwarded-For": ip} if ip else {}
    r = client.post(
        "/users/signup",
        json={
            "email": email, "password": "pw1234", "name": email.split("@")[0],
            "device_id": device_id
        },
        headers=headers
    )
    assert r.status_code == 200, r.text
    return r.json()


def make_flight(client, **overrides):
    now = datetime.now()
    data = {
        "flight_no": "AF101", "departure_airport": "GMP",
        "arrival_airport": "CJU",
        "departure_time": (now + timedelta(days=7)).isoformat(),
        "arrival_time": (now + timedelta(days=7, hours=1)).isoformat(),
        "price": 30000, "total_seats": 10,
        "sale_open_at": (now - timedelta(days=1)).isoformat(),
    }
    data.update(overrides)
    r = client.post("/flights", json=data)
    assert r.status_code == 200, r.text
    return r.json()


def reserve(client, user_id, flight_id, ip=None, **extra):
    headers = {"X-Forwarded-For": ip} if ip else {}
    return client.post(
        "/reservations",
        json={"user_id": user_id, "flight_id": flight_id, **extra},
        headers=headers
    )
