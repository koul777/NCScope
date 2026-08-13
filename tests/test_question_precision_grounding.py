from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.services.question_precision_grounding import (
    PRECISION_GROUNDING_POLICY,
    evaluate_question_precision_grounding,
)


def _material(
    material_id: str,
    *,
    question_id: str,
    value_kind: str,
    field: str,
    value: str = "검증된 후보자 공개 값",
    candidate_visible: bool = True,
    origin_verified: bool = True,
    source_kind: str = "uploaded_document",
    **extra: Any,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "material_id": material_id,
        "source_kind": source_kind,
        "document_sha256": "a" * 64,
        "locator": {"page": 2, "block": 3},
        "field": field,
        "value": value,
        "value_kind": value_kind,
        "candidate_visible": candidate_visible,
        "origin_verified": origin_verified,
        "question_id": question_id,
    }
    row.update(extra)
    return row


def _registry(*items: dict[str, Any]) -> dict[str, Any]:
    return {"policy": "candidate-material-registry-v1", "items": list(items)}


@pytest.mark.parametrize(
    ("fixture_id", "item", "registry"),
    [
        (
            "P01",
            {
                "type": "상황면접",
                "question": (
                    "A 요구액은 7억 원, B 요구액은 5억 원이고 한도는 9억 원입니다. "
                    "조정안을 제안하세요."
                ),
            },
            None,
        ),
        (
            "P02",
            {
                "type": "상황면접",
                "question": (
                    "A 요구액은 7억 원, B 요구액은 5억 원이고 한도는 9억 원입니다. "
                    "조정안을 제안하세요."
                ),
                "follow_ups": [
                    "선택한 항목의 7억 원 당초액을 얼마로 조정하겠습니까?"
                ],
            },
            None,
        ),
        (
            "P03",
            {
                "type": "상황면접",
                "question": "어느 항목을 얼마만큼 조정할지 근거와 함께 제안하세요.",
            },
            None,
        ),
        (
            "P04",
            {
                "type": "발표면접",
                "question": "측정할 목표값과 산식을 직접 설계해 주세요.",
            },
            None,
        ),
        (
            "P05",
            {
                "type": "경험면접",
                "question": (
                    "당시 결과가 몇 % 달라졌는지 가능한 범위에서 설명해 주세요."
                ),
            },
            None,
        ),
        (
            "P06",
            {
                "type": "경험면접",
                "question": (
                    "직접 작성한 회신의 핵심 표현을 기억나는 범위에서 설명해 주세요."
                ),
            },
            None,
        ),
        (
            "P07",
            {
                "type": "상황면접",
                "question": (
                    "계약서에서 납기·변경·검수 중 어떤 유형의 조항을 우선 "
                    "확인하시겠습니까?"
                ),
            },
            None,
        ),
        (
            "P08",
            {
                "question_id": "q-p08",
                "type": "상황면접",
                "question": "제12조제2항의 적용 조건을 인용해 판단하세요.",
                "case_material_refs": ["mat_clause_12_2"],
            },
            _registry(
                _material(
                    "mat_clause_12_2",
                    question_id="q-p08",
                    value_kind="clause_excerpt",
                    field="가상 계약서 제12조제2항 원문",
                    value="후보자에게 공개된 조문 발췌",
                )
            ),
        ),
        (
            "P09",
            {
                "question_id": "q-p09",
                "type": "발표면접",
                "question": (
                    "제공 산식으로 수료율을 계산하고 분자·분모를 밝히세요."
                ),
                "case_material_refs": ["mat_formula_rate", "mat_rate_values"],
            },
            _registry(
                _material(
                    "mat_formula_rate",
                    question_id="q-p09",
                    value_kind="formula",
                    field="수료율 산식",
                    value="수료자 수를 신청자 수로 나눈 검증 산식",
                ),
                _material(
                    "mat_rate_values",
                    question_id="q-p09",
                    value_kind="raw_values",
                    field="수료자 수와 신청자 수",
                    value="후보자 공개 원자료",
                    fields=["수료자 수", "신청자 수"],
                ),
            ),
        ),
        (
            "P10",
            {
                "type": "상황면접",
                "question": (
                    "가상 계약금액 8천만 원 중 1천만 원 증액 요청입니다. "
                    "승인 범위를 판단하세요."
                ),
            },
            None,
        ),
        (
            "P11",
            {
                "type": "발표면접",
                "question": (
                    "신청 200명 중 수료 120명입니다. 이 값으로 수료율을 계산하세요."
                ),
            },
            None,
        ),
        (
            "P12",
            {
                "type": "상황면접",
                "question": (
                    "방금 본인이 제안한 목표값을 선택한 근거를 설명하세요."
                ),
                "answer_slots": ["decision.target_value"],
            },
            None,
        ),
    ],
)
def test_acceptance_positive_corpus(
    fixture_id: str, item: dict[str, Any], registry: Any
) -> None:
    result = evaluate_question_precision_grounding(item, registry)

    assert result["policy"] == PRECISION_GROUNDING_POLICY, fixture_id
    assert result["passed"] is True, (fixture_id, result)
    assert result["issue_codes"] == [], fixture_id
    assert all(demand["disposition"] == "allow" for demand in result["demands"])


@pytest.mark.parametrize(
    ("fixture_id", "item", "registry", "expected_reason"),
    [
        (
            "N01",
            {
                "question": "예산표에서 당초 요구액이 얼마였는지 정확히 말하세요."
            },
            None,
            "missing_material_ref",
        ),
        (
            "N02",
            {
                "question": "당초 요구액과 조정액을 대조해 차액을 계산하세요."
            },
            None,
            "missing_amount_material",
        ),
        (
            "N03",
            {
                "question": (
                    "분자·분모와 기준연도 값을 자료에서 가져와 계산 과정을 "
                    "설명하세요."
                ),
                "task_conditions": {"case_materials": [{"source": "성과표"}]},
            },
            None,
            "missing_candidate_visible_formula_material",
        ),
        (
            "N04",
            {"question": "관련 규정이 몇 조 몇 항인지 말하세요."},
            None,
            "missing_clause_excerpt",
        ),
        (
            "N05",
            {
                "question": "연간 집행액을 표에서 찾으세요.",
                "case_material_refs": ["mat_unknown"],
            },
            None,
            "dangling_material_ref",
        ),
        (
            "N06",
            {
                "question_id": "q-n06",
                "question": "공문상 정확한 금액을 말하세요.",
                "case_material_refs": ["mat_hidden_amount"],
            },
            _registry(
                _material(
                    "mat_hidden_amount",
                    question_id="q-n06",
                    value_kind="amount",
                    field="공문상 금액",
                    candidate_visible=False,
                )
            ),
            "material_not_candidate_visible",
        ),
        (
            "N07",
            {
                "question_id": "q-n07",
                "question": "이 사업의 수료율을 계산하세요.",
                "case_material_refs": ["mat_other_rate"],
            },
            _registry(
                _material(
                    "mat_other_rate",
                    question_id="q-other",
                    value_kind="ratio",
                    field="다른 사업 수료율",
                )
            ),
            "cross_question_material_ref",
        ),
        (
            "N08",
            {"question": "제공표의 전년 대비 증가율을 정확히 말하세요."},
            None,
            "missing_material_ref",
        ),
        (
            "N09",
            {
                "question_id": "q-n09",
                "question": "항목별 단가와 수량을 계산하세요.",
                "case_material_refs": ["mat_total_amount"],
            },
            _registry(
                _material(
                    "mat_total_amount",
                    question_id="q-n09",
                    value_kind="amount",
                    field="사업 총액",
                )
            ),
            "material_kind_or_field_mismatch",
        ),
        (
            "N10",
            {
                "question": "계약서 제8조 원문을 인용하세요.",
                "case_material_refs": ["mat_fake_origin"],
                "case_materials": [
                    {
                        "material_id": "mat_fake_origin",
                        "origin_verified": True,
                    }
                ],
            },
            None,
            "dangling_material_ref",
        ),
        (
            "N11",
            {
                "question": (
                    "성과표 자료를 제공해 드리겠습니다. 표의 정확한 수료율과 "
                    "계산 과정을 설명하세요."
                )
            },
            None,
            "claimed_material_not_attached",
        ),
        (
            "N12",
            {
                "question_id": "q-n12",
                "question": "A부서의 당초 요구액을 정확히 말하세요.",
                "case_material_refs": ["mat_dept_b_amount"],
            },
            _registry(
                _material(
                    "mat_dept_b_amount",
                    question_id="q-n12",
                    value_kind="amount",
                    field="B부서 당초 요구액",
                )
            ),
            "material_semantic_mismatch",
        ),
    ],
)
def test_acceptance_reject_corpus(
    fixture_id: str,
    item: dict[str, Any],
    registry: Any,
    expected_reason: str,
) -> None:
    result = evaluate_question_precision_grounding(item, registry)

    assert result["passed"] is False, (fixture_id, result)
    assert "unsupported_precision_demand" in result["issue_codes"]
    assert expected_reason in result["issue_codes"], (fixture_id, result)
    assert result["metrics"]["rejected_demand_count"] == 1


def test_v18_resource_allocation_quantity_is_rejected_without_numeric_totals() -> None:
    """Freeze the human/blind-panel false positive that escaped precision v1."""

    result = evaluate_question_precision_grounding(
        {
            "type": "발표면접",
            "question": (
                "세 사업이 늘어난 인력·예산을 모두 요구하지만 총량은 동결되어 "
                "있습니다. 현 배분 유지·공통 비율 조정·단계 지원 중 어느 방식을 "
                "선택할지 정하고, 공통 기준으로 사업별 예산과 인력을 어떻게 "
                "배분할지 설명해 주세요. 공통 기준·사업별 배정량·성과 확인 지표·"
                "재조정 조건을 담은 배분안 한 장을 발표해 주세요."
            ),
            "evaluation_points": [
                "공통 기준을 모든 사업에 적용한다.",
                "사업별 배분량을 수치화한다.",
                "성과 확인 시점과 재조정 조건을 제시한다.",
                "예외 사유를 같은 배분안에 기록한다.",
            ],
        }
    )

    assert result["policy"] == "precision-grounding-v2"
    assert result["passed"] is False
    assert "missing_allocation_total" in result["issue_codes"]
    assert {demand["location"] for demand in result["demands"]} == {
        "question",
        "evaluation_points[1]",
    }
    assert all(
        demand["kind"] == "quantified_allocation"
        for demand in result["demands"]
    )
    assert all(
        demand["required_value_kinds"]
        == ["allocation_amount_total", "allocation_quantity_total"]
        for demand in result["demands"]
    )


@pytest.mark.parametrize(
    ("question", "evaluation_points"),
    [
        (
            "총예산과 가용 인원이 동결된 상황에서 과제별 지원량을 정해 "
            "지원조정표에 표시해 주세요.",
            [],
        ),
        (
            "예산과 정원은 늘릴 수 없습니다. 각 부서에 몇 원과 몇 명씩 "
            "배정할지 제시해 주세요.",
            [],
        ),
        (
            "한정된 예산으로 세 사업의 지원 방식을 정해 주세요.",
            ["사업별 예산 배분 수치를 정량화한다."],
        ),
        (
            "사용 가능한 재원의 총액은 제시되지 않았습니다. 사업별 배분 비율을 "
            "명시한 조정안을 작성해 주세요.",
            [],
        ),
    ],
)
def test_quantified_allocation_paraphrases_require_visible_totals(
    question: str, evaluation_points: list[str]
) -> None:
    result = evaluate_question_precision_grounding(
        {"type": "발표면접", "question": question, "evaluation_points": evaluation_points}
    )

    assert result["passed"] is False, result
    assert result["metrics"]["rejected_demand_count"] >= 1
    assert any(
        demand["kind"] == "quantified_allocation"
        for demand in result["demands"]
    )
    assert "missing_allocation_total" in result["issue_codes"]


@pytest.mark.parametrize(
    "question",
    [
        (
            "예산과 인력이 부족합니다. 사업별 지원 우선순위와 상·중·하 지원 "
            "수준을 정하고 배분 기준을 설명해 주세요."
        ),
        (
            "한정된 자원에서 현 배분 유지·차등 지원·순차 지원 중 하나를 "
            "선택하고 그 이유를 설명해 주세요."
        ),
        (
            "사업별 지원 범위를 최소·기본·확대 중에서 고르고 재검토 조건을 "
            "제시해 주세요."
        ),
    ],
)
def test_relative_allocation_choices_do_not_create_numeric_precision_demand(
    question: str,
) -> None:
    result = evaluate_question_precision_grounding({"question": question})

    assert result["passed"] is True, result
    assert result["demands"] == []


@pytest.mark.parametrize(
    "question",
    [
        (
            "총예산은 9억 원이고 가용 인력은 6명입니다. 세 사업의 사업별 "
            "예산 배분액과 인력 배정량을 표시한 배분안을 작성해 주세요."
        ),
        (
            "A 50%, B 30%, C 20%의 선택지가 주어졌습니다. 이 사업별 배분 "
            "비율을 명시하고 선택 근거를 설명해 주세요."
        ),
        (
            "가용 자원 총량은 100단위입니다. 과제별 배정량과 재조정 조건을 "
            "표시해 주세요."
        ),
    ],
)
def test_quantified_allocation_accepts_complete_self_contained_totals(
    question: str,
) -> None:
    result = evaluate_question_precision_grounding({"question": question})

    assert result["passed"] is True, result
    assert result["demands"]
    assert all(
        demand["basis"] == "self_contained_scenario"
        for demand in result["demands"]
    )


def test_candidate_owned_past_allocation_quantities_remain_allowed() -> None:
    result = evaluate_question_precision_grounding(
        {
            "type": "경험면접",
            "question": (
                "과거 본인이 세 사업에 예산과 인력을 배분했던 실제 경험을 "
                "말씀해 주세요. 당시 사업별 배분액과 배정 인원, 그 결정의 "
                "결과를 설명해 주세요."
            ),
        }
    )

    assert result["passed"] is True, result
    assert result["demands"]
    assert all(demand["basis"] == "candidate_owned" for demand in result["demands"])


def test_quantified_allocation_accepts_trusted_visible_total_materials() -> None:
    result = evaluate_question_precision_grounding(
        {
            "question_id": "q-allocation-totals",
            "type": "발표면접",
            "question": (
                "후보자에게 제공된 총자원 자료를 사용해 사업별 예산 배분액과 "
                "인력 배정량을 표시한 배분안을 작성해 주세요."
            ),
            "case_material_refs": [
                "mat_allocation_budget_total",
                "mat_allocation_staff_total",
            ],
        },
        _registry(
            _material(
                "mat_allocation_budget_total",
                question_id="q-allocation-totals",
                value_kind="amount",
                field="총예산",
                value="후보자에게 공개된 예산 총액",
            ),
            _material(
                "mat_allocation_staff_total",
                question_id="q-allocation-totals",
                value_kind="quantity",
                field="총인력",
                value="후보자에게 공개된 배정 가능 인원",
            ),
        ),
    )

    assert result["passed"] is True, result
    assert result["demands"][0]["basis"] == "trusted_material"
    assert set(result["demands"][0]["matched_material_ids"]) == {
        "mat_allocation_budget_total",
        "mat_allocation_staff_total",
    }


@pytest.mark.parametrize(
    "question",
    [
        "첨부된 표를 바탕으로 ROI를 정확히 산출",
        "원자료를 토대로 정확한 손익분기점",
        "첨부 계약서상 위약금 액수",
        "재무표를 참고해 전년 대비 증감률",
    ],
)
def test_source_bound_precision_paraphrases_require_server_material(
    question: str,
) -> None:
    result = evaluate_question_precision_grounding({"question": question})

    assert result["passed"] is False, result
    assert result["metrics"]["precision_demand_count"] == 1
    assert result["metrics"]["rejected_demand_count"] == 1
    assert "unsupported_precision_demand" in result["issue_codes"]
    assert "missing_material_ref" in result["issue_codes"]
    assert result["demands"][0]["mode"] in {"calculate", "retrieve"}


def test_source_bound_quote_paraphrase_requires_clause_excerpt() -> None:
    result = evaluate_question_precision_grounding(
        {"question": "첨부 계약서의 위약 조항을 그대로 옮겨 적으세요."}
    )

    assert result["passed"] is False
    assert result["metrics"]["precision_demand_count"] == 1
    assert result["demands"][0]["mode"] == "quote"
    assert result["demands"][0]["kind"] == "clause_quote"
    assert "missing_clause_excerpt" in result["issue_codes"]


@pytest.mark.parametrize(
    ("question_id", "question", "material_id", "value_kind", "field"),
    [
        (
            "q-source-metric",
            "첨부된 표를 바탕으로 ROI를 정확히 산출",
            "mat_source_metric_values",
            "raw_values",
            "ROI 산출 입력값",
        ),
        (
            "q-source-amount",
            "첨부 계약서상 위약금 액수",
            "mat_source_penalty_amount",
            "amount",
            "계약서 위약금 액수",
        ),
        (
            "q-source-clause",
            "첨부 계약서의 위약 조항을 그대로 옮겨 적으세요.",
            "mat_source_clause_excerpt",
            "clause_excerpt",
            "계약서 위약 조항 원문",
        ),
    ],
)
def test_source_bound_precision_paraphrase_accepts_trusted_visible_material(
    question_id: str,
    question: str,
    material_id: str,
    value_kind: str,
    field: str,
) -> None:
    result = evaluate_question_precision_grounding(
        {
            "question_id": question_id,
            "question": question,
            "case_material_refs": [material_id],
        },
        _registry(
            _material(
                material_id,
                question_id=question_id,
                value_kind=value_kind,
                field=field,
            )
        ),
    )

    assert result["passed"] is True, result
    assert result["demands"][0]["basis"] == "trusted_material"
    assert result["demands"][0]["matched_material_ids"] == [material_id]


@pytest.mark.parametrize(
    "question",
    [
        "원자료를 토대로 ROI 개선 전략을 제안하세요.",
        "재무표를 참고할 때 증감률 해석의 한계를 설명하세요.",
        "첨부 계약서의 위약금 제도가 사업자에게 미치는 영향을 분석하세요.",
        "제공된 표를 바탕으로 수익성 악화 원인을 분석하고 개선안을 제안하세요.",
    ],
)
def test_source_reference_with_general_analysis_is_not_exact_value_recall(
    question: str,
) -> None:
    result = evaluate_question_precision_grounding({"question": question})

    assert result["passed"] is True, result
    assert result["demands"] == []


def test_past_source_mismatch_with_sum_is_not_a_hidden_calculation_request() -> None:
    result = evaluate_question_precision_grounding(
        {
            "type": "경험면접",
            "question": (
                "마감 30분 전 원자료와 집계표의 합계가 달랐던 실제 경험이 있다면 "
                "말씀해 주세요. 어떤 문서를 점검하고 어떤 조치를 했으며 처리 결과는 "
                "무엇이었습니까?"
            ),
        }
    )

    assert result["passed"] is True, result
    assert result["demands"] == []


def test_candidate_owned_source_metric_remains_allowed_with_memory_boundary() -> None:
    result = evaluate_question_precision_grounding(
        {
            "type": "경험면접",
            "question": (
                "당시 본인이 원자료를 토대로 산출한 ROI가 얼마였는지 "
                "기억나는 범위에서 설명해 주세요."
            ),
        }
    )

    assert result["passed"] is True, result
    assert result["demands"][0]["basis"] == "candidate_owned"


def test_same_precision_request_is_checked_in_main_and_every_follow_up() -> None:
    wording = "관련 규정이 몇 조 몇 항인지 말하세요."
    main = evaluate_question_precision_grounding({"question": wording})
    follow_up = evaluate_question_precision_grounding(
        {"question": "판단 절차를 말씀해 주세요.", "follow_ups": [wording]}
    )

    assert main["passed"] is follow_up["passed"] is False
    assert main["demands"][0]["reason"] == follow_up["demands"][0]["reason"]
    assert main["demands"][0]["text_sha256"] == follow_up["demands"][0]["text_sha256"]
    assert main["demands"][0]["location"] == "question"
    assert follow_up["demands"][0]["location"] == "follow_ups[0]"


@pytest.mark.parametrize("source", ["openai_api", "codex_cli", "claude_code"])
def test_provider_source_never_changes_precision_verdict(source: str) -> None:
    result = evaluate_question_precision_grounding(
        {
            "question": "예산표에서 당초 요구액이 얼마였는지 정확히 말하세요.",
            "question_source": source,
        }
    )

    assert result["passed"] is False
    assert result["demands"][0]["reason"] == "missing_material_ref"


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"origin_verified": False}, "material_origin_unverified"),
        ({"document_sha256": "not-a-sha"}, "invalid_material_provenance"),
        ({"locator": {}}, "invalid_material_provenance"),
        ({"value_kind": "model_claimed_value"}, "invalid_material_value_kind"),
        ({"value": ""}, "material_value_missing"),
    ],
)
def test_server_registry_contract_is_fail_closed(
    override: dict[str, Any], reason: str
) -> None:
    material = _material(
        "mat_contract_amount",
        question_id="q-contract",
        value_kind="amount",
        field="공문상 금액",
    )
    material.update(override)
    result = evaluate_question_precision_grounding(
        {
            "question_id": "q-contract",
            "question": "공문상 정확한 금액을 말하세요.",
            "case_material_refs": ["mat_contract_amount"],
        },
        _registry(material),
    )

    assert result["passed"] is False
    assert reason in result["issue_codes"]


def test_unrelated_or_invalid_refs_fail_even_without_a_detected_demand() -> None:
    result = evaluate_question_precision_grounding(
        {
            "question": "필요한 자료와 확인 순서를 말씀해 주세요.",
            "case_material_refs": ["not a material id"],
        }
    )

    assert result["metrics"]["precision_demand_count"] == 0
    assert result["passed"] is False
    assert {"invalid_material_id", "dangling_material_ref"}.issubset(
        result["issue_codes"]
    )


def test_appended_proposal_does_not_excuse_missing_fixed_input_amounts() -> None:
    result = evaluate_question_precision_grounding(
        {
            "question": (
                "당초 요구액과 실제 집행액을 대조해 차액을 계산한 뒤 "
                "새 조정안을 제안하세요."
            )
        }
    )

    assert result["passed"] is False
    assert result["demands"][0]["basis"] == "none"
    assert result["demands"][0]["reason"] == "missing_amount_material"


def test_past_experience_finding_a_document_mismatch_is_not_value_retrieval() -> None:
    result = evaluate_question_precision_grounding(
        {
            "type": "경험면접",
            "question": (
                "승인본과 원자료의 수치 불일치를 찾아 관계 부서와 수정한 실제 경험 "
                "한 가지를 말씀해 주세요. 당시 어떤 자료를 대조했고 수정 산출물과 "
                "처리시간 결과는 무엇이었습니까?"
            ),
            "follow_ups": [
                "원자료의 수치 불일치를 찾은 뒤 어떤 판단 기준을 적용했습니까?"
            ],
        }
    )

    assert result["passed"] is True
    assert result["demands"] == []


def test_open_estimate_of_time_required_is_not_hidden_amount_recall() -> None:
    result = evaluate_question_precision_grounding(
        {
            "type": "인바스켓면접",
            "question": "동시에 접수된 요청의 처리 우선순위를 정해 주세요.",
            "follow_ups": ["직접 처리할 경우 예상되는 시간 소요는 얼마입니까?"],
        }
    )

    assert result["passed"] is True
    assert result["demands"] == []


@pytest.mark.parametrize(
    "follow_up",
    [
        (
            "앞서 제시한 정정표에서 중복 실적을 놓칠 가능성이 있다면 어떤 "
            "원자료를 대조하고 어느 항목을 수정하시겠습니까?"
        ),
        (
            "발표에서 원인 근거로 선택한 수치가 단순한 시기 차이가 아니라는 점을 "
            "어떤 자료와 대조해 확인하시겠습니까?"
        ),
    ],
)
def test_asking_which_source_to_cross_check_is_not_hidden_value_retrieval(
    follow_up: str,
) -> None:
    result = evaluate_question_precision_grounding(
        {
            "type": "발표면접",
            "question": "서로 다른 월별 실적의 원인을 판정해 분석표를 제시해 주세요.",
            "follow_ups": [follow_up],
        }
    )

    assert result["passed"] is True
    assert result["demands"] == []


def test_exact_value_lookup_from_a_named_table_remains_blocked() -> None:
    result = evaluate_question_precision_grounding(
        {
            "type": "직무지식면접",
            "question": "정정표에서 확정된 정확한 합계를 찾아 말해 주세요.",
        }
    )

    assert result["passed"] is False
    assert result["demands"][0]["mode"] == "retrieve"


def test_duplicate_server_material_ids_are_rejected() -> None:
    first = _material(
        "mat_duplicate_amount",
        question_id="q-duplicate",
        value_kind="amount",
        field="공문상 금액",
    )
    second = dict(first)
    result = evaluate_question_precision_grounding(
        {
            "question_id": "q-duplicate",
            "question": "공문상 정확한 금액을 말하세요.",
            "case_material_refs": ["mat_duplicate_amount"],
        },
        _registry(first, second),
    )

    assert result["passed"] is False
    assert result["checks"]["no_duplicate_material_ids"] is False
    assert "duplicate_material_id" in result["issue_codes"]


def test_provider_embedded_registry_cannot_self_attest_hidden_value() -> None:
    result = evaluate_question_precision_grounding(
        {
            "question_id": "q-self-attested",
            "question": "공문상 정확한 금액을 말하세요.",
            "case_material_refs": ["mat_self_attested_amount"],
            "material_registry": _registry(
                _material(
                    "mat_self_attested_amount",
                    question_id="q-self-attested",
                    value_kind="amount",
                    field="공문상 금액",
                )
            ),
        }
    )

    assert result["passed"] is False
    assert "dangling_material_ref" in result["issue_codes"]


def test_result_never_copies_question_or_material_value() -> None:
    secret_question = "공문상 정확한 금액을 말하세요. 내부사건-비공개-토큰"
    secret_value = "극비 원문 금액 987654321원"
    result = evaluate_question_precision_grounding(
        {
            "question_id": "q-private",
            "question": secret_question,
            "case_material_refs": ["mat_private_amount"],
        },
        _registry(
            _material(
                "mat_private_amount",
                question_id="q-private",
                value_kind="amount",
                field="공문상 금액",
                value=secret_value,
            )
        ),
    )
    serialized = json.dumps(result, ensure_ascii=False)

    assert result["passed"] is True
    assert secret_question not in serialized
    assert "내부사건-비공개-토큰" not in serialized
    assert secret_value not in serialized
    assert "987654321" not in serialized
    assert result["demands"][0]["text_sha256"]


def test_recorded_baseline_and_revised_questions_match_14_of_16_expectation() -> None:
    report_path = (
        Path(__file__).resolve().parents[1]
        / "reports"
        / "revised_prompt_cross_provider_20260814.json"
    )
    if not report_path.exists():
        pytest.skip("overnight cross-provider evidence report is not present")
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    results: list[tuple[str, str, str, dict[str, Any]]] = []
    for set_name in ("baseline_analysis", "revised_analysis"):
        for provider in report[set_name]:
            for row in provider["questions"]:
                results.append(
                    (
                        set_name,
                        provider["provider"],
                        row["case"],
                        evaluate_question_precision_grounding(row["question"]),
                    )
                )

    assert len(results) == 16
    failures = {
        (set_name, provider, case)
        for set_name, provider, case, result in results
        if not result["passed"]
    }
    assert failures == {
        ("baseline_analysis", "codex_cli", "performance_indicator"),
        ("baseline_analysis", "claude_code", "performance_indicator"),
    }
    assert sum(result["passed"] for *_, result in results) == 14
