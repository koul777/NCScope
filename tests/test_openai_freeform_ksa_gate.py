from __future__ import annotations

import pytest

from app.services.question_quality_orchestrator import evaluate_ksa_measurement


NATURAL_PRESENTATION_QUESTION = (
    "한국학 연구·교육의 세계화를 위한 차년도 사업 방향을 제안하셔야 합니다. 제공 자료는 "
    "최근 3년간 사업별 예산과 참여자 실적, 해외 한국학 수요 조사, 내부 인력 현황이며, "
    "신규 재원은 5억 원이고 추가 채용은 불가능합니다. 경영진은 단기간에 참여자 수를 "
    "늘리길 원하지만 연구부서는 장기적인 디지털 표준 구축을 우선해 두 요구를 동시에 "
    "충족하기 어렵습니다. 20분 동안 자료를 검토한 뒤 7분 이내로 우선사업, 자원 배분, "
    "판단 근거, 1년 차 성과지표를 발표해 주십시오."
)


def _presentation_item(*, source: str, evidence_id: str) -> dict[str, object]:
    return {
        "type": "발표면접",
        "question_focus": "경영환경 분석 능력",
        "question_focus_type": "기술",
        "question_focus_surface": "경영환경 검토 절차",
        "question_source": source,
        "question_evidence_required": True,
        "question_evidence_id": evidence_id,
        "question": NATURAL_PRESENTATION_QUESTION,
    }


@pytest.mark.parametrize(
    "source",
    [
        "openai_api",
        "openai_api_quality_repaired_fields",
        "codex_cli",
        "claude_code",
    ],
)
def test_evidence_linked_freeform_providers_measure_ksa_without_raw_label(
    source: str,
) -> None:
    item = _presentation_item(source=source, evidence_id="ksa_environment_analysis")

    result = evaluate_ksa_measurement(item)

    assert item["question_focus"] not in NATURAL_PRESENTATION_QUESTION
    assert result["passed"] is True, result
    assert all(result["checks"].values())


@pytest.mark.parametrize("evidence_id", ["", "   "])
def test_openai_freeform_translation_requires_nonempty_evidence_id(
    evidence_id: str,
) -> None:
    result = evaluate_ksa_measurement(
        _presentation_item(source="openai_api", evidence_id=evidence_id)
    )

    assert result["checks"]["focus_visible"] is False
    assert result["checks"]["evidence_linked"] is False
    assert result["passed"] is False


def test_openai_freeform_translation_does_not_broaden_legacy_model_source() -> None:
    result = evaluate_ksa_measurement(
        _presentation_item(source="model", evidence_id="ksa_environment_analysis")
    )

    assert result["checks"]["focus_visible"] is False
    assert result["passed"] is False


def test_openai_freeform_tool_alias_still_requires_the_ksa_domain() -> None:
    result = evaluate_ksa_measurement(
        {
            "type": "인바스켓면접",
            "question_focus": "예산프로그램 활용 능력",
            "question_focus_type": "기술",
            "question_focus_surface": "예산프로그램 활용·검증 절차",
            "question_source": "openai_api",
            "question_evidence_required": True,
            "question_evidence_id": "ksa_budget_program",
            "question": (
                "오전 9시에 인사 서류 세 건이 동시에 도착했습니다. 어떤 순서로 처리하고, "
                "인사시스템에서 무엇을 조회·기록하며, 담당자에게 어떤 결정을 요청하시겠습니까?"
            ),
        }
    )

    assert result["checks"]["focus_visible"] is False
    assert result["passed"] is False


@pytest.mark.parametrize(
    ("method", "focus", "kind", "question"),
    [
        (
            "상황면접",
            "프로젝트 예산(Budget)을 수립할 수 있는 능력",
            "기술",
            "연간 사업계획 마감일에 부서 요구안 합계가 가용 재원을 초과하고 이월액도 "
            "확정되지 않았습니다. 확인 결과에 따라 우선순위를 조정한 예산안을 제시해 주십시오.",
        ),
        (
            "상황면접",
            "계약 관련 리스크를 식별할 수 있는 능력",
            "기술",
            "해외 연구용역 착수 직전 제안요청서와 계약서 초안의 검수 조건이 다릅니다. "
            "누락된 책임을 확인해 체결 여부를 판단하고 계약 보완안을 제시해 주십시오.",
        ),
        (
            "발표면접",
            "핵심성과지표 설정 능력",
            "기술",
            "월간 실적표에서 참여자는 늘었지만 재참여율은 낮아졌습니다. 우선 개선안을 "
            "선택하고 목표값과 측정항목을 담은 성과관리표를 발표해 주십시오.",
        ),
        (
            "경험면접",
            "외국어 의사소통 능력",
            "기술",
            "해외 기관과 영문 요구사항의 해석이 달랐던 사례에서 상대 표현을 어떻게 "
            "확인하고 합의를 만들었으며, 회신과 결과에 무엇을 남겼는지 말씀해 주십시오.",
        ),
    ],
)
@pytest.mark.parametrize("source", ["openai_api", "codex_cli", "claude_code"])
def test_real_korean_ksa_predicates_are_semantically_translated_without_label_copy(
    method: str,
    focus: str,
    kind: str,
    question: str,
    source: str,
) -> None:
    item = {
        "type": method,
        "question_focus": focus,
        "question_focus_type": kind,
        "question_focus_surface": "내부 평가용 표면어",
        "question_source": source,
        "question_evidence_required": True,
        "question_evidence_id": "ksa_exact_official_row",
        "question": question,
    }

    result = evaluate_ksa_measurement(item)

    assert focus not in question
    assert result["passed"] is True, result
    assert result["checks"]["focus_visible"] is True
    assert result["checks"]["ksa_type_operationalized"] is True


@pytest.mark.parametrize(
    ("focus", "question"),
    [
        (
            "프로젝트 예산(Budget)을 수립할 수 있는 능력",
            "해외 기관과 영문 표현의 차이를 확인하고 합의 결과를 회신으로 남긴 경험을 말씀해 주십시오.",
        ),
        (
            "외국어 의사소통 능력",
            "부서별 사업비와 집행액을 비교해 예산 조정안을 작성한 경험을 말씀해 주십시오.",
        ),
        (
            "계약 관련 리스크를 식별할 수 있는 능력",
            "월간 참여자와 재참여율을 비교해 성과관리표의 목표값을 설정한 경험을 말씀해 주십시오.",
        ),
    ],
)
def test_freeform_translation_rejects_cross_domain_semantic_substitution(
    focus: str,
    question: str,
) -> None:
    result = evaluate_ksa_measurement(
        {
            "type": "경험면접",
            "question_focus": focus,
            "question_focus_type": "기술",
            "question_focus_surface": "내부 평가용 표면어",
            "question_source": "openai_api",
            "question_evidence_required": True,
            "question_evidence_id": "ksa_exact_official_row",
            "question": question,
        }
    )

    assert result["checks"]["focus_visible"] is False
    assert result["passed"] is False


@pytest.mark.parametrize(
    (
        "model_substitute",
        "method",
        "focus",
        "surface",
        "evidence_id",
        "question",
        "cross_domain_question",
    ),
    [
        (
            "codex_cli",
            "상황면접",
            "프로젝트 예산(Budget)을 수립할 수 있는 능력",
            "프로젝트 예산(Budget) 작성·검토 절차",
            "ksa_495f0b0c8e9e9e7e86b8e112",
            "연간 사업계획 확정일이 오늘인데, 부서별 요구안의 산출 근거와 최근 집행자료가 "
            "서로 맞지 않아 전체 재원 한도를 넘을 가능성이 발견되었습니다. 어떤 요구안을 "
            "우선 조정할지 판단하고, 필요한 자료가 아직 확보되지 않은 항목은 조건부로 "
            "구분한 예산 조정표를 제시해 주세요.",
            "해외 기관의 영문 회신과 회의 설명이 서로 달랐습니다. 표현의 의미를 확인하고 "
            "상대 기관과 합의한 결과를 회신 기록으로 제시해 주세요.",
        ),
        (
            "codex_cli",
            "발표면접",
            "핵심성과지표 설정 능력",
            "핵심성과지표 설정·확인 절차",
            "ksa_e59b2c61aa7d5891cdfc1144",
            "기관의 월별 사업실적표에서 한 사업의 처리 건수는 급증했지만 완료율은 하락했고, "
            "같은 기간 민원기록에는 처리 지연이 반복되어 있습니다. 추가 재원이 제한된 "
            "상황에서 이 현상의 핵심 원인을 진단하고 우선 적용할 개선안을 선택한 뒤, "
            "목표값·측정자료·확인주기를 담은 성과관리표를 제시하는 발표를 해 주세요.",
            "과업지시서와 계약서의 검수 조건이 다릅니다. 누락된 책임을 확인해 체결 여부를 "
            "판단하고 계약 보완안을 제시해 주세요.",
        ),
        (
            "claude_code",
            "상황면접",
            "계약 관련 리스크를 식별할 수 있는 능력",
            "계약 관련 리스크 식별·검증 절차",
            "ksa_94810a54098612e6a6680c43",
            "해외 연구용역 발주를 위한 계약서 초안을 검토하던 중, 정산 조항에 명시된 환율 "
            "적용 기준이 연구원 내부 지침의 기준과 다르고, 계약 상대 기관에는 오늘 중 "
            "서명본을 회신해야 하는 상황입니다. 지금 가장 먼저 무엇을 확인해 계약상의 "
            "리스크를 판단하고, 회신할 수정 요청안에는 어떤 내용을 담으시겠습니까?",
            "월별 실적표에서 참여자는 늘었지만 만족도는 하락했습니다. 원인을 진단하고 "
            "달성 여부를 확인할 지표를 발표해 주세요.",
        ),
        (
            "claude_code",
            "발표면접",
            "핵심성과지표 설정 능력",
            "핵심성과지표 설정·확인 절차",
            "ksa_e59b2c61aa7d5891cdfc1144",
            "귀하에게 최근 1년간 사업별 월별 성과지표표와 이용자 민원 접수 기록이 주어지고, "
            "그중 한 사업의 만족도 지표가 특정 월에 급격히 하락한 것이 확인됩니다. 이 "
            "자료를 바탕으로 하락 원인을 진단하고, 제한된 예산 안에서 실행할 대안과 그 "
            "대안의 달성 여부를 확인할 지표를 발표해 주십시오.",
            "부서별 사업비 산출 근거와 집행 실적이 다릅니다. 자료를 대조해 재원 한도에 "
            "맞춘 예산 조정안을 제시해 주세요.",
        ),
    ],
)
def test_revised_provider_questions_use_incident_action_output_semantic_bridge(
    model_substitute: str,
    method: str,
    focus: str,
    surface: str,
    evidence_id: str,
    question: str,
    cross_domain_question: str,
) -> None:
    item = {
        "type": method,
        "question_focus": focus,
        "question_focus_type": "기술",
        "question_focus_surface": surface,
        # The public runtime, regardless of which local model substituted in
        # this experiment, is request-scoped OpenAI BYOK with exact evidence.
        "question_source": "openai_api",
        "question_evidence_required": True,
        "question_evidence_id": evidence_id,
        "model_substitute": model_substitute,
        "question": question,
    }

    result = evaluate_ksa_measurement(item)

    assert focus not in question
    assert result["passed"] is True, result
    assert result["checks"]["focus_visible"] is True
    assert result["checks"]["ksa_type_operationalized"] is True
    assert result["checks"]["observable_task"] is True

    negative = evaluate_ksa_measurement({**item, "question": cross_domain_question})

    assert negative["checks"]["focus_visible"] is False
    assert negative["checks"]["ksa_type_operationalized"] is False
    assert negative["passed"] is False
