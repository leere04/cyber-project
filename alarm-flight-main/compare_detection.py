"""Run from the project root: python compare_detection.py
Uses a temporary SQLite DB; never connects to backend/app.db.
"""
import csv
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if not (ROOT / 'backend/models.py').exists():
    raise SystemExit('이 파일을 backend 폴더 옆, alarm-flight-main 폴더에 넣어 주세요.')

with tempfile.TemporaryDirectory(prefix='alarm-flight-validation-') as temporary:
    os.environ['DATABASE_URL'] = 'sqlite:///' + str(Path(temporary) / 'unused.db')
    sys.path.insert(0, str(ROOT / 'backend'))
    from sqlmodel import SQLModel, Session
    from database import make_engine
    from models import Flight, SignupRequest, ReservationRequest
    from routers.users import do_signup
    from routers.reservations import do_reservation

    import rule_engine
    from sqlmodel import select, func
    from models import EventLog, Action
    original_recent_attempts = rule_engine.recent_attempts

    def account_attempts(session, ctx, since):
        count = session.exec(
            select(func.count()).select_from(EventLog).where(
                EventLog.action.in_([Action.RESERVATION_ATTEMPT, Action.RESERVATION_REJECTED]),
                EventLog.timestamp >= since,
                EventLog.user_id == ctx.user.id
            )
        ).one()
        return count + 1

    # 기대 판정은 현재 규칙 설정의 회귀 검증용이며, 실제 부정 행위 정답 라벨이 아닙니다.
    cases = [
        ('independent', '각자 다른 IP·기기 정상 이용', 3, ['CONFIRMED'] * 3),
        ('shared_ip', '가족 3명: 같은 IP, 다른 기기, 쿠폰 없음', 3, ['CONFIRMED', 'CONFIRMED', 'CAPTCHA_REQUIRED']),
        ('distributed', '분산 자동화 가정: 같은 IP, 다른 기기 ID의 3계정, 쿠폰 없음', 3, ['CONFIRMED', 'CONFIRMED', 'CAPTCHA_REQUIRED']),
        ('shared_device', '공유 기기 2명: 다른 IP, 쿠폰 없음', 2, ['CONFIRMED', 'CAPTCHA_REQUIRED']),
        ('coupon_accounts', '같은 IP·기기 2계정 가입 쿠폰 사용', 2, ['CONFIRMED', 'BLOCKED']),
        ('repeat', '한 계정의 빠른 예약 요청 3회, 쿠폰 없음', 3, ['CONFIRMED'] * 3),
    ]
    rows = []
    import itertools
    for variant, (key, title, count, expected) in itertools.product(['baseline', 'account_only'], cases):
        rule_engine.recent_attempts = original_recent_attempts if variant == 'baseline' else account_attempts
        if variant == 'account_only' and key in ('shared_ip', 'distributed'):
            expected = ['CONFIRMED'] * 3
        engine = make_engine('sqlite:///' + str(Path(temporary) / (variant + '_' + key + '.db')))
        SQLModel.metadata.create_all(engine)
        try:
            with Session(engine) as session:
                now = datetime.now()
                flight = Flight(
                    flight_no='TEST101', departure_airport='GMP', arrival_airport='CJU',
                    departure_time=now + timedelta(days=7),
                    arrival_time=now + timedelta(days=7, hours=1),
                    price=49000, total_seats=20, remaining_seats=20,
                    is_special=False, sale_open_at=now - timedelta(days=1)
                )
                session.add(flight)
                session.commit()
                session.refresh(flight)
                signup = None
                for index in range(count):
                    shared_ip = key in ('shared_ip', 'distributed', 'coupon_accounts', 'repeat')
                    shared_device = key in ('shared_device', 'coupon_accounts', 'repeat')
                    ip = '192.0.2.1' if shared_ip else f'192.0.2.{index + 1}'
                    device = 'shared-test-device' if shared_device else f'device-{index}'
                    if key != 'repeat' or signup is None:
                        signup = do_signup(session, SignupRequest(
                            email=f'{key}-{index}@example.com', name=f'test-{index}',
                            password='test-only-password', device_id=device
                        ), ip, 'isolated-validation')
                    use_coupon = key == 'coupon_accounts'
                    before = session.get(Flight, flight.id).remaining_seats
                    result = do_reservation(session, ReservationRequest(
                        user_id=signup.user.id, flight_id=flight.id, seat_count=1,
                        coupon_id=signup.coupon.id if use_coupon else None,
                        device_id=device
                    ), ip, 'isolated-validation')
                    session.refresh(flight)
                    decision = result.decision.value if hasattr(result.decision, 'value') else str(result.decision)
                    seat_ok = before - flight.remaining_seats == (1 if decision == 'CONFIRMED' else 0)
                    row = dict(variant=variant, scenario=key, description=title, sequence=index + 1,
                               expected=expected[index], actual=decision,
                               rule_score=result.rule_score, risk_score=result.risk_score,
                               reasons=result.reasons, seats_before=before,
                               seats_after=flight.remaining_seats,
                               decision_matches=decision == expected[index], seat_check=seat_ok)
                    rows.append(row)
                    print(f"[{variant}] {title} / {index + 1}회: {decision}, {result.risk_score}점 "
                          f"[기대 일치={row['decision_matches']}, 좌석 정상={seat_ok}]")
        finally:
            engine.dispose()

    rule_engine.recent_attempts = original_recent_attempts
    output = ROOT / 'experiments' / ('comparison_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    output.mkdir(parents=True)
    report = {
        'scope': '격리된 합성 시나리오에서 현재 규칙 동작 검증. 실제 탐지 정확도 또는 AI 성능 평가가 아님.',
        'results': rows,
        'all_checks_passed': all(r['decision_matches'] and r['seat_check'] for r in rows),
        'interpretation': [
            'baseline은 기존 계정 또는 IP 기준, account_only는 계정 기준 반복 요청 집계.',
            '가족 이용과 분산 자동화 가정은 서버에 전달하는 특징이 동일하다. 설명만으로 서버는 둘을 구분할 수 없다.',
            '공유 IP 정상 이용의 인증 부담 감소와 분산 자동화에 대한 대응 감소를 함께 비교한다.',
            '현재 40/70 기준값의 회귀 기대값이며 탐지 정확도 평가가 아니다.',
            '운영 코드·운영 DB는 변경하지 않았다. 계정별 집계는 이 실행 프로세스에만 적용된다.'
        ]
    }
    (output / 'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    with (output / 'results.csv').open('w', newline='', encoding='utf-8-sig') as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, 'reasons': ' | '.join(row['reasons'])})
    print('\n결과 저장:', output)
    print('전체 검사:', 'PASS' if report['all_checks_passed'] else '차이 발견 — results.json 확인')
    print('주의: 이 PASS는 탐지 정확도 100%라는 뜻이 아닙니다.')
    if not report['all_checks_passed']:
        sys.exit(1)
