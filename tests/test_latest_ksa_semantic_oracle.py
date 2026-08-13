from __future__ import annotations

import re
from dataclasses import dataclass

import pytest

from app.services.question_quality_orchestrator import (
    RUNTIME_QUESTION_ORCHESTRATION_POLICY,
    evaluate_ksa_measurement,
)


@dataclass(frozen=True)
class LatestSemanticCase:
    case_id: str
    method: str
    ksa_type: str
    factor: str
    question: str
    oracle: str
    rationale: str


# Frozen independently after a question-text review.  This test deliberately
# does not read the generation report: regenerating a report or copying its
# stale annotations cannot silently relabel the human oracle.
LATEST_CASES = (
    LatestSemanticCase(
        case_id="indicator_definition",
        method="직무지식면접",
        ksa_type="지식",
        factor="지표 운영 정의서에 대한 개념",
        question=(
            "같은 참여자가 두 사업에 중복 집계되고 일부 실적은 기준일 뒤에 "
            "확정되어 부서별 월간 실적표가 서로 다릅니다. 이번 보고에 반영할 "
            "실적의 범위를 어떻게 판정하겠습니까? 포함·제외·중복 처리 결론이 "
            "기록된 정정표 한 장을 제시해 설명해 주세요."
        ),
        oracle="A",
        rationale=(
            "포함·제외, 중복, 기준일을 판정하고 정정표에 남겨 지표 정의를 적용한다."
        ),
    ),
    LatestSemanticCase(
        case_id="research_plan_review",
        method="상황면접",
        ksa_type="기술",
        factor="연구 계획서 검토 기술",
        question=(
            "공동연구 신청 마감일에 신청서의 총연구비와 세부 계획의 합계가 다르고, "
            "참여기관 한 곳의 역할 설명도 비어 있습니다. 연구책임자는 우선 제출해 "
            "달라고 요청하고 확인 가능한 기관 담당자는 오후에만 연락됩니다. 제출 "
            "가능 여부에 대한 첫 판단을 내리고, 불일치 항목·확인 근거·보완 상태가 "
            "담긴 제출 전 보완표를 제시해 주세요."
        ),
        oracle="A",
        rationale=(
            "신청서와 계획의 금액·역할 누락을 검토해 제출 판단과 보완 상태를 만든다."
        ),
    ),
    LatestSemanticCase(
        case_id="numeric_accuracy",
        method="경험면접",
        ksa_type="태도",
        factor="수리적 정확도를 확보하려는 자세",
        question=(
            "마감 지연이나 관계 부서의 반발을 감수하더라도 부서 원자료와 집계표의 "
            "불일치를 바로잡기로 결정했던 실제 사례를 설명해 주세요. 당시 본인이 "
            "내린 결정과 직접 수행한 대조 작업, 수정 전후 값과 승인 흔적이 연결된 "
            "검증 기록을 중심으로 결과까지 말씀해 주세요."
        ),
        oracle="A",
        rationale=(
            "마감·반발 비용 속 직접 검산과 수정 전후 값·승인 흔적을 요구한다."
        ),
    ),
    LatestSemanticCase(
        case_id="plan_actual_gap",
        method="발표면접",
        ksa_type="기술",
        factor="계획대비 실적 분석 능력",
        question=(
            "세 사업의 월별 계획·집행·성과 추이표와 관련 민원 요약을 검토한 결과, "
            "한 사업은 집행액이 계획에 근접하지만 성과가 두 달 연속 크게 "
            "낮아졌습니다. 이 사업에서 가장 중요한 차이의 원인을 하나로 판정하고, "
            "계획값·실적값·원인 근거가 보이는 분석표 한 장으로 발표해 주세요."
        ),
        oracle="A",
        rationale=(
            "계획·집행·성과의 차이 원인을 판정하고 계획값·실적값 분석표로 보인다."
        ),
    ),
    LatestSemanticCase(
        case_id="research_fund_rule",
        method="직무지식면접",
        ksa_type="지식",
        factor="부처별 연구개발사업 관리규정에 대한 지식",
        question=(
            "연구책임자가 제출한 회의비 집행서에는 사업 공통지침상 인정되는 증빙은 "
            "갖춰져 있지만, 지원기관의 해당 사업 안내에서는 참석자와 연구과제의 "
            "관련성을 추가로 확인하도록 정하고 있습니다. 공통지침과 사업별 안내의 "
            "목적·적용 대상·특례 여부를 따져 이 건의 인정, 보완 또는 반려 중 하나를 "
            "판단하고, 적용 근거·판정·요구 증빙이 담긴 검토기록 한 건을 제시해 주세요."
        ),
        oracle="A",
        rationale=(
            "공통지침과 사업별 안내의 적용순서·특례로 집행 판정과 증빙을 결정한다."
        ),
    ),
    LatestSemanticCase(
        case_id="agency_negotiation",
        method="토론면접",
        ksa_type="기술",
        factor="관련기관ㆍ단체 담당자와의 협상 기술",
        question=(
            "[토론과제] 공동 연구사업의 중간보고가 임박한 가운데 주관기관은 일정 "
            "준수를 위해 핵심 결과만 먼저 제출하자는 입장이고, 검증기관은 원자료와 "
            "산출 과정까지 함께 받아야 검토를 시작할 수 있다는 입장입니다. 양측 "
            "주장의 근거를 확인한 뒤 이번 제출에서 수용할 경계를 결정하고, 합의 "
            "범위·유보 사항·책임 주체가 담긴 협의기록을 제시하세요. 합의가 어렵다면 "
            "남은 쟁점과 결정권자에게 넘길 기준을 기록하세요."
        ),
        oracle="A",
        rationale=(
            "상충 기관 요구의 수용 경계와 책임·유보 사항을 협의기록으로 도출한다."
        ),
    ),
    LatestSemanticCase(
        case_id="personal_data_protection",
        method="인바스켓면접",
        ksa_type="지식",
        factor="개인정보보호법",
        question=(
            "오전 중 세 건이 동시에 도착했습니다. 한 시간 안에 보내 달라는 외부 "
            "연구자의 참여자 명단 요청에는 연락처까지 포함되어 있고 이용 목적과 "
            "제공 근거가 적혀 있지 않습니다. 오늘 결재가 필요한 인사 현황 보고서에는 "
            "다른 직원의 평가자료가 잘못 첨부되었으며, 오후까지 처리해야 하는 당사자의 "
            "정보 정정 요청은 담당자가 부재 중입니다. 업무 목적에 필요한 최소 항목만 "
            "권한 있는 사람에게 제공하고, 근거가 불명확한 제공은 보류한다는 원칙에 "
            "따라 처리 순서와 처리 주체를 결정한 뒤, 순서·주체·보류 사유가 담긴 "
            "처리대장을 제시해 주세요."
        ),
        oracle="A",
        rationale=(
            "목적·제공근거·권한·최소범위로 민감자료 요청을 분류하고 기록한다."
        ),
    ),
    LatestSemanticCase(
        case_id="measurement_framework",
        method="창의적 문제해결력면접",
        ksa_type="태도",
        factor="성과측정 기준 수립을 위한 체계적 사고",
        question=(
            "사업 성과 집계에서 동일 참여자가 여러 프로그램에 참여할 때마다 중복 "
            "산입되어 분기별 결과가 흔들리고 있습니다. 부서장은 이번 분기의 높은 "
            "수치를 유지하길 원하지만, 실무팀은 이전 기간과 비교 가능한 기준을 "
            "요구하며 전담 인력은 한 명뿐입니다. 어느 한쪽에 불리한 결과가 나올 수 "
            "있음을 감수하고 이번 집계에 적용할 기준을 하나로 결정한 뒤, 측정 "
            "차원·포함 및 제외 조건·관찰 기간이 담긴 기준표를 제시해 주세요."
        ),
        oracle="A",
        rationale=(
            "불리한 수치를 감수하며 측정차원·포함/제외·관찰기간을 직접 정의한다."
        ),
    ),
    LatestSemanticCase(
        case_id="closing_report_rules",
        method="인바스켓면접",
        ksa_type="지식",
        factor="회계보고서 및 분석·검토보고서 작성 요령",
        question=(
            "오늘 결재 가능한 범위가 팀장 전결까지인 상황에서, 정오까지 답해야 하는 "
            "증빙 보완 요청, 오후 결재 예정인 결산 초안, 내일 기관장 회의에 쓰일 "
            "요약자료가 동시에 도착했습니다. 결산 초안에는 증빙이 확인되지 않은 "
            "금액이 확정값처럼 기재되어 있고 요약자료도 이를 인용하고 있습니다. "
            "무엇을 먼저 처리할지 판단하고, 각 문서의 처리 순서·처리 주체·미확인 "
            "금액의 표시 방식을 담은 처리결정표 한 장을 제시해 주십시오."
        ),
        oracle="A",
        rationale=(
            "미확인 금액의 확정·잠정 경계와 문서별 표시 방식을 직접 적용한다."
        ),
    ),
    LatestSemanticCase(
        case_id="review_report_writing",
        method="경험면접",
        ksa_type="기술",
        factor="분석·검토보고서 작성 능력",
        question=(
            "예산계획표와 결산 원장의 실적이 서로 맞지 않았지만 보고 마감은 임박했던 "
            "실제 사례를 말씀해 주십시오. 본인이 가장 중요하다고 판정한 차이 한 건, "
            "그 판단을 위해 취한 행동, 그리고 계획값·실적값·차이 근거가 드러나도록 "
            "직접 작성한 검토보고서가 어떻게 활용되었는지 결과 증거와 함께 설명해 "
            "주십시오."
        ),
        oracle="A",
        rationale=(
            "원자료 차이를 분석해 직접 작성한 보고서와 의사결정 활용 결과를 묻는다."
        ),
    ),
    LatestSemanticCase(
        case_id="civil_form_process",
        method="상황면접",
        ksa_type="기술",
        factor="관공서 서식 작성과 민원프로세스 파악 기술",
        question=(
            "관계기관 제출 서식에는 접수일과 처리 결과가 필수인데 접수일이 비어 있고, "
            "내부 민원 기록에는 날짜가 있으나 같은 건임을 확인할 식별정보 일부가 "
            "다릅니다. 제출 마감은 오늘이고 담당 기관에는 한 차례만 확인할 수 "
            "있습니다. 내부 기록의 날짜를 서식에 반영할 수 있는지 먼저 판단하고, "
            "항목별 출처·미확정 항목·수정 이력이 표시된 보완 서식 한 부를 제시해 "
            "주십시오."
        ),
        oracle="A",
        rationale=(
            "서식과 민원기록의 동일성·누락을 확인해 출처와 수정이력 있는 보완본을 만든다."
        ),
    ),
    LatestSemanticCase(
        case_id="resource_allocation_fairness",
        method="발표면접",
        ksa_type="태도",
        factor="합리적인 자원분배 기준을 설정하려는 자세",
        question=(
            "다음 분기 총인력과 예산이 동결된 가운데, 연구지원 세 사업이 모두 증액을 "
            "요구하고 있습니다. 한 사업은 최근 실적이 급감했지만 의무 지원 대상이 "
            "많고, 다른 사업은 실적이 높으나 특정 부서만 이용하며, 나머지 사업은 "
            "신규 과제라 비교 실적이 없습니다. 성과가 높은 사업에 몰아 달라는 "
            "경영진의 요구와 최소 서비스 유지를 요구하는 현장 의견이 충돌할 때 "
            "배분을 하나로 결정하고, 공통 기준·사업별 조정량·예외 사유가 보이는 "
            "배분안 한 장을 발표해 주십시오."
        ),
        oracle="A",
        rationale=(
            "압박 속 공통 배분기준과 사업별 조정·예외를 공개적으로 결정한다."
        ),
    ),
    LatestSemanticCase(
        case_id="objective_evaluation",
        method="토론면접",
        ksa_type="태도",
        factor="평가에 대한 객관적 자세의 유지",
        question=(
            "[토론과제] 신규 문화사업의 성과를 판정하려는데, 기획부서는 모든 사업에 "
            "정량 증빙을 엄격히 적용해야 한다고 주장하고 현업부서는 참여자 특성과 "
            "장기 효과를 반영하지 않으면 성과가 왜곡된다고 맞섭니다. 두 입장을 "
            "검토한 뒤 어느 조건에서 어떤 근거를 우선할지 하나로 판단하고, 확인할 "
            "사실과 적용 범위가 담긴 공동 평가 원칙안 한 장을 제시하십시오. 합의가 "
            "어렵다면 남은 쟁점과 결정권자에게 넘길 기준을 원칙안에 표시하십시오."
        ),
        oracle="A",
        rationale=(
            "정량근거와 현장맥락을 조건부 비교해 적용범위가 있는 공동 원칙을 만든다."
        ),
    ),
    LatestSemanticCase(
        case_id="visualization_data_accuracy",
        method="창의적 문제해결력면접",
        ksa_type="태도",
        factor="데이터의 정확성을 추구하는 태도",
        question=(
            "월간 성과 화면을 만들 때마다 같은 사업의 건수가 설문 추출본, 업무시스템 "
            "내보내기 파일, 부서 집계표에서 서로 다르게 나타납니다. 담당자는 게시 "
            "일정을 지키기 위해 한 출처만 쓰자고 하지만, 검증에 투입할 수 있는 "
            "인원은 한 명뿐입니다. 가장 가능성이 높은 원인 하나를 판단하고, 대조 "
            "항목·소규모 시험·중단 기준이 담긴 검증 실험표 한 장을 제시하십시오."
        ),
        oracle="B",
        rationale=(
            "원인·실험·중단만 묻고 게시 지연 등 비용 있는 정확성 선택과 책임을 "
            "요구하지 않아 일반 문제해결 기술로 대체 가능하다."
        ),
    ),
    LatestSemanticCase(
        case_id="data_use_ethics",
        method="경험면접",
        ksa_type="태도",
        factor="기업 내 데이터 수집 및 활용에 대한 윤리적 태도",
        question=(
            "성과 보고 마감이 임박한 상황에서, 승인받은 이용 목적이나 접근 범위를 "
            "벗어날 수 있는 내부 자료를 쓰면 업무가 빨라지는 유혹을 받은 실제 사례를 "
            "설명해 주십시오. 당시 본인이 내린 사용 여부 판단과 직접 취한 행동, 감수한 "
            "일정상 불이익, 최종 결과를 말하고, 그 판단을 남긴 이용 판단 기록의 핵심 "
            "내용을 제시하십시오."
        ),
        oracle="A",
        rationale=(
            "목적·권한 밖 사용 유혹에서 직접 선택·비용·결과와 판단 기록을 묻는다."
        ),
    ),
    LatestSemanticCase(
        case_id="document_register",
        method="인바스켓면접",
        ksa_type="기술",
        factor="문서 대장 기록 능력",
        question=(
            "오늘 회신해야 하는 외부기관 협조 공문, 이미 접수번호가 부여됐지만 "
            "발신기관명이 잘못된 정정 요청, 부서장 결재가 끝나지 않은 내일 보고 "
            "문서가 동시에 도착했습니다. 본인은 접수와 등록은 할 수 있지만 정정 "
            "승인과 최종 결재 권한은 없습니다. 세 문서의 처리 순서와 각 처리 주체를 "
            "하나로 결정하고, 접수시각·현재 상태·보류 또는 이관 사유가 보이는 접수 "
            "등록표를 제시하십시오."
        ),
        oracle="A",
        rationale=(
            "접수번호·시각·상태·보류/이관 사유를 권한별 처리와 함께 등록한다."
        ),
    ),
)


def _item(
    case: LatestSemanticCase,
    *,
    question: str | None = None,
    method: str | None = None,
    source: str = "openai_api",
) -> dict[str, object]:
    return {
        "type": method or case.method,
        "question_focus": case.factor,
        "question_focus_type": case.ksa_type,
        "question_focus_surface": "내부 추적용 표면 힌트",
        "question_source": source,
        "question_evidence_required": True,
        "question_evidence_id": f"ksa_latest_{case.case_id}",
        "question": question or case.question,
    }


def test_latest_oracle_is_complete_and_independently_explained() -> None:
    assert RUNTIME_QUESTION_ORCHESTRATION_POLICY.endswith("_v21")
    assert len(LATEST_CASES) == 16
    assert len({case.case_id for case in LATEST_CASES}) == 16
    assert [case.oracle for case in LATEST_CASES].count("A") == 15
    assert [case.oracle for case in LATEST_CASES].count("B") == 1
    assert all(len(case.rationale) >= 20 for case in LATEST_CASES)


@pytest.mark.parametrize("case", LATEST_CASES, ids=lambda case: case.case_id)
@pytest.mark.parametrize("source", ["openai_api", "codex_cli", "claude_code"])
def test_latest_human_oracle_matches_every_provider_path(
    case: LatestSemanticCase,
    source: str,
) -> None:
    result = evaluate_ksa_measurement(_item(case, source=source))

    assert case.factor not in case.question
    assert result["passed"] is (case.oracle == "A"), (case.rationale, result)
    if case.oracle == "A":
        assert all(result["checks"].values()), (case.rationale, result)
    else:
        assert result["checks"]["focus_visible"] is False, result
        assert result["checks"]["ksa_type_operationalized"] is False, result


@pytest.mark.parametrize("target", LATEST_CASES, ids=lambda case: case.case_id)
@pytest.mark.parametrize("source", LATEST_CASES, ids=lambda case: case.case_id)
def test_latest_all_pairs_reject_cross_factor_substitution(
    target: LatestSemanticCase,
    source: LatestSemanticCase,
) -> None:
    if target.case_id == source.case_id:
        pytest.skip("identity pair is covered by the positive oracle")

    result = evaluate_ksa_measurement(
        _item(target, question=source.question, method=source.method)
    )

    assert result["checks"]["focus_visible"] is False, result
    assert result["checks"]["ksa_type_operationalized"] is False, result
    assert result["passed"] is False, result


@pytest.mark.parametrize("case", LATEST_CASES, ids=lambda case: case.case_id)
def test_latest_semantic_bridge_rejects_incident_only_and_output_only(
    case: LatestSemanticCase,
) -> None:
    clauses = [
        clause.strip()
        for clause in re.split(r"(?<=[.!?])\s+", case.question)
        if clause.strip()
    ]
    assert len(clauses) >= 2

    incident_only = f"{clauses[0]} 이 상황의 배경만 설명해 주세요."
    output_only = clauses[-1]

    for incomplete in (incident_only, output_only):
        result = evaluate_ksa_measurement(_item(case, question=incomplete))
        assert result["passed"] is False, (case.case_id, incomplete, result)


METAMORPHIC_ARTIFACT_CASES = (
    (
        "indicator_definition",
        "센터별 집계표에서 같은 참여자가 거듭 산입되고 마감 뒤 확정분 때문에 "
        "수치가 다릅니다. 이번 보고 대상의 범위를 판정한 뒤 포함 대상·중복 제외·"
        "관찰 기간을 적은 적용 판정표를 작성해 주세요.",
    ),
    (
        "plan_actual_gap",
        "분기 계획·집행·성과 추이에서 집행은 목표선에 가깝지만 산출량은 크게 "
        "낮아 차이가 생겼습니다. 핵심 격차의 원인을 분석하고 목표선·실적치·원인이 "
        "대응하는 원인 분석표로 발표해 주세요.",
    ),
    (
        "measurement_framework",
        "회원 중복 산입으로 성과 집계가 흔들리지만 책임자는 높은 값을 유지하라고 "
        "압박하고 검토 인력도 부족합니다. 비교 가능성을 위해 집계 원칙을 수립하고 "
        "측정 단위·산입 및 제외·집계 기간을 담은 집계 원칙표를 작성해 주세요.",
    ),
    (
        "closing_report_rules",
        "결산 초안에 증빙이 확인되지 않은 금액이 확정값처럼 적혀 있고 결재 기한도 "
        "임박했습니다. 처리 순서·담당 주체·미확인 금액 표시 방식을 정한 보고 "
        "처리표를 제시해 주세요.",
    ),
    (
        "visualization_data_accuracy",
        "같은 지표가 원자료와 두 추출본에서 서로 다르지만 게시 일정은 임박했고 "
        "담당 인력은 한 명입니다. 정확성을 위해 게시 연기를 선택하고 일정 지연의 "
        "책임을 감수하겠다는 결정을 밝힌 뒤, 원인 가설을 검증할 비교 항목·표본 "
        "시험·판정 경계를 담은 대조 시험표를 작성해 주세요.",
    ),
    (
        "document_register",
        "회신 공문과 정정 요청, 미결재 보고서가 동시에 왔고 발신번호 오류를 바꿀 "
        "권한은 없습니다. 처리 순서와 각 처리 주체를 정하고 등록 시각·처리 상태·"
        "이관 사유를 남긴 문서 등록부를 제시해 주세요.",
    ),
)


@pytest.mark.parametrize(
    ("case_id", "question"),
    METAMORPHIC_ARTIFACT_CASES,
    ids=[case[0] for case in METAMORPHIC_ARTIFACT_CASES],
)
def test_semantic_bridge_accepts_paraphrases_with_different_artifact_names(
    case_id: str,
    question: str,
) -> None:
    case = next(case for case in LATEST_CASES if case.case_id == case_id)
    result = evaluate_ksa_measurement(_item(case, question=question))

    assert case.factor not in question
    assert result["passed"] is True, result


NEGATED_OUTPUT_CASES = (
    (
        "indicator_definition",
        "포함·제외·중복 처리 결론이 기록된 정정표 없이 구두로만 설명해 주세요.",
    ),
    (
        "plan_actual_gap",
        "계획값·실적값·원인 근거가 보이는 분석표를 만들지 말고 구두로 설명해 주세요.",
    ),
    (
        "visualization_data_accuracy",
        "대조 항목·소규모 시험·중단 기준이 담긴 검증 실험표 없이 답해 주세요.",
    ),
    (
        "document_register",
        "접수시각·현재 상태·이관 사유가 보이는 접수 등록표를 작성하지 말고 답해 주세요.",
    ),
)


@pytest.mark.parametrize(
    ("case_id", "negated_tail"),
    NEGATED_OUTPUT_CASES,
    ids=[case[0] for case in NEGATED_OUTPUT_CASES],
)
def test_latest_bridge_does_not_count_negated_artifact_production(
    case_id: str,
    negated_tail: str,
) -> None:
    case = next(case for case in LATEST_CASES if case.case_id == case_id)
    clauses = re.split(r"(?<=[.!?])\s+", case.question)
    question = " ".join([*clauses[:-1], negated_tail])

    result = evaluate_ksa_measurement(_item(case, question=question))

    assert result["passed"] is False, result
    assert result["checks"]["focus_visible"] is False, result


def test_latest_bridge_rejects_factor_keywords_as_a_heading_list() -> None:
    case = LATEST_CASES[0]
    keyword_salad = (
        "실적표 중복 범위 기준일 포함 제외 판정 정정표 제시해 주세요."
    )

    result = evaluate_ksa_measurement(_item(case, question=keyword_salad))

    assert result["passed"] is False, result
    assert result["checks"]["focus_visible"] is False, result
