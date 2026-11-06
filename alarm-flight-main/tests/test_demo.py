def test_seed_is_idempotent(client):
    first = client.post("/demo/seed").json()["flights"]
    second = client.post("/demo/seed").json()["flights"]

    assert len(first) == 3
    assert [f["id"] for f in first] == [f["id"] for f in second]


def test_scenario_normal(client):
    body = client.post("/demo/scenarios/normal").json()

    assert body["final_decision"] == "CONFIRMED"
    assert body["steps"][0]["reasons"] == []


def test_scenario_suspicious_then_captcha(client):
    body = client.post("/demo/scenarios/suspicious").json()
    s = body["steps"][0]

    assert body["final_decision"] == "CAPTCHA_REQUIRED"
    assert s["reasons"][0].startswith("MULTI_ACCOUNT_DEVICE")

    a, b = s["captcha_question"].replace("= ?", "").split("+")
    r = client.post(
        f"/reservations/{s['reservation_id']}/captcha",
        json={"answer": str(int(a) + int(b))}
    )
    assert r.json()["decision"] == "CONFIRMED"


def test_scenario_bot(client):
    body = client.post("/demo/scenarios/bot").json()

    assert body["final_decision"] == "BLOCKED"
    assert all(s["decision"] == "BLOCKED" for s in body["steps"])

    hits = {r.split("(")[0] for s in body["steps"] for r in s["reasons"]}
    assert {"MULTI_ACCOUNT_DEVICE", "MULTI_ACCOUNT_IP",
            "FAST_AFTER_OPEN"} <= hits


def test_scenarios_repeatable_and_stats(client):
    for name in ("normal", "suspicious", "bot", "normal", "suspicious", "bot"):
        assert client.post(f"/demo/scenarios/{name}").status_code == 200

    by_decision = client.get("/stats").json()["detection"]["by_decision"]
    assert by_decision == {"CONFIRMED": 2, "CAPTCHA_REQUIRED": 2, "BLOCKED": 8}


def test_unknown_scenario(client):
    assert client.post("/demo/scenarios/xxx").status_code == 422


def test_reset_requires_confirm(client):
    client.post("/demo/scenarios/normal")

    assert client.post("/demo/reset").status_code == 400
    assert client.post("/demo/reset", params={"confirm": True}).status_code == 200
    assert client.get("/stats").json()["total_users"] == 0
