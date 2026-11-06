"""
Risk Score 계산과 최종 판정

Risk Score (0~100) = Rule Score 와 AI Score 의 가중 평균
- AI 모델이 없으면 (get_ai_score 가 None) Rule Score 만 사용
- AI 모델을 연결하면 get_ai_score 만 구현하고 AI_WEIGHT 를 올리면 됨
"""

from typing import Optional

from models import Status


# 판정 기준 (Risk Score)
CAPTCHA_THRESHOLD = 40  # 이상이면 CAPTCHA_REQUIRED
BLOCK_THRESHOLD = 70    # 이상이면 BLOCKED

# Rule / AI 가중치 (AI 연결 후 예: RULE_WEIGHT = 0.6, AI_WEIGHT = 0.4)
RULE_WEIGHT = 1.0
AI_WEIGHT = 0.0


def get_ai_score(features: dict) -> Optional[float]:
    """
    B 담당 연결 지점: 부정 예약 확률(0~1)을 돌려주면 됨.

    features 는 rule_engine.collect_features() 결과와 같은 dict.
    예)
        model = joblib.load("ml/model.pkl")
        return float(model.predict_proba([to_vector(features)])[0][1])

    모델이 아직 없으면 None.
    """
    return None


def calculate_risk(rule_score: float, ai_score: Optional[float]) -> float:

    rule = min(rule_score, 100)

    if ai_score is None or AI_WEIGHT == 0:
        return round(rule, 1)

    ai = ai_score * 100
    risk = (RULE_WEIGHT * rule + AI_WEIGHT * ai) / (RULE_WEIGHT + AI_WEIGHT)

    return round(min(risk, 100), 1)


def decide(risk_score: float) -> str:

    if risk_score >= BLOCK_THRESHOLD:
        return Status.BLOCKED
    if risk_score >= CAPTCHA_THRESHOLD:
        return Status.CAPTCHA_REQUIRED

    return Status.CONFIRMED
