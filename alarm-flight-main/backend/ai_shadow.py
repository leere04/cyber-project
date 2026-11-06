"""로컬 기준 모델의 관찰용 추론. 실패해도 예약 규칙 판정은 계속합니다."""
import hashlib
import json
import math
from pathlib import Path
from functools import lru_cache

FEATURES = [
    'device_account_count', 'ip_account_count', 'seconds_since_sale_open',
    'recent_attempt_count', 'signup_coupon_account_count', 'coupon_failure_count',
    'account_age_minutes', 'seat_count', 'is_special_flight', 'uses_coupon',
    'has_device_id', 'seconds_since_previous_account_attempt',
    'prior_ip_account_count_60s', 'prior_ip_attempt_count_60s',
    'prior_ip_same_flight_attempt_count_60s', 'prior_ip_same_flight_ratio_60s',
]
MODEL_DIR = Path(__file__).resolve().parent / 'ml'


def vectorize(features):
    missing_keys = [key for key in FEATURES if key not in features]
    if missing_keys:
        raise ValueError('필수 특징 누락: ' + ', '.join(missing_keys))
    vector = []
    for key in FEATURES:
        raw = features[key]
        missing = raw is None or (isinstance(raw, str) and not raw.strip())
        if missing:
            value = 0.0
        elif isinstance(raw, str) and raw.strip().lower() in ('true', 'false'):
            value = float(raw.strip().lower() == 'true')
        else:
            value = float(raw)
        if not math.isfinite(value):
            raise ValueError('유한하지 않은 특징: ' + key)
        vector.extend([value, float(missing)])
    return vector


@lru_cache(maxsize=2)
def load_bundle(model_path, model_stamp, summary_path, summary_stamp):
    import joblib
    import sklearn
    from sklearn.tree import DecisionTreeClassifier
    metadata = json.loads(Path(summary_path).read_text(encoding='utf-8'))
    if metadata.get('experiment_type') != 'ml_baseline_v1':
        raise ValueError('모델 메타데이터 형식 불일치')
    if metadata.get('sklearn_version') != sklearn.__version__:
        raise ValueError('학습 환경과 scikit-learn 버전이 다릅니다')
    # 본 프로젝트에서 직접 학습한 로컬 파일만 여기에 배치합니다.
    bundle = joblib.load(model_path)
    names = [n for key in FEATURES for n in (key, key + '__missing')]
    if not isinstance(bundle, dict) or bundle.get('features') != FEATURES or bundle.get('feature_names') != names:
        raise ValueError('모델 특징 순서 불일치')
    model = bundle['model']
    if not isinstance(model, DecisionTreeClassifier) or model.n_features_in_ != len(names) or list(model.classes_) != [0, 1]:
        raise ValueError('모델 종류, 입력 수 또는 클래스 불일치')
    digest = hashlib.sha256(Path(model_path).read_bytes()).hexdigest()
    return model, digest


def predict_shadow(features):
    result = {'mode': 'shadow', 'status': 'unavailable', 'score': None,
              'prediction': None, 'model_sha256': None,
              'score_meaning': '가중 학습 모델의 미보정 출력값. 실제 부정 확률이 아닙니다.'}
    try:
        model_path, summary_path = MODEL_DIR / 'model.joblib', MODEL_DIR / 'summary.json'
        if not model_path.is_file() or not summary_path.is_file():
            result['reason'] = 'MODEL_FILES_MISSING'
            return result
        model, digest = load_bundle(str(model_path), model_path.stat().st_mtime_ns,
                                    str(summary_path), summary_path.stat().st_mtime_ns)
        vector = vectorize(features)
        score = float(model.predict_proba([vector])[0][1])
        prediction = int(model.predict([vector])[0])
        if not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError('잘못된 모델 출력')
        result.update(status='ok', score=score,
                      prediction='abuse' if prediction == 1 else 'normal',
                      model_sha256=digest)
    except Exception as exc:
        result.update(reason=type(exc).__name__, error=str(exc)[:300])
    return result


def get_ai_score(features):
    return predict_shadow(features)['score']
