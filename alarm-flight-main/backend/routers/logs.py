from typing import Optional
import random

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, func, select

import risk
from database import get_session
from events import client_ip, write_log
from models import (
    Action, EventLog, LogCreate, Reservation, Status, User, UserPublic
)
from rule_engine import RULES


router = APIRouter(tags=["관리자: 로그·통계"])


def check_user(session: Session, user_id: Optional[int]):
    if user_id is not None and not session.get(User, user_id):
        raise HTTPException(404, "회원을 찾을 수 없습니다")


# 로그 직접 생성 (프론트에서 화면 이동 등 행동을 남길 때)
@router.post("/logs", response_model=EventLog)
def create_log(
    data: LogCreate,
    request: Request,
    ip: Optional[str] = Depends(client_ip),
    session: Session = Depends(get_session)
):

    check_user(session, data.user_id)

    log = write_log(
        session, ip=ip,
        user_agent=request.headers.get("user-agent"),
        **data.model_dump()
    )
    session.commit()
    session.refresh(log)

    return log


# 전체 로그 조회 (최신순)
@router.get("/logs", response_model=list[EventLog])
def get_logs(
    action: Optional[str] = None,
    limit: int = 100,
    session: Session = Depends(get_session)
):

    statement = select(EventLog)

    if action:
        statement = statement.where(EventLog.action == action)

    statement = statement.order_by(EventLog.id.desc()).limit(limit)

    return session.exec(statement).all()


# 이상 로그만 조회 (최신순)
@router.get("/logs/anomalies", response_model=list[EventLog])
def get_anomaly_logs(
    limit: int = 100,
    session: Session = Depends(get_session)
):

    statement = (
        select(EventLog)
        .where(EventLog.is_anomaly == True)
        .order_by(EventLog.id.desc())
        .limit(limit)
    )

    return session.exec(statement).all()


def rate(part: int, total: int) -> float:
    return round(part / total * 100, 1) if total else 0


def count_by(session, column, model) -> dict:
    rows = session.exec(
        select(column, func.count()).select_from(model).group_by(column)
    ).all()
    return {key: count for key, count in rows}


# 대시보드용 통계
@router.get("/stats")
def get_stats(session: Session = Depends(get_session)):

    logs = session.exec(select(EventLog)).all()

    # 전체 로그 기준
    total_logs = len(logs)
    anomaly_logs = sum(1 for log in logs if log.is_anomaly)

    # 예약 요청(탐지 수행) 기준
    attempts = [
        log for log in logs if log.action == Action.RESERVATION_ATTEMPT
    ]
    anomaly_attempts = sum(1 for log in attempts if log.is_anomaly)

    by_decision = {
        Status.CONFIRMED: 0, Status.CAPTCHA_REQUIRED: 0, Status.BLOCKED: 0
    }
    rule_hits = {code: 0 for code in RULES}
    for log in attempts:
        by_decision[log.decision] = by_decision.get(log.decision, 0) + 1
        for reason in log.reasons or []:
            code = reason.split("(")[0]
            rule_hits[code] = rule_hits.get(code, 0) + 1

    by_status = {
        Status.CONFIRMED: 0, Status.CAPTCHA_REQUIRED: 0,
        Status.BLOCKED: 0, Status.CANCELLED: 0
    }
    by_status.update(count_by(session, Reservation.status, Reservation))

    avg_risk = (
        round(sum(log.risk_score for log in attempts) / len(attempts), 1)
        if attempts else 0
    )

    return {
        # 기존 키 (전체 로그 기준)
        "total_logs": total_logs,
        "anomaly_logs": anomaly_logs,
        "anomaly_rate": rate(anomaly_logs, total_logs),

        "total_users": session.exec(
            select(func.count()).select_from(User)
        ).one(),
        "blocked_users": session.exec(
            select(func.count()).select_from(User)
            .where(User.is_blocked == True)
        ).one(),

        # 예약 (현재 상태 기준, CAPTCHA 통과 시 CONFIRMED 로 바뀜)
        "reservations": {
            "total": sum(by_status.values()),
            "by_status": by_status,
        },

        # 탐지 (예약 요청 시점 판정 기준)
        "detection": {
            "total_attempts": len(attempts),
            "anomaly_count": anomaly_attempts,
            "anomaly_rate": rate(anomaly_attempts, len(attempts)),
            "by_decision": by_decision,
            "avg_risk_score": avg_risk,
            "rule_hits": rule_hits,
            "rejected_requests": sum(
                1 for log in logs
                if log.action == Action.RESERVATION_REJECTED
            ),
        },

        "logs_by_action": count_by(session, EventLog.action, EventLog),
    }


# 현재 탐지 규칙·판정 기준 조회
@router.get("/rules")
def get_rules():
    return {
        "rules": RULES,
        "thresholds": {
            "captcha": risk.CAPTCHA_THRESHOLD,
            "block": risk.BLOCK_THRESHOLD,
        },
        "weights": {
            "rule": risk.RULE_WEIGHT,
            "ai": risk.AI_WEIGHT,
        },
    }


# 회원 차단 / 해제 (관리자)
@router.post("/users/{user_id}/block", response_model=UserPublic)
def block_user(
    user_id: int,
    blocked: bool = True,
    session: Session = Depends(get_session)
):

    user = session.get(User, user_id)
    if not user:
        raise HTTPException(404, "회원을 찾을 수 없습니다")

    user.is_blocked = blocked
    session.add(user)
    session.commit()
    session.refresh(user)

    return user


# 특정 사용자 로그 조회 (최신순)
@router.get("/logs/user/{user_id}", response_model=list[EventLog])
def get_user_logs(user_id: int, session: Session = Depends(get_session)):

    statement = (
        select(EventLog)
        .where(EventLog.user_id == user_id)
        .order_by(EventLog.id.desc())
    )

    return session.exec(statement).all()


# 임시 이상행동 탐지 (초기 테스트용, 실제 탐지는 POST /reservations 에서 수행)
@router.post("/detect", response_model=EventLog, deprecated=True)
def detect(
    user_id: int,
    action: str,
    session: Session = Depends(get_session)
):

    check_user(session, user_id)

    # 임시 AI 역할: 0~1 사이의 이상 점수를 생성
    score = round(random.random(), 2)

    log = write_log(
        session, action,
        user_id=user_id,
        ai_score=score,
        risk_score=score * 100,
        # 0.7 이상이면 이상 행동으로 판정
        is_anomaly=score >= 0.7,
        message="임시 랜덤 점수"
    )
    session.commit()
    session.refresh(log)

    return log
