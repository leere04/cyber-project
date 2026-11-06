from typing import Optional

from fastapi import Header, Request
from sqlmodel import Session

from models import EventLog


# 시연용: X-Forwarded-For 헤더로 클라이언트 IP 를 지정할 수 있게 함
# (Swagger 에서 요청이 모두 127.0.0.1 로 들어오기 때문)
# 실제 운영에서는 신뢰할 수 있는 프록시 뒤에서만 True 로 둬야 함
TRUST_X_FORWARDED_FOR = True


def client_ip(
    request: Request,
    x_forwarded_for: Optional[str] = Header(
        default=None,
        description="시연용: 요청 IP 지정 (예: 10.0.0.1)"
    )
) -> Optional[str]:

    if TRUST_X_FORWARDED_FOR and x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()

    return request.client.host if request.client else None


def write_log(session: Session, action: str, **fields) -> EventLog:
    """EventLog 를 세션에 추가만 함. commit 은 호출한 쪽에서."""

    log = EventLog(action=action, **fields)
    session.add(log)

    return log
