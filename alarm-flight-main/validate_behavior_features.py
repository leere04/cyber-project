"""프로젝트 루트에서 실행: python validate_behavior_features.py
임시 SQLite에 통제된 이벤트를 넣고 실제 행동 정보 계산 함수를 검증합니다.
HTTP 예약 실행/탐지 정확도/AI 성능 검증은 아닙니다.
"""
import csv
import hashlib
import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    backend = root / 'backend'
    if not (backend / 'models.py').exists():
        raise SystemExit('backend 폴더 옆에 이 파일을 넣어 주세요.')
    sys.path.insert(0, str(backend))
    from sqlmodel import SQLModel, Session, create_engine
    from sqlalchemy import event
    from models import User, Flight, EventLog, Action
    from rule_engine import RuleContext, collect_behavior_features

    now = datetime(2026, 10, 26, 12, 0, 0)
    ip = '192.0.2.1'
    attempt = Action.RESERVATION_ATTEMPT
    rejected = Action.RESERVATION_REJECTED

    # 기록: (현재보다 몇 초 전, 회원, 항공편, 행동, IP)
    # 음수는 미래 기록입니다. 각 사례는 별도 DB에서 실행합니다.
    cases = [
        ('first', '이전 요청 없음', [], ip, (None, 0, 0, 0, None)),
        ('repeat', '10초 전 같은 계정·항공편 요청', [(10, 1, 1, attempt, ip)], ip, (10, 1, 1, 1, 1)),
        ('multi_account', '같은 IP의 다른 계정과 다른 항공편', [(20, 1, 1, attempt, ip), (5, 2, 2, attempt, ip)], ip, (20, 2, 2, 1, 0.5)),
        ('expired', '61초 전 기록은 60초 집계에서 제외', [(61, 1, 1, attempt, ip)], ip, (61, 0, 0, 0, None)),
        ('boundary', '정확히 60초 전 포함, 현재·미래 제외', [(60, 1, 1, attempt, ip), (0, 2, 1, attempt, ip), (-1, 1, 1, attempt, ip)], ip, (60, 1, 1, 1, 1)),
        ('other_events', '상세 조회·검색·CAPTCHA 제외', [(8, 1, 1, Action.FLIGHT_VIEW, ip), (7, 1, 1, Action.FLIGHT_SEARCH, ip), (6, 1, 1, Action.CAPTCHA_PASSED, ip)], ip, (None, 0, 0, 0, None)),
        ('other_ip', '다른 IP는 IP 집계에서 제외', [(12, 1, 1, attempt, '192.0.2.2')], ip, (12, 0, 0, 0, None)),
        ('missing_ip', '현재 IP 없음은 0 대신 결측값', [(12, 1, 1, attempt, ip)], None, (12, None, None, None, None)),
        ('rejected', '회원·항공편 불명인 거절도 요청 수에 포함', [(15, 1, 1, attempt, ip), (5, None, None, rejected, ip)], ip, (15, 1, 2, 1, 0.5)),
        ('distinct_users', '같은 계정 2회는 계정 수 1개', [(20, 1, 1, attempt, ip), (5, 1, 1, attempt, ip)], ip, (5, 1, 2, 2, 1)),
    ]
    keys = ['seconds_since_previous_account_attempt', 'prior_ip_account_count_60s',
            'prior_ip_attempt_count_60s', 'prior_ip_same_flight_attempt_count_60s',
            'prior_ip_same_flight_ratio_60s']
    results, checks = [], []
    with tempfile.TemporaryDirectory(prefix='flight-behavior-') as temporary:
        for code, description, records, context_ip, values in cases:
            engine = create_engine('sqlite:///' + str(Path(temporary) / (code + '.db')))
            @event.listens_for(engine, 'connect')
            def foreign_keys(connection, _):
                connection.execute('PRAGMA foreign_keys=ON')
            try:
                SQLModel.metadata.create_all(engine)
                with Session(engine) as session:
                    for user_id in (1, 2):
                        session.add(User(id=user_id, email=f'test{user_id}@example.com', name='실험', password_hash='not-a-login', created_at=now-timedelta(days=1)))
                    for flight_id in (1, 2):
                        session.add(Flight(id=flight_id, flight_no=f'TEST{flight_id}', departure_airport='GMP', arrival_airport='CJU', departure_time=now+timedelta(days=1), arrival_time=now+timedelta(days=1,hours=1), price=49000, total_seats=20, remaining_seats=20, created_at=now))
                    session.commit()
                    for age, user_id, flight_id, action, record_ip in records:
                        session.add(EventLog(timestamp=now-timedelta(seconds=age), user_id=user_id, flight_id=flight_id, action=action, ip=record_ip))
                    session.commit()
                    ctx = RuleContext(user=session.get(User, 1), flight=session.get(Flight, 1), coupon=None, seat_count=1, ip=context_ip, device_id='test-device', now=now)
                    actual = collect_behavior_features(session, ctx)
                    expected = dict(zip(keys, values))
                    expected.update(behavior_feature_version=1, behavior_window_seconds=60)
                    passed = all(key in actual and actual[key] == value for key, value in expected.items())
                    results.append(dict(scenario=code, description=description, passed=passed, expected=expected, actual=actual))
                    for key, value in expected.items():
                        checks.append(dict(scenario=code, description=description, feature=key, expected=json.dumps(value), actual=json.dumps(actual.get(key)), passed=key in actual and actual[key] == value))
                    print(f"{'PASS' if passed else 'FAIL'} | {description}")
            finally:
                engine.dispose()
    output = root / 'experiments' / ('behavior_validation_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    output.mkdir(parents=True, exist_ok=False)
    passed_count = sum(row['passed'] for row in results)
    report = dict(scope='통제된 합성 이벤트를 통한 행동 정보 계산 함수 검증. HTTP 예약 흐름·탐지 정확도·AI 성능 평가는 아님.',
                  rule_engine_sha256=hashlib.sha256((backend/'rule_engine.py').read_bytes()).hexdigest(),
                  scenario_count=len(results), passed_count=passed_count,
                  all_checks_passed=passed_count == len(results), results=results)
    (output/'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    with (output/'checks.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(checks[0]))
        writer.writeheader()
        writer.writerows(checks)
    print(f'\n전체 검사: {passed_count}/{len(results)} 사례 통과')
    print('결과 저장:', output)
    print('이 결과는 행동 정보 계산 검사이며 탐지 정확도가 아닙니다.')
    if not report['all_checks_passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
