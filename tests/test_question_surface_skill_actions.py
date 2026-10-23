"""Skill prompts retain the task without inventing a verification procedure."""

from __future__ import annotations

import pytest

from app.main import SUPPORTED_INTERVIEW_METHODS, _attach_ksa_evidence_to_strategy
from app.services.question_surface import public_task_object, stable_ksa_evidence_id
from scripts.benchmark_question_method_matrix import build_template_question, evidence_row


# One sample per changed replacement/fallback rule, including both particles.
SKILL_ACTION_CASES = (
    ("수요예측 기법", "수요 예측·검증 절차", "수요 예측"),
    ("통계 기법", "통계 방법 적용·검증 절차", "통계 방법 적용"),
    ("문서관리 시스템", "문서관리 도구 활용 절차", "문서관리 도구 활용"),
    ("사업계획 수립 능력", "사업계획 작성·검토 절차", "사업계획 수립"),
    ("소비자 패턴분석 능력", "소비자 패턴 검토 절차", "소비자 패턴 분석"),
    ("문서 오류 검증 기술", "문서 오류 확인·검증 절차", "문서 오류 검증"),
    ("보고서 작성 능력", "보고서 산출물 작성 절차", "보고서 작성"),
    ("운임원가산정", "운임원가 계산·검증 절차", "운임원가 계산"),
    ("예산프로그램 활용 능력", "예산프로그램 활용·검증 절차", "예산프로그램 활용"),
    ("규칙 적용 능력", "규칙 적용·검증 절차", "규칙 적용"),
    ("설계도면 해석 능력", "설계도면 해석·검증 절차", "설계도면 해석"),
    ("투자 의사결정 능력", "투자 의사결정·검증 절차", "투자 의사결정"),
    ("새로운 영업기회를 찾아낼 수 있는 기회포착 능력", "새로운 영업기회 포착·검증 절차", "새로운 영업기회 포착"),
    ("최적 조건으로 자료를 수정·보완할 수 있는 능력", "최적 조건으로 자료 수정·보완·검증 절차", "최적 조건으로 자료 수정·보완"),
    ("교육과정 개발 능력", "교육과정 개발·검증 절차", "교육과정 개발"),
    ("통계 분석 방법", "통계 분석 방법 적용·검증 절차", "통계 분석 방법 적용"),
    ("예산프로그램", "예산프로그램 활용·검증 절차", "예산프로그램 활용"),
    ("장비 조작 능력", "장비 조작·검증 절차", "장비 조작"),
    ("현장 자료를 정리하고", "현장 자료 정리·검증 절차", "현장 자료 정리"),
)


def skill_row(factor: str) -> dict[str, str]:
    return {
        "major_code": "02",
        "major_name": "경영·회계·사무",
        "sub_name": "사무행정",
        "unit_code": "0202030201_17v2",
        "unit_name": "문서작성",
        "unit_definition": "문서 작성 계획을 수립하고 자료를 조사하여 문서를 작성하는 능력이다.",
        "element_name": "문서 작성하기",
        "ksa_type": "기술",
        "factor_name": factor,
        "ksa_no": "1",
    }


@pytest.mark.parametrize(("factor", "old_surface", "expected"), SKILL_ACTION_CASES)
def test_skill_surface_keeps_action_without_added_verification(
    factor: str, old_surface: str, expected: str,
) -> None:
    surface, source = public_task_object(factor_name=factor, ksa_type="기술")

    assert surface == expected
    assert source == "factor_repair"
    assert old_surface not in surface
    assert "절차" not in surface
    if "검증" not in factor:
        assert "검증" not in surface


@pytest.mark.parametrize("method", SUPPORTED_INTERVIEW_METHODS)
@pytest.mark.parametrize(("factor", "old_surface", "expected"), SKILL_ACTION_CASES)
def test_every_method_keeps_skill_action_and_official_evidence(
    method: str, factor: str, old_surface: str, expected: str,
) -> None:
    row = skill_row(factor)
    for variation_index in (0, 27):
        item = build_template_question(row, method, variation_index=variation_index)
        evaluated = _attach_ksa_evidence_to_strategy(
            {"interview_questions": [item], "question_plan_used": {"total_main_count": 1}},
            [evidence_row(row)],
        )
        question = evaluated["interview_questions"][0]
        quality = evaluated["question_quality_report"]["items"][0]
        visible = "\n".join([question["question"], *question["follow_ups"]])

        assert question["question_focus_surface"] == expected
        assert expected in question["question"]
        assert old_surface not in visible
        assert f"{expected}에 따라" not in visible
        assert f"{expected} 절차" not in visible
        assert f"{expected}·검증" not in visible
        assert question["question_focus"] == factor
        assert question["ksa_evidence"][0]["factorName"] == factor
        assert question["question_evidence_id"] == stable_ksa_evidence_id(evidence_row(row))
        assert quality["ready"] is True, (method, factor, quality.get("issues"))
        assert quality["score"] == 1.0


@pytest.mark.parametrize(
    ("factor", "expected"),
    (
        ("규칙 적용", "규칙을 적용하는 작업"),
        ("문서 오류 검증", "문서 오류를 검증하는 작업"),
        ("장비 조작", "장비를 조작하는 작업"),
        ("요구조사 수행능력", "요구조사"),
        ("정보 수집 능력", "정보 수집"),
        ("보안 점검 및 설정 수행 능력", "보안 점검 및 설정"),
        ("관련 장비를 다루는 능력", "관련 장비를 다루는 작업"),
        ("검사 절차", "검사 절차 적용"),
    ),
)
def test_unshortened_skill_keeps_its_factor_anchor(factor: str, expected: str) -> None:
    surface, source = public_task_object(
        factor_name=factor,
        ksa_type="기술",
        element_name="문서 작성하기",
        competency_name="문서작성",
    )

    assert surface == expected
    assert source == "factor_repair"
    assert "문서 작성" not in surface
    for word in ("검증", "확인", "점검", "절차"):
        if word not in factor:
            assert word not in surface
