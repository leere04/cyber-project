"""저장된 요청의 정책 판정 비교. 서버·DB·예약 상태는 변경하지 않습니다."""
import csv
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path


def proposed_decision(row):
    # 정답(label)이나 시나리오 이름은 판정에 사용하지 않습니다.
    reasons = json.loads(row['reasons'])
    hits = {reason.split('(', 1)[0] for reason in reasons}
    shared_signals = {'MULTI_ACCOUNT_DEVICE', 'MULTI_ACCOUNT_IP'}
    review_signals = shared_signals | {'COUPON_FARMING', 'RAPID_REPEAT'}
    if (row['decision'] == 'BLOCKED' and hits & shared_signals
            and hits <= review_signals):
        return 'REVIEW_REQUIRED'
    return row['decision']


def main():
    root = Path(__file__).resolve().parent
    if len(sys.argv) > 1:
        source = Path(sys.argv[1]).expanduser().resolve()
    else:
        files = sorted((root / 'experiments').glob('scenario_data_*/requests.csv'),
                       key=lambda p: p.parent.name)
        if not files:
            raise ValueError('experiments 안에 scenario_data_.../requests.csv가 없습니다.')
        source = files[-1]
    with source.open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    required = {'row_id', 'scenario', 'label', 'decision', 'reasons'}
    if not rows or not required <= rows[0].keys():
        raise ValueError('시나리오 수집기의 requests.csv 파일을 선택해주세요.')
    if len({r['row_id'] for r in rows}) != len(rows):
        raise ValueError('중복 row_id가 있습니다.')
    decisions = {'CONFIRMED', 'CAPTCHA_REQUIRED', 'BLOCKED'}
    for row in rows:
        if row['decision'] not in decisions or row['label'] not in {'normal', 'abuse'}:
            raise ValueError('알 수 없는 판정 또는 label이 있습니다.')
        reasons = json.loads(row['reasons'])
        if not isinstance(reasons, list) or not all(isinstance(x, str) for x in reasons):
            raise ValueError('reasons 형식이 잘못되었습니다.')
        row['proposed_decision'] = proposed_decision(row)
    results = []
    for label in ('normal', 'abuse'):
        subset = [r for r in rows if r['label'] == label]
        results.append({'label': label, 'count': len(subset),
                        'before': dict(Counter(r['decision'] for r in subset)),
                        'after': dict(Counter(r['proposed_decision'] for r in subset))})
    report = {
        'scope': '고정된 합성 요청 기록에 대한 오프라인 정책 비교. 실제 서비스 성능 평가 아님.',
        'source': str(source), 'request_count': len(rows),
        'policy': '기존 BLOCKED 중 공유 IP/기기 신호가 있고, 사유가 IP·기기·가입쿠폰·반복요청 규칙으로만 구성된 요청을 REVIEW_REQUIRED로 전환',
        'results': results,
        'scenarios': [
            {'scenario': name,
             'before': dict(Counter(r['decision'] for r in rows if r['scenario'] == name)),
             'after': dict(Counter(r['proposed_decision'] for r in rows if r['scenario'] == name))}
            for name in dict.fromkeys(r['scenario'] for r in rows)
        ],
        'notes': [
            'REVIEW_REQUIRED는 비교용 가상 판정입니다. 서버에 구현한 상태가 아닙니다.',
            '추가 확인의 성공·실패, 최종 예약 여부 및 좌석·쿠폰 처리는 평가하지 않았습니다.',
            '이전 요청의 판정이 달라졌을 때 이후 특징이 바뀌는 효과는 재현하지 않았습니다.',
            '추가 확인은 현재의 간단한 CAPTCHA와 다릅니다. 실제 사람별 혜택 자격 확인 절차는 미구현입니다.',
            '정상 차단 감소와 함께 남용 요청의 즉시 차단도 감소합니다. 우수한 정책임을 입증하는 실험이 아닙니다.',
            'label은 집계에만 사용하며 정책 판정에는 사용하지 않습니다.',
        ],
    }
    output = root / 'experiments' / ('review_policy_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    output.mkdir(parents=True)
    with (output / 'comparison.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (output / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'비교 완료: {len(rows)}건 / 서버·DB 변경 없음')
    for result in results:
        print('\n' + ('정상으로 설정한 요청' if result['label'] == 'normal' else '남용으로 설정한 요청'))
        print('  기존:', result['before'])
        print('  비교:', result['after'])
    print('\n주의: REVIEW_REQUIRED는 미구현 추가 확인 절차를 뜻하는 가상 판정입니다.')
    print('결과 폴더:', output)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError) as exc:
        print('실행 중단:', exc)
        sys.exit(1)
