import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

BASE_URL = "http://127.0.0.1:8000"
RUN_ID = uuid.uuid4().hex[:10]
DEVICE_ID = f"demo-device-{RUN_ID}"

RESULT_FILE = Path(__file__).resolve().parent / f"demo_result_{RUN_ID}.json"
results = []


def api(method, path, body=None):
    payload = (
        json.dumps(body).encode("utf-8")
        if body is not None else None
    )

    request = Request(
        BASE_URL + path,
        data=payload,
        method=method,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "AlarmFlight-Local-Demo"
        }
    )

    try:
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8")
        raise RuntimeError(
            f"HTTP {error.code}: {detail}"
        ) from error


def save_results():
    RESULT_FILE.write_text(
        json.dumps(
            {
                "run_id": RUN_ID,
                "scenario": "동일 기기 다중 계정 가입 쿠폰 예약",
                "results": results
            },
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )


def main():
    print("\n=== 동일 기기 · 다중 계정 쿠폰 예약 시연 ===")
    print("대상: 내 컴퓨터의 모의 예약 서버")
    print("계정 3개 생성, 계정당 예약 1회 요청")
    print("기존 기록은 삭제하지 않습니다.\n")

    flights = api("GET", "/flights")
    now = datetime.now()
    candidates = []

    for flight in flights:
        if not isinstance(flight, dict):
            continue

        if flight.get("id") is None:
            continue

        if flight.get("remaining_seats", 0) < 3:
            continue

        # 현재 데모에서는 일반 항공편을 사용합니다.
        if flight.get("is_special"):
            continue

        sale_open = flight.get("sale_open_at")

        if sale_open:
            opened_at = datetime.fromisoformat(sale_open)

            # 시간대가 있는 응답도 비교할 수 있도록 처리합니다.
            compare_now = (
                datetime.now(opened_at.tzinfo)
                if opened_at.tzinfo else now
            )

            # 오픈 직후 규칙과 이번 시나리오가 섞이지 않도록 합니다.
            if (compare_now - opened_at).total_seconds() < 10:
                continue

        candidates.append(flight)

    if not candidates:
        print("판매가 시작됐고 잔여 좌석이 3석 이상인 일반 항공편이 없습니다.")
        print("항공편 데이터를 확인한 뒤 실행해 주세요.")
        return

    flight = candidates[0]

    print(f"대상 항공편: {flight.get('flight_no')}")
    print(f"항공편 ID: {flight['id']}")
    print(f"공통 기기 ID: {DEVICE_ID}\n")

    for number in range(1, 4):
        print(f"--- 계정 {number}/3 ---")

        try:
            member = api("POST", "/users/signup", {
                "name": f"시연회원{number}",
                "email": f"demo-{RUN_ID}-{number}@example.com",
                "password": uuid.uuid4().hex,
                "device_id": DEVICE_ID
            })

            user_id = member["user"]["id"]
            coupon_id = member["coupon"]["id"]

            print(f"회원번호: {user_id}")
            print(f"가입 쿠폰: {coupon_id}")

            result = api("POST", "/reservations", {
                "user_id": user_id,
                "flight_id": flight["id"],
                "seat_count": 1,
                "coupon_id": coupon_id,
                "device_id": DEVICE_ID
            })

            results.append({
                "sequence": number,
                "user_id": user_id,
                "response": result
            })
            save_results()

            print(f"예약번호: {result['reservation']['id']}")
            print(f"판정: {result['decision']}")
            print(f"규칙 점수: {result['rule_score']}")
            print(f"위험 점수: {result['risk_score']}")

            reasons = result.get("reasons", [])

            if reasons:
                print("탐지 사유:")
                for reason in reasons:
                    print(f"  - {reason}")
            else:
                print("탐지 사유: 없음")

        except (RuntimeError, URLError, TimeoutError, ValueError, KeyError) as error:
            results.append({
                "sequence": number,
                "error": str(error)
            })
            save_results()

            print(f"요청 처리 확인 필요: {error}")
            print("중복 요청을 피하기 위해 시연을 중단합니다.")
            break

        print()
        time.sleep(1)

    print("\n=== 시연 종료 ===")
    print(f"결과 저장: {RESULT_FILE.name}")
    print("관리자 화면에서 위 예약번호와 탐지 사유를 확인하세요.")
    print("확정된 예약은 좌석과 쿠폰을 사용한 상태로 남습니다.")
    print("추가 확인 요청의 CAPTCHA는 자동으로 풀지 않습니다.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, URLError, TimeoutError, ValueError) as error:
        print(f"\n서버 또는 데이터 확인 필요: {error}")
        