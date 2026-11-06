# alarm-flight
# AI-Based Fraudulent Booking Detection System

## 1. 프로젝트 소개

가상의 저가항공 예약 플랫폼에서 발생하는 사용자 행동을 기록하고,
Rule 기반 탐지와 AI 모델을 이용하여 정상 사용자와 부정 예약 봇을 구분하는 프로젝트입니다.

주요 탐지 대상은 다음과 같습니다.

- 특가 항공권을 자동으로 선점하는 예약 봇
- 여러 계정을 이용한 신규 가입 쿠폰 반복 사용
- 요청 간격과 기기 정보를 변경하는 우회형 봇

실제 항공사나 여행 사이트에는 접근하지 않으며,
팀이 제작한 로컬 모의 예약 플랫폼과 합성 데이터만 사용합니다.

---

## 2. 전체 시스템 흐름

1. 사용자가 회원가입하고 가입 쿠폰을 받습니다.
2. 항공권을 검색하고 항공편을 선택합니다.
3. 쿠폰을 적용해 예약을 시도합니다.
4. FastAPI 서버가 사용자 행동을 Event Log로 DB에 저장합니다.
5. Rule Engine이 Rule Score와 탐지 이유를 계산합니다.
6. AI 모델이 AI Score를 계산합니다. *(모델 연결 전, 현재는 Rule Score만 사용)*
7. 두 점수를 결합하여 Risk Score를 계산합니다.
8. Risk Score에 따라 예약 허용, CAPTCHA 또는 예약 제한을 적용합니다.
9. 관리자 API(추후 관리자 화면)에서 로그와 탐지 결과를 확인합니다.

### 탐지 판정 흐름

```text
POST /reservations
 → 요청 검증 (회원·항공편·좌석·쿠폰)
 → Rule Engine : 규칙별 점수 합산 → Rule Score + 탐지 이유(reasons)
 → AI Score    : 모델 연결 지점 (현재 None)
 → Risk Score  : Rule·AI 가중 평균 (AI 없으면 Rule Score 그대로)
 → 판정
      0 ~ 39  CONFIRMED         예약 확정 (좌석 차감, 쿠폰 사용)
     40 ~ 69  CAPTCHA_REQUIRED  모의 CAPTCHA 통과 시 확정
     70 ~     BLOCKED           예약 제한
```

규칙·점수·판정 기준은 [docs/backend.md](docs/backend.md#4-탐지-규칙-backendrule_enginepy-의-rules) 참고.

---

## 3. 백엔드 실행

Python 3.12 기준입니다.

```bash
# 설치 (최초 1회)
pip install -r backend/requirements.txt

# 서버 실행 (프로젝트 루트에서)
uvicorn main:app --reload --app-dir backend

# 테스트
python -m pytest
```

- Swagger (API 테스트·시연): http://localhost:8000/docs
- 상세 API 목록, 탐지 규칙, Swagger 시연 순서: **[docs/backend.md](docs/backend.md)**

### 데이터베이스

- 현재 개발 DB는 **SQLite** (`backend/app.db`, 서버 첫 실행 시 자동 생성, Git에는 올리지 않음)
- PostgreSQL은 향후 전환 예정입니다. DB 연결은 `DATABASE_URL` 환경변수로 바꿀 수 있는 구조이며,
  전환 시 PostgreSQL 드라이버 설치와 실제 동작 확인이 필요합니다.
- 테이블 구조가 바뀌면 `backend/app.db`를 삭제하고 다시 실행합니다. (마이그레이션 도구 미사용)

### `/detect` API 안내

`POST /detect`는 초기 테스트용 랜덤 점수 API로, 기존 호환성을 위해 **deprecated 상태로 남겨둔** 것입니다.
실제 예약 탐지는 `POST /reservations`의 Rule Engine 흐름을 사용합니다.

---

## 4. 역할 분담과 현재 상태

### A: 프론트엔드·실험·결과 통합

- 회원가입·항공권 검색·예약 화면
- 쿠폰·CAPTCHA·차단 화면
- 관리자 대시보드
- 실험 조건 설계
- 결과 그래프 작성
- 보고서와 발표자료 통합

**현재 상태**: 시작 전 (`frontend/` 비어 있음).
사용할 API는 준비되어 있습니다. 예약 결과의 `decision` 값으로 화면을 분기하고,
대시보드는 `GET /stats`, `GET /logs/anomalies`, `GET /reservations`를 사용합니다.

### B: 데이터·Feature·AI 모델

- 정상 사용자 데이터 생성
- 빠른 정상 사용자 데이터 생성
- 단순 봇·우회형 봇 데이터 생성
- Event Log를 Feature로 변환
- Isolation Forest, Random Forest, XGBoost 학습
- 모델 저장 및 성능 분석
- 보고서 데이터·AI 부분 작성

**현재 상태**: 시작 전 (`ml/` 비어 있음).
예약 요청마다 Feature가 Event Log(`features`)에 저장됩니다.
모델은 `backend/risk.py`의 `get_ai_score(features)`에 연결하면 Risk Score에 반영됩니다.

### C: 백엔드·DB·Rule·방어정책

| 항목 | 상태 |
|---|---|
| FastAPI 서버 (회원·쿠폰·항공편·예약 API) | 완료 |
| 데이터베이스 | SQLite로 구현, PostgreSQL 전환 예정 |
| 사용자 행동 로그 저장 (Event Log) | 완료 |
| Rule Engine (규칙 6개) | 완료 |
| 모의 CAPTCHA | 완료 |
| Rule+AI Hybrid Risk Score | 구조 완료, AI 모델 연결 대기 |
| 관리자 API (로그·통계) | 완료 |
| 시연용 데이터·시나리오 API | 완료 |
| Rate Limit (요청 자체 차단) | 미구현 (짧은 시간 반복 요청은 `RAPID_REPEAT` 규칙으로 탐지) |
| AI 모델 API 연동 | B 모델 완성 후 |
| Docker 실행 환경 | TODO |
| 보고서 시스템 부분 작성 | 진행 예정 |

---

## 5. 프로젝트 폴더

```text
project-root/
├── frontend/       # A: 사용자 화면과 관리자 화면 (예정)
├── backend/        # C: FastAPI, DB, Rule Engine
│   ├── main.py
│   ├── database.py
│   ├── models.py
│   ├── events.py
│   ├── rule_engine.py
│   ├── risk.py
│   └── routers/
├── ml/             # B: 데이터 생성, Feature, AI 모델 (예정)
├── experiments/    # A·B: 반복 실험과 평가 코드 (예정)
├── data/           # 합성 데이터와 실험 결과 (예정)
├── docs/           # API·시연 가이드 (backend.md), 회의록, ERD, 계획서
├── tests/          # 백엔드 테스트 (pytest)
└── README.md
```

> TODO: Docker 실행 환경(`docker-compose.yml`)은 아직 없습니다.

---

## 6. Branch

현재는 `main` 브랜치 하나만 사용합니다.
작업이 늘어나면 `develop`(통합), `feature/*`(개인 작업) 브랜치를 도입할 예정입니다.
