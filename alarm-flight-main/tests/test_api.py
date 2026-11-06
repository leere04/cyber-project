from datetime import datetime, timedelta

from conftest import make_flight, reserve, signup


# -------------------------
# 회원 · 쿠폰
# -------------------------

def test_root(client):
    assert client.get("/").status_code == 200


def test_signup_issues_coupon_and_hides_password(client):
    body = signup(client, "a@test.com", ip="1.1.1.1", device_id="dev-a")

    assert "password_hash" not in body["user"]
    assert body["coupon"]["coupon_type"] == "signup"
    assert body["coupon"]["discount_amount"] == 10000


def test_duplicate_email_rejected(client):
    signup(client, "a@test.com")
    r = client.post(
        "/users/signup",
        json={"email": "a@test.com", "password": "x", "name": "x"}
    )
    assert r.status_code == 400


def test_get_user_and_404(client):
    uid = signup(client, "a@test.com")["user"]["id"]
    assert client.get(f"/users/{uid}").json()["email"] == "a@test.com"
    assert client.get("/users/999").status_code == 404


def test_issue_event_coupon(client):
    uid = signup(client, "a@test.com")["user"]["id"]

    r = client.post("/coupons", json={"user_id": uid, "discount_amount": 3000})
    assert r.status_code == 200
    assert r.json()["coupon_type"] == "event"

    assert len(client.get(f"/users/{uid}/coupons").json()) == 2
    assert client.post("/coupons", json={"user_id": 999}).status_code == 404


# -------------------------
# 항공편
# -------------------------

def test_flight_create_search_detail(client):
    f = make_flight(client, is_special=True)
    make_flight(client, flight_no="AF201", arrival_airport="PUS")

    assert f["remaining_seats"] == 10
    assert len(client.get("/flights").json()) == 2
    assert len(client.get("/flights", params={"arrival_airport": "CJU"}).json()) == 1
    assert len(client.get("/flights", params={"special_only": True}).json()) == 1
    assert client.get(f"/flights/{f['id']}").status_code == 200
    assert client.get("/flights/999").status_code == 404


def test_invalid_flight_rejected(client):
    r = client.post("/flights", json={
        "flight_no": "X", "departure_airport": "A", "arrival_airport": "B",
        "departure_time": datetime.now().isoformat(),
        "arrival_time": datetime.now().isoformat(),
        "price": 1000, "total_seats": 0
    })
    assert r.status_code == 400


# -------------------------
# 예약 기본 동작
# -------------------------

def test_reservation_with_coupon(client):
    body = signup(client, "a@test.com", ip="1.1.1.1", device_id="dev-a")
    uid, cid = body["user"]["id"], body["coupon"]["id"]
    fid = make_flight(client)["id"]

    r = reserve(client, uid, fid, ip="1.1.1.1", seat_count=2,
                coupon_id=cid, device_id="dev-a")
    assert r.status_code == 200, r.text

    assert client.get(f"/flights/{fid}").json()["remaining_seats"] == 8
    coupon = client.get(f"/users/{uid}/coupons").json()[0]
    assert coupon["used_at"] is not None
    assert len(client.get(f"/users/{uid}/reservations").json()) == 1


def test_reservation_validation_errors(client):
    a = signup(client, "a@test.com", ip="1.1.1.1")
    b = signup(client, "b@test.com", ip="2.2.2.2")
    uid, cid = a["user"]["id"], a["coupon"]["id"]
    fid = make_flight(client, total_seats=1)["id"]
    closed = make_flight(
        client, flight_no="AF999",
        sale_open_at=(datetime.now() + timedelta(hours=1)).isoformat()
    )["id"]

    assert reserve(client, 999, fid).status_code == 404
    assert reserve(client, uid, 999).status_code == 404
    assert reserve(client, uid, closed).status_code == 400
    assert reserve(client, uid, fid, seat_count=0).status_code == 400
    assert reserve(client, uid, fid, seat_count=2).status_code == 400
    assert reserve(client, uid, fid, coupon_id=b["coupon"]["id"]).status_code == 400
    assert reserve(client, uid, fid, coupon_id=cid).status_code == 200
    # 좌석 소진
    assert reserve(client, uid, fid).status_code == 400


def test_get_reservation(client):
    uid = signup(client, "a@test.com")["user"]["id"]
    fid = make_flight(client)["id"]
    rid = reserve(client, uid, fid).json()["reservation"]["id"]

    assert client.get(f"/reservations/{rid}").status_code == 200
    assert client.get("/reservations/999").status_code == 404


# -------------------------
# 이벤트 로그
# -------------------------

def test_signup_and_search_are_logged(client):
    uid = signup(client, "a@test.com", ip="9.9.9.9", device_id="dev-a")["user"]["id"]
    fid = make_flight(client)["id"]
    client.get("/flights", params={"user_id": uid})
    client.get(f"/flights/{fid}", params={"user_id": uid})

    logs = client.get(f"/logs/user/{uid}").json()
    actions = [log["action"] for log in logs]

    assert "SIGNUP" in actions
    assert "COUPON_ISSUED" in actions
    assert "FLIGHT_SEARCH" in actions
    assert "FLIGHT_VIEW" in actions

    signup_log = next(log for log in logs if log["action"] == "SIGNUP")
    assert signup_log["ip"] == "9.9.9.9"
    assert signup_log["device_id"] == "dev-a"


def test_manual_log_and_filters(client):
    uid = signup(client, "a@test.com")["user"]["id"]

    r = client.post("/logs", json={"action": "PAGE_VIEW", "user_id": uid})
    assert r.status_code == 200
    assert client.post("/logs", json={"action": "X", "user_id": 999}).status_code == 404

    only = client.get("/logs", params={"action": "PAGE_VIEW"}).json()
    assert len(only) == 1 and only[0]["action"] == "PAGE_VIEW"


def test_stats_keys(client):
    stats = client.get("/stats").json()
    for key in ("total_logs", "anomaly_logs", "anomaly_rate"):
        assert key in stats
