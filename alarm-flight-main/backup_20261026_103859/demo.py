"""
시연용 API (Swagger 에서 버튼 한 번으로 시나리오 실행)

실제 API 와 같은 함수(do_signup, do_reservation)를 그대로 호출하므로
탐지 결과도 실제 요청과 동일함.
DEMO_MODE=0 으로 실행하면 이 라우터는 등록되지 않음.
"""

from datetime import datetime, timedelta
from enum import Enum
import random

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, delete, select

from database import get_session
from models import (
    Coupon, EventLog, Flight, Reservation, ReservationRequest,
    SignupRequest, User
)
from routers.reservations import do_reservation
from routers.users import do_signup


router = APIRouter(prefix="/demo", tags=["시연"])

DEMO_USER_AGENT = "demo-script"


class Scenario(str, Enum):
    normal = "normal"
    suspicious = "suspicious"
    bot = "bot"


def seed_flights(session: Session) -> list[Flight]:
    """시연용 항공편 (이미 있으면 새로 만들지 않음)"""

    now = datetime.now()
    tomorrow = now.replace(hour=9, minute=0, second=0, microsecond=0) \
        + timedelta(days=1)

    flights = [
        dict(flight_no="AF101", departure_airport="GMP", arrival_airport="CJU",
             departure_time=tomorrow, arrival_time=tomorrow + timedelta(hours=1),
             price=49000, total_seats=180, is_special=False,
             sale_open_at=now - timedelta(days=7)),
        dict(flight_no="AF202", departure_airport="GMP", arrival_airport="PUS",
             departure_time=tomorrow + timedelta(hours=3),
             arrival_time=tomorrow + timedelta(hours=4),
             price=39000, total_seats=180, is_special=False,
             sale_open_at=now - timedelta(days=7)),
        # 판매 시작 전 특가 (SALE_NOT_OPEN 확인용)
        dict(flight_no="AF888", departure_airport="ICN", arrival_airport="NRT",
             departure_time=tomorrow + timedelta(days=30),
             arrival_time=tomorrow + timedelta(days=30, hours=2),
             price=19000, total_seats=20, is_special=True,
             sale_open_at=now + timedelta(days=1)),
    ]

    result = []
    for data in flights:
        flight = session.exec(
            select(Flight).where(Flight.flight_no == data["flight_no"])
        ).first()
        if not flight:
            flight = Flight(**data, remaining_seats=data["total_seats"])
            session.add(flight)
        result.append(flight)

    session.commit()
    for flight in result:
        session.refresh(flight)

    return result


def regular_flight(session: Session) -> Flight:
    return next(
        f for f in seed_flights(session) if f.flight_no == "AF101"
    )


def flash_sale_flight(session: Session) -> Flight:
    """방금 판매가 열린 특가 항공편 (시나리오마다 새로 생성)"""

    now = datetime.now()
    flight = Flight(
        flight_no=f"AF9{random.randint(10, 99)}",
        departure_airport="GMP", arrival_airport="CJU",
        departure_time=now + timedelta(days=14),
        arrival_time=now + timedelta(days=14, hours=1),
        price=9900, total_seats=10, remaining_seats=10,
        is_special=True, sale_open_at=now - timedelta(milliseconds=500)
    )
    session.add(flight)
    session.commit()
    session.refresh(flight)

    return flight


def new_user(session, tag, name, ip, device_id):
    return do_signup(
        session,
        SignupRequest(
            email=f"{name}.{tag}@demo.com", password="demo1234",
            name=name, device_id=device_id
        ),
        ip, DEMO_USER_AGENT
    )


def book(session, signup_result, flight, ip, device_id, use_coupon=True):
    return do_reservation(
        session,
        ReservationRequest(
            user_id=signup_result.user.id, flight_id=flight.id,
            coupon_id=signup_result.coupon.id if use_coupon else None,
            device_id=device_id
        ),
        ip, DEMO_USER_AGENT
    )


def step(label, user, result):
    return {
        "step": label,
        "user_id": user.user.id,
        "email": user.user.email,
        "decision": result.decision,
        "risk_score": result.risk_score,
        "reasons": result.reasons,
        "reservation_id": result.reservation.id,
        "captcha_question": result.captcha_question,
    }


# 시연용 항공편 생성
@router.post("/seed")
def seed(session: Session = Depends(get_session)):
    return {"flights": seed_flights(session)}


# 시나리오 실행
@router.post("/scenarios/{scenario}")
def run_scenario(scenario: Scenario, session: Session = Depends(get_session)):
    """
    - normal: 정상 사용자 1명이 가입 쿠폰으로 일반 항공편 예약 → CONFIRMED
    - suspicious: 같은 기기에서 두 번째 계정이 예약 → CAPTCHA_REQUIRED
    - bot: 한 IP·기기에서 만든 계정 4개가 특가 오픈 직후 쿠폰으로 연속 예약 → BLOCKED
    """

    # 실행할 때마다 다른 계정·IP·기기 사용 (반복 시연 가능)
    tag = f"{datetime.now():%H%M%S}{random.randint(100, 999)}"

    def rand_ip():
        return f"{random.randint(11, 220)}.{random.randint(0, 255)}." \
               f"{random.randint(0, 255)}.{random.randint(1, 254)}"

    if scenario == Scenario.normal:
        ip, device = rand_ip(), f"iphone-{tag}"
        flight = regular_flight(session)
        user = new_user(session, tag, "normal", ip, device)
        result = book(session, user, flight, ip, device)

        steps = [step("정상 사용자 예약 (가입 쿠폰 사용)", user, result)]

    elif scenario == Scenario.suspicious:
        device = f"shared-tablet-{tag}"
        flight = regular_flight(session)
        first = new_user(session, tag, "family1", rand_ip(), device)
        second_ip = rand_ip()
        second = new_user(session, tag, "family2", second_ip, device)
        result = book(session, second, flight, second_ip, device,
                      use_coupon=False)

        steps = [step("같은 기기에서 만든 두 번째 계정으로 예약", second, result)]
        steps[0]["hint"] = (
            f"POST /reservations/{result.reservation.id}/captcha 에 "
            f"captcha_question 의 답을 보내면 예약이 확정됨"
        )
        steps[0]["first_account_id"] = first.user.id

    else:
        ip, device = rand_ip(), f"headless-chrome-{tag}"
        bots = [
            new_user(session, tag, f"bot{i}", ip, device) for i in range(1, 5)
        ]
        flight = flash_sale_flight(session)

        steps = [
            step(f"봇 계정 {i} 특가 예약", bot,
                 book(session, bot, flight, ip, device))
            for i, bot in enumerate(bots, start=1)
        ]

    return {
        "scenario": scenario,
        "final_decision": steps[-1]["decision"],
        "steps": steps,
    }


# 전체 데이터 삭제 (시연 전 초기화)
@router.post("/reset")
def reset(confirm: bool = False, session: Session = Depends(get_session)):

    if not confirm:
        raise HTTPException(400, "confirm=true 로 호출해야 모든 데이터가 삭제됩니다")

    # 외래키 순서대로 삭제
    for model in (EventLog, Reservation, Coupon, User, Flight):
        session.exec(delete(model))
    session.commit()

    return {"message": "모든 데이터를 삭제했습니다"}
