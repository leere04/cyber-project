"""
Risk Score 계산과 최종 판정 · AI 관찰 모드

Risk Score (0~100) = Rule Score 와 AI Score 의 가중 평균
- AI 모델이 없으면 (get_ai_score 가 None) Rule Score 만 사용
- 현재 AI_WEIGHT=0: AI 출력은 기록용이며 예약 판정에는 반영하지 않음
"""

from typing import Optional

from models import Status
from ai_shadow import get_ai_score


# 판정 기준 (Risk Score)
CAPTCHA_THRESHOLD = 40  # 이상이면 CAPTCHA_REQUIRED
BLOCK_THRESHOLD = 70    # 이상이면 BLOCKED

# Rule / AI 가중치 (AI 연결 후 예: RULE_WEIGHT = 0.6, AI_WEIGHT = 0.4)
RULE_WEIGHT = 1.0
AI_WEIGHT = 0.0



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
