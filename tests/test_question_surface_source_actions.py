"""Fallback surfaces preserve source actions without inventing verification."""

from __future__ import annotations

import copy
import re

import pytest

from app.main import SUPPORTED_INTERVIEW_METHODS, _attach_ksa_evidence_to_strategy
from app.services.question_surface import public_task_object, stable_ksa_evidence_id
from scripts.benchmark_question_method_matrix import build_template_question, evidence_row


CASES = (
    ("기술", "자료 분류", "자료 분석하기", "자료를 분류하는 작업", "factor_repair"),
    ("기술", "정보 수집", "자료 분석하기", "정보를 수집하는 작업", "factor_repair"),
    ("기술", "계획 수립", "자료 분석하기", "계획을 수립하는 작업", "factor_repair"),
    ("기술", "목표 목적의 수립", "자료 분석하기", "목표 목적의 수립 작업", "factor_repair"),
    ("기술", "회의 계획", "자료 분석하기", "회의 계획 작업", "factor_repair"),
    ("기술", "비용편익분석", "자료 분석하기", "비용편익분석 작업", "factor_repair"),
    ("기술", "규칙 적용", "자료 분석하기", "규칙을 적용하는 작업", "factor_repair"),
    ("기술", "문서 오류 검증", "자료 분석하기", "문서 오류를 검증하는 작업", "factor_repair"),
    ("기술", "장비 점검", "자료 분석하기", "장비를 점검하는 작업", "factor_repair"),
    ("기술", "설비 유지", "자료 분석하기", "설비를 유지하는 작업", "factor_repair"),
    ("기술", "장비 조작", "자료 분석하기", "장비를 조작하는 작업", "factor_repair"),
    ("기술", "검사 절차", "자료 분석하기", "검사 절차 적용", "factor_repair"),
    ("기술", "관련 장비를 다루는 능력", "자료 분석하기", "관련 장비를 다루는 작업", "factor_repair"),
    ("기술", "분석 능력", "자료 분석하기", "자료 분석", "element_name"),
    ("기술", "설정 능력", "조건 설정하기", "조건 설정", "element_name"),
    ("기술", "선택 능력", "재료 선택하기", "재료 선택", "element_name"),
    ("지식", "문서 분류 기준", "자료 분석하기", "문서 분류에 적용할 기준", "factor_repair"),
    ("지식", "OECD DAC 평가기준 및 품질관리 기준", "자료 분석하기", "업무에 적용할 OECD DAC 평가기준 및 품질관리 기준", "factor_repair"),
    ("지식", "문서 보안 지식", "자료 분석하기", "문서 보안 적용 기준", "factor_repair"),
    ("지식", "지식", "자료 분석하기", "자료 분석에 적용할 기준", "element_name"),
    ("기술", "실시간 처리", "자료 분석하기", "실시간 처리 작업", "factor_repair"),
    ("기술", "고객 불만내용 유형별 분류", "자료 분석하기", "고객 불만내용 유형별 분류 작업", "factor_repair"),
    ("기술", "환경변화에 대한 예측", "자료 분석하기", "환경변화에 대한 예측 작업", "factor_repair"),
    ("기술", "소음과 냄새를 통한 점검", "자료 분석하기", "소음과 냄새를 통한 점검 작업", "factor_repair"),
    ("기술", "효율적 운영", "자료 분석하기", "효율적 운영 작업", "factor_repair"),
    ("기술", "차단기 자동 및 수동 조작", "자료 분석하기", "차단기 자동 및 수동 조작 작업", "factor_repair"),
    ("기술", "원자로 출력분포 비정상 대응", "자료 분석하기", "원자로 출력분포 비정상 대응 작업", "factor_repair"),
)


def _row(kind: str, factor: str, element: str) -> dict[str, str]:
    return {
        "major_code": "02", "major_name": "경영·회계·사무", "sub_name": "사무행정",
        "unit_code": "0202030201_17v2", "unit_name": "문서작성",
        "unit_definition": "자료를 조사하여 문서를 작성하는 능력이다.",
        "element_name": element, "ksa_type": kind, "factor_name": factor, "ksa_no": "1",
    }


@pytest.mark.parametrize(("kind", "factor", "element", "expected", "source"), CASES)
def test_fallback_surface_uses_source_action_without_added_checks(
    kind: str, factor: str, element: str, expected: str, source: str,
) -> None:
    surface, actual_source = public_task_object(
        factor_name=factor, ksa_type=kind, element_name=element,
    )
    assert (surface, actual_source) == (expected, source)
    assert 3 <= len(re.sub(r"\W", "", surface)) <= 80
    assert re.sub(r"\s", "", surface) != re.sub(r"\s", "", factor)
    original = element if source == "element_name" else factor
    for word in ("검증", "확인", "점검", "절차"):
        if word not in original:
            assert word not in surface
        else:
            assert word in surface


def test_long_source_action_keeps_named_methods_within_surface_limit() -> None:
    factor = "PERT(Project Evaluation and Review Tech.)/CPM(Critical Path Method) 기법 활용"
    surface, source = public_task_object(factor_name=factor, ksa_type="기술")
    assert source == "factor_repair"
    assert "PERT(Project Evaluation and Review Tech.)/CPM(Critical Path Method)" in surface
    assert "활용" in surface
    assert len(surface) <= 80
    assert not any(word in surface for word in ("검증", "확인", "점검", "절차"))


@pytest.mark.parametrize("clause", ("제작 설치 되었는지", "성능이 발휘되는지"))
def test_long_knowledge_preserves_explicit_source_check(clause: str) -> None:
    scope = f"사용자 요구규격(URS, User Requirment Specification)에 맞게 {clause}"
    factor = f"{scope} 확인할 수 있는 장비/시스템에 대한 지식"
    surface, source = public_task_object(factor_name=factor, ksa_type="지식")
    assert surface == f"{scope}를 확인하는 기준"
    assert source == "factor_repair"
    assert len(surface) <= 80
    assert "검증" not in surface and "절차" not in surface


@pytest.mark.parametrize("method", SUPPORTED_INTERVIEW_METHODS)
@pytest.mark.parametrize(
    ("kind", "factor", "element", "expected", "source"),
    # These short labels collide with unrelated template words even at the
    # baseline. Their unchanged safety diagnostics are pinned separately below.
    [case for case in CASES if case[1] not in {"분석 능력", "선택 능력", "문서 보안 지식"}],
)
def test_source_action_all_methods_preserve_surface_evidence_and_quality(
    method: str, kind: str, factor: str, element: str, expected: str, source: str,
) -> None:
    row = _row(kind, factor, element)
    evidence = evidence_row(row)
    item = build_template_question(row, method)
    evaluated = _attach_ksa_evidence_to_strategy(
        {"interview_questions": [item], "question_plan_used": {"total_main_count": 1}}, [evidence],
    )
    question = evaluated["interview_questions"][0]
    quality = evaluated["question_quality_report"]["items"][0]
    assert question["question_focus_surface"] == expected
    assert expected in question["question"]
    assert question["question_task_frame"]["surface_source"] == source
    assert question["question_focus"] == factor
    assert question["ksa_evidence"][0]["factorName"] == factor
    assert question["question_evidence_id"] == stable_ksa_evidence_id(evidence)
    assert quality["ready"] is True, quality.get("issues")
    assert quality["score"] == 1.0
    again = _attach_ksa_evidence_to_strategy(
        {"interview_questions": [copy.deepcopy(question)], "question_plan_used": {"total_main_count": 1}}, [evidence],
    )["interview_questions"][0]
    for key in ("question", "follow_ups", "question_focus_surface", "question_evidence_id"):
        assert again[key] == question[key]


@pytest.mark.parametrize("method", SUPPORTED_INTERVIEW_METHODS)
@pytest.mark.parametrize(
    ("kind", "factor", "element", "expected", "unsafe_methods"),
    (
        ("기술", "분석 능력", "자료 분석하기", "자료 분석",
         {"발표면접", "인바스켓면접", "창의적 문제해결력면접"}),
        ("기술", "선택 능력", "재료 선택하기", "재료 선택",
         {"경험면접", "인바스켓면접", "창의적 문제해결력면접"}),
        ("지식", "문서 보안 지식", "자료 분석하기", "문서 보안 적용 기준",
         {"발표면접", "창의적 문제해결력면접"}),
    ),
)
def test_short_alias_safety_diagnostics_are_not_bypassed(
    method: str, kind: str, factor: str, element: str, expected: str,
    unsafe_methods: set[str],
) -> None:
    row = _row(kind, factor, element)
    item = build_template_question(row, method)
    evaluated = _attach_ksa_evidence_to_strategy(
        {"interview_questions": [item], "question_plan_used": {"total_main_count": 1}},
        [evidence_row(row)],
    )
    question = evaluated["interview_questions"][0]
    quality = evaluated["question_quality_report"]["items"][0]
    assert question["question_focus_surface"] == expected
    assert expected in question["question"]
    assert question["question_focus"] == factor
    assert question["question_evidence_id"] == stable_ksa_evidence_id(evidence_row(row))
    assert quality["ready"] is (method not in unsafe_methods)
    assert quality["issues"] == (["candidate_surface_safe"] if method in unsafe_methods else [])
