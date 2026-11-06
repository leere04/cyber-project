from datetime import datetime, timedelta
from typing import Optional
import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from database import get_session
from events import client_ip, write_log
from models import Action, Coupon, CouponIssueRequest, User


router = APIRouter(tags=["쿠폰"])


def issue_coupon(
    session: Session,
    user_id: int,
    coupon_type: str,
    discount_amount: int,
    valid_days: int,
    ip: Optional[str] = None,
    device_id: Optional[str] = None
) -> Coupon:
    """쿠폰 1장 발급 + 로그. commit 은 호출한 쪽에서."""

    coupon = Coupon(
        code=secrets.token_hex(6).upper(),
        user_id=user_id,
        coupon_type=coupon_type,
        discount_amount=discount_amount,
        expires_at=datetime.now() + timedelta(days=valid_days)
    )
    session.add(coupon)

    write_log(
        session, Action.COUPON_ISSUED,
        user_id=user_id, ip=ip, device_id=device_id,
        message=f"{coupon_type} 쿠폰 {discount_amount}원"
    )

    return coupon


# 쿠폰 발급 (이벤트 쿠폰 등, 가입 쿠폰은 회원가입 시 자동 발급)
# 쿠폰 사용은 POST /reservations 의 coupon_id 로 처리
@router.post("/coupons", response_model=Coupon)
def create_coupon(
    data: CouponIssueRequest,
    ip: Optional[str] = Depends(client_ip),
    session: Session = Depends(get_session)
):

    if not session.get(User, data.user_id):
        raise HTTPException(404, "회원을 찾을 수 없습니다")
    if data.discount_amount <= 0 or data.valid_days <= 0:
        raise HTTPException(400, "할인 금액과 유효 기간은 0보다 커야 합니다")

    coupon = issue_coupon(
        session, data.user_id, data.coupon_type,
        data.discount_amount, data.valid_days, ip=ip
    )
    session.commit()
    session.refresh(coupon)

    return coupon


# 쿠폰 조회
@router.get("/coupons/{coupon_id}", response_model=Coupon)
def get_coupon(coupon_id: int, session: Session = Depends(get_session)):

    coupon = session.get(Coupon, coupon_id)

    if not coupon:
        raise HTTPException(404, "쿠폰을 찾을 수 없습니다")

    return coupon
