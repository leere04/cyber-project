"""
규칙 기반 부정 예약 탐지

- 점수·기준값은 아래 RULES 에서 수정
- 규칙을 끄려면 "enabled": False
- evaluate() 가 돌려주는 features 는 EventLog 에 저장되어 AI 학습 데이터로도 쓰임
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from sqlmodel import Session, func, or_, select

from models import Action, Coupon, EventLog, Flight, Reservation, User


# -------------------------
# 규칙 설정
# -------------------------

RULES = {
    # 같은 기기에서 threshold 개 이상의 계정이 활동
    "MULTI_ACCOUNT_DEVICE": {
        "enabled": True,
        "score": 40,
        "threshold": 2,
        "window_minutes": 24 * 60,
        "description": "같은 기기에서 여러 계정 사용",
    },
    # 같은 IP 에서 threshold 개 이상의 계정이 활동
    "MULTI_ACCOUNT_IP": {
        "enabled": True,
        "score": 30,
        "threshold": 3,
        "window_minutes": 24 * 60,
        "description": "같은 IP에서 여러 계정 사용",
    },
    # 판매 오픈 후 seconds 초 이내 예약
    "FAST_AFTER_OPEN": {
        "enabled": True,
        "score": 40,
        "seconds": 3,
        "description": "판매 오픈 직후 지나치게 빠른 예약",
    },
    # window_seconds 동안 같은 계정 또는 IP 의 예약 요청이 threshold 회 이상
    "RAPID_REPEAT": {
        "enabled": True,
        "score": 30,
        "threshold": 3,
        "window_seconds": 60,
        "description": "짧은 시간 동안 반복되는 예약 요청",
    },
    # 같은 IP·기기에서 threshold 개 이상 계정의 가입 쿠폰이 예약에 쓰임
    "COUPON_FARMING": {
        "enabled": True,
        "score": 30,
        "threshold": 2,
        "window_minutes": 24 * 60,
        "description": "같은 IP·기기에서 여러 계정의 가입 쿠폰 사용",
    },
    # 사용할 수 없는 쿠폰(남의 쿠폰, 이미 쓴 쿠폰 등)으로 threshold 회 이상 시도
    "COUPON_RETRY": {
        "enabled": True,
        "score": 20,
        "threshold": 2,
        "window_minutes": 60,
        "description": "사용 불가 쿠폰으로 반복 예약 시도",
    },
}


# -------------------------
# 입력 / 결과
# -------------------------

@dataclass
class RuleContext:
    user: User
    flight: Flight
    coupon: Optional[Coupon]
    seat_count: int
    ip: Optional[str]
    device_id: Optional[str]
    now: datetime


@dataclass
class RuleResult:
    score: float = 0
    reasons: list[str] = field(default_factory=list)
    hits: list[str] = field(default_factory=list)
    features: dict = field(default_factory=dict)

    def add(self, code: str, detail: str):
        rule = RULES[code]
        self.score += rule["score"]
        self.hits.append(code)
        self.reasons.append(
            f"{code}(+{rule['score']}): {rule['description']} - {detail}"
        )


# -------------------------
# Feature 계산
# -------------------------

def accounts_seen(session, column, value, since, current_user_id) -> int:
    """column == value 로 활동한 서로 다른 계정 수 (현재 계정 포함)"""

    if not value:
        return 1

    rows = session.exec(
        select(EventLog.user_id).distinct().where(
            column == value,
            EventLog.timestamp >= since,
            EventLog.user_id.is_not(None)
        )
    ).all()

    return len(set(rows) | {current_user_id})


def recent_attempts(session, ctx, since) -> int:
    """최근 예약 요청 수 (같은 계정 또는 같은 IP, 현재 요청 포함)"""

    same_actor = [EventLog.user_id == ctx.user.id]
    if ctx.ip:
        same_actor.append(EventLog.ip == ctx.ip)

    count = session.exec(
        select(func.count()).select_from(EventLog).where(
            EventLog.action.in_([
                Action.RESERVATION_ATTEMPT, Action.RESERVATION_REJECTED
            ]),
            EventLog.timestamp >= since,
            or_(*same_actor)
        )
    ).one()

    return count + 1


def signup_coupon_accounts(session, ctx, since) -> int:
    """같은 IP·기기에서 가입 쿠폰으로 예약을 시도한 계정 수 (현재 요청 포함)"""

    same_source = []
    if ctx.ip:
        same_source.append(Reservation.request_ip == ctx.ip)
    if ctx.device_id:
        same_source.append(Reservation.device_id == ctx.device_id)
    if not same_source:
        return 0

    users = set(session.exec(
        select(Reservation.user_id).distinct()
        .join(Coupon, Reservation.coupon_id == Coupon.id)
        .where(
            Coupon.coupon_type == "signup",
            Reservation.created_at >= since,
            or_(*same_source)
        )
    ).all())

    if ctx.coupon and ctx.coupon.coupon_type == "signup":
        users.add(ctx.user.id)

    return len(users)


def coupon_failures(session, ctx, since) -> int:
    """최근 쿠폰 오류로 거절된 예약 요청 수 (같은 계정)"""

    logs = session.exec(
        select(EventLog).where(
            EventLog.action == Action.RESERVATION_REJECTED,
            EventLog.user_id == ctx.user.id,
            EventLog.timestamp >= since
        )
    ).all()

    return sum(
        1 for log in logs
        if any(reason.startswith("COUPON_") for reason in log.reasons or [])
    )


def collect_features(session: Session, ctx: RuleContext) -> dict:

    def since(rule, key="window_minutes"):
        unit = "minutes" if key == "window_minutes" else "seconds"
        return ctx.now - timedelta(**{unit: RULES[rule][key]})

    seconds_since_open = None
    if ctx.flight.sale_open_at:
        seconds_since_open = round(
            (ctx.now - ctx.flight.sale_open_at).total_seconds(), 2
        )

    return {
        "device_account_count": accounts_seen(
            session, EventLog.device_id, ctx.device_id,
            since("MULTI_ACCOUNT_DEVICE"), ctx.user.id
        ),
        "ip_account_count": accounts_seen(
            session, EventLog.ip, ctx.ip,
            since("MULTI_ACCOUNT_IP"), ctx.user.id
        ),
        "seconds_since_sale_open": seconds_since_open,
        "recent_attempt_count": recent_attempts(
            session, ctx, since("RAPID_REPEAT", "window_seconds")
        ),
        "signup_coupon_account_count": signup_coupon_accounts(
            session, ctx, since("COUPON_FARMING")
        ),
        "coupon_failure_count": coupon_failures(
            session, ctx, since("COUPON_RETRY")
        ),
        "account_age_minutes": round(
            (ctx.now - ctx.user.created_at).total_seconds() / 60, 1
        ),
        "seat_count": ctx.seat_count,
        "is_special_flight": ctx.flight.is_special,
        "uses_coupon": ctx.coupon is not None,
        "has_device_id": ctx.device_id is not None,
    }


# -------------------------
# 규칙 적용
# -------------------------

def evaluate(session: Session, ctx: RuleContext) -> RuleResult:

    f = collect_features(session, ctx)
    result = RuleResult(features=f)

    def on(code):
        return RULES[code]["enabled"]

    rule = RULES["MULTI_ACCOUNT_DEVICE"]
    if on("MULTI_ACCOUNT_DEVICE") and f["device_account_count"] >= rule["threshold"]:
        result.add(
            "MULTI_ACCOUNT_DEVICE",
            f"기기 {ctx.device_id} 에서 계정 {f['device_account_count']}개 "
            f"(기준 {rule['threshold']}개)"
        )

    rule = RULES["MULTI_ACCOUNT_IP"]
    if on("MULTI_ACCOUNT_IP") and f["ip_account_count"] >= rule["threshold"]:
        result.add(
            "MULTI_ACCOUNT_IP",
            f"IP {ctx.ip} 에서 계정 {f['ip_account_count']}개 "
            f"(기준 {rule['threshold']}개)"
        )

    rule = RULES["FAST_AFTER_OPEN"]
    elapsed = f["seconds_since_sale_open"]
    if on("FAST_AFTER_OPEN") and elapsed is not None \
            and 0 <= elapsed < rule["seconds"]:
        result.add(
            "FAST_AFTER_OPEN",
            f"오픈 {elapsed}초 후 예약 (기준 {rule['seconds']}초)"
        )

    rule = RULES["RAPID_REPEAT"]
    if on("RAPID_REPEAT") and f["recent_attempt_count"] >= rule["threshold"]:
        result.add(
            "RAPID_REPEAT",
            f"{rule['window_seconds']}초 동안 {f['recent_attempt_count']}회 요청 "
            f"(기준 {rule['threshold']}회)"
        )

    rule = RULES["COUPON_FARMING"]
    if on("COUPON_FARMING") \
            and f["signup_coupon_account_count"] >= rule["threshold"]:
        result.add(
            "COUPON_FARMING",
            f"가입 쿠폰 사용 계정 {f['signup_coupon_account_count']}개 "
            f"(기준 {rule['threshold']}개)"
        )

    rule = RULES["COUPON_RETRY"]
    if on("COUPON_RETRY") and f["coupon_failure_count"] >= rule["threshold"]:
        result.add(
            "COUPON_RETRY",
            f"쿠폰 오류 {f['coupon_failure_count']}회 "
            f"(기준 {rule['threshold']}회)"
        )

    return result
