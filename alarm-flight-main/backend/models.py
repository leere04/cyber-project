from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Column
from sqlmodel import SQLModel, Field


# EventLog.action 값
class Action:
    SIGNUP = "SIGNUP"
    COUPON_ISSUED = "COUPON_ISSUED"
    FLIGHT_SEARCH = "FLIGHT_SEARCH"
    FLIGHT_VIEW = "FLIGHT_VIEW"
    RESERVATION_ATTEMPT = "RESERVATION_ATTEMPT"    # 탐지까지 진행된 예약 요청
    RESERVATION_REJECTED = "RESERVATION_REJECTED"  # 검증 실패 (좌석 부족, 쿠폰 오류 등)
    CAPTCHA_PASSED = "CAPTCHA_PASSED"
    CAPTCHA_FAILED = "CAPTCHA_FAILED"
    RESERVATION_CANCELLED = "RESERVATION_CANCELLED"


# Reservation.status 값 (앞의 세 개가 탐지 판정 결과)
class Status:
    CONFIRMED = "CONFIRMED"
    CAPTCHA_REQUIRED = "CAPTCHA_REQUIRED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


# -------------------------
# 1. DB 테이블
# -------------------------

# 회원
# PostgreSQL 에서 user 는 예약어라서 테이블 이름을 users 로 지정
class User(SQLModel, table=True):
    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    password_hash: str
    name: str
    phone: Optional[str] = None
    # 다중 계정 탐지용: 가입 당시 IP·기기 정보
    signup_ip: Optional[str] = Field(default=None, index=True)
    signup_device_id: Optional[str] = Field(default=None, index=True)
    is_blocked: bool = False
    created_at: datetime = Field(default_factory=datetime.now)


# 쿠폰 (회원에게 발급된 쿠폰 1장 = 1행)
class Coupon(SQLModel, table=True):
    __tablename__ = "coupons"

    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(unique=True, index=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    coupon_type: str = "signup"  # signup, event 등
    discount_amount: int
    issued_at: datetime = Field(default_factory=datetime.now)
    expires_at: Optional[datetime] = None
    used_at: Optional[datetime] = None  # None 이면 미사용


# 항공편
class Flight(SQLModel, table=True):
    __tablename__ = "flights"

    id: Optional[int] = Field(default=None, primary_key=True)
    flight_no: str = Field(index=True)  # 예: AF101
    departure_airport: str  # 예: GMP
    arrival_airport: str  # 예: CJU
    departure_time: datetime = Field(index=True)
    arrival_time: datetime
    price: int
    total_seats: int
    remaining_seats: int
    # 특가 항공편 여부와 판매 시작 시각 (오픈 직후 선점 봇 탐지용)
    is_special: bool = False
    sale_open_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.now)


# 예약
class Reservation(SQLModel, table=True):
    __tablename__ = "reservations"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    flight_id: int = Field(foreign_key="flights.id", index=True)
    seat_count: int = 1
    total_price: int
    coupon_id: Optional[int] = Field(default=None, foreign_key="coupons.id")
    status: str = Field(default=Status.CONFIRMED, index=True)

    # 탐지 결과
    rule_score: float = 0
    ai_score: Optional[float] = None
    risk_score: float = 0
    reasons: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    # 모의 CAPTCHA (정답은 응답에 포함하지 않음)
    captcha_question: Optional[str] = None
    captcha_answer: Optional[str] = None

    # 봇 탐지용: 예약 요청 당시 IP·기기 정보
    request_ip: Optional[str] = None
    device_id: Optional[str] = None
    user_agent: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)


# 사용자 행동 로그 (탐지 결과 + AI 학습 데이터)
class EventLog(SQLModel, table=True):
    __tablename__ = "event_logs"

    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=datetime.now, index=True)
    user_id: Optional[int] = Field(
        default=None, foreign_key="users.id", index=True
    )
    flight_id: Optional[int] = Field(default=None, foreign_key="flights.id")
    reservation_id: Optional[int] = Field(
        default=None, foreign_key="reservations.id"
    )
    action: str = Field(index=True)
    ip: Optional[str] = Field(default=None, index=True)
    device_id: Optional[str] = Field(default=None, index=True)
    user_agent: Optional[str] = None

    # 탐지 결과
    rule_score: float = 0
    ai_score: Optional[float] = None  # 0~1, AI 모델 연결 전에는 None
    risk_score: float = 0
    decision: Optional[str] = None  # CONFIRMED / CAPTCHA_REQUIRED / BLOCKED
    is_anomaly: bool = Field(default=False, index=True)
    reasons: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    # AI 모델 입력용 Feature (탐지 시점 값)
    features: dict = Field(default_factory=dict, sa_column=Column(JSON))
    message: Optional[str] = None


# -------------------------
# 2. 요청/응답 모델 (DB 테이블 아님)
# -------------------------

class SignupRequest(SQLModel):
    email: str
    password: str
    name: str
    phone: Optional[str] = None
    device_id: Optional[str] = None


# 비밀번호 해시를 빼고 돌려주는 회원 정보
class UserPublic(SQLModel):
    id: int
    email: str
    name: str
    phone: Optional[str]
    is_blocked: bool
    created_at: datetime


class SignupResponse(SQLModel):
    user: UserPublic
    coupon: Coupon


class FlightCreate(SQLModel):
    flight_no: str
    departure_airport: str
    arrival_airport: str
    departure_time: datetime
    arrival_time: datetime
    price: int
    total_seats: int
    is_special: bool = False
    sale_open_at: Optional[datetime] = None


class ReservationRequest(SQLModel):
    user_id: int
    flight_id: int
    seat_count: int = 1
    coupon_id: Optional[int] = None
    device_id: Optional[str] = None


# captcha_answer 를 뺀 예약 정보
class ReservationPublic(SQLModel):
    id: int
    user_id: int
    flight_id: int
    seat_count: int
    total_price: int
    coupon_id: Optional[int]
    status: str
    rule_score: float
    ai_score: Optional[float]
    risk_score: float
    reasons: list[str]
    captcha_question: Optional[str]
    request_ip: Optional[str]
    device_id: Optional[str]
    user_agent: Optional[str]
    created_at: datetime


# 예약 요청 결과 (프론트는 decision 으로 화면 분기)
class ReservationResult(SQLModel):
    decision: str
    message: str
    rule_score: float
    ai_score: Optional[float]
    risk_score: float
    reasons: list[str]
    captcha_question: Optional[str] = None
    reservation: ReservationPublic


class CaptchaRequest(SQLModel):
    answer: str


class CouponIssueRequest(SQLModel):
    user_id: int
    discount_amount: int = 5000
    coupon_type: str = "event"
    valid_days: int = 30


# 프론트·외부에서 직접 남기는 로그
class LogCreate(SQLModel):
    action: str
    user_id: Optional[int] = None
    flight_id: Optional[int] = None
    device_id: Optional[str] = None
    message: Optional[str] = None
    rule_score: float = 0
    ai_score: Optional[float] = None
    risk_score: float = 0
    is_anomaly: bool = False
