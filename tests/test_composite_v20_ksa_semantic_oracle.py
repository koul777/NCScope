from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.services.question_quality_orchestrator import (
    RUNTIME_QUESTION_ORCHESTRATION_POLICY,
    evaluate_ksa_measurement,
)


@dataclass(frozen=True)
class CompositeCase:
    case_id: str
    method: str
    ksa_type: str
    factor: str
    question: str
    paraphrase: str
    rationale: str


# Independent source fixture: no runtime report loading or embedded automated
# annotation.  The seven cases were adjudicated from their visible task alone.
COMPOSITE_V20_CASES = (
    CompositeCase(
        "indicator_definition",
        "직무지식면접",
        "지식",
        "지표 운영 정의서에 대한 개념",
        "부서 실적표에는 같은 교육 프로그램 참여자가 월별로 중복 합산되고 기관 "
        "집계표에는 연간 고유 참여자로 계산되어 두 수치가 다릅니다. 어느 집계가 "
        "공식 보고에 적합한지 판정하고, 적용 대상·측정 기간·중복 처리 규칙이 표시된 "
        "판정표 한 장을 제시해 주세요.",
        "사업 집계에서 같은 이용자가 매월 반복 산입되지만 연간 보고는 고유 인원으로 "
        "계산해 값이 차이 납니다. 보고용 수치를 결정하고 집계 대상·관찰 기간·중복 "
        "산입 원칙을 담은 적용 판정표를 작성해 주세요.",
        "서로 다른 집계값에 지표 정의의 대상·기간·중복 규칙을 적용해 공식값을 "
        "판정하고 그 세 경계를 산출물에 남긴다.",
    ),
    CompositeCase(
        "agency_negotiation",
        "토론면접",
        "기술",
        "관련기관ㆍ단체 담당자와의 협상 기술",
        "[토론과제] 지원기관은 제출 일정을 위해 현재 자료를 우선 접수하자는 "
        "입장이고 참여기관은 검증되지 않은 수치가 공식 기록이 될 위험 때문에 확인 "
        "후 제출하자는 입장입니다. 양측의 일정상 한계와 자료 사용 위험을 검토해 "
        "수용 가능한 협의 경계를 정하고, 합의 범위·유보 항목·미합의 시 이송 기준이 "
        "담긴 협의기록을 제시해 주세요.",
        "[토론과제] 관리기관은 기한 내 일부 자료를 받으려 하고 수행기관은 오류 "
        "영향을 이유로 검증 완료 뒤 제출하자고 합니다. 두 기관의 제약과 위험을 "
        "비교해 수용 범위를 결정하고, 교환 조건·남은 쟁점·결정권자 이송 경계를 "
        "담은 조정 의사록으로 토론해 주세요.",
        "양측의 양립하기 어려운 제약과 위험을 근거로 수용 경계를 정하고 합의·유보·"
        "이송 필드가 있는 협의기록을 만든다.",
    ),
    CompositeCase(
        "measurement_framework",
        "창의적 문제해결력면접",
        "태도",
        "성과측정 기준 수립을 위한 체계적 사고",
        "성과에서 공동 참가자가 중복 집계되거나 온라인 참가자가 누락되는 현상이 "
        "반복되고, 기존 산식 유지와 즉시 변경의 장단점이 맞서며 담당자는 한 "
        "명입니다. 다음 분기에 시험할 보정 방식을 선택하고, 원인 가설·참가자 포함 "
        "및 중복 처리 규칙·채택 또는 중단 조건을 담은 검증안을 제시해 주세요.",
        "실적의 누락과 중복이 반복되지만 기존 비교 연속성도 유지해야 하고 시험 "
        "인력은 부족합니다. 적용할 집계 방식을 결정하고, 검증 가설·산입 대상·중복 "
        "원칙·수정 또는 중단 경계를 적은 보정 시험표를 작성해 주세요.",
        "측정 방식의 상충효과 속에서 보정안을 선택하고 포함·중복 규칙을 작은 시험과 "
        "결과 의존 채택·중단 조건에 연결한다.",
    ),
    CompositeCase(
        "civil_form_process",
        "상황면접",
        "기술",
        "관공서 서식 작성과 민원프로세스 파악 기술",
        "관계기관 제출 서식에는 책임자 확인 표시가 빠지고 같은 건의 민원 회신에는 "
        "서로 다른 처리 상태가 적혀 있으며 기한은 오늘입니다. 최종 승인 권한이 "
        "없을 때 최초 보완 방향을 결정하고 필수 입력란·첨부 증빙·처리 단계가 "
        "표시된 보완본을 제시해 주세요.",
        "기관 양식의 필수 확인란이 누락되고 민원 기록의 상태도 서로 다르며 제출 "
        "마감이 임박했습니다. 승인 권한 범위에서 첫 수정 방향을 판단하고 필수 "
        "항목·근거 첨부·진행 상태를 연결한 정정본을 작성해 주세요.",
        "서식 누락과 민원 상태 충돌에 권한 경계를 적용해 첫 보완을 판단하고 입력란·"
        "증빙·처리 단계가 결속된 실제 보완본을 만든다.",
    ),
    CompositeCase(
        "objective_evaluation",
        "토론면접",
        "태도",
        "평가에 대한 객관적 자세의 유지",
        "[토론과제] 사업 수치 목표는 충족했지만 현업은 장기 참여 기반이라는 정성 "
        "자료도 반영해 달라고 요청하고, 정량 기준의 동일 적용과 현장 자료 비중 확대 "
        "주장이 맞섭니다. 확인할 사실을 짚은 뒤 공통 평가 원칙 하나를 선택하고, "
        "남은 쟁점과 결정권자에게 넘길 기준을 포함한 평가 원칙표를 제시해 토론해 "
        "주세요.",
        "[토론과제] 계량 성과와 장기 현장 효과를 두고 두 부서의 평가 입장이 "
        "충돌합니다. 검증할 자료를 구분한 뒤 공동 판정 원칙을 채택하고, 합의되지 "
        "않은 범위와 상위 결정자 이송 조건을 담은 판정 원칙표로 토론해 주세요.",
        "정량과 정성 근거가 충돌하는 상황에서 공통 원칙을 선택하고 미합의 범위와 "
        "권한자 이송 조건을 기록해 특정 답을 유도하지 않는다.",
    ),
    CompositeCase(
        "visualization_data_accuracy",
        "창의적 문제해결력면접",
        "태도",
        "데이터의 정확성을 추구하는 태도",
        "성과 화면의 참여자 수가 원천 시스템보다 반복적으로 크게 나타나며 수작업 "
        "병합 오류와 집계 기간 차이라는 설명이 맞서고 있습니다. 담당 인력은 한 "
        "명이고 화면 갱신은 내일입니다. 먼저 검증할 원인 하나를 선택하고, 사용 가능 "
        "범위·예상 오류 영향·채택 또는 중단 기준을 담은 소규모 검증 기록표를 제시해 "
        "주세요.",
        "대시보드 값이 원자료보다 계속 높고 중복 결합과 기준 기간 차이라는 가설이 "
        "있지만 점검 인력과 게시 기한이 제한됩니다. 우선 확인할 설명을 결정하고, "
        "적용 범위·오류 영향·수정 또는 중단 경계를 담은 간이 검증표를 작성해 주세요.",
        "제약 아래 검증 우선순위를 선택하면서 데이터 사용 경계와 오류 귀결, 결과에 "
        "따른 중단 조건까지 같은 기록에 회계한다.",
    ),
    CompositeCase(
        "document_register",
        "인바스켓면접",
        "기술",
        "문서 대장 기록 능력",
        "오늘 회신할 외부기관 공문, 등록 문서의 수신 부서 정정 요청, 결재권자 확인이 "
        "필요한 보고서가 동시에 도착했고 본인은 접수와 단순 정정만 할 수 있습니다. "
        "세 문서의 처리 순서와 각 처리 주체를 결정하고, 문서 식별정보·현재 처리 "
        "상태·정정 또는 보류 이력이 연결된 접수대장을 제시해 주세요.",
        "회신 기한이 다른 협조문, 수신처 변경 요청, 승인 대기 보고서가 함께 "
        "도착했지만 담당 권한은 등록과 정정에 한정됩니다. 문서별 우선순위와 담당 "
        "주체를 정하고, 접수번호·등록 상태·변경 이력을 연결한 문서 대장을 작성해 "
        "주세요.",
        "마감과 권한이 다른 문서의 순서·주체를 결정하고 식별자·현재 상태·변경 이력이 "
        "연결된 대장을 직접 산출한다.",
    ),
)


def _item(
    case: CompositeCase,
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
        "question_evidence_id": f"ksa_composite_v20_{case.case_id}",
        "question": question or case.question,
        "follow_ups": ["추가 근거가 나오면 판단을 어떻게 바꾸겠습니까?"] * 3,
        "evaluation_points": ["관찰 가능한 판단과 산출물을 제시한다."] * 4,
    }


def _case(case_id: str) -> CompositeCase:
    return next(case for case in COMPOSITE_V20_CASES if case.case_id == case_id)


def test_composite_v20_oracle_is_frozen_and_complete() -> None:
    assert RUNTIME_QUESTION_ORCHESTRATION_POLICY.endswith("_v21")
    assert len(COMPOSITE_V20_CASES) == 7
    assert len({case.case_id for case in COMPOSITE_V20_CASES}) == 7
    assert all(len(case.rationale) >= 40 for case in COMPOSITE_V20_CASES)
    assert all(case.factor not in case.question for case in COMPOSITE_V20_CASES)
    assert "감수" not in "".join(case.question for case in COMPOSITE_V20_CASES)


@pytest.mark.parametrize("case", COMPOSITE_V20_CASES, ids=lambda case: case.case_id)
@pytest.mark.parametrize("source", ["openai_api", "codex_cli", "claude_code"])
def test_composite_v20_human_a_passes_every_provider(
    case: CompositeCase,
    source: str,
) -> None:
    result = evaluate_ksa_measurement(_item(case, source=source))
    assert result["passed"] is True, (case.rationale, result)


@pytest.mark.parametrize("case", COMPOSITE_V20_CASES, ids=lambda case: case.case_id)
@pytest.mark.parametrize("source", ["openai_api", "codex_cli", "claude_code"])
def test_composite_v20_accepts_relational_paraphrases(
    case: CompositeCase,
    source: str,
) -> None:
    result = evaluate_ksa_measurement(
        _item(case, question=case.paraphrase, source=source)
    )
    assert result["passed"] is True, (case.rationale, result)


@pytest.mark.parametrize("target", COMPOSITE_V20_CASES, ids=lambda case: case.case_id)
@pytest.mark.parametrize("source", COMPOSITE_V20_CASES, ids=lambda case: case.case_id)
def test_composite_v20_rejects_all_cross_factor_swaps(
    target: CompositeCase,
    source: CompositeCase,
) -> None:
    if target.case_id == source.case_id:
        pytest.skip("identity is covered by positive oracle")
    result = evaluate_ksa_measurement(
        _item(target, question=source.question, method=source.method)
    )
    assert result["checks"]["focus_visible"] is False, result
    assert result["passed"] is False, result


REMOVALS = (
    ("indicator_definition", "definition-fields", "적용 대상·측정 기간·중복 처리 규칙", "검토 의견"),
    (
        "agency_negotiation",
        "boundary",
        "수용 가능한 협의 경계를 정하고, 합의 범위·유보 항목·미합의 시 이송 기준이 "
        "담긴 협의기록",
        "양측 의견을 요약하고, 일반 입장만 담긴 회의 개요",
    ),
    ("agency_negotiation", "risk", "일정상 한계와 자료 사용 위험", "일반 입장"),
    (
        "measurement_framework",
        "choice",
        "다음 분기에 시험할 보정 방식을 선택하고, 원인 가설·참가자 포함 및 중복 "
        "처리 규칙·채택 또는 중단 조건",
        "기존 자료를 검토하고, 원인 가설·참가자 포함 및 중복 처리 규칙·관찰 항목",
    ),
    ("measurement_framework", "rule-result", "참가자 포함 및 중복 처리 규칙·채택 또는 중단 조건", "회의 의견"),
    ("civil_form_process", "form-fields", "필수 입력란·첨부 증빙·처리 단계", "검토 의견"),
    (
        "objective_evaluation",
        "principle-choice",
        "확인할 사실을 짚은 뒤 공통 평가 원칙 하나를 선택하고, 남은 쟁점과 "
        "결정권자에게 넘길 기준을 포함한 평가 원칙표",
        "확인할 사실을 짚은 뒤 두 입장을 요약하고, 일반 의견을 포함한 회의 개요",
    ),
    ("objective_evaluation", "escalation", "남은 쟁점과 결정권자에게 넘길 기준", "참여자 의견"),
    ("visualization_data_accuracy", "use-consequence", "사용 가능 범위·예상 오류 영향·채택 또는 중단 기준", "검증 의견"),
    ("document_register", "actor-decision", "처리 순서와 각 처리 주체를 결정하고", "문서 제목을 읽고"),
    ("document_register", "trace-fields", "문서 식별정보·현재 처리 상태·정정 또는 보류 이력", "문서 종류"),
)


@pytest.mark.parametrize(("case_id", "dimension", "old", "new"), REMOVALS)
def test_composite_v20_rejects_relation_removals(
    case_id: str, dimension: str, old: str, new: str
) -> None:
    case = _case(case_id)
    assert old in case.question
    result = evaluate_ksa_measurement(
        _item(case, question=case.question.replace(old, new, 1))
    )
    assert result["passed"] is False, (dimension, result)


NEGATIONS = (
    (
        "indicator_definition",
        "어느 집계가 공식 보고에 적합한지 판정하고, 적용 대상·측정 기간·중복 처리 "
        "규칙이 표시된 판정표 한 장을 제시해 주세요.",
        "공식 집계를 판정하지 말고, 두 수치의 일반 개요만 말해 주세요.",
    ),
    (
        "agency_negotiation",
        "수용 가능한 협의 경계를 정하고, 합의 범위·유보 항목·미합의 시 이송 기준이 "
        "담긴 협의기록을 제시해 주세요.",
        "협의 경계를 정하지 말고, 양측 의견만 요약해 주세요.",
    ),
    (
        "measurement_framework",
        "다음 분기에 시험할 보정 방식을 선택하고, 원인 가설·참가자 포함 및 중복 "
        "처리 규칙·채택 또는 중단 조건을 담은 검증안을 제시해 주세요.",
        "보정 방식을 선택하지 말고, 관찰된 현상만 요약해 주세요.",
    ),
    (
        "civil_form_process",
        "최초 보완 방향을 결정하고 필수 입력란·첨부 증빙·처리 단계가 표시된 보완본을 "
        "제시해 주세요.",
        "보완 방향을 결정하지 말고 구두로 일반 안내만 해 주세요.",
    ),
    (
        "objective_evaluation",
        "확인할 사실을 짚은 뒤 공통 평가 원칙 하나를 선택하고, 남은 쟁점과 "
        "결정권자에게 넘길 기준을 포함한 평가 원칙표를 제시해 토론해 주세요.",
        "평가 원칙을 선택하지 말고 두 입장의 개요만 토론해 주세요.",
    ),
    (
        "visualization_data_accuracy",
        "먼저 검증할 원인 하나를 선택하고, 사용 가능 범위·예상 오류 영향·채택 또는 "
        "중단 기준을 담은 소규모 검증 기록표를 제시해 주세요.",
        "검증 원인을 선택하지 말고 현상만 요약해 주세요.",
    ),
    (
        "document_register",
        "세 문서의 처리 순서와 각 처리 주체를 결정하고, 문서 식별정보·현재 처리 "
        "상태·정정 또는 보류 이력이 연결된 접수대장을 제시해 주세요.",
        "처리 순서나 주체를 결정하지 말고 문서 제목만 나열해 주세요.",
    ),
)


@pytest.mark.parametrize(("case_id", "old", "new"), NEGATIONS)
def test_composite_v20_rejects_negated_operations(
    case_id: str, old: str, new: str
) -> None:
    case = _case(case_id)
    assert old in case.question
    result = evaluate_ksa_measurement(
        _item(case, question=case.question.replace(old, new, 1))
    )
    assert result["passed"] is False, result


@pytest.mark.parametrize("case", COMPOSITE_V20_CASES, ids=lambda case: case.case_id)
@pytest.mark.parametrize("source", ["openai_api", "codex_cli", "claude_code"])
def test_composite_v20_rejects_keyword_salad(
    case: CompositeCase, source: str
) -> None:
    salad = f"{case.factor} 상황 근거 판단 선택 결과 기록 표 제시해 주세요."
    result = evaluate_ksa_measurement(_item(case, question=salad, source=source))
    assert result["passed"] is False, result
