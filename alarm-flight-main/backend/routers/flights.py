from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from database import get_session
from events import client_ip, write_log
from models import Action, Flight, FlightCreate, User


router = APIRouter(tags=["항공편"])


def log_if_user(session, action, user_id, ip, flight_id=None, message=None):
    """user_id 가 있을 때만 조회 행동을 로그로 남김"""

    if user_id is None or not session.get(User, user_id):
        return

    write_log(
        session, action,
        user_id=user_id, flight_id=flight_id, ip=ip, message=message
    )
    session.commit()


# 항공편 등록 (관리자·테스트용)
@router.post("/flights", response_model=Flight)
def create_flight(data: FlightCreate, session: Session = Depends(get_session)):

    if data.total_seats < 1 or data.price < 0:
        raise HTTPException(400, "좌석 수는 1 이상, 가격은 0 이상이어야 합니다")

    flight = Flight(
        **data.model_dump(),
        remaining_seats=data.total_seats
    )
    session.add(flight)
    session.commit()
    session.refresh(flight)

    return flight


# 항공편 검색 (user_id 를 주면 검색 행동을 로그로 남김)
@router.get("/flights", response_model=list[Flight])
def search_flights(
    departure_airport: Optional[str] = None,
    arrival_airport: Optional[str] = None,
    special_only: bool = False,
    user_id: Optional[int] = None,
    ip: Optional[str] = Depends(client_ip),
    session: Session = Depends(get_session)
):

    statement = select(Flight)

    if departure_airport:
        statement = statement.where(
            Flight.departure_airport == departure_airport
        )
    if arrival_airport:
        statement = statement.where(
            Flight.arrival_airport == arrival_airport
        )
    if special_only:
        statement = statement.where(Flight.is_special == True)

    flights = session.exec(statement.order_by(Flight.departure_time)).all()

    log_if_user(
        session, Action.FLIGHT_SEARCH, user_id, ip,
        message=f"{departure_airport or '*'}->{arrival_airport or '*'}"
    )

    # 로그 저장 후 항공편 데이터를 다시 불러옵니다.
    for flight in flights:
        session.refresh(flight)

    return flights


# 항공편 상세 조회 (user_id 를 주면 조회 행동을 로그로 남김)
@router.get("/flights/{flight_id}", response_model=Flight)
def get_flight(
    flight_id: int,
    user_id: Optional[int] = None,
    ip: Optional[str] = Depends(client_ip),
    session: Session = Depends(get_session)
):

    flight = session.get(Flight, flight_id)

    if not flight:
        raise HTTPException(404, "항공편을 찾을 수 없습니다")

    log_if_user(session, Action.FLIGHT_VIEW, user_id, ip, flight_id=flight_id)
    session.refresh(flight)

    return flight
