import os
from pathlib import Path

from sqlalchemy import event
from sqlmodel import SQLModel, Session, create_engine


BASE_DIR = Path(__file__).resolve().parent

# 기본값: backend/app.db (SQLite, 실행 위치와 관계없음)
# PostgreSQL 로 바꿀 때는 환경변수만 설정하면 됨
#   예) DATABASE_URL=postgresql+psycopg://user:pw@localhost:5432/alarm_flight
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"sqlite:///{BASE_DIR / 'app.db'}"
)


def make_engine(url: str):

    if not url.startswith("sqlite"):
        return create_engine(url)

    engine = create_engine(
        url,
        connect_args={"check_same_thread": False}
    )

    # SQLite 는 기본적으로 외래키 검사를 하지 않으므로 켜줌
    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    return engine


engine = make_engine(DATABASE_URL)


def create_db_and_tables():
    import models  # noqa: F401  테이블 등록용

    SQLModel.metadata.create_all(engine)


# FastAPI 의존성: 요청마다 세션 1개
def get_session():
    with Session(engine) as session:
        yield session
