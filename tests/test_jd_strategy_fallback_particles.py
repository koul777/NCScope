"""Provider-free fallback questions choose Korean particles by the final consonant."""

from __future__ import annotations

import re

import pytest

from app.main import SUPPORTED_INTERVIEW_METHODS
from app.services.jd_strategy import _build_ncs_code_template_fallback_question

_PARTICLE_PAIRS = {
    "을": ("을", "를"),
    "를": ("을", "를"),
    "과": ("과", "와"),
    "와": ("과", "와"),
    "이": ("이", "가"),
    "가": ("이", "가"),
}
_CHECKED = re.compile(r"([가-힣])(을|를) 쟁점으로|([가-힣])(과|와) 관련|([가-힣])(이|가) 중요했던")


def _particle_errors(text: str) -> list[str]:
    errors = []
    for match in _CHECKED.finditer(text):
        groups = [group for group in match.groups() if group]
        last, particle = groups[0], groups[1]
        consonant, vowel = _PARTICLE_PAIRS[particle]
        has_final = (ord(last) - ord("가")) % 28 != 0
        if particle != (consonant if has_final else vowel):
            errors.append(match.group(0))
    return errors


@pytest.mark.parametrize("method", sorted(SUPPORTED_INTERVIEW_METHODS))
@pytest.mark.parametrize("index", range(5))
@pytest.mark.parametrize("factor", ["문서 요구사항 파악", "자료 오류 점검", "고객 응대 태도"])
def test_fallback_question_particles_follow_final_consonant(method: str, index: int, factor: str) -> None:
    question = _build_ncs_code_template_fallback_question(
        unit={"ncsClCd": "0202020101_23v4", "compeUnitName": "문서작성", "ncsSubdCdnm": "사무행정"},
        comp_name="문서작성",
        ncs_code="0202020101_23v4",
        ksa_terms=[factor],
        evidence_terms=[factor],
        index=index,
        method_override=method,
    )
    texts = [str(question.get("question") or "")]
    texts.extend(str(item) for item in question.get("follow_ups") or [])

    for text in texts:
        assert _particle_errors(text) == [], text
        assert "순서을" not in text
        assert "범위을" not in text
