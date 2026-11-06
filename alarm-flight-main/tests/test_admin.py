from conftest import make_flight, reserve, signup


def test_stats_counts_decisions_and_rules(client):
    normal = signup(client, "a@test.com", ip="1.1.1.1", device_id="dev-a")
    signup(client, "s1@test.com", ip="2.2.2.2", device_id="shared")
    shared = signup(client, "s2@test.com", ip="3.3.3.3", device_id="shared")
    bots = [
        signup(client, f"bot{i}@test.com", ip="6.6.6.6", device_id="bot")
        for i in range(3)
    ]
    fid = make_flight(client)["id"]

    reserve(client, normal["user"]["id"], fid, ip="1.1.1.1", device_id="dev-a")
    reserve(client, shared["user"]["id"], fid, ip="3.3.3.3", device_id="shared")
    reserve(client, bots[0]["user"]["id"], fid, ip="6.6.6.6", device_id="bot")
    reserve(client, 999, fid)  # 거절 (없는 회원)

    stats = client.get("/stats").json()

    # 기존 키 유지
    assert {"total_logs", "anomaly_logs", "anomaly_rate"} <= stats.keys()

    assert stats["total_users"] == 6
    assert stats["reservations"]["total"] == 3
    assert stats["reservations"]["by_status"] == {
        "CONFIRMED": 1, "CAPTCHA_REQUIRED": 1, "BLOCKED": 1, "CANCELLED": 0
    }

    detection = stats["detection"]
    assert detection["total_attempts"] == 3
    assert detection["anomaly_count"] == 2
    assert detection["anomaly_rate"] == 66.7
    assert detection["by_decision"] == {
        "CONFIRMED": 1, "CAPTCHA_REQUIRED": 1, "BLOCKED": 1
    }
    assert detection["rule_hits"]["MULTI_ACCOUNT_DEVICE"] == 2
    assert detection["rule_hits"]["MULTI_ACCOUNT_IP"] == 1
    assert detection["rejected_requests"] == 1
    assert stats["logs_by_action"]["SIGNUP"] == 6


def test_stats_empty_db(client):
    stats = client.get("/stats").json()

    assert stats["anomaly_rate"] == 0
    assert stats["detection"]["anomaly_rate"] == 0
    assert stats["reservations"]["total"] == 0


def test_rules_endpoint(client):
    body = client.get("/rules").json()

    assert "MULTI_ACCOUNT_DEVICE" in body["rules"]
    assert body["thresholds"] == {"captcha": 40, "block": 70}


def test_block_and_unblock_user(client):
    uid = signup(client, "a@test.com", ip="1.1.1.1")["user"]["id"]
    fid = make_flight(client)["id"]

    assert client.post(f"/users/{uid}/block").json()["is_blocked"] is True
    assert reserve(client, uid, fid, ip="1.1.1.1").status_code == 403
    assert client.get("/stats").json()["blocked_users"] == 1

    client.post(f"/users/{uid}/block", params={"blocked": False})
    assert reserve(client, uid, fid, ip="1.1.1.1").status_code == 200
    assert client.post("/users/999/block").status_code == 404
