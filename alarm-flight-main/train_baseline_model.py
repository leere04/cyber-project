"""합성 예약 데이터의 첫 ML 기준 모델. 실제 서버/DB에는 연결하지 않습니다."""
import csv
import hashlib
import json
import math
import platform
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

FEATURES = [
    'device_account_count', 'ip_account_count', 'seconds_since_sale_open',
    'recent_attempt_count', 'signup_coupon_account_count', 'coupon_failure_count',
    'account_age_minutes', 'seat_count', 'is_special_flight', 'uses_coupon',
    'has_device_id', 'seconds_since_previous_account_attempt',
    'prior_ip_account_count_60s', 'prior_ip_attempt_count_60s',
    'prior_ip_same_flight_attempt_count_60s', 'prior_ip_same_flight_ratio_60s',
]
SEED = 20261026


def feature_vector(row):
    values = []
    for name in FEATURES:
        raw = row['feature__' + name].strip()
        missing = not raw
        if missing:
            value = 0.0
        elif raw.lower() in ('true', 'false'):
            value = float(raw.lower() == 'true')
        else:
            value = float(raw)
        if not math.isfinite(value):
            raise ValueError('유한한 숫자가 아닌 특징값: ' + name)
        values.extend([value, float(missing)])
    return values


def metrics(y, pred):
    tn = sum(a == 0 and b == 0 for a, b in zip(y, pred))
    fp = sum(a == 0 and b == 1 for a, b in zip(y, pred))
    fn = sum(a == 1 and b == 0 for a, b in zip(y, pred))
    tp = sum(a == 1 and b == 1 for a, b in zip(y, pred))
    div = lambda a, b: a / b if b else None
    return {'tn': tn, 'fp': fp, 'fn': fn, 'tp': tp,
            'accuracy': div(tn + tp, len(y)), 'precision': div(tp, tp + fp),
            'recall': div(tp, tp + fn), 'false_positive_rate': div(fp, tn + fp),
            'f1': div(2 * tp, 2 * tp + fp + fn)}


def main():
    try:
        import joblib
        import sklearn
        from sklearn.model_selection import GroupShuffleSplit
        from sklearn.tree import DecisionTreeClassifier, export_text
    except ImportError:
        raise ValueError('먼저 python -m pip install scikit-learn 을 실행해 주세요.')

    root = Path(__file__).resolve().parent
    if len(sys.argv) > 1:
        source = Path(sys.argv[1]).expanduser().resolve()
    else:
        candidates = sorted((root / 'experiments').glob('scenario_data_*/requests.csv'),
                            key=lambda p: p.parent.name)
        if not candidates:
            raise ValueError('experiments/scenario_data_.../requests.csv가 없습니다.')
        source = candidates[-1]
    with source.open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    required = {'row_id', 'split_group', 'scenario', 'label', 'decision'} | {
        'feature__' + name for name in FEATURES}
    if not rows or not required <= rows[0].keys():
        raise ValueError('시나리오 수집기의 requests.csv 파일이 필요합니다.')
    if len({r['row_id'] for r in rows}) != len(rows):
        raise ValueError('row_id가 중복되었습니다.')
    if any(r['label'] not in ('normal', 'abuse') or not r['split_group']
           or r['decision'] not in ('CONFIRMED', 'CAPTCHA_REQUIRED', 'BLOCKED') for r in rows):
        raise ValueError('label, split_group 또는 decision 형식 오류입니다.')
    X = [feature_vector(r) for r in rows]
    y = [int(r['label'] == 'abuse') for r in rows]
    groups = [r['split_group'] for r in rows]
    if len(set(groups)) < 4:
        raise ValueError('독립된 split_group이 최소 4개 필요합니다.')
    splitter = GroupShuffleSplit(n_splits=1, test_size=0.3, random_state=SEED)
    train, test = next(splitter.split(X, y, groups))
    train_groups = sorted({groups[i] for i in train})
    test_groups = sorted({groups[i] for i in test})
    assert not set(train_groups) & set(test_groups)
    if {y[i] for i in train} != {0, 1} or {y[i] for i in test} != {0, 1}:
        raise ValueError('학습/평가에 정상과 남용 라벨이 모두 필요합니다. 데이터를 보충해 주세요.')

    # 결과를 본 뒤 파라미터나 분할을 반복 선택하지 않습니다.
    # 희소한 남용 라벨도 학습하도록 클래스별 가중치를 균형화합니다.
    params = dict(max_depth=3, min_samples_leaf=5, class_weight='balanced', random_state=SEED)
    model = DecisionTreeClassifier(**params)
    model.fit([X[i] for i in train], [y[i] for i in train])
    predicted = model.predict([X[i] for i in test]).tolist()
    truth = [y[i] for i in test]
    comparisons = {
        'all_normal': metrics(truth, [0] * len(test)),
        'rule_block_only': metrics(truth, [int(rows[i]['decision'] == 'BLOCKED') for i in test]),
        'rule_any_intervention': metrics(truth, [int(rows[i]['decision'] != 'CONFIRMED') for i in test]),
        'decision_tree': metrics(truth, predicted),
    }
    feature_names = [n for f in FEATURES for n in (f, f + '__missing')]
    signatures = {}
    for values, label in zip(X, y):
        signatures.setdefault(tuple(values), set()).add(label)
    conflicts = sum(len(labels) > 1 for labels in signatures.values())
    report = {
        'experiment_type': 'ml_baseline_v1',
        'scope': '합성 시나리오 내부의 그룹 분리 평가. 실제 이용자 탐지 성능 또는 독립된 현실 데이터 평가가 아님.',
        'source': str(source), 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'python_version': platform.python_version(), 'sklearn_version': sklearn.__version__,
        'model_parameters': params, 'features': FEATURES,
        'preprocessing': '특징별 빈 값은 0으로 채우고 별도 missing 지시값을 추가. 학습 통계 사용 없음.',
        'request_count': len(rows), 'train_count': len(train), 'test_count': len(test),
        'train_groups': train_groups, 'test_groups': test_groups, 'group_overlap': False,
        'train_labels': dict(Counter(rows[i]['label'] for i in train)),
        'test_labels': dict(Counter(rows[i]['label'] for i in test)),
        'conflicting_feature_patterns': conflicts, 'comparisons': comparisons,
        'metric_definition': 'positive=남용. 규칙 비교는 BLOCKED만 positive인 경우와 CAPTCHA_REQUIRED까지 포함한 경우를 따로 표시.',
        'notes': [
            'label은 사전 시나리오 가정입니다. 실제 부정 이용 확정 정보가 아닙니다.',
            '같은 반복의 6개 시나리오는 하나의 split_group으로 묶어 학습과 평가에 섞지 않았습니다.',
            '분리된 그룹도 동일한 생성 로직을 공유합니다. 새로운 공격 방식에 대한 일반화는 검증하지 않았습니다.',
            'ID·시나리오·판정·점수·사유·라벨은 모델 입력에서 제외했습니다.',
            '가족과 다계정 남용은 동일한 특징에 서로 다른 라벨이 있어 완벽하게 구별할 수 없습니다.',
            '정확도만으로 판단하지 말고 정상 오탐(fp), 남용 놓침(fn), 정밀도와 재현율을 함께 확인하세요.',
            'ML 결과는 정상/남용 예측입니다. 예약 승인·인증·차단의 3단계 운영 정책으로 연결하지 않았습니다.',
            '모델은 저장만 했으며 운영 서버의 ai_score는 여전히 연결 전입니다.',
            '평가 데이터를 보고 정책이나 파라미터를 변경하면 새로운 평가 데이터가 필요합니다.',
        ],
    }
    out = root / 'experiments' / ('ml_baseline_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    out.mkdir(parents=True)
    (out / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'tree.txt').write_text(export_text(model, feature_names=feature_names), encoding='utf-8')
    joblib.dump({'model': model, 'features': FEATURES, 'feature_names': feature_names,
                 'preprocessing': report['preprocessing']}, out / 'model.joblib')
    with (out / 'test_predictions.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['row_id', 'split_group', 'scenario', 'label', 'rule_decision', 'ml_prediction'])
        writer.writeheader()
        for i, p in zip(test, predicted):
            row = rows[i]
            writer.writerow({'row_id': row['row_id'], 'split_group': row['split_group'],
                             'scenario': row['scenario'], 'label': row['label'],
                             'rule_decision': row['decision'], 'ml_prediction': 'abuse' if p else 'normal'})
    print(f'학습 완료: 학습 {len(train)}건 / 평가 {len(test)}건')
    print(f'그룹 분리: 학습 {len(train_groups)}개 / 평가 {len(test_groups)}개 / 겹침 없음')
    print('평가 라벨:', report['test_labels'])
    for key, title in [('all_normal', '모두 정상으로 예측'), ('rule_block_only', '규칙: 차단만 남용으로 간주'),
                       ('rule_any_intervention', '규칙: 인증·차단을 남용으로 간주'), ('decision_tree', 'ML 의사결정나무')]:
        m = comparisons[key]
        pct = lambda v: '계산 불가' if v is None else f'{v * 100:.1f}%'
        print(f"\n{title}: 정상 오탐 {m['fp']}건 / 남용 놓침 {m['fn']}건")
        print(f"  정밀도 {pct(m['precision'])} / 재현율 {pct(m['recall'])} / 정확도 {pct(m['accuracy'])}")
    print('\n주의: 합성 데이터 내부 평가이며, 실제 서비스 성능이 아닙니다.')
    print('운영 서버는 변경하지 않았습니다. 이 summary.json은 기존 실험 탭의 지원 형식이 아닙니다.')
    print('결과 폴더:', out)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError) as exc:
        print('실행 중단:', exc)
        sys.exit(1)
