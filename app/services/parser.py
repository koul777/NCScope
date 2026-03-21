"""
Parser - 텍스트 파싱 유틸리티
"""

from __future__ import annotations

import re
from typing import Any

# 요구사항 중요 키워드 마커
_KEY_MARKERS = [
    "필수", "필요", "요구", "자격", "우대", "경험", "능력", "역량",
    "학력", "자격증", "전공", "경력", "기술", "업무", "담당",
]


def split_sentences(text: str) -> list[str]:
    """텍스트를 문장 단위로 분리."""
    if not text or not text.strip():
        return []

    # 문장 구분자: . ! ? ; 와 줄바꿈
    parts = re.split(r"[.!?;\n]+", text)
    return [p.strip() for p in parts if p.strip()]


def extract_requirements(text: str, source: str) -> list[dict[str, Any]]:
    """텍스트에서 요구사항 목록 추출.

    Returns:
        List of dicts with: item (str), source (str), weight (float 0.2~1.0)
    """
    sentences = split_sentences(text)
    result: list[dict[str, Any]] = []

    for sent in sentences:
        item = sent[:180]  # 최대 180자
        weight = 0.2

        # 핵심 키워드 포함 시 가중치 상승
        for marker in _KEY_MARKERS:
            if marker in item:
                weight = min(weight + 0.3, 1.0)
                break

        # 긴 문장 (35자 이상) 추가 가중치
        if len(item) > 35:
            weight = min(weight + 0.2, 1.0)

        result.append({"item": item, "source": source, "weight": weight})

    # 가중치 내림차순 정렬
    result.sort(key=lambda x: x["weight"], reverse=True)

    return result[:30]
