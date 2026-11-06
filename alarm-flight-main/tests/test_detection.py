from datetime import datetime, timedelta

import risk
import rule_engine
from conftest import make_flight, reserve, signup


def codes(result):
    return [reason.split("(")[0] for reason in result["reasons"]]


def solve(question):
    a, b = question.replace("= ?", "").split("+")
    return str(int(a) + int(b))


# -------------------------
# 시나리오 A / B / C
# -------------------------

def test_normal_user_confirmed(client):
    body = signup(client, "a@test.com", ip="1.1.1.1", device_id="dev-a")
    fid = make_flight(client)["id"]

    r = reserve(client, body["user"]["id"], fid, ip="1.1.1.1",
                device_id="dev-a", coupon_id=body["coupon"]["id"])
    result = r.json()

    assert result["decision"] == "CONFIRMED"
    assert result["risk_score"] == 0
    assert result["reasons"] == []
    assert client.get(f"/flights/{fid}").json()["remaining_seats"] == 9

    attempt = client.get("/logs", params={"action": "RESERVATION_ATTEMPT"}).json()[0]
    assert attempt["decision"] == "CONFIRMED"
    assert attempt["is_anomaly"] is False
    assert attempt["reservation_id"] == result["reservation"]["id"]
    assert attempt["features"]["device_account_count"] == 1


def test_shared_device_requires_captcha(client):
    signup(client, "a@test.com", ip="1.1.1.1", device_id="shared")
    b = signup(client, "b@test.com", ip="2.2.2.2", device_id="shared")
    fid = make_flight(client)["id"]

    r = reserve(client, b["user"]["id"], fid, ip="2.2.2.2",
                device_id="shared", coupon_id=b["coupon"]["id"])
    result = r.json()

    assert result["decision"] == "CAPTCHA_REQUIRED"
    assert codes(result) == ["MULTI_ACCOUNT_DEVICE"]
    assert "captcha_answer" not in result["reservation"]
    # CAPTCHA 통과 전에는 좌석·쿠폰 그대로
    assert client.get(f"/flights/{fid}").json()["remaining_seats"] == 10
    assert client.get(f"/coupons/{b['coupon']['id']}").json()["used_at"] is None

    rid = result["reservation"]["id"]
    wrong = client.post(f"/reservations/{rid}/captcha", json={"answer": "-1"})
    assert wrong.status_code == 400

    ok = client.post(
        f"/reservations/{rid}/captcha",
        json={"answer": solve(result["captcha_question"])}
    )
    assert ok.status_code == 200
    assert ok.json()["decision"] == "CONFIRMED"
    assert client.get(f"/flights/{fid}").json()["remaining_seats"] == 9
    assert client.get(f"/coupons/{b['coupon']['id']}").json()["used_at"]

    # 이미 확정된 예약에 다시 CAPTCHA 제출 불가
    again = client.post(f"/reservations/{rid}/captcha", json={"answer": "1"})
    assert again.status_code == 400

    actions = [log["action"] for log in client.get("/logs").json()]
    assert "CAPTCHA_FAILED" in actions and "CAPTCHA_PASSED" in actions


def test_bot_blocked(client):
    bots = [
        signup(client, f"bot{i}@test.com", ip="6.6.6.6", device_id="bot-dev")
        for i in range(3)
    ]
    fid = make_flight(client)["id"]
    bot = bots[-1]

    r = reserve(client, bot["user"]["id"], fid, ip="6.6.6.6",
                device_id="bot-dev", coupon_id=bot["coupon"]["id"])
    result = r.json()

    assert result["decision"] == "BLOCKED"
    assert set(codes(result)) >= {"MULTI_ACCOUNT_DEVICE", "MULTI_ACCOUNT_IP"}
    assert result["risk_score"] >= risk.BLOCK_THRESHOLD
    # 차단된 요청은 좌석·쿠폰을 쓰지 않음
    assert client.get(f"/flights/{fid}").json()["remaining_seats"] == 10
    assert client.get(f"/coupons/{bot['coupon']['id']}").json()["used_at"] is None

    anomalies = client.get("/logs/anomalies").json()
    assert anomalies[0]["decision"] == "BLOCKED"


# -------------------------
# 개별 규칙
# -------------------------

def test_fast_after_open(client):
    uid = signup(client, "a@test.com", ip="1.1.1.1")["user"]["id"]
    fid = make_flight(
        client, is_special=True,
        sale_open_at=(datetime.now() - timedelta(seconds=1)).isoformat()
    )["id"]

    result = reserve(client, uid, fid, ip="1.1.1.1").json()

    assert "FAST_AFTER_OPEN" in codes(result)
    assert result["decision"] == "CAPTCHA_REQUIRED"


def test_rapid_repeat(client):
    uid = signup(client, "a@test.com", ip="1.1.1.1")["user"]["id"]
    fid = make_flight(client)["id"]

    results = [reserve(client, uid, fid, ip="1.1.1.1").json() for _ in range(3)]

    assert "RAPID_REPEAT" not in codes(results[1])
    assert "RAPID_REPEAT" in codes(results[2])


def test_coupon_farming(client):
    a = signup(client, "a@test.com", ip="3.3.3.3", device_id="d1")
    b = signup(client, "b@test.com", ip="3.3.3.3", device_id="d2")
    fid = make_flight(client)["id"]

    first = reserve(client, a["user"]["id"], fid, ip="3.3.3.3",
                    device_id="d1", coupon_id=a["coupon"]["id"]).json()
    second = reserve(client, b["user"]["id"], fid, ip="3.3.3.3",
                     device_id="d2", coupon_id=b["coupon"]["id"]).json()

    assert "COUPON_FARMING" not in codes(first)
    assert "COUPON_FARMING" in codes(second)


def test_coupon_retry(client):
    a = signup(client, "a@test.com", ip="1.1.1.1")
    b = signup(client, "b@test.com", ip="2.2.2.2")
    uid = a["user"]["id"]
    fid = make_flight(client)["id"]

    for _ in range(2):
        r = reserve(client, uid, fid, ip="1.1.1.1", coupon_id=b["coupon"]["id"])
        assert r.status_code == 400

    rejected = client.get("/logs", params={"action": "RESERVATION_REJECTED"}).json()
    assert rejected[0]["reasons"] == ["COUPON_INVALID"]

    result = reserve(client, uid, fid, ip="1.1.1.1").json()
    assert "COUPON_RETRY" in codes(result)


def test_disabled_rule_does_not_fire(client, monkeypatch):
    monkeypatch.setitem(
        rule_engine.RULES, "MULTI_ACCOUNT_DEVICE",
        {**rule_engine.RULES["MULTI_ACCOUNT_DEVICE"], "enabled": False}
    )
    signup(client, "a@test.com", ip="1.1.1.1", device_id="shared")
    b = signup(client, "b@test.com", ip="2.2.2.2", device_id="shared")
    fid = make_flight(client)["id"]

    result = reserve(client, b["user"]["id"], fid, ip="2.2.2.2",
                     device_id="shared").json()

    assert result["decision"] == "CONFIRMED"


# -------------------------
# 예약 취소
# -------------------------

def test_cancel_restores_seats_and_coupon(client):
    body = signup(client, "a@test.com", ip="1.1.1.1")
    fid = make_flight(client)["id"]
    result = reserve(client, body["user"]["id"], fid, ip="1.1.1.1",
                     seat_count=2, coupon_id=body["coupon"]["id"]).json()
    rid = result["reservation"]["id"]

    r = client.post(f"/reservations/{rid}/cancel")
    assert r.json()["decision"] == "CANCELLED"
    assert client.get(f"/flights/{fid}").json()["remaining_seats"] == 10
    assert client.get(f"/coupons/{body['coupon']['id']}").json()["used_at"] is None
    assert client.post(f"/reservations/{rid}/cancel").status_code == 400


def test_list_reservations_by_status(client):
    a = signup(client, "a@test.com", ip="1.1.1.1", device_id="shared")
    b = signup(client, "b@test.com", ip="2.2.2.2", device_id="shared")
    fid = make_flight(client)["id"]
    reserve(client, a["user"]["id"], fid, ip="1.1.1.1")
    reserve(client, b["user"]["id"], fid, ip="2.2.2.2", device_id="shared")

    assert len(client.get("/reservations").json()) == 2
    pending = client.get("/reservations", params={"status": "CAPTCHA_REQUIRED"}).json()
    assert len(pending) == 1 and pending[0]["user_id"] == b["user"]["id"]


# -------------------------
# Risk Score / 판정
# -------------------------

def test_risk_uses_rule_only_without_ai():
    assert risk.calculate_risk(30, None) == 30
    assert risk.calculate_risk(150, None) == 100


def test_risk_combines_ai_when_weighted(monkeypatch):
    monkeypatch.setattr(risk, "RULE_WEIGHT", 0.6)
    monkeypatch.setattr(risk, "AI_WEIGHT", 0.4)

    # 0.6 * 50 + 0.4 * 90 = 66
    assert risk.calculate_risk(50, 0.9) == 66.0


def test_decide_thresholds():
    assert risk.decide(0) == "CONFIRMED"
    assert risk.decide(39.9) == "CONFIRMED"
    assert risk.decide(40) == "CAPTCHA_REQUIRED"
    assert risk.decide(70) == "BLOCKED"


def test_ai_score_hook_is_used(client, monkeypatch):
    import routers.reservations as reservations

    monkeypatch.setattr(reservations, "get_ai_score", lambda features: 0.95)
    monkeypatch.setattr(risk, "AI_WEIGHT", 1.0)

    uid = signup(client, "a@test.com", ip="1.1.1.1")["user"]["id"]
    fid = make_flight(client)["id"]
    result = reserve(client, uid, fid, ip="1.1.1.1").json()

    # rule 0, ai 95 -> (0 + 95) / 2
    assert result["ai_score"] == 0.95
    assert result["risk_score"] == 47.5
    assert result["decision"] == "CAPTCHA_REQUIRED"
