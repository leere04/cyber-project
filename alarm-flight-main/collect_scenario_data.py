"""격리된 합성 시나리오 수집. 프로젝트 루트에서 실행.
실제 예약 처리 함수를 호출하되 시간을 실험용으로 고정합니다.
HTTP·실제 사용자 행동·AI 모델 성능을 측정하지 않습니다.
"""
import argparse
import csv
import hashlib
import json
import os
import random
import sys
import tempfile
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

SCENARIOS = [
    ('independent', '서로 다른 IP·기기의 3명', 'normal'),
    ('family_ip', '가족 3명: 같은 IP, 다른 기기, 쿠폰 없음', 'normal'),
    ('shared_device', '공유 기기의 3명: 같은 IP·기기, 쿠폰 없음', 'normal'),
    ('allowed_automation', '허용된 자동화: 한 계정으로 쿠폰 없이 3회 예약', 'normal'),
    ('family_coupon', '가족 3명이 공유 기기에서 각자의 가입 쿠폰 사용', 'normal'),
    ('coupon_abuse', '한 사람이 3계정으로 가입 혜택 반복 수령 시도', 'mixed'),
]


def write_csv(path, rows):
    columns = list(dict.fromkeys(key for row in rows for key in row))
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for k, v in row.items()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeats', type=int, default=10)
    parser.add_argument('--seed', type=int, default=20261026)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 100:
        parser.error('--repeats는 1~100이어야 합니다.')
    root = Path(__file__).resolve().parent
    backend = root/'backend'
    if not (backend/'models.py').exists():
        raise SystemExit('이 파일은 backend 폴더 옆에 넣어 주세요.')
    output = root/'experiments'/('scenario_data_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    output.mkdir(parents=True, exist_ok=False)
    rng = random.Random(args.seed)
    rows, labels, feature_rows, group_summaries = [], [], [], []
    with tempfile.TemporaryDirectory(prefix='flight-dataset-') as temporary:
        # 백엔드 import 전에 운영 DB 연결 주소를 임시 주소로 바꿉니다.
        os.environ['DATABASE_URL'] = 'sqlite:///'+str(Path(temporary)/'unused.db')
        sys.path.insert(0, str(backend))
        from sqlmodel import SQLModel, Session, select
        from database import make_engine
        from models import User, Flight, Coupon, EventLog, Action, Reservation, ReservationRequest
        from routers import reservations as booking
        from rule_engine import RULES
        from risk import CAPTCHA_THRESHOLD, BLOCK_THRESHOLD

        class Clock:
            value = None
            @classmethod
            def now(cls, tz=None):
                return cls.value if tz is None else cls.value.replace(tzinfo=tz)

        for repeat in range(args.repeats):
            # 같은 반복 묶음은 학습/평가 시 함께 분리하도록 그룹 ID 제공.
            base = datetime(2026,10,26,12) + timedelta(days=repeat)
            gaps = [rng.choice([0.5,2,10,35,65,120]) for _ in range(2)]
            account_age = rng.choice([10,60,1440,43200])
            pair_features = {}
            for scenario, description, scenario_class in SCENARIOS:
                run_id = f'seed{args.seed}-repeat{repeat:03d}-{scenario}'
                split_group = f'seed{args.seed}-repeat{repeat:03d}'
                engine = make_engine('sqlite:///'+str(Path(temporary)/(run_id+'.db')))
                outcomes = Counter()
                try:
                    SQLModel.metadata.create_all(engine)
                    with Session(engine) as session:
                        for uid in (1,2,3):
                            session.add(User(id=uid,email=f'u{uid}@example.com',password_hash='test-only-no-login',name='실험회원',created_at=base-timedelta(minutes=account_age)))
                        flight = Flight(id=1,flight_no='TEST',departure_airport='GMP',arrival_airport='CJU',departure_time=base+timedelta(days=7),arrival_time=base+timedelta(days=7,hours=1),price=49000,total_seats=30,remaining_seats=30,is_special=False,sale_open_at=base-timedelta(days=1),created_at=base-timedelta(days=2))
                        session.add(flight); session.commit()
                        coupon_scenario = scenario in ('family_coupon','coupon_abuse')
                        shared_device = scenario in ('shared_device','family_coupon','coupon_abuse','allowed_automation')
                        for uid in (1,2,3):
                            session.add(Coupon(id=uid,code=f'C{uid}',user_id=uid,coupon_type='signup',discount_amount=10000,issued_at=base-timedelta(minutes=account_age),expires_at=base+timedelta(days=30)))
                        session.commit()
                        # 첫 요청부터 계정 3개가 관측되는 인위적 효과를 피하기 위해
                        # 계정별 첫 등장 직전에 과거 가입 이벤트를 추가합니다.
                        elapsed = 0.0
                        seen = set()
                        current_features = []
                        for sequence in (1,2,3):
                            if sequence>1: elapsed += gaps[sequence-2]
                            Clock.value = base+timedelta(seconds=elapsed)
                            uid = 1 if scenario=='allowed_automation' else sequence
                            ip = f'192.0.2.{uid}' if scenario=='independent' else '192.0.2.1'
                            device = 'shared' if shared_device else f'device-{uid}'
                            if uid not in seen:
                                session.add(EventLog(user_id=uid,action=Action.SIGNUP,ip=ip,device_id=device,timestamp=base-timedelta(minutes=account_age)))
                                session.commit(); seen.add(uid)
                            coupon_id = uid if coupon_scenario else None
                            before = session.get(Flight,1).remaining_seats
                            # 실제 처리 함수를 호출하고 저장 시각을 다음 요청 전에 실험 시계에 맞춥니다.
                            with patch.object(booking,'datetime',Clock):
                                result = booking.do_reservation(session,ReservationRequest(user_id=uid,flight_id=1,seat_count=1,coupon_id=coupon_id,device_id=device),ip,'isolated-scenario-generator')
                            reservation = session.get(Reservation,result.reservation.id)
                            event_log = session.exec(select(EventLog).where(EventLog.reservation_id==reservation.id,EventLog.action==Action.RESERVATION_ATTEMPT)).one()
                            reservation.created_at = Clock.value
                            event_log.timestamp = Clock.value
                            session.add(reservation);session.add(event_log);session.commit()
                            session.refresh(flight)
                            features = dict(event_log.features)
                            if features.get('behavior_feature_version')!=1:
                                raise RuntimeError('새 행동 정보 버전 1이 없습니다. rule_engine.py 적용 상태를 확인하세요.')
                            seat_ok = before-flight.remaining_seats == (1 if result.decision=='CONFIRMED' else 0)
                            coupon_ok = True
                            if coupon_id:
                                coupon = session.get(Coupon,coupon_id);session.refresh(coupon)
                                coupon_ok = (coupon.used_at is not None)==(result.decision=='CONFIRMED')
                            # 정답은 규칙 결과와 무관한 실험 정책. 첫 혜택 요청은 정상,
                            # 같은 실소유자의 두 번째 계정부터 반복 혜택 시도로 설정.
                            label = 'abuse' if scenario=='coupon_abuse' and sequence>=2 else 'normal'
                            basis = '동일 실소유자의 두 번째 이상 계정으로 가입 혜택 요청' if label=='abuse' else '실험 정책상 허용된 요청(자동화 자체는 남용 아님)'
                            row_id = f'{run_id}-{sequence}'
                            identity=dict(row_id=row_id,run_id=run_id,split_group=split_group,sequence=sequence)
                            flat={'feature__'+k:v for k,v in features.items()}
                            feature_rows.append({**identity,**flat})
                            labels.append({**identity,'scenario':scenario,'label':label,'label_source':'synthetic_policy_v1','label_basis':basis})
                            rows.append({**identity,'scenario':scenario,'description':description,'label':label,'decision':result.decision,'rule_score':result.rule_score,'ai_score':result.ai_score,'risk_score':result.risk_score,'reasons':result.reasons,'seat_check':seat_ok,'coupon_check':coupon_ok,**flat})
                            outcomes[result.decision]+=1
                            current_features.append(features)
                        if coupon_scenario: pair_features[scenario]=current_features
                        group_summaries.append(dict(run_id=run_id,scenario=scenario,description=description,decisions=dict(outcomes)))
                finally: engine.dispose()
            # 의도가 다른 두 시나리오는 관측값이 같도록 구성: 모델의 한계 사례.
            if pair_features['family_coupon']!=pair_features['coupon_abuse']:
                raise RuntimeError('가족/남용 비교쌍의 관측 정보가 달라졌습니다. 생성 조건 확인이 필요합니다.')
            print(f'{repeat+1}/{args.repeats} 반복 완료 · 누적 {len(rows)}개 요청')
        source_files=['rule_engine.py','risk.py','models.py','routers/reservations.py']
        summary=dict(schema_version=1,scope='격리된 합성 시나리오에서 실제 예약 처리 함수를 호출한 수집 결과. HTTP 부하·실제 탐지 정확도·AI 성능 평가는 아님.',seed=args.seed,repeats=args.repeats,scenario_count=len(SCENARIOS),request_count=len(rows),label_counts=dict(Counter(r['label'] for r in labels)),decision_counts=dict(Counter(r['decision'] for r in rows)),resource_checks_passed=all(r['seat_check'] and r['coupon_check'] for r in rows),identical_observation_pairs=args.repeats,
                     scenarios=[dict(code=k,description=d) for k,d,_ in SCENARIOS],rules=RULES,thresholds=dict(captcha=CAPTCHA_THRESHOLD,block=BLOCK_THRESHOLD),source_hashes={name:hashlib.sha256((backend/name).read_bytes()).hexdigest() for name in source_files},groups=group_summaries,
                     notes=['실험 시간은 고정 시계로 진행하며 실제로 60초를 기다리지 않습니다.','회원과 쿠폰은 실험용으로 준비하고 가입 시점 이벤트를 재현합니다.','CAPTCHA 해결·예약 취소·동시성·만료 쿠폰은 이번 수집 범위에 포함하지 않습니다.','정답은 사전에 정한 시나리오 정책이며 실제 부정 이용 확인 결과가 아닙니다.','가족과 혜택 남용 비교쌍은 동일한 관측 정보에 다른 정답이 있습니다. 현재 특징만으로 완벽히 구별할 수 없습니다.','모델 학습 시 ID·시나리오·판정·점수·사유·검사 결과는 입력에서 제외하세요.','같은 split_group의 행은 학습과 평가에 나누어 넣지 마세요. 분할만으로 실제 서비스 일반화가 보장되지 않습니다.','남용 요청 비율은 실험 설계에 의해 정해지며 실제 서비스 발생률이 아닙니다.'])
    write_csv(output/'requests.csv',rows)
    write_csv(output/'features.csv',feature_rows)
    write_csv(output/'labels.csv',labels)
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print('\n수집 완료:',len(rows),'개 요청')
    print('좌석·쿠폰 검사:', 'PASS' if summary['resource_checks_passed'] else 'FAIL')
    print('저장 위치:',output)
    print('summary.json / requests.csv / features.csv / labels.csv')
    print('합성 실험 데이터이며 실제 탐지 정확도 결과가 아닙니다.')
    if not summary['resource_checks_passed']: raise SystemExit(1)


if __name__=='__main__':
    main()
