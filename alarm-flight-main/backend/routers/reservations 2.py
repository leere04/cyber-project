from datetime import datetime
from typing import Optional
import random

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select, update

from database import get_session
from events import client_ip, write_log
from models import (
    Action, CaptchaRequest, Coupon, Flight, Reservation, ReservationPublic,
    ReservationRequest, ReservationResult, Status, User
)
from risk import calculate_risk, decide, get_ai_score
from rule_engine import RuleContext, evaluate


router = APIRouter(tags=["예약"])

MESSAGES = {
    Status.CONFIRMED: "예약이 확정되었습니다",
    Status.CAPTCHA_REQUIRED: (
        "추가 인증이 필요합니다. "
        "POST /reservations/{id}/captcha 로 답을 보내주세요"
    ),
    Status.BLOCKED: "부정 예약으로 의심되어 예약이 제한되었습니다",
    Status.CANCELLED: "예약이 취소되었습니다",
}


def to_result(reservation: Reservation, message: Optional[str] = None):
    return ReservationResult(
        decision=reservation.status,
        message=(message or MESSAGES[reservation.status]).replace(
            "{id}", str(reservation.id)
        ),
        rule_score=reservation.rule_score,
        ai_score=reservation.ai_score,
        risk_score=reservation.risk_score,
        reasons=reservation.reasons,
        captcha_question=reservation.captcha_question,
        reservation=ReservationPublic.model_validate(reservation)
    )


def consume(session, flight_id, seat_count, coupon_id, now) -> Optional[str]:
    """
    좌석 차감 + 쿠폰 사용 처리.
    조건부 UPDATE 로 처리해서 동시 요청에도 좌석이 음수가 되거나
    쿠폰이 두 번 쓰이지 않음. 실패하면 오류 코드를 돌려줌.
    """

    seats = session.exec(
        update(Flight)
        .where(Flight.id == flight_id, Flight.remaining_seats >= seat_count)
        .values(remaining_seats=Flight.remaining_seats - seat_count)
    )
    if seats.rowcount != 1:
        return "SOLD_OUT"

    if coupon_id is not None:
        used = session.exec(
            update(Coupon)
            .where(Coupon.id == coupon_id, Coupon.used_at.is_(None))
            .values(used_at=now)
        )
        if used.rowcount != 1:
            return "COUPON_ALREADY_USED"

    return None


def do_reservation(
    session: Session,
    data: ReservationRequest,
    ip: Optional[str],
    user_agent: Optional[str]
) -> ReservationResult:

    now = datetime.now()

    def reject(status_code, code, message, user=None, flight=None):
        # 거절된 요청도 탐지에 쓰이므로 로그로 남김
        session.rollback()
        write_log(
            session, Action.RESERVATION_REJECTED,
            user_id=user.id if user else None,
            flight_id=flight.id if flight else None,
            ip=ip, device_id=data.device_id, user_agent=user_agent,
            reasons=[code], message=message
        )
        session.commit()
        raise HTTPException(status_code, message)

    # 1. 요청 검증
    user = session.get(User, data.user_id)
    if not user:
        reject(404, "USER_NOT_FOUND", "회원을 찾을 수 없습니다")
    if user.is_blocked:
        reject(403, "USER_BLOCKED", "차단된 회원입니다", user)

    flight = session.get(Flight, data.flight_id)
    if not flight:
        reject(404, "FLIGHT_NOT_FOUND", "항공편을 찾을 수 없습니다", user)
    if data.seat_count < 1:
        reject(400, "INVALID_SEAT_COUNT",
               "좌석 수는 1 이상이어야 합니다", user, flight)
    if flight.sale_open_at and now < flight.sale_open_at:
        reject(400, "SALE_NOT_OPEN",
               "아직 판매가 시작되지 않았습니다", user, flight)
    if flight.remaining_seats < data.seat_count:
        reject(400, "SOLD_OUT", "잔여 좌석이 부족합니다", user, flight)

    total_price = flight.price * data.seat_count

    coupon = None
    if data.coupon_id is not None:
        coupon = session.get(Coupon, data.coupon_id)
        if not coupon or coupon.user_id != user.id:
            reject(400, "COUPON_INVALID",
                   "사용할 수 없는 쿠폰입니다", user, flight)
        if coupon.used_at:
            reject(400, "COUPON_ALREADY_USED",
                   "이미 사용한 쿠폰입니다", user, flight)
        if coupon.expires_at and now > coupon.expires_at:
            reject(400, "COUPON_EXPIRED", "만료된 쿠폰입니다", user, flight)

        total_price = max(total_price - coupon.discount_amount, 0)

    # 2. 탐지: Rule Score + AI Score -> Risk Score -> 판정
    rules = evaluate(session, RuleContext(
        user=user, flight=flight, coupon=coupon,
        seat_count=data.seat_count,
        ip=ip, device_id=data.device_id, now=now
    ))
    ai_score = get_ai_score(rules.features)
    risk_score = calculate_risk(rules.score, ai_score)
    decision = decide(risk_score)

    reservation = Reservation(
        user_id=user.id,
        flight_id=flight.id,
        seat_count=data.seat_count,
        total_price=total_price,
        coupon_id=coupon.id if coupon else None,
        status=decision,
        rule_score=rules.score,
        ai_score=ai_score,
        risk_score=risk_score,
        reasons=rules.reasons,
        request_ip=ip,
        device_id=data.device_id,
        user_agent=user_agent
    )

    # 3. 판정별 처리
    if decision == Status.CONFIRMED:
        error = consume(
            session, flight.id, data.seat_count, reservation.coupon_id, now
        )
        if error:
            reject(400, error, "예약 처리 중 좌석 또는 쿠폰이 소진되었습니다",
                   user, flight)

    elif decision == Status.CAPTCHA_REQUIRED:
        # 모의 CAPTCHA: 간단한 덧셈 문제 (좌석·쿠폰은 통과 후 처리)
        a, b = random.randint(1, 9), random.randint(1, 9)
        reservation.captcha_question = f"{a} + {b} = ?"
        reservation.captcha_answer = str(a + b)

    # BLOCKED: 좌석·쿠폰을 사용하지 않고 기록만 남김

    session.add(reservation)
    session.flush()

    write_log(
        session, Action.RESERVATION_ATTEMPT,
        user_id=user.id, flight_id=flight.id, reservation_id=reservation.id,
        ip=ip, device_id=data.device_id, user_agent=user_agent,
        rule_score=rules.score, ai_score=ai_score, risk_score=risk_score,
        decision=decision, is_anomaly=decision != Status.CONFIRMED,
        reasons=rules.reasons, features=rules.features
    )
    session.commit()
    session.refresh(reservation)

    return to_result(reservation)


# 예약 요청 (+ 쿠폰 사용, 부정 예약 탐지)
@router.post("/reservations", response_model=ReservationResult)
def create_reservation(
    data: ReservationRequest,
    request: Request,
    ip: Optional[str] = Depends(client_ip),
    session: Session = Depends(get_session)
):
    """
    결과의 decision 에 따라 처리:
    - CONFIRMED: 예약 확정 (좌석 차감, 쿠폰 사용)
    - CAPTCHA_REQUIRED: captcha_question 을 보여주고 /captcha 로 답 제출
    - BLOCKED: 예약 제한
    """
    return do_reservation(
        session, data, ip, request.headers.get("user-agent")
    )


def get_or_404(session, reservation_id) -> Reservation:

    reservation = session.get(Reservation, reservation_id)

    if not reservation:
        raise HTTPException(404, "예약을 찾을 수 없습니다")

    return reservation


# 모의 CAPTCHA 답 제출
@router.post(
    "/reservations/{reservation_id}/captcha",
    response_model=ReservationResult
)
def solve_captcha(
    reservation_id: int,
    data: CaptchaRequest,
    ip: Optional[str] = Depends(client_ip),
    session: Session = Depends(get_session)
):

    reservation = get_or_404(session, reservation_id)

    if reservation.status != Status.CAPTCHA_REQUIRED:
        raise HTTPException(400, "CAPTCHA 대기 중인 예약이 아닙니다")

    log_fields = dict(
        user_id=reservation.user_id, flight_id=reservation.flight_id,
        reservation_id=reservation.id, ip=ip
    )

    if data.answer.strip() != reservation.captcha_answer:
        write_log(session, Action.CAPTCHA_FAILED, **log_fields)
        session.commit()
        raise HTTPException(400, "CAPTCHA 답이 틀렸습니다")

    error = consume(
        session, reservation.flight_id, reservation.seat_count,
        reservation.coupon_id, datetime.now()
    )
    if error:
        session.rollback()
        raise HTTPException(400, "좌석 또는 쿠폰이 이미 소진되었습니다")

    reservation.status = Status.CONFIRMED
    reservation.captcha_answer = None
    session.add(reservation)
    write_log(session, Action.CAPTCHA_PASSED, **log_fields)
    session.commit()
    session.refresh(reservation)

    return to_result(reservation, "CAPTCHA 통과, 예약이 확정되었습니다")


# 예약 취소 (확정 예약이면 좌석·쿠폰 복구)
@router.post(
    "/reservations/{reservation_id}/cancel",
    response_model=ReservationResult
)
def cancel_reservation(
    reservation_id: int,
    ip: Optional[str] = Depends(client_ip),
    session: Session = Depends(get_session)
):

    reservation = get_or_404(session, reservation_id)

    if reservation.status not in (Status.CONFIRMED, Status.CAPTCHA_REQUIRED):
        raise HTTPException(400, "취소할 수 없는 예약입니다")

    if reservation.status == Status.CONFIRMED:
        session.exec(
            update(Flight)
            .where(Flight.id == reservation.flight_id)
            .values(
                remaining_seats=Flight.remaining_seats + reservation.seat_count
            )
        )
        if reservation.coupon_id is not None:
            session.exec(
                update(Coupon)
                .where(Coupon.id == reservation.coupon_id)
                .values(used_at=None)
            )

    reservation.status = Status.CANCELLED
    reservation.captcha_answer = None
    session.add(reservation)
    write_log(
        session, Action.RESERVATION_CANCELLED,
        user_id=reservation.user_id, flight_id=reservation.flight_id,
        reservation_id=reservation.id, ip=ip
    )
    session.commit()
    session.refresh(reservation)

    return to_result(reservation)


# 예약 목록 (관리자용, 최신순)
@router.get("/reservations", response_model=list[ReservationPublic])
def list_reservations(
    status: Optional[str] = None,
    limit: int = 100,
    session: Session = Depends(get_session)
):

    statement = select(Reservation)

    if status:
        statement = statement.where(Reservation.status == status)

    statement = statement.order_by(Reservation.id.desc()).limit(limit)

    return session.exec(statement).all()


# 예약 조회
@router.get(
    "/reservations/{reservation_id}",
    response_model=ReservationPublic
)
def get_reservation(
    reservation_id: int,
    session: Session = Depends(get_session)
):
    return get_or_404(session, reservation_id)
