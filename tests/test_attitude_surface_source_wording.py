"""Attitude surfaces keep the official KSA verb instead of inventing one.

Regression for R9-C.  ``원활한 커뮤니케이션을 하고자 하는 적극적인 태도`` was
shown as ``…커뮤니케이션을 실행하는 적극적인 행동`` (a verb the KSA never
names), and stripping the intention ending ``하려는``/``하는`` before
``태도``/``자세`` left broken stems such as ``정확도 기 행동``,
``고객과 협 관련 행동`` and ``담수심 깊게 행동``.  The public surface now keeps
the official clause and only replaces the attitude head noun with ``행동``.
"""

from __future__ import annotations

import pytest

from app.main import SUPPORTED_INTERVIEW_METHODS, _question_for_method, _question_task_frame
from app.services.question_surface import public_task_object

SUBJECT = "공적개발원조사업 총괄운영관리"
DETAIL = "공적개발원조"
DEFINITION = "공적개발원조사업을 총괄 운영한다."

# (official attitude factor, expected public surface, wording the old surface produced)
CASES = (
    (
        "원활한 커뮤니케이션을 하고자 하는 적극적인 태도",
        "원활한 커뮤니케이션을 하려는 적극적인 행동",
        "커뮤니케이션을 실행하는",
    ),
    (
        "세밀한 검토 및 적용으로 정확도를 기하려는 자세",
        "세밀한 검토 및 적용으로 정확도를 기하려는 행동",
        "정확도 기 행동",
    ),
    (
        "업무 변동 시 변동사항을 고객과 협의하려는 태도",
        "업무 변동 시 변동사항을 고객과 협의하려는 행동",
        "고객과 협 관련",
    ),
    (
        "도복이 우려되는 경우 담수심을 깊게 하려는 태도",
        "도복이 우려되는 경우 담수심을 깊게 하려는 행동",
        "담수심 깊게 행동",
    ),
    (
        "사고재발방지를 위한 대책과 조치를 강구하는 자세",
        "사고재발방지를 위한 대책과 조치를 강구하는 행동",
        "강구 관련 행동",
    ),
    (
        "고객이 강요당하는 느낌이 들지 않도록 하는 자세",
        "고객이 강요당하는 느낌이 들지 않도록 하는 행동",
        "않도록 관련 행동",
    ),
    (
        "대기 고객의 불편함을 줄이기 위해 배려하는 자세",
        "대기 고객의 불편함을 줄이기 위해 배려하는 행동",
        "배려 관련 행동",
    ),
    (
        "여러 분야의 요소들을 종합적으로 연계하려는 태도",
        "여러 분야의 요소들을 종합적으로 연계하려는 행동",
        "종합적으로 연계 행동",
    ),
    (
        "적립식 예금 세일즈를 적극적으로 완수하려는 자세",
        "적립식 예금 세일즈를 적극적으로 완수하려는 행동",
        "적극적으로 완수 행동",
    ),
    (
        "고객 · 소비자의 특성을 정확히 파악하려는 자세",
        "고객 · 소비자의 특성을 정확히 파악하려는 행동",
        "정확히 파악 행동",
    ),
    (
        "정확성을 유지하려는 태도",
        "정확성을 유지하려는 행동",
        "정확성 준수 행동",
    ),
    (
        "정확성을 우선하려는 태도",
        "정확성을 우선하려는 행동",
        "정확성 우선 행동",
    ),
    (
        "시료전처리 규정에 대하여 철저히 검토하려는 의지",
        "시료전처리 규정에 대하여 철저히 검토하려는 행동",
        "철저히 검토 행동",
    ),
)

# Wording that only exists because the surface rewrote the official clause.
INVENTED_OR_BROKEN = (
    "실행하는",
    " 관련 행동",
    "실행한 ",
)


@pytest.mark.parametrize(("factor", "expected", "old_wording"), CASES)
def test_attitude_surface_keeps_official_clause(factor: str, expected: str, old_wording: str) -> None:
    surface, source = public_task_object(factor_name=factor, ksa_type="태도")

    assert source == "factor_repair"
    assert surface == expected
    assert old_wording not in surface
    for wording in INVENTED_OR_BROKEN:
        assert wording not in surface
    assert surface.endswith("행동")
    assert "행동 기준" not in surface


@pytest.mark.parametrize("method", SUPPORTED_INTERVIEW_METHODS)
@pytest.mark.parametrize(("factor", "expected", "old_wording"), CASES)
def test_attitude_surface_reads_in_every_interview_method(
    method: str, factor: str, expected: str, old_wording: str
) -> None:
    frame = _question_task_frame(
        focus=factor,
        focus_type="태도",
        subject=SUBJECT,
        detail=DETAIL,
        comp_def=DEFINITION,
        evidence_row={"factorName": factor, "ksaTypeName": "태도", "ncsClCd": "0101010104_17v2"},
    )
    assert frame["task_object"] == expected
    for index in (0, 3, 14):
        question = _question_for_method(
            method=method,
            subject=SUBJECT,
            focus=factor,
            detail=DETAIL,
            comp_def=DEFINITION,
            focus_type="태도",
            variation_index=index,
            task_frame=frame,
        )
        assert expected in question, question
        assert old_wording not in question, question
        for wording in INVENTED_OR_BROKEN:
            assert wording not in question, question
        # The official label itself is evidence, not candidate wording.
        assert factor not in question
        assert "행동 기준" not in question
        assert "행동이 드러난 행동" not in question
        assert f"{expected} 행동" not in question
