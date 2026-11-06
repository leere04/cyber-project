import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

BASE = "http://127.0.0.1:8000"


def api(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=10) as response:
        return json.load(response)


def main():
    print("좌석과 쿠폰 보호 검증을 시작합니다.")

    before = api("/flights/1")["remaining_seats"]

    result = api("/reservations", {
        "user_id": 7,
        "flight_id": 1,
        "seat_count": 1,
        "coupon_id": 7,
        "device_id": "demo-device-9dec9120b9",
    })

    after = api("/flights/1")["remaining_seats"]
    coupons = api("/users/7/coupons")
    coupon = next(c for c in coupons if c["id"] == 7)

    print("\n===== 검증 결과 =====")
    print("예약 판정:", result["decision"])
    print("요청 전 남은 좌석:", before)
    print("요청 후 남은 좌석:", after)
    print("쿠폰 사용 시각:", coupon["used_at"])

    if result["decision"] != "BLOCKED":
        print("차단 판정이 아니므로 차단 시 보호 여부는 검증하지 못했습니다.")
    elif before == after and coupon["used_at"] is None:
        print("검증 성공: 차단된 요청에서 좌석과 쿠폰이 소모되지 않았습니다.")
    else:
        print("확인 필요: 좌석 또는 쿠폰 상태가 예상과 다릅니다.")


if __name__ == "__main__":
    try:
        main()
    except HTTPError as error:
        print("서버 오류:", error.code)
        print(error.read().decode("utf-8", errors="replace"))
    except (URLError, TimeoutError, ValueError, KeyError, StopIteration) as error:
        print("확인 중 오류:", error)