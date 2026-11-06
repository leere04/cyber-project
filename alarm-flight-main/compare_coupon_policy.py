"""프로젝트 루트에서 실행. 임시 DB의 동일 이력으로 쿠폰 규칙 변경 전후 비교.
실제 예약 API/좌석 차감/쿠폰 유효성/AI 정확도 검사는 아닙니다.
"""
import csv
import hashlib
import json
import sys
import tempfile
import types
from datetime import datetime, timedelta
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    backend = root / 'backend'
    if not (backend / 'rule_engine.py').exists():
        raise SystemExit('backend 폴더 옆에 compare_coupon_policy.py를 넣어 주세요.')
    sys.path.insert(0, str(backend))
    from sqlmodel import SQLModel, Session, create_engine
    from sqlalchemy import event
    from models import User, Flight, Coupon, Reservation, EventLog, Action
    import rule_engine as updated
    from risk import calculate_risk, decide, CAPTCHA_THRESHOLD, BLOCK_THRESHOLD

    # 설치된 실제 소스에서 이번에 추가한 조건 두 줄만 제거한 메모리 복사본.
    # 디스크의 소스/운영 DB는 수정하지 않습니다.
    source = (backend/'rule_engine.py').read_text(encoding='utf-8')
    guard = '            and ctx.coupon is not None \\\n            and ctx.coupon.coupon_type == "signup" \\\n'
    if source.count(guard) != 1:
        raise SystemExit('이번에 제공한 rule_engine.py로 교체한 후 다시 실행해 주세요. 비교 조건을 찾지 못했습니다.')
    legacy = types.ModuleType('coupon_policy_before')
    sys.modules[legacy.__name__] = legacy
    exec(compile(source.replace(guard, '', 1), '<coupon_policy_before>', 'exec'), legacy.__dict__)

    now = datetime(2026, 10, 26, 12, 0)
    rows = []
    # 최소 조건에서의 변화와, 다른 위험 신호가 남는 경우를 모두 확인합니다.
    contexts = [('coupon_history_only', '쿠폰 이력만 기준 충족', False),
                ('combined', '쿠폰 이력 + 기기 3계정 + IP 3계정', True)]
    with tempfile.TemporaryDirectory(prefix='coupon-policy-') as temporary:
        for case, title, combined in contexts:
            engine = create_engine('sqlite:///' + str(Path(temporary)/(case+'.db')))
            @event.listens_for(engine, 'connect')
            def foreign_keys(connection, _):
                connection.execute('PRAGMA foreign_keys=ON')
            try:
                SQLModel.metadata.create_all(engine)
                with Session(engine) as session:
                    for uid in (1, 2, 3):
                        session.add(User(id=uid, email=f'{uid}@example.com', name='실험회원', password_hash='not-a-login', created_at=now-timedelta(days=2)))
                    session.add(Flight(id=1, flight_no='TEST', departure_airport='GMP', arrival_airport='CJU', departure_time=now+timedelta(days=1), arrival_time=now+timedelta(days=1,hours=1), price=49000, total_seats=20, remaining_seats=20, sale_open_at=now-timedelta(days=1), created_at=now))
                    session.commit()
                    for cid, uid, kind in [(1,1,'signup'), (2,2,'signup'), (3,3,'signup'), (4,3,'event')]:
                        session.add(Coupon(id=cid, code=f'TEST{cid}', user_id=uid, coupon_type=kind, discount_amount=10000, issued_at=now-timedelta(days=1), expires_at=now+timedelta(days=10), used_at=now-timedelta(minutes=5) if cid<3 else None))
                    session.commit()
                    for uid in (1,2):
                        session.add(Reservation(user_id=uid, flight_id=1, total_price=39000, coupon_id=uid, request_ip='192.0.2.1', device_id='shared', created_at=now-timedelta(minutes=5)))
                        # 이력만 사례에서는 다른 곳에서 가입 후 이 IP로 예약한 상태를 구성.
                        session.add(EventLog(user_id=uid, action=Action.SIGNUP, ip='192.0.2.1' if combined else f'192.0.2.{uid+10}', device_id='shared' if combined else f'other{uid}', timestamp=now-timedelta(minutes=10)))
                    session.commit()
                    for kind, coupon_id in [('none',None), ('signup',3), ('event',4)]:
                        ctx = updated.RuleContext(user=session.get(User,3), flight=session.get(Flight,1), coupon=session.get(Coupon,coupon_id) if coupon_id else None, seat_count=1, ip='192.0.2.1', device_id='shared', now=now)
                        before = legacy.evaluate(session,ctx)
                        after = updated.evaluate(session,ctx)
                        coupon_score = updated.RULES['COUPON_FARMING']['score'] if 'COUPON_FARMING' in before.hits else 0
                        expected_drop = 0 if kind=='signup' else coupon_score
                        other_same = [h for h in before.hits if h!='COUPON_FARMING']==[h for h in after.hits if h!='COUPON_FARMING']
                        passed = (before.score-after.score==expected_drop and other_same and before.features==after.features
                                  and ('COUPON_FARMING' in after.hits)==(kind=='signup' and 'COUPON_FARMING' in before.hits))
                        br, ar = calculate_risk(before.score,None), calculate_risk(after.score,None)
                        row = dict(scenario=case, description=title, coupon_type=kind, before_score=before.score, after_score=after.score, before_decision=decide(br), after_decision=decide(ar), before_hits=before.hits, after_hits=after.hits, features=after.features, passed=passed)
                        rows.append(row)
                        print(f"{'PASS' if passed else 'FAIL'} | {title} / {kind}: {before.score} → {after.score}점, {decide(br)} → {decide(ar)}")
            finally:
                engine.dispose()
    output=root/'experiments'/('coupon_comparison_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    output.mkdir(parents=True,exist_ok=False)
    all_pass=all(r['passed'] for r in rows)
    report=dict(scope='동일 합성 이력에 대한 쿠폰 규칙 조건 변경 전후 비교. HTTP/좌석/실제 탐지 성능 평가는 아님.',
                baseline_method='현재 소스의 가입 쿠폰 조건 두 줄만 제거한 메모리 복사본',
                rule_engine_sha256=hashlib.sha256(source.encode()).hexdigest(),
                thresholds=dict(captcha=CAPTCHA_THRESHOLD,block=BLOCK_THRESHOLD),
                rules=updated.RULES, all_checks_passed=all_pass, results=rows)
    (output/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    with (output/'results.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0])); writer.writeheader()
        for row in rows:
            writer.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else v for k,v in row.items()})
    print(f"\n전체 검사: {sum(r['passed'] for r in rows)}/{len(rows)} 사례 통과")
    print('결과 저장:',output)
    print('PASS는 변경 조건의 일관성 확인이며 정상 사용자 오탐률 개선을 입증하지 않습니다.')
    if not all_pass: raise SystemExit(1)


if __name__=='__main__':
    main()
