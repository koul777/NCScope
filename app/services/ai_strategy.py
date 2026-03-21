"""
AI Strategy - 채용공고 순위 및 전략 수립 (레거시 - 선택사항)
주: 현재 프로젝트에서는 question_generation.py가 주요 엔진입니다.
"""

from typing import Any
from app.settings import settings


def rank_postings_with_openai(
    desired_job: str,
    desired_region: str,
    strengths: str,
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """OpenAI로 채용공고 순위 매기기 (선택사항)"""
    if not candidates:
        return []
    return candidates[:5]


def build_strategy_with_openai(
    desired_job: str,
    strengths: str,
    posting_data: dict[str, Any],
) -> dict[str, Any]:
    """OpenAI로 채용 전략 수립 (선택사항)"""
    return {
        "job_title": desired_job,
        "match_score": 0.75,
        "strengths": [strengths[:30]],
        "gaps": ["경험 부족"],
        "recommendations": ["더 많은 경험 쌓기"],
    }
