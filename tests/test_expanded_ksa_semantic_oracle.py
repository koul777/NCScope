from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.services.question_quality_orchestrator import evaluate_ksa_measurement


@dataclass(frozen=True)
class SemanticCase:
    case_id: str
    method: str
    ksa_type: str
    factor: str
    question: str
    oracle: str


CASES = (
    SemanticCase(
        "indicator_definition",
        "직무지식면접",
        "지식",
        "지표 운영 정의서에 대한 개념",
        "한 부서의 월간 실적표에서 같은 활동이 ‘참여 인원’과 ‘수료 인원’에 중복 "
        "반영됐고, 일부 사업은 집계 대상 기간이 다르게 설정돼 있습니다. 현재 제공된 "
        "자료만으로 확정할 수 있는 값과 추가 확인이 필요한 값을 구분해 최종 반영 "
        "기준을 판단하고, 정정된 실적표 1부를 제시해 주십시오.",
        "B",
    ),
    SemanticCase(
        "research_plan_review",
        "상황면접",
        "기술",
        "연구 계획서 검토 기술",
        "공동연구 신청 마감일에 온라인 신청서의 예산 항목과 첨부된 연구계획서의 "
        "비용 구성이 서로 다르고, 책임연구자는 연구 내용 수정도 함께 요청했습니다. "
        "제출 가능 여부에 대한 첫 판단을 내리고, 마감 전에 사용할 보완안 1부를 "
        "제시해 주십시오.",
        "A",
    ),
    SemanticCase(
        "numeric_accuracy",
        "경험면접",
        "태도",
        "수리적 정확도를 확보하려는 자세",
        "보고 마감이 임박한 상황에서 부서가 제출한 원자료와 전체 집계값이 맞지 않는 "
        "사실을 직접 발견하고도 일정 준수와 정정 중 하나를 판단해야 했던 실제 사례를 "
        "설명해 주십시오. 당시 본인의 역할과 행동, 판단 결과를 보여 주는 대조 기록 "
        "1부의 구성까지 말씀해 주십시오.",
        "A",
    ),
    SemanticCase(
        "plan_actual_gap",
        "발표면접",
        "기술",
        "계획대비 실적 분석 능력",
        "제공된 월별 집행·성과 추이표에는 한 사업의 집행액이 급증했지만 이용 성과는 "
        "정체돼 있고, 같은 기간 서비스 지연 민원이 늘어난 것으로 나타납니다. 추가 "
        "예산이 제한된 조건에서 원인을 진단하고 두 가지 대응안을 비교해 최우선 "
        "조정안을 결정한 뒤, 이를 담은 실행 보고서 1부를 발표해 주십시오.",
        "A",
    ),
    SemanticCase(
        "research_fund_rule",
        "직무지식면접",
        "지식",
        "부처별 연구개발사업 관리규정에 대한 지식",
        "연구자가 제출한 출장비 증빙의 출장 기간은 결과보고서와 일치하지만, 지원기관 "
        "지침과 연구원 내부 기준에서 인정 범위가 다르게 안내되어 있습니다. 어떤 근거를 "
        "우선 적용할지 판단하고, 그 판단을 반영한 보완·반려 검토서 한 건을 제시해 "
        "주십시오.",
        "A",
    ),
    SemanticCase(
        "agency_negotiation",
        "토론면접",
        "기술",
        "관련기관ㆍ단체 담당자와의 협상 기술",
        "[토론과제] 공동 연구사업의 중간보고 마감이 임박한 가운데 관계기관은 일정 "
        "준수를 위해 핵심 수치만 먼저 제출하자고 하고, 연구원 내부 부서는 근거자료 "
        "검증이 끝나기 전에는 제출할 수 없다고 주장합니다. 양측이 확인할 사실을 "
        "바탕으로 어느 범위까지 제출할지 합의하고, 공동 합의안 한 건을 도출해 주십시오.",
        "A",
    ),
    SemanticCase(
        "personal_data_protection",
        "인바스켓면접",
        "지식",
        "개인정보보호법",
        "오전 중 인사담당자의 연구 참여자 명단 열람 요청, 정오까지 처리해야 하는 "
        "외부기관의 연락처 포함 자료 회신, 잘못 첨부된 급여파일의 즉시 회수 요청이 "
        "동시에 도착했습니다. 본인에게는 외부 제공 승인 권한이 없을 때 세 건의 "
        "우선순위와 처리 주체를 결정하고, 업무처리표 한 건을 작성해 주십시오.",
        "B",
    ),
    SemanticCase(
        "measurement_framework",
        "창의적 문제해결력면접",
        "태도",
        "성과측정 기준 수립을 위한 체계적 사고",
        "최근 세 차례 실적 점검에서 해외 학술행사 성과가 반복해서 빠졌지만, 부서는 "
        "입력 화면의 불편을 원인으로 보고 있고 점검 담당자는 증빙 기준의 모호함을 "
        "원인으로 보고 있습니다. 추가 인력이나 시스템 개편 없이 먼저 검증할 원인 "
        "가설 하나를 선택하고, 소규모 시험계획서 한 건을 제시해 주십시오.",
        "B",
    ),
    SemanticCase(
        "closing_report_rules",
        "인바스켓면접",
        "지식",
        "회계보고서 및 분석·검토보고서 작성 요령",
        "오늘 정오까지 확정해야 하는 결산 초안, 오전 중 회신을 요구받은 증빙 보완 "
        "요청, 내일 경영진 회의에 사용할 요약자료가 동시에 도착했습니다. 일부 금액은 "
        "서로 맞지 않고 지원자에게는 최종 승인 권한이 없습니다. 세 문서의 처리 순서와 "
        "담당 주체를 결정하고, 그 내용을 담은 처리 배정표를 제시해 주십시오.",
        "B",
    ),
    SemanticCase(
        "review_report_writing",
        "경험면접",
        "기술",
        "분석·검토보고서 작성 능력",
        "보고 마감이 임박한 상황에서 예산계획과 결산실적의 원자료가 서로 맞지 않았던 "
        "실제 사례를 설명해 주십시오. 당시 본인이 어떤 자료를 신뢰할지 판단하여 직접 "
        "만든 의사결정용 보고서와 그 활용 결과를 함께 제시해 주십시오.",
        "A",
    ),
    SemanticCase(
        "civil_form_process",
        "상황면접",
        "기술",
        "관공서 서식 작성과 민원프로세스 파악 기술",
        "관계기관에 오늘 제출할 신청서에는 참여 인원이 열 명으로 적혀 있지만, 같은 "
        "건의 민원 회신 기록에는 열두 명으로 기재되어 있고 필수 첨부파일 하나도 "
        "누락되어 있습니다. 담당자와 연락 가능한 시간이 한 시간뿐이라면 무엇을 먼저 "
        "확인할지 판단하고, 제출 가능한 수정안을 제시해 주십시오.",
        "A",
    ),
    SemanticCase(
        "resource_allocation_fairness",
        "발표면접",
        "태도",
        "합리적인 자원분배 기준을 설정하려는 자세",
        "세 연구지원 사업의 월별 실적표, 인력·예산 추가 요청서, 이용자 불편 기록이 "
        "주어졌습니다. 한 사업은 최근 실적이 급감했지만 추가 인력을 요구하고 있고, "
        "전체 가용 예산은 모든 요구를 충족하기에 부족합니다. 어느 사업의 자원을 우선 "
        "조정할지 판단하고, 사업별 배분 근거와 담당자·일정·성과지표가 담긴 자원조정 "
        "실행표를 발표해 주십시오.",
        "A",
    ),
    SemanticCase(
        "objective_evaluation",
        "토론면접",
        "태도",
        "평가에 대한 객관적 자세의 유지",
        "[토론과제] 신규 시범사업의 중간 성과를 검토하는 과정에서, 기획부서는 동일한 "
        "수치 기준을 엄격히 적용해 계속 여부를 결정해야 한다고 주장하고 현업부서는 "
        "지역별 운영 여건과 초기 시행착오를 반영해야 한다고 주장합니다. 양측 주장을 "
        "판단하기 위해 확인할 사실을 정하고, 어느 경우까지 예외를 인정할지 토론하여 "
        "공동 평가 원칙안 한 부를 제시하십시오.",
        "B",
    ),
    SemanticCase(
        "visualization_data_accuracy",
        "창의적 문제해결력면접",
        "태도",
        "데이터의 정확성을 추구하는 태도",
        "월별 사업 성과를 그린 화면에서 같은 지표가 재무 파일, 현업 입력표, 집계 "
        "시스템마다 반복적으로 다르게 나타납니다. 담당자 한 명이 반나절만 사용할 수 "
        "있고 추가 예산은 없는 상황에서 가장 먼저 검증할 원인 가설을 판단하고, 소규모 "
        "검증 실험 설계서 한 부를 제시하십시오.",
        "B",
    ),
    SemanticCase(
        "data_use_ethics",
        "경험면접",
        "태도",
        "기업 내 데이터 수집 및 활용에 대한 윤리적 태도",
        "성과 보고 마감이 임박한 상황에서 사용 목적이나 열람 권한이 불분명한 내부 "
        "자료를 빨리 활용해 달라는 요청을 받은 실제 사례를 설명해 주십시오. 당시 "
        "본인이 내린 판단과 직접 취한 행동, 그 결과를 확인할 수 있는 승인·변경 기록 "
        "한 건을 제시해 주십시오.",
        "A",
    ),
    SemanticCase(
        "document_register",
        "인바스켓면접",
        "기술",
        "문서 대장 기록 능력",
        "오전 업무 시작과 동시에 정오까지 회신해야 하는 대외 협조 공문, 잘못 등록된 "
        "수신처를 즉시 고쳐 달라는 요청, 오늘 중 부서장 결재가 필요한 보고서가 "
        "도착했습니다. 수신처 변경은 상급자 승인 없이는 확정할 수 없는 상황에서 처리 "
        "우선순위와 각 처리 주체를 판단하고, 보류·보고 상태를 포함한 처리 배정표 한 "
        "부를 작성하십시오.",
        "B",
    ),
)


def _item(
    case: SemanticCase,
    *,
    question: str | None = None,
    method: str | None = None,
    source: str = "openai_api",
):
    return {
        "type": method or case.method,
        "question_focus": case.factor,
        "question_focus_type": case.ksa_type,
        "question_focus_surface": "내부 추적용 표면어",
        "question_source": source,
        "question_evidence_required": True,
        "question_evidence_id": f"ksa_{case.case_id}",
        "question": question or case.question,
    }


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
@pytest.mark.parametrize("source", ["openai_api", "codex_cli", "claude_code"])
def test_expanded_corpus_matches_independent_human_oracle(
    case: SemanticCase,
    source: str,
) -> None:
    result = evaluate_ksa_measurement(_item(case, source=source))

    assert case.factor not in case.question
    assert result["passed"] is (case.oracle == "A"), result
    if case.oracle == "A":
        assert all(result["checks"].values()), result
    else:
        assert result["checks"]["focus_visible"] is False, result
        assert result["checks"]["ksa_type_operationalized"] is False, result


@pytest.mark.parametrize(
    ("target_case_id", "source_case_id"),
    [
        ("personal_data_protection", "plan_actual_gap"),
        ("plan_actual_gap", "document_register"),
        ("agency_negotiation", "numeric_accuracy"),
        ("document_register", "research_plan_review"),
    ],
)
def test_factor_owned_bridges_reject_cross_domain_substitution(
    target_case_id: str,
    source_case_id: str,
) -> None:
    cases = {case.case_id: case for case in CASES}
    target = cases[target_case_id]
    source = cases[source_case_id]

    result = evaluate_ksa_measurement(
        _item(target, question=source.question, method=source.method)
    )

    assert result["checks"]["focus_visible"] is False, result
    assert result["checks"]["ksa_type_operationalized"] is False, result
    assert result["passed"] is False, result


def test_specific_definition_bridge_wins_over_broader_indicator_bridge() -> None:
    target = next(case for case in CASES if case.case_id == "indicator_definition")
    broad_kpi_question = (
        "월간 실적표에서 처리 건수는 급증했지만 만족도는 하락했습니다. 원인을 진단하고 "
        "우선 개선안을 선택한 뒤 목표값과 측정자료를 담은 성과관리표를 발표해 주십시오."
    )

    result = evaluate_ksa_measurement(
        _item(target, question=broad_kpi_question, method="발표면접")
    )

    assert result["checks"]["focus_visible"] is False, result
    assert result["checks"]["ksa_type_operationalized"] is False, result
    assert result["passed"] is False, result


def test_generic_type_shape_is_independent_from_focus_grounding() -> None:
    result = evaluate_ksa_measurement(
        {
            "type": "상황면접",
            "question_focus": "외국어 의사소통 능력",
            "question_focus_type": "기술",
            "question_focus_surface": "내부 추적용 표면어",
            "question_source": "openai_api",
            "question_evidence_required": True,
            "question_evidence_id": "ksa_foreign_communication",
            "question": (
                "부서별 사업비와 집행액이 맞지 않는 상황입니다. 원자료를 대조해 어느 "
                "항목을 조정할지 판단하고 예산 수정안을 제시해 주십시오."
            ),
        }
    )

    assert result["checks"]["focus_visible"] is False, result
    assert result["checks"]["ksa_type_operationalized"] is True, result
    assert result["passed"] is False, result
