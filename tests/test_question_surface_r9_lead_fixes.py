"""Lead fixes after the R9 reviews: short skill actions and joined 수행 surfaces."""

from __future__ import annotations

import pytest

from app.services.question_surface import _repair_factor_object, public_task_object


@pytest.mark.parametrize(
    ("factor", "action", "invented"),
    (
        ("분석 능력", "분석", "검토"),
        ("작성 능력", "작성", "산출물"),
        ("산정 능력", "계산", "계산·검증"),
    ),
)
def test_short_skill_action_does_not_fall_through_to_invented_procedure(
    factor: str, action: str, invented: str
) -> None:
    # R7-A behaviour: the bare action is returned and public_task_object
    # rejects it as too short, using the element name instead.
    assert _repair_factor_object(factor, "기술") == action
    surface, source = public_task_object(
        factor_name=factor, ksa_type="기술", element_name="자료 분석하기"
    )
    assert source == "element_name"
    assert invented not in surface


@pytest.mark.parametrize(
    ("factor", "expected"),
    (
        ("보안제품 BMT/PoC 계획·수행 능력", "보안제품 BMT/PoC 계획·수행"),
        ("사용성 테스트 계획ㆍ수행 능력", "사용성 테스트 계획ㆍ수행"),
        ("역할수행 능력", "역할 수행"),
        ("면담 수행능력", "면담 수행"),
    ),
)
def test_retained_performance_noun_keeps_middle_dot_join(factor: str, expected: str) -> None:
    surface, source = public_task_object(factor_name=factor, ksa_type="기술")
    assert (surface, source) == (expected, "factor_repair")
    assert "· 수행" not in surface
    assert "ㆍ 수행" not in surface
