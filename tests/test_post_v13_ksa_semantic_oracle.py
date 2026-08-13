from __future__ import annotations

import re
from dataclasses import dataclass

import pytest

from app.services.question_quality_orchestrator import (
    RUNTIME_QUESTION_ORCHESTRATION_POLICY,
    evaluate_ksa_measurement,
)


@dataclass(frozen=True)
class PostV13SemanticCase:
    case_id: str
    method: str
    ksa_type: str
    factor: str
    question: str
    oracle: str
    rationale: str
    follow_ups: tuple[str, ...] = ()


# Frozen from a full-text human adjudication of the second out-of-sample
# generation.  The test intentionally does not read reports at runtime.  Only
# the main question can establish KSA necessity; the three B-case follow-ups
# are retained because they document evidence that arrived too late to rescue
# a generic or incomplete main task.
POST_V13_CASES = (
    PostV13SemanticCase(
        "indicator_definition",
        "직무지식면접",
        "지식",
        "지표 운영 정의서에 대한 개념",
        "한 부서의 월별 실적표에는 동일한 참여자가 여러 차례 포함되어 있고, 다른 "
        "부서는 중도 이탈자를 제외해 두 부서의 집계 방식이 서로 다릅니다. 사업 "
        "목적과 측정 기간을 고려해 어떤 실적을 포함하거나 제외할지 판단하고, 기존 "
        "값·적용 기준·수정값이 담긴 정정 기록을 제시해 주세요.",
        "A",
        "실적의 포함·제외와 기간 경계를 적용하고 변경 전후 값과 기준을 기록한다.",
    ),
    PostV13SemanticCase(
        "research_plan_review",
        "상황면접",
        "기술",
        "연구 계획서 검토 기술",
        "공동연구 신청 마감일에 신청서의 연구기간과 연구계획서의 세부 일정이 다르고, "
        "참여기관 담당자는 출장 중이며 연구책임자는 즉시 제출을 요구하고 있습니다. "
        "제출 여부에 대한 첫 판단을 내리고, 불일치 항목·확인 출처·요구 수정사항이 "
        "담긴 보완요청서를 제시해 주세요.",
        "A",
        "신청서와 계획서의 불일치를 검토해 제출 판단과 항목별 보완요청을 만든다.",
    ),
    PostV13SemanticCase(
        "numeric_accuracy",
        "경험면접",
        "태도",
        "수리적 정확도를 확보하려는 자세",
        "보고 마감을 앞두고 부서 원자료와 집계표의 수치가 맞지 않았지만 관계자가 "
        "예정대로 보고해 달라고 요구했던 실제 사례를 말씀해 주세요. 당시 본인이 "
        "보류·수정·반려 중 무엇을 선택해 어떻게 행동했고 어떤 비용을 감수했는지, "
        "검증되지 않은 값·선택 근거·책임 주체를 남긴 판단 기록과 최종 결과를 함께 "
        "설명해 주세요.",
        "A",
        "압박 속 본인의 비용 있는 선택·직접 행동·책임 기록과 결과를 요구한다.",
    ),
    PostV13SemanticCase(
        "plan_actual_gap",
        "발표면접",
        "기술",
        "계획대비 실적 분석 능력",
        "여러 사업의 월별 계획액·집행액·성과 추이가 제시되어 있으며, 한 사업은 "
        "집행이 늘었지만 성과가 급감했습니다. 가장 중요하게 다룰 차이 한 건과 그 "
        "원인을 판정하고, 계획값·실적값·차이 근거가 표시된 분석표 한 장으로 발표해 "
        "주세요.",
        "A",
        "계획·집행·성과 차이의 원인을 판정하고 값과 근거가 대응하는 분석표를 만든다.",
    ),
    PostV13SemanticCase(
        "research_fund_rule",
        "직무지식면접",
        "지식",
        "부처별 연구개발사업 관리규정에 대한 지식",
        "지원기관 안내문은 특정 연구활동비의 집행을 허용하지만 협약서에는 같은 "
        "비용을 제한하고 있으며, 연구책임자는 오늘 지급을 요청하고 있습니다. 두 "
        "문서의 적용 관계와 제한의 예외 인정 여부를 판단하고, 적용 근거·예외 성립 "
        "여부·보완 또는 반려 결론이 담긴 검토 기록을 제시해 주세요.",
        "A",
        "상충 문서의 적용 관계와 예외를 규정 지식으로 판정해 검토 기록에 남긴다.",
    ),
    PostV13SemanticCase(
        "agency_negotiation",
        "토론면접",
        "기술",
        "관련기관ㆍ단체 담당자와의 협상 기술",
        "[토론과제] 공동 연구사업의 중간보고 마감이 임박한 가운데, 주관기관은 일정 "
        "준수를 위해 핵심 자료부터 제출하자는 입장이고 지원기관은 오류 방지를 위해 "
        "증빙 전체를 확인한 뒤 일괄 제출하자는 입장입니다. 양측 주장의 근거가 되는 "
        "사실을 검토하여 수용 범위와 예외가 담긴 협의안을 도출하되, 공동안이 "
        "어렵다면 남은 쟁점과 결정권자에게 이송할 기준을 제시해 주세요.",
        "A",
        "상충하는 기관 요구의 수용 경계와 예외·미합의 이송을 협의 결과로 도출한다.",
    ),
    PostV13SemanticCase(
        "personal_data_protection",
        "인바스켓면접",
        "지식",
        "개인정보보호법",
        "오전 중 세 건이 동시에 도착했습니다. 외부기관은 오늘까지 연구 참여자 "
        "명단과 연락처를 요구했지만 이용 목적과 제공 근거를 밝히지 않았고, "
        "인사담당자는 잘못 포함된 직원 정보를 즉시 삭제해 달라고 요청했으며, "
        "부서장은 해당 명단이 첨부된 보고서를 곧 결재해 달라고 지시했습니다. "
        "본인에게 외부 제공 승인 권한이 없는 조건에서 처리 순서와 각 건의 처리 "
        "주체·보류 여부를 결정하고, 목적·제공 범위·조치 상태가 표시된 처리목록을 "
        "제시해 주세요.",
        "A",
        "목적·제공근거·권한·최소범위를 적용해 세 요청을 분류하고 상태를 기록한다.",
    ),
    PostV13SemanticCase(
        "measurement_framework",
        "창의적 문제해결력면접",
        "태도",
        "성과측정 기준 수립을 위한 체계적 사고",
        "여러 부서의 실적에서 공동사업 성과가 반복해서 빠지거나 중복되지만, "
        "경영진은 보고 지연을 허용하지 않고 부서들은 자신들에게 유리한 집계를 "
        "요구하고 있습니다. 일정 지연과 부서 반발을 감수하더라도 이번 보고에 "
        "적용할 측정 기준을 하나로 결정하고, 측정 차원·포함 및 제외 경계·관찰 "
        "기간이 담긴 기준서를 제시해 주세요.",
        "A",
        "불리한 비용을 감수하며 측정 차원·경계·기간을 가진 기준을 직접 결정한다.",
    ),
    PostV13SemanticCase(
        "closing_report_rules",
        "인바스켓면접",
        "지식",
        "회계보고서 및 분석·검토보고서 작성 요령",
        "오늘 접수된 문서는 세 가지입니다. 결재 권한이 없는 연구비 결산 초안은 "
        "내일 오전 경영진 보고에 사용될 예정이고, 증빙 누락을 알리는 보완 요청은 "
        "오늘 안에 연구책임자에게 보내야 하며, 이미 결재된 보고자료에는 잠정 금액이 "
        "확정값처럼 표시되어 즉시 정정 요청이 들어왔습니다. 담당 결재자는 외부 "
        "일정 중이고 동료 한 명에게 한 건만 맡길 수 있을 때, 문서별 처리 순서와 "
        "직접 처리·위임·보고 대상을 결정하고 그 판단을 담은 검토 기록 한 장을 "
        "제시해 주십시오.",
        "B",
        "주질문은 우선순위와 배정만 요구하고 보고서의 확정·잠정 표시 규칙은 후속에만 있다.",
        (
            "방금 1순위로 정한 문서와 처리 주체를 기준으로, 다른 두 문서를 뒤로 "
            "미뤄도 된다고 본 근거와 그 사이 발생할 수 있는 누락 위험을 설명해 주십시오.",
            "앞서 제시한 검토 기록에서 잠정 금액이나 미확인 증빙을 어떻게 구분해 "
            "표시했는지 불분명하다면, 본문·주석·증빙 연결 방식을 어떻게 보완하시겠습니까?",
            "결재권자가 마감 전까지 복귀하지 못하는 경우, 어느 범위까지 작성하고 "
            "어떤 사항을 승인 대기 상태로 남길지 설명해 주십시오.",
        ),
    ),
    PostV13SemanticCase(
        "review_report_writing",
        "경험면접",
        "기술",
        "분석·검토보고서 작성 능력",
        "예산계획표와 결산 원장의 실적이 서로 맞지 않는 상태에서 보고 마감이 "
        "임박했던 실제 사례를 설명해 주십시오. 당시 본인이 핵심 차이를 어떻게 "
        "판정하고 조치했는지, 그 판단을 반영해 직접 작성한 검토보고서와 실제 활용 "
        "결과까지 말씀해 주십시오.",
        "A",
        "원자료 차이를 판정해 직접 작성한 검토보고서와 실제 활용 결과를 입증한다.",
    ),
    PostV13SemanticCase(
        "civil_form_process",
        "상황면접",
        "기술",
        "관공서 서식 작성과 민원프로세스 파악 기술",
        "관계기관 제출 서식에는 신청 금액의 단위가 천 원으로 표시되어 있지만, 같은 "
        "민원의 접수 기록에는 원 단위 금액과 서로 다른 사업명이 적혀 있습니다. "
        "제출 기한은 오늘이고 기관 담당자에게 한 차례만 확인할 수 있을 때, 어떤 "
        "항목을 먼저 확정 또는 보류할지 판단하고 그 내용을 반영한 수정 서식을 "
        "제시해 주십시오.",
        "A",
        "서식과 민원기록의 단위·사업명 불일치를 확인해 수정 서식에 반영한다.",
    ),
    PostV13SemanticCase(
        "resource_allocation_fairness",
        "발표면접",
        "태도",
        "합리적인 자원분배 기준을 설정하려는 자세",
        "다음 분기 연구지원 예산은 동결되었는데, 세 사업 모두 증액을 요구하고 "
        "있습니다. 한 사업은 집행률이 높지만 수혜자가 적고, 다른 사업은 집행률이 "
        "낮지만 민원이 급증했으며, 나머지 사업은 신규 협약 이행을 위해 최소 인력이 "
        "필요합니다. 일부 부서의 반발과 일정 지연을 감수하더라도 배분 결정을 하나로 "
        "정하고, 공통 판정 기준·사업별 조정량·결정 책임자가 표시된 배분안 한 장을 "
        "발표해 주십시오.",
        "A",
        "제한된 총자원에서 공통 기준과 맞물린 조정량을 비용·책임과 함께 결정한다.",
    ),
    PostV13SemanticCase(
        "objective_evaluation",
        "토론면접",
        "태도",
        "평가에 대한 객관적 자세의 유지",
        "[토론과제] 신규 국제교류 사업의 중간 성과를 심의하는 자리에서 현업 부서는 "
        "참여기관의 질적 성과를 반영해 계속 지원하자고 하고, 평가 담당자는 사전에 "
        "정한 정량 증빙이 부족하므로 지원을 보류하자고 주장합니다. 양측이 확인해야 "
        "할 사실을 검토해 적용할 평가 원칙을 결정하고, 합의 범위 또는 남은 쟁점과 "
        "상위 결정권자에게 넘길 기준이 담긴 토론 결과서 한 장을 제시하세요.",
        "B",
        "집단 평가원칙과 제3자 책임으로 끝나 지원자 자신의 비용·행동·결과 책임이 없다.",
        (
            "방금 수용한 상대 측 주장과 받아들이지 않은 주장을 구분하고, 각각 어떤 "
            "자료로 타당성을 확인하겠습니까?",
            "앞서 정한 평가 원칙 때문에 일정 지연이나 현업 반발이 생긴다면 무엇을 "
            "감수하고 누가 결과에 책임져야 합니까?",
            "공통 원칙을 다른 신규 사업에도 적용할 범위, 예외를 인정할 조건, 다음 "
            "평가에서 준수 여부를 확인할 방법을 제시하세요.",
        ),
    ),
    PostV13SemanticCase(
        "visualization_data_accuracy",
        "창의적 문제해결력면접",
        "태도",
        "데이터의 정확성을 추구하는 태도",
        "월간 성과 화면의 참여기관 수가 원장 자료보다 반복해서 크게 표시되고, 공개 "
        "일정은 오늘이지만 검증에는 담당자 한 명만 투입할 수 있습니다. 게시 지연을 "
        "감수하고 보류할 값과 그대로 사용할 값을 결정한 뒤, 원자료 출처·불일치 "
        "항목·보류 또는 사용 근거가 담긴 판단 기록 한 장을 제시하세요.",
        "B",
        "비용과 보류 선택은 있으나 본인의 직접 실행 및 잘못된 결과의 책임을 요구하지 않는다.",
        (
            "방금 보류한 값에서 오류가 생겼다고 본 가설을 반증하려면 어떤 두 자료를 "
            "어떤 키와 기간으로 대조하겠습니까?",
            "앞서 선택한 검증 방법을 최소 범위로 시험한다면 관찰할 결과와 시험을 "
            "중단할 조건을 어떻게 정하겠습니까?",
            "같은 불일치가 재발하지 않도록 원자료 인수 단계에 남길 검증 흔적과 "
            "책임자를 제시하세요.",
        ),
    ),
    PostV13SemanticCase(
        "data_use_ethics",
        "경험면접",
        "태도",
        "기업 내 데이터 수집 및 활용에 대한 윤리적 태도",
        "성과 분석 마감이 임박한 상황에서 접근권한이나 이용 목적이 불분명한 내부 "
        "자료를 쓰면 업무가 빨라지지만 부적절한 이용이 될 수 있었던 실제 사례를 "
        "설명해 주세요. 본인이 사용·제한·보류 중 무엇을 선택해 어떻게 행동했고 어떤 "
        "결과가 났는지 밝힌 뒤, 자료의 목적·접근 주체·허용한 공유 범위가 남은 이용 "
        "결정 기록을 제시하세요.",
        "A",
        "업무 편의의 포기 속 본인 선택·직접 행동·결과와 이용 경계 기록을 요구한다.",
    ),
    PostV13SemanticCase(
        "document_register",
        "인바스켓면접",
        "기술",
        "문서 대장 기록 능력",
        "오늘 오전, 정오까지 회신해야 하는 외부기관 협조 공문, 이미 등록된 "
        "수신일자가 잘못됐다는 정정 요청, 오후 결재회의에 올릴 미결재 보고서가 "
        "동시에 도착했습니다. 정정 승인과 최종 결재 권한은 상급자에게 있고 상급자는 "
        "오전에 부재합니다. 세 문서의 처리 순서와 처리 주체를 결정하고, 접수번호·현재 "
        "상태·다음 조치가 표시된 접수대장 초안을 제시하세요.",
        "A",
        "권한에 따라 문서를 배정하고 접수번호·상태·다음 조치를 대장에 연결한다.",
    ),
)


def _item(
    case: PostV13SemanticCase,
    *,
    source: str = "openai_api",
    question: str | None = None,
    method: str | None = None,
) -> dict[str, object]:
    return {
        "type": method or case.method,
        "question_focus": case.factor,
        "question_focus_type": case.ksa_type,
        "question_focus_surface": "내부 추적용 표면 힌트",
        "question_source": source,
        "question_evidence_required": True,
        "question_evidence_id": f"ksa_post_v13_{case.case_id}",
        "question": question or case.question,
        "follow_ups": list(case.follow_ups),
    }


def test_post_v13_oracle_is_frozen_and_complete() -> None:
    assert RUNTIME_QUESTION_ORCHESTRATION_POLICY.endswith("_v21")
    assert len(POST_V13_CASES) == 16
    assert len({case.case_id for case in POST_V13_CASES}) == 16
    assert [case.oracle for case in POST_V13_CASES].count("A") == 13
    assert [case.oracle for case in POST_V13_CASES].count("B") == 3
    assert {
        case.case_id for case in POST_V13_CASES if case.oracle == "B"
    } == {
        "closing_report_rules",
        "objective_evaluation",
        "visualization_data_accuracy",
    }
    assert all(len(case.rationale) >= 20 for case in POST_V13_CASES)


@pytest.mark.parametrize("case", POST_V13_CASES, ids=lambda case: case.case_id)
@pytest.mark.parametrize("source", ["openai_api", "codex_cli", "claude_code"])
def test_post_v13_human_oracle_matches_every_provider(
    case: PostV13SemanticCase,
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


@pytest.mark.parametrize("target", POST_V13_CASES, ids=lambda case: case.case_id)
@pytest.mark.parametrize("source", POST_V13_CASES, ids=lambda case: case.case_id)
def test_post_v13_all_pairs_reject_cross_factor_substitution(
    target: PostV13SemanticCase,
    source: PostV13SemanticCase,
) -> None:
    if target.case_id == source.case_id:
        pytest.skip("identity pair is covered by the positive oracle")

    result = evaluate_ksa_measurement(
        _item(target, question=source.question, method=source.method)
    )

    assert result["passed"] is False, result
    assert result["checks"]["focus_visible"] is False, result
    assert result["checks"]["ksa_type_operationalized"] is False, result


@pytest.mark.parametrize(
    "case",
    [case for case in POST_V13_CASES if case.oracle == "B"],
    ids=lambda case: case.case_id,
)
def test_follow_up_only_evidence_cannot_rescue_the_main_question(
    case: PostV13SemanticCase,
) -> None:
    assert len(case.follow_ups) == 3

    result = evaluate_ksa_measurement(_item(case))

    assert result["passed"] is False, result
    assert result["checks"]["focus_visible"] is False, result


@pytest.mark.parametrize(
    "case",
    [case for case in POST_V13_CASES if case.oracle == "A"],
    ids=lambda case: case.case_id,
)
def test_post_v13_a_cases_require_incident_and_output_in_the_main_question(
    case: PostV13SemanticCase,
) -> None:
    clauses = [
        clause.strip()
        for clause in re.split(r"(?<=[.!?])\s+", case.question)
        if clause.strip()
    ]
    assert len(clauses) >= 2

    incomplete_questions = (
        f"{clauses[0]} 이 상황의 배경만 설명해 주세요.",
        clauses[-1],
    )
    for incomplete in incomplete_questions:
        result = evaluate_ksa_measurement(_item(case, question=incomplete))
        assert result["passed"] is False, (case.case_id, incomplete, result)


PARAPHRASED_ARTIFACT_CASES = (
    (
        "indicator_definition",
        "센터별 집계표에서 같은 이용자가 중복 산입되고 이탈자의 처리도 달라 수치가 "
        "어긋납니다. 사업 목적과 관찰 기간에 맞춰 어느 실적을 포함·제외할지 판정하고, "
        "종전 수치·판정 규칙·변경 후 수치를 담은 수정 대장을 작성해 주세요.",
    ),
    (
        "research_plan_review",
        "연구 신청서의 수행 기간과 연구계획의 세부 일정이 어긋났고 제출 기한도 임박했습니다. "
        "제출 가능 여부를 판단한 뒤 차이 항목·확인 근거·수정 요구를 담은 정정 "
        "조치표를 작성해 주세요.",
    ),
    (
        "numeric_accuracy",
        "보고 기한을 지키라는 요구 속에서 원자료와 집계값의 오류를 발견한 실제 "
        "사례를 설명해 주세요. 본인이 일정 준수의 부담을 감수하며 직접 대조해 내린 "
        "결정과 판단 근거·승인 주체·수정 전후 결과를 담은 대조 내역을 말씀해 주세요.",
    ),
    (
        "research_fund_rule",
        "사업별 안내는 연구비 사용을 허용하지만 협약은 같은 집행을 제한해 두 근거가 "
        "충돌합니다. 어느 근거를 적용할지와 예외 조건을 판단하고 적용 근거·보완 또는 "
        "반려 판정·예외 여부가 적힌 집행검토표를 작성해 주세요.",
    ),
    (
        "agency_negotiation",
        "[토론과제] 주관기관과 지원기관이 마감 자료의 선제출 범위를 두고 상반된 "
        "입장을 주장합니다. 사실 근거를 확인해 수용 경계를 합의하고 적용 범위·유보 "
        "쟁점·결정권자 이송 기준을 담은 조정 의사록을 도출해 주세요.",
    ),
    (
        "personal_data_protection",
        "외부기관의 연락처 명단 요청과 잘못 첨부된 개인정보 회수 요청이 동시에 "
        "왔지만 본인에게 제공 승인 권한은 없습니다. 이용 목적·제공 근거·권한과 최소 "
        "항목을 기준으로 처리 순서와 처리 주체를 정하고 목적·허용 범위·보류 상태를 담은 "
        "제공 검토표를 작성해 주세요.",
    ),
    (
        "measurement_framework",
        "공동사업 실적이 누락되거나 중복되지만 책임자는 높은 값을 유지하라고 압박하고 "
        "검토 인력도 부족합니다. 수치 하락을 감수하고 집계 원칙을 채택한 뒤 측정 "
        "단위·산입·제외·집계 기간을 담은 원칙서를 작성해 주세요.",
    ),
    (
        "civil_form_process",
        "기관 제출 양식의 금액 단위와 민원 회신 기록의 단위가 다르고 사업명도 "
        "불일치합니다. 무엇을 먼저 확인해 보완할지 판단하고 일치하도록 고친 정정 "
        "양식을 제시해 주세요.",
    ),
    (
        "data_use_ethics",
        "보고 마감 때문에 이용 목적과 접근 권한이 불명확한 자료를 빨리 쓰라는 요청을 "
        "받은 실제 사례를 설명해 주세요. 본인이 사용을 제한하며 직접 조치한 행동과 "
        "승인 결과를 밝히고 목적·권한 주체·허용한 공유 범위를 담은 접근 검토 대장을 "
        "제시해 주세요.",
    ),
    (
        "document_register",
        "회신 공문과 잘못 등록된 수신처 정정 요청, 미결재 보고서가 동시에 왔고 변경 "
        "승인 권한은 없습니다. 처리 순서와 각 처리 주체를 정하고 문서번호·등록 시각·현재 "
        "상태·후속 조치를 담은 수발신 기록표를 작성해 주세요.",
    ),
)


@pytest.mark.parametrize(
    ("case_id", "question"),
    PARAPHRASED_ARTIFACT_CASES,
    ids=[case[0] for case in PARAPHRASED_ARTIFACT_CASES],
)
def test_v14_accepts_compositional_artifact_paraphrases(
    case_id: str,
    question: str,
) -> None:
    case = next(case for case in POST_V13_CASES if case.case_id == case_id)

    result = evaluate_ksa_measurement(_item(case, question=question))

    assert case.factor not in question
    assert result["passed"] is True, result


ATTITUDE_REMOVAL_CASES = (
    (
        "numeric_accuracy",
        "보고 마감에 원자료와 집계표의 수치가 맞지 않았던 실제 사례를 설명해 주세요. "
        "본인이 관련 원칙을 설명했고 일반 의견·승인 주체를 담은 검토 기록을 "
        "제시해 주세요.",
    ),
    (
        "measurement_framework",
        "공동사업 성과가 누락되거나 중복되고 전담 인력은 한 명입니다. 이번 집계에 "
        "적용할 측정 기준을 결정하고 측정 차원·포함·제외·관찰 기간을 담은 기준서를 "
        "제시해 주세요.",
    ),
    (
        "data_use_ethics",
        "마감이 임박한 때 이용 목적과 접근 권한이 불분명한 자료를 빨리 써 달라는 "
        "요청을 받은 실제 사례를 설명해 주세요. 본인의 사용 판단과 목적·접근 주체·"
        "공유 범위를 담은 이용 결정 기록을 제시해 주세요.",
    ),
)


@pytest.mark.parametrize(
    ("case_id", "question"),
    ATTITUDE_REMOVAL_CASES,
    ids=[case[0] for case in ATTITUDE_REMOVAL_CASES],
)
def test_attitude_does_not_pass_after_cost_action_or_outcome_removal(
    case_id: str,
    question: str,
) -> None:
    case = next(case for case in POST_V13_CASES if case.case_id == case_id)

    result = evaluate_ksa_measurement(_item(case, question=question))

    assert result["passed"] is False, result


@pytest.mark.parametrize(
    ("case_id", "question"),
    (
        (
            "closing_report_rules",
            "결산 초안의 증빙과 금액이 맞지 않고 결재 기한도 임박했습니다. 작성 "
            "기준을 적용해 확정값과 잠정값, 연결 증빙, 승인 필요 여부를 구분하되 "
            "결산검토서를 작성하지 말고 구두로만 설명해 주세요.",
        ),
        (
            "research_plan_review",
            "연구 신청서와 세부 계획의 일정이 다르고 제출 마감도 임박했습니다. 제출 "
            "여부를 판단하고 차이·확인 출처·수정 요구가 담긴 정정 조치표 없이 "
            "설명해 주세요.",
        ),
        (
            "data_use_ethics",
            "마감 압박 속 목적과 권한이 불명확한 자료를 빨리 써 달라는 요청을 받은 "
            "실제 사례에서 본인이 직접 사용을 제한해 조치했고 승인 결과도 "
            "확인했습니다. 목적·접근 주체·공유 범위가 있는 접근 검토 대장을 작성하지 "
            "말고 답해 주세요.",
        ),
    ),
    ids=("closing", "plan-review", "data-ethics"),
)
def test_v14_rejects_negated_artifact_production(
    case_id: str,
    question: str,
) -> None:
    case = next(case for case in POST_V13_CASES if case.case_id == case_id)

    result = evaluate_ksa_measurement(_item(case, question=question))

    assert result["passed"] is False, result


def test_v14_rejects_artifact_cross_swap_and_keyword_salad() -> None:
    plan_case = next(
        case for case in POST_V13_CASES if case.case_id == "research_plan_review"
    )
    cross_swapped = (
        "연구 신청서와 세부 계획의 기간이 다르고 제출 마감도 임박했습니다. 제출 "
        "가능 여부와 차이 항목을 검토한 뒤, 사업별 배정량·예외 사유가 담긴 자원 "
        "배분안을 발표해 주세요."
    )
    salad = (
        "연구 계획 신청서 불일치 제출 검토 확인 보완 요청서 출처 수정 제시해 주세요."
    )

    for invalid in (cross_swapped, salad):
        result = evaluate_ksa_measurement(_item(plan_case, question=invalid))
        assert result["passed"] is False, (invalid, result)
