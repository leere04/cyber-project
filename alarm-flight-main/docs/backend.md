# 백엔드 실행·시연 가이드

FastAPI + SQLModel, 개발 DB 는 SQLite(`backend/app.db`).

## 1. 실행

```bash
# 설치 (최초 1회)
pip install -r backend/requirements.txt

# 서버 실행 (프로젝트 루트에서)
uvicorn main:app --reload --app-dir backend

# 또는 backend 폴더에서
cd backend
uvicorn main:app --reload
```

- Swagger: http://localhost:8000/docs
- DB 파일은 실행 위치와 관계없이 항상 `backend/app.db` (Git 에 올라가지 않음)
- 테이블 구조가 바뀌면 `backend/app.db` 를 지우고 다시 실행 (SQLite 는 자동 마이그레이션 없음)

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `DATABASE_URL` | `sqlite:///backend/app.db` | PostgreSQL 로 바꿀 때 `postgresql+psycopg://user:pw@host:5432/db` (드라이버 `psycopg` 설치 필요) |
| `DEMO_MODE` | `1` | `0` 이면 `/demo` API 비활성화 |

### 테스트

```bash
python -m pytest        # 프로젝트 루트에서, 임시 DB 사용
```

## 2. 구조

```text
backend/
├── main.py            # 앱 생성, 라우터 등록
├── database.py        # DB 연결 (DATABASE_URL), 세션
├── models.py          # 테이블 + 요청/응답 모델
├── events.py          # 클라이언트 IP, EventLog 기록
├── rule_engine.py     # 탐지 규칙 (점수·기준값 설정)
├── risk.py            # Rule + AI → Risk Score → 판정, AI 연결 지점
└── routers/
    ├── users.py        # 회원가입, 회원 조회
    ├── coupons.py      # 쿠폰 발급·조회
    ├── flights.py      # 항공편 등록·검색
    ├── reservations.py # 예약, CAPTCHA, 취소
    ├── logs.py         # 로그, 통계, 규칙, 회원 차단 (관리자)
    └── demo.py         # 시연용 데이터·시나리오
```

## 3. 예약 탐지 흐름

```text
POST /reservations
 → 요청 검증 (회원·항공편·좌석·쿠폰)   실패 시 RESERVATION_REJECTED 로그 + 4xx
 → Rule Engine: rule_score, reasons, features
 → AI Score: risk.get_ai_score(features)   (현재 None)
 → Risk Score = Rule·AI 가중 평균 (AI 없으면 Rule Score)
 → 판정  0~39 CONFIRMED / 40~69 CAPTCHA_REQUIRED / 70~ BLOCKED
 → RESERVATION_ATTEMPT 로그 (점수, 판정, 이유, features)
```

| 판정 | 처리 |
|---|---|
| `CONFIRMED` | 좌석 차감, 쿠폰 사용 |
| `CAPTCHA_REQUIRED` | 덧셈 문제 반환. `POST /reservations/{id}/captcha` 정답 시 확정 |
| `BLOCKED` | 좌석·쿠폰 사용 안 함, 기록만 남김 |

## 4. 탐지 규칙 (`backend/rule_engine.py` 의 `RULES`)

| 규칙 | 점수 | 조건 |
|---|---|---|
| `MULTI_ACCOUNT_DEVICE` | +40 | 24시간 내 같은 기기에서 계정 2개 이상 |
| `MULTI_ACCOUNT_IP` | +30 | 24시간 내 같은 IP 에서 계정 3개 이상 |
| `FAST_AFTER_OPEN` | +40 | 판매 오픈 후 3초 이내 예약 |
| `RAPID_REPEAT` | +30 | 60초 내 같은 계정·IP 의 예약 요청 3회 이상 |
| `COUPON_FARMING` | +30 | 24시간 내 같은 IP·기기에서 2개 이상 계정이 가입 쿠폰으로 예약 |
| `COUPON_RETRY` | +20 | 1시간 내 쿠폰 오류(남의 쿠폰·사용한 쿠폰·만료)로 2회 이상 거절 |

판정 기준·가중치는 `backend/risk.py` 의 `CAPTCHA_THRESHOLD`, `BLOCK_THRESHOLD`, `RULE_WEIGHT`, `AI_WEIGHT`.
현재 설정은 `GET /rules` 로 확인 가능.

## 5. API

| Method | 경로 | 설명 |
|---|---|---|
| POST | `/users/signup` | 회원가입 + 가입 쿠폰(10,000원) 자동 발급 |
| GET | `/users/{id}` | 회원 조회 |
| GET | `/users/{id}/coupons` | 회원 쿠폰 |
| GET | `/users/{id}/reservations` | 회원 예약 |
| POST | `/coupons` | 쿠폰 발급 (이벤트 쿠폰) |
| GET | `/coupons/{id}` | 쿠폰 조회 |
| POST | `/flights` | 항공편 등록 |
| GET | `/flights` | 항공편 검색 (`departure_airport`, `arrival_airport`, `special_only`, `user_id`) |
| GET | `/flights/{id}` | 항공편 상세 (`user_id` 주면 조회 로그) |
| POST | `/reservations` | 예약 요청 → 탐지 → 판정 |
| POST | `/reservations/{id}/captcha` | 모의 CAPTCHA 답 제출 |
| POST | `/reservations/{id}/cancel` | 예약 취소 (좌석·쿠폰 복구) |
| GET | `/reservations` | 예약 목록 (`status` 필터) |
| GET | `/reservations/{id}` | 예약 조회 |
| GET | `/logs` | 전체 로그 (`action`, `limit`) |
| POST | `/logs` | 로그 직접 기록 (프론트 행동 로그) |
| GET | `/logs/anomalies` | 이상 탐지 로그 |
| GET | `/logs/user/{id}` | 회원별 로그 |
| GET | `/stats` | 대시보드 통계 |
| GET | `/rules` | 탐지 규칙·기준 |
| POST | `/users/{id}/block` | 회원 차단/해제 (`blocked=false` 로 해제) |
| POST | `/detect` | (deprecated) 초기 랜덤 탐지 |
| POST | `/demo/seed` | 시연용 항공편 생성 |
| POST | `/demo/scenarios/{normal\|suspicious\|bot}` | 시나리오 자동 실행 |
| POST | `/demo/reset?confirm=true` | 전체 데이터 삭제 |

**IP 지정**: Swagger 요청은 모두 `127.0.0.1` 로 들어오므로, 회원가입·예약 API 의
`X-Forwarded-For` 입력칸에 IP 를 넣어 서로 다른 사용자를 흉내 냄.

## 6. Swagger 시연 순서

### 빠른 시연 (버튼 한 번)

1. `POST /demo/reset` (`confirm=true`) → `POST /demo/seed`
2. `POST /demo/scenarios/normal` → `CONFIRMED`, risk 0
3. `POST /demo/scenarios/suspicious` → `CAPTCHA_REQUIRED`, risk 40
   - 응답의 `reservation_id`, `captcha_question` 으로 `POST /reservations/{id}/captcha` → `CONFIRMED`
4. `POST /demo/scenarios/bot` → 봇 4개 모두 `BLOCKED`, risk 100
   (같은 기기 + 같은 IP + 오픈 직후 + 반복 요청 + 쿠폰 파밍)
5. `GET /stats`, `GET /logs/anomalies` 로 결과 확인

### 직접 입력 시연

`POST /demo/reset` → `POST /demo/seed` 후 AF101 의 `id` 를 확인 (아래에서 `flight_id` 1 로 가정).

**A. 정상 사용자 → CONFIRMED**

1. `POST /users/signup`, X-Forwarded-For `211.10.10.10`
   ```json
   {"email": "normal@demo.com", "password": "pw1234", "name": "정상", "device_id": "iphone-01"}
   ```
   → 응답의 `user.id`, `coupon.id` 확인
2. `GET /flights?departure_airport=GMP&user_id={user.id}`
3. `POST /reservations`, X-Forwarded-For `211.10.10.10`
   ```json
   {"user_id": 1, "flight_id": 1, "coupon_id": 1, "device_id": "iphone-01"}
   ```
   → `CONFIRMED`, `risk_score` 0, 가격 할인 적용

**B. 의심 사용자 → CAPTCHA_REQUIRED**

1. `POST /users/signup`, X-Forwarded-For `175.20.20.1`, `device_id` `tablet-01`, 이메일 `family1@demo.com`
2. `POST /users/signup`, X-Forwarded-For `175.20.20.2`, `device_id` `tablet-01`, 이메일 `family2@demo.com`
3. 두 번째 계정으로 `POST /reservations`, X-Forwarded-For `175.20.20.2`
   ```json
   {"user_id": 3, "flight_id": 1, "device_id": "tablet-01"}
   ```
   → `CAPTCHA_REQUIRED`, risk 40, 이유 `MULTI_ACCOUNT_DEVICE`
4. `POST /reservations/{id}/captcha` 에 `{"answer": "정답"}` → `CONFIRMED`

**C. 봇·악용 사용자 → BLOCKED**

1. `POST /users/signup` 3번, 모두 X-Forwarded-For `45.66.66.66`, `device_id` `bot-pc-01`
   (이메일 `bot1@demo.com`, `bot2@demo.com`, `bot3@demo.com`)
2. 세 번째 계정으로 가입 쿠폰을 써서 `POST /reservations`, X-Forwarded-For `45.66.66.66`
   ```json
   {"user_id": 6, "flight_id": 1, "coupon_id": 6, "device_id": "bot-pc-01"}
   ```
   → `BLOCKED`, risk 70, 이유 `MULTI_ACCOUNT_DEVICE` + `MULTI_ACCOUNT_IP`
3. `GET /stats` → `detection.by_decision` 이 1 / 1 / 1

## 7. 팀원 연결 지점

**B (AI 모델)**
- 학습 데이터: `GET /logs?action=RESERVATION_ATTEMPT` 의 `features`, `decision`
- 모델 연결: `backend/risk.py` 의 `get_ai_score(features)` 가 0~1 값을 돌려주게 구현
  → `AI_WEIGHT` 를 올리면 Risk Score 에 반영 (예: `RULE_WEIGHT = 0.6`, `AI_WEIGHT = 0.4`)
- features 항목: `device_account_count`, `ip_account_count`, `seconds_since_sale_open`,
  `recent_attempt_count`, `signup_coupon_account_count`, `coupon_failure_count`,
  `account_age_minutes`, `seat_count`, `is_special_flight`, `uses_coupon`, `has_device_id`

**A (프론트)**
- 예약 화면: `POST /reservations` 응답의 `decision` 으로 분기
  (`CONFIRMED` 완료 화면 / `CAPTCHA_REQUIRED` 는 `captcha_question` 표시 후 `/captcha` / `BLOCKED` 차단 화면)
- 관리자 대시보드: `GET /stats`, `GET /logs/anomalies`, `GET /reservations`, `GET /rules`
- 기기 식별: 브라우저별로 `device_id` 를 만들어(예: localStorage UUID) 회원가입·예약 요청에 포함
