from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.services.question_quality_orchestrator import (
    RUNTIME_QUESTION_ORCHESTRATION_POLICY,
    evaluate_ksa_measurement,
)


@dataclass(frozen=True)
class FinalV17Case:
    case_id: str
    method: str
    ksa_type: str
    factor: str
    question: str
    follow_ups: tuple[str, str, str]
    evaluation_points: tuple[str, str, str, str]
    oracle: str
    rationale: str


# Human oracle frozen independently from the final fresh generation.  The
# fixture intentionally contains no report path or runtime report loader.
FINAL_V17_CASES = (
    FinalV17Case(
        "indicator_definition",
        "직무지식면접",
        "지식",
        "지표 운영 정의서에 대한 개념",
        "두 부서가 같은 참여자를 각각 연인원과 실인원으로 집계했고, 중도 이탈자와 "
        "공동행사 참여자의 처리도 서로 다른 월별 실적표가 제출되었습니다. 공식 산정 "
        "원칙을 확정할 때 포함 범위와 중복 처리의 예외를 어떻게 판정할지 설명하고, "
        "적용 기준·예외·수정 이력을 담은 기준 정리표 한 장을 제시해 주십시오.",
        (
            "방금 적용하겠다고 한 기준이 공동행사 참여자에게도 타당하다는 근거는 "
            "무엇이며, 그 근거가 불명확하면 어떻게 표시하겠습니까?",
            "앞서 제시한 예외 가운데 원래 집계 목적을 왜곡할 위험이 가장 큰 것은 "
            "무엇이고, 기준 정리표에서 그 위험을 어떻게 드러내겠습니까?",
            "수정된 실적표가 같은 기준으로 다시 산출되었는지 확인할 때 어떤 원자료와 "
            "변경 기록을 대조하겠습니까?",
        ),
        (
            "집계 목적에 맞춰 포함 대상과 제외 대상을 구분하는 논리",
            "동일 참여자의 중복 여부를 판별하는 기준",
            "예외 적용 사유와 오류 위험을 구체적으로 설명하는 능력",
            "적용 기준과 변경 흔적을 확인할 수 있는 기준 정리표의 완결성",
        ),
        "A",
        "실적 불일치에 산정 원칙을 적용해 포함·중복·예외를 판정하고 수정 이력이 있는 "
        "독립 산출물을 요구한다.",
    ),
    FinalV17Case(
        "research_plan_review",
        "상황면접",
        "기술",
        "연구 계획서 검토 기술",
        "공동연구 신청 마감이 오늘인데 신청서에는 참여기관이 세 곳으로, 연구 "
        "내용서에는 두 곳으로 기재되어 있고 한 기관의 역할과 예산 근거도 빠져 "
        "있습니다. 연구책임자는 우선 제출을 요구하고 담당 기관에는 연락할 수 있는 "
        "상황입니다. 제출 가능 여부에 관한 첫 판단을 내리고, 문서별 불일치·확인 "
        "근거·요청할 수정 내용을 담은 보완요청서 한 장을 작성해 주십시오.",
        (
            "방금 첫 판단에서 우선 확인하겠다고 한 항목이 해결되지 않는다면 제출 "
            "여부와 보완요청 내용을 어떻게 바꾸겠습니까?",
            "앞서 선택한 조치에서 참여기관의 역할과 비용 간 연결을 빠뜨렸다면, 어느 "
            "자료를 대조해 어떤 문구를 고치겠습니까?",
            "수정본을 받은 뒤 신청서와 연구 내용서가 일치한다고 판정할 최소 확인 "
            "항목은 무엇입니까?",
        ),
        (
            "서로 다른 문서의 참여기관 정보를 대응시켜 불일치를 식별하는 능력",
            "마감 압박 속에서도 제출 가능 여부를 근거 있게 판정하는 능력",
            "누락된 역할과 비용 근거를 연결하여 구체적인 수정을 요구하는 능력",
            "수정본의 문서 간 일치와 누락 해소를 확인하는 방법",
        ),
        "A",
        "신청서와 계획 내용을 역할·예산 차원으로 대조하고 제출 판단과 구체적 "
        "보완요청서를 함께 산출한다.",
    ),
    FinalV17Case(
        "numeric_accuracy",
        "경험면접",
        "태도",
        "수리적 정확도를 확보하려는 자세",
        "성과 집계 마감 직전에 부서 원자료와 취합표의 값이 맞지 않는 사실을 직접 "
        "발견했던 사례를 말씀해 주십시오. 일정 지연이나 관계 부서의 반발을 "
        "감수하면서 본인이 보류 또는 수정 중 무엇을 선택해 직접 처리했는지, 그 "
        "선택에 대한 결과 책임과 수정 전후 값·근거 자료·승인 흔적이 남은 판단 기록 "
        "한 건을 중심으로 설명해 주십시오.",
        (
            "방금 언급한 불일치를 발견하게 한 대조 과정에서 어떤 단위·기간·합계 "
            "관계를 확인했으며, 본인이 직접 한 검산은 무엇입니까?",
            "앞서 말씀하신 선택으로 실제 감수한 일정 또는 관계상의 비용은 "
            "무엇이었고, 그럼에도 그 행동을 택한 이유는 무엇입니까?",
            "답변에 수정 전후 결과나 승인 증거가 분명하지 않다면, 당시 기록 중 "
            "무엇으로 오류 정정과 본인의 책임 이행을 입증할 수 있습니까?",
        ),
        (
            "실제 불일치의 대상과 발견 경위를 구체적으로 설명하는 정도",
            "단위·기간·합계 관계를 대조하고 직접 검산한 행동",
            "압박 속에서 정확한 결과를 위해 비용을 감수한 본인의 선택",
            "수정 전후 값과 근거 및 승인 흔적으로 결과 책임을 입증하는 정도",
        ),
        "A",
        "과거의 실제 불일치에서 본인이 비용을 감수해 직접 보류·수정한 선택과 수정 "
        "전후 및 승인 증거를 묻는다.",
    ),
    FinalV17Case(
        "plan_actual_gap",
        "발표면접",
        "기술",
        "계획대비 실적 분석 능력",
        "세 사업의 월별 계획액·집행액·성과량 추이에서 한 사업만 집행은 늘었지만 "
        "성과량은 하락했고, 관련 민원도 같은 달에 증가한 자료가 제시되었습니다. 이 "
        "사업에서 가장 중요한 차이의 원인을 하나로 판정하고, 계획값·실적값·원인 "
        "근거가 보이는 분석표 한 장으로 발표해 주십시오.",
        (
            "방금 핵심 근거로 사용한 수치가 일시적 변동일 가능성을 배제하려면 어떤 "
            "기간과 원자료를 추가로 대조하겠습니까?",
            "앞서 판정한 원인과 반대되는 자료가 확인된다면 어떤 다른 설명을 우선 "
            "검토하고 분석표를 어떻게 수정하겠습니까?",
            "확인된 차이에 대응할 조정안 두 가지 중 하나를 먼저 시행한다면, 선택 "
            "기준과 효과를 확인할 지표는 무엇입니까?",
        ),
        (
            "계획과 집행 및 성과의 차이를 동일 기간과 단위로 비교하는 능력",
            "여러 이상 징후 중 핵심 차이를 선별하는 판단",
            "수치 변화와 민원 기록을 연결해 원인을 설명하는 근거성",
            "계획값·실적값·원인 근거를 명확히 보여 주는 분석표의 전달력",
        ),
        "A",
        "계획·집행·성과의 이상 관계에서 핵심 격차 원인을 판정하고 세 값이 대응하는 "
        "분석표를 발표하게 한다.",
    ),
    FinalV17Case(
        "research_fund_rule",
        "직무지식면접",
        "지식",
        "부처별 연구개발사업 관리규정에 대한 지식",
        "지원기관 지침상 제한되는 지출이 협약서에는 허용된 것으로 표시되고 내부 "
        "기준에는 별도 승인이 필요하다고 적힌 집행서류가 접수되었습니다. 세 근거의 "
        "적용 대상과 효력 범위를 따져 보완과 반려 중 하나를 판단하고, 적용 근거·예외 "
        "인정 여부·오류 항목이 담긴 검토기록 한 장을 제시해 주십시오.",
        (
            "방금 적용 우선순위로 제시한 근거가 해당 사업이나 집행 시점에 적용된다고 "
            "확인할 자료는 무엇입니까?",
            "앞서 예외를 인정하거나 인정하지 않은 판단에서, 지출 목적이나 승인 "
            "시점이 달라지면 결론이 어떻게 바뀝니까?",
            "수정된 서류가 다시 제출되었을 때 같은 오류가 해소되었음을 어떤 기록끼리 "
            "대조해 확인하시겠습니까?",
        ),
        (
            "사업·기간·지출 유형을 기준으로 서로 다른 근거의 적용 범위를 구별한다.",
            "예외의 성립 조건과 적용되지 않는 경계를 조건부로 설명한다.",
            "보완 또는 반려 결론과 그 근거를 하나의 검토기록에 연결한다.",
            "재제출 서류와 승인·증빙 기록을 대조해 오류 해소 여부를 확인한다.",
        ),
        "A",
        "서로 다른 권위가 제한·허용·별도 승인을 규정한 실제 충돌에서 적용 범위와 "
        "예외를 판정해 검토기록으로 남긴다.",
    ),
    FinalV17Case(
        "agency_negotiation",
        "토론면접",
        "기술",
        "관련기관ㆍ단체 담당자와의 협상 기술",
        "[토론과제] 공동 연구사업의 중간보고 마감이 임박한 가운데 지원기관은 일정 "
        "준수를 위해 핵심 결과만 먼저 받겠다고 하고, 수행기관은 검증되지 않은 자료 "
        "제출에 따른 책임을 이유로 전체 확인이 끝날 때까지 미루자고 합니다. 양측이 "
        "교환할 조건과 양보할 수 없는 경계를 정하고, 확인할 사실을 근거로 공동안을 "
        "도출하되 합의가 어렵다면 남은 쟁점과 결정권자 이송 기준이 드러나는 협의기록 "
        "한 장을 제시하십시오.",
        (
            "방금 수용한 상대 측 요구는 어떤 자료나 사실을 확인했을 때만 유지할 수 "
            "있습니까?",
            "앞서 양보하지 않겠다고 정한 경계 중 상대가 다시 조정을 요구한다면, 수용 "
            "여부를 가를 교환 조건은 무엇입니까?",
            "공동안의 적용 대상과 예외, 이행 확인 기준, 담당 주체와 후속 점검 시점을 "
            "어떻게 정하시겠습니까?",
        ),
        (
            "양측의 일정상 이익과 검증 책임을 분리해 확인할 사실을 제시한다.",
            "요구의 수용 범위와 불수용 경계를 구체적인 교환 조건으로 조정한다.",
            "합의 내용 또는 미합의 쟁점과 상위 결정 이송 기준을 협의기록에 남긴다.",
            "적용 범위·예외·이행 확인 기준과 담당 주체를 명확히 정한다.",
        ),
        "A",
        "선제출과 검증 완료 후 제출의 대립을 교환 조건·비양보 경계로 조정하고 "
        "미합의 쟁점과 이송 기준까지 기록한다.",
    ),
    FinalV17Case(
        "personal_data_protection",
        "인바스켓면접",
        "지식",
        "개인정보보호법",
        "오전 중 외부기관의 연구 참여자 명단 긴급 회신 요청, 오늘 결재가 필요한 "
        "인사자료 오류 정정안, 내일까지 제출할 민원 처리현황 보고서가 동시에 "
        "도착했습니다. 외부 요청에는 이용 목적과 제공 근거가 빠져 있고, 본인에게는 "
        "외부 제공 승인 권한이 없습니다. 목적·권한·필요 범위를 기준으로 세 건의 처리 "
        "순서와 직접처리·보고·보류 주체를 결정하고, 건별 순서·담당 주체·보류 사유가 "
        "담긴 처리결정표 한 장을 제시해 주십시오.",
        (
            "방금 첫 순위로 정한 문서와 처리 주체를 선택한 근거는 무엇이며, 그 "
            "선택으로 뒤로 밀린 업무의 위험은 어떻게 통제하겠습니까?",
            "앞서 외부 요청을 처리할 수 있다고 답했다면 제공 범위를 어디까지 줄일 "
            "것이며, 보류한다고 답했다면 어떤 정보가 보완되어야 판단을 "
            "바꾸겠습니까?",
            "오발송이나 과다 제공을 막기 위해 회신 전 확인할 권한 기록, 수신자 정보, "
            "보관 기한을 어떤 방식으로 점검하시겠습니까?",
        ),
        (
            "각 요청의 목적, 승인 권한, 필요한 정보 범위를 구분한다.",
            "마감과 정보 노출 위험을 함께 고려해 처리 순서를 정한다.",
            "직접처리·보고·보류 결정을 건별 담당 주체 및 사유와 연결해 기록한다.",
            "회신 범위와 사전 확인 항목을 제시해 오발송·과다 제공 위험을 통제한다.",
        ),
        "A",
        "목적·법적 근거·승인 권한·최소 제공 범위로 세 요청을 분류하고 건별 처리 "
        "주체와 보류 사유를 결정표에 남긴다.",
    ),
    FinalV17Case(
        "measurement_framework",
        "창의적 문제해결력면접",
        "태도",
        "성과측정 기준 수립을 위한 체계적 사고",
        "사업 성과 집계에서 일부 부서의 활동이 매 분기 빠지지만, 경영진은 공시 "
        "일정을 이유로 기존 방식의 즉시 사용을 요구하고 해당 부서는 기준 변경에 "
        "반대하고 있습니다. 공시 지연이나 부서 반발 중 발생할 비용을 본인이 "
        "감수하면서 기존 기준의 사용 보류 또는 수정을 하나로 결정하고 직접 "
        "반영하며, 그 결과에 본인이 책임지는 판단기록 한 장에 측정 차원·포함 및 제외 "
        "경계·관찰 기간을 명시해 주십시오.",
        (
            "방금 정한 포함·제외 경계가 반복 누락의 원인이라는 설명과 맞지 않는 반증 "
            "자료가 나온다면 어떤 부분을 다시 판단하시겠습니까?",
            "앞서 선택한 기준을 제한된 인력으로 시험한다면 어떤 소규모 검증을 직접 "
            "수행하고, 어떤 관찰 결과에서 채택을 중단하겠습니까?",
            "수정된 기준을 실제 집계에 적용하기 위한 담당 범위와 첫 점검 시점을 "
            "어떻게 정하시겠습니까?",
        ),
        (
            "일정 지연 또는 관계 부서 반발이라는 비용을 감수하는 선택을 명확히 밝힌다.",
            "선택한 보류 또는 수정 조치를 본인이 직접 반영하고 결과 책임의 범위를 기록한다.",
            "측정 차원·포함 및 제외 경계·관찰 기간이 서로 일관된 기준을 제시한다.",
            "반증 가능성과 제한된 자원을 고려한 소규모 검증 및 중단 조건을 설명한다.",
        ),
        "A",
        "반복 누락과 공시 압박 속에서 본인이 비용을 감수해 기준을 직접 보류·수정하고 "
        "책임지며 측정 경계를 기록한다.",
    ),
    FinalV17Case(
        "closing_report_rules",
        "인바스켓면접",
        "지식",
        "회계보고서 및 분석·검토보고서 작성 요령",
        "결산 초안의 미확인 금액 정정 요청은 오늘 정오, 증빙 보완 요청은 오늘 퇴근 "
        "전, 경영진 보고자료는 내일 오전까지 처리해야 하지만 본인에게는 초안 수정 "
        "권한만 있고 최종 승인 권한은 없습니다. 세 문서의 처리 순서와 직접 "
        "처리·위임·보고 대상을 결정하고, 확정값과 잠정값의 본문·주석 배치 및 증빙 "
        "연결이 드러나는 검토기록 한 장을 제시해 주십시오.",
        (
            "방금 1순위로 정한 문서와 처리 주체를 기준으로, 다른 문서를 뒤로 미뤘을 "
            "때 생길 수 있는 가장 큰 오류를 어떻게 통제하겠습니까?",
            "답변에서 잠정값이나 증빙이 빠졌다면 어느 항목을 어떻게 고쳐 "
            "기록하시겠습니까? 빠지지 않았다면 해당 표기 방식이 오해를 막는 이유를 "
            "설명해 주십시오.",
            "최종 승인권자가 마감 전 연락되지 않을 경우, 권한을 넘지 않으면서 "
            "보고자료에 반영할 수 있는 범위와 제외할 범위를 어떻게 나누겠습니까?",
        ),
        (
            "마감과 승인 권한을 함께 고려하여 문서별 처리 순서와 주체를 구분한다",
            "선택한 우선순위로 발생할 수 있는 누락 또는 오보고 위험을 구체적으로 설명한다",
            "같은 검토기록 안에서 확정값과 잠정값을 구별하고 본문과 주석의 배치를 정한다",
            "기재한 금액이나 판단을 확인 가능한 증빙과 연결하고 승인 불가 시 반영 범위를 제시한다",
        ),
        "A",
        "마감·권한에 따른 처리 판단과 별개로 확정·잠정값, 본문·주석 위치, 증빙 연결을 "
        "주질문의 검토기록에 직접 요구한다.",
    ),
    FinalV17Case(
        "review_report_writing",
        "경험면접",
        "기술",
        "분석·검토보고서 작성 능력",
        "예산계획표와 결산 원장의 금액이 맞지 않은 상태에서 보고 마감이 임박했던 "
        "실제 사례를 떠올려 주십시오. 당시 본인이 어떤 차이를 보고서에 반영할 "
        "대상으로 판정해 직접 작성했으며, 그 판단이 담긴 차이 검토표 한 장과 실제 "
        "승인·정정 또는 의사결정 결과를 설명해 주십시오.",
        (
            "방금 언급한 원자료 가운데 그 차이를 판정하는 데 가장 결정적이었던 "
            "자료는 무엇이며, 다른 자료보다 신뢰한 이유는 무엇입니까?",
            "앞서 설명한 차이 검토표에서 본인이 직접 작성한 부분과 다른 담당자에게 "
            "확인받은 부분을 구분하고, 활용 결과를 입증할 기록을 제시해 주십시오.",
            "직접 경험이 없다면 서로 다른 집행자료를 대조해 보고용 문서를 만든 유사 "
            "경험을 답하고, 그마저 없다면 같은 상황에서 작성할 표의 구성과 검산 "
            "방법을 설명해 주십시오.",
        ),
        (
            "마감 압박과 자료 불일치가 있었던 실제 상황 및 본인의 역할을 구체적으로 밝힌다",
            "판정에 사용한 원자료와 선택 이유를 설명한다",
            "계획값·실적값·차이 사유가 연결된 검토표에서 본인의 작성 흔적을 구분한다",
            "승인·정정·의사결정 등 산출물의 실제 활용 결과와 확인 근거를 제시한다",
        ),
        "A",
        "실제 자료 불일치에서 본인이 반영 대상을 판정해 직접 만든 분석표와 승인·정정 "
        "또는 의사결정 활용 결과를 입증한다.",
    ),
    FinalV17Case(
        "civil_form_process",
        "상황면접",
        "기술",
        "관공서 서식 작성과 민원프로세스 파악 기술",
        "관계기관 제출 서식에는 사업기간 종료일이 비어 있고 첨부된 내부 문서와 민원 "
        "회신 기록에는 서로 다른 종료일이 적혀 있으며 제출 마감은 오늘입니다. 가장 "
        "먼저 어떤 값의 기재 가능 여부를 판단할지 정하고, 필수 항목·확인 출처·보완 "
        "상태가 표시된 수정 서식 한 장을 제시해 주십시오.",
        (
            "방금 첫 확인 대상으로 고른 기록에서 담당자 확인이나 작성 근거가 "
            "발견되지 않는다면, 기재·공란 유지·제출 보류 중 어떤 쪽으로 판단을 "
            "바꾸겠습니까?",
            "답변에서 민원인에게 이미 안내된 내용과의 불일치를 다루지 않았다면 "
            "어떻게 보완하시겠습니까? 다뤘다면 어떤 변경 흔적을 남길지 설명해 "
            "주십시오.",
            "기관 담당자의 구두 확인만 마감 전에 확보된 경우, 서식 제출과 민원 "
            "회신을 각각 어디까지 진행하고 어떤 후속 확인을 남기겠습니까?",
        ),
        (
            "충돌한 기록 중 첫 확인 대상을 정하고 그 선택 근거를 설명한다",
            "근거 유무에 따라 기재·공란 유지·제출 보류의 조건을 구분한다",
            "수정 서식에 필수 항목, 확인 출처와 보완 상태를 식별 가능하게 표시한다",
            "기존 민원 안내와 달라지는 내용을 변경 흔적 및 후속 확인과 연결한다",
        ),
        "A",
        "서식과 민원 회신의 날짜 충돌에서 첫 확인·조건부 기재 판단을 내리고 출처와 "
        "보완 상태가 보이는 수정 서식을 만든다.",
    ),
    FinalV17Case(
        "resource_allocation_fairness",
        "발표면접",
        "태도",
        "합리적인 자원분배 기준을 설정하려는 자세",
        "세 연구지원 사업의 인력·예산 요구는 모두 늘었지만 총자원은 동결되어 있고, "
        "실적이 낮은 사업을 줄이면 해당 부서의 강한 반발과 핵심 일정 지연이 "
        "예상됩니다. 그 비용을 감수할 사업별 배분을 본인이 직접 하나로 결정하고 "
        "결과가 목표에 못 미칠 경우의 책임까지 본인이 지겠다는 전제에서, 공통 "
        "기준·사업별 배분량·책임 범위가 표시된 배분안 한 장을 발표해 주십시오.",
        (
            "방금 적용한 공통 기준 때문에 불리해진 사업이 제시한 반대 자료 중 어떤 "
            "사실이 확인되면 배분 결정을 수정하겠습니까?",
            "발표에서 본인이 감수하겠다고 한 비용이 실제로 발생한다면, 선택한 배분을 "
            "유지하거나 철회할 경계를 무엇으로 정하겠습니까?",
            "동일한 기준을 적용해도 사업의 필수 의무를 수행할 수 없는 경우, 예외를 "
            "허용할 범위와 그 결과를 확인할 지표를 어떻게 정하겠습니까?",
        ),
        (
            "제한된 총자원 안에서 모든 사업에 일관되게 적용할 구별 가능한 기준을 제시한다",
            "부서 반발이나 핵심 일정 지연이라는 비용을 감수하는 하나의 배분을 직접 결정한다",
            "배분안에 공통 기준, 사업별 배분량과 본인의 책임 범위를 명확히 기록한다",
            "반대 자료나 필수 의무를 근거로 결정의 수정 경계와 결과 확인 기준을 설명한다",
        ),
        "A",
        "동결된 총자원에서 공통 기준으로 배분을 직접 결정하고 반발·지연 비용 및 "
        "목표 미달 책임을 배분안에 명시한다.",
    ),
    FinalV17Case(
        "objective_evaluation",
        "토론면접",
        "태도",
        "평가에 대한 객관적 자세의 유지",
        "[토론과제] 시범사업의 성과를 확정해야 하는데, 기획부서는 예정된 보고 "
        "일정을 지키기 위해 정량 실적을 우선 적용하자는 입장이고 현업부서는 지역별 "
        "운영 여건을 반영하지 않은 평가는 수용할 수 없다는 입장입니다. 두 입장의 "
        "근거를 확인한 뒤 일정 지연이나 관계 부서의 반발을 감수하고서라도 본인이 "
        "채택할 평가 기준을 하나로 결정하고, 직접 관철할 조치와 결과에 대한 책임 "
        "주체, 적용 범위, 미합의 시 이송 기준이 담긴 평가원칙안 한 장을 제시해 "
        "토론해 주십시오.",
        (
            "방금 말씀하신 결정에서 상대 입장 중 수용한 근거와 배제한 근거는 "
            "무엇이며, 이를 확인하기 위해 어떤 성과자료와 현장 사실을 "
            "대조하겠습니까?",
            "앞서 제시한 평가원칙안에서 예외로 남긴 대상은 무엇이며, 그 예외가 "
            "자의적으로 확대되지 않도록 누가 어떤 결과를 책임져야 합니까?",
            "공동안에 이르지 못할 경우 결정권자에게 이송할 핵심 쟁점과 재검토 "
            "조건을 어떻게 정하겠습니까?",
        ),
        (
            "상충하는 정량 자료와 현장 사실을 구분하여 확인할 근거를 제시한다",
            "일정 지연이나 부서 반발을 감수하는 일관된 기준 선택을 설명한다",
            "선택한 기준을 적용하기 위한 본인의 직접 조치와 결과 책임을 명시한다",
            "평가원칙안에 적용 범위와 예외 또는 이송 조건을 구체적으로 기록한다",
        ),
        "A",
        "정량 실적과 지역 운영 여건의 충돌에서 비용을 감수할 본인 기준, 직접 관철 "
        "조치, 결과 책임과 적용 경계를 결속한다.",
    ),
    FinalV17Case(
        "visualization_data_accuracy",
        "창의적 문제해결력면접",
        "태도",
        "데이터의 정확성을 추구하는 태도",
        "내일 공개할 성과 화면에서 같은 지표가 원천 시스템별로 반복해서 다르게 "
        "나타나지만, 검증에 투입할 수 있는 인력은 한 명뿐입니다. 공개 지연을 "
        "감수하고 본인이 우선 보류하거나 수정할 데이터 범위를 하나로 결정한 뒤, "
        "직접 수행할 최소 검증과 그 결과에 대한 본인의 책임이 드러나도록 대상 "
        "항목·검증 가설·중단 기준을 담은 판단 기록 한 장을 제시해 주십시오.",
        (
            "방금 선택한 검증 가설을 틀렸다고 판단하게 만들 반증 자료는 무엇이며, "
            "그 자료가 나오면 보류 범위를 어떻게 바꾸겠습니까?",
            "앞서 제시한 최소 검증에서 관찰 결과가 중단 기준에 정확히 걸칠 경우 어떤 "
            "결정을 내리고 그 결과를 어떻게 설명하겠습니까?",
            "같은 불일치를 다음 갱신 때 조기에 발견하도록 원천별 대조 규칙과 변경 "
            "이력을 어떻게 남기겠습니까?",
        ),
        (
            "반복 불일치를 설명하는 검증 가능한 가설을 특정한다",
            "제한된 인력 아래 보류 또는 수정 범위와 최소 검증을 서로 연결한다",
            "공개 지연이라는 비용을 감수한 직접 행동과 결과 책임을 명확히 한다",
            "판단 기록에 대상 항목·검증 가설·중단 기준을 판별 가능하게 제시한다",
        ),
        "A",
        "공개 지연 비용 아래 본인이 데이터 범위를 직접 보류·수정하고 결과를 "
        "책임지며 대상·가설·최소검증·중단 기준을 기록한다.",
    ),
    FinalV17Case(
        "data_use_ethics",
        "경험면접",
        "태도",
        "기업 내 데이터 수집 및 활용에 대한 윤리적 태도",
        "마감이나 업무 편의를 위해 확보한 자료를 그대로 분석에 쓰자는 요구를 "
        "받았지만, 수집 목적·접근권한·공유 대상 중 하나가 맞지 않아 사용 범위를 "
        "제한하거나 사용을 거절했던 실제 사례를 설명해 주십시오. 본인이 감수한 일정 "
        "지연이나 관계자의 반발, 직접 취한 조치와 그 결과에 대한 책임이 함께 "
        "드러나도록 당시의 판단을 남긴 승인·반려 기록 한 건을 중심으로 말씀해 "
        "주십시오.",
        (
            "방금 언급한 자료에서 실제 업무 목적과 맞지 않았던 이용 범위는 "
            "무엇이었으며, 그 판단을 뒷받침한 확인 내용은 무엇입니까?",
            "앞서 말씀하신 조치로 일정이나 관계자에게 어떤 영향이 생겼고, 본인이 "
            "책임진 결과를 승인 기록이나 변경 내역으로 어떻게 확인할 수 있습니까?",
            "직접 경험이 없다면 유사한 자료 이용 상황을 제시하고, 사용을 허용할 "
            "범위와 승인 또는 반려 기록에 남길 내용을 설명해 주십시오.",
        ),
        (
            "자료의 수집 목적·접근권한·공유 대상 가운데 실제 충돌 지점을 구체화한다",
            "편의 포기나 반발을 감수하고 사용 제한 또는 거절을 선택한 이유를 설명한다",
            "본인이 직접 취한 조치와 그 결과에 대한 책임을 실제 변화나 기록으로 입증한다",
            "승인·반려 기록에 허용 범위와 판단 근거를 구분하여 남긴다",
        ),
        "A",
        "실제 목적·권한·공유 범위 충돌에서 본인이 지연·반발을 감수해 직접 "
        "제한·거절하고 결과 책임을 승인·반려 기록으로 입증한다.",
    ),
    FinalV17Case(
        "document_register",
        "인바스켓면접",
        "기술",
        "문서 대장 기록 능력",
        "오늘 회신해야 하지만 공식 접수번호가 없는 협조 공문, 이미 결재된 문서의 "
        "수신처 정정 요청, 오후 결재 마감이 임박했지만 본인에게 승인 권한이 없는 "
        "보고 문서가 동시에 도착했습니다. 각 문서의 처리 순서와 "
        "직접처리·위임·보고 주체를 결정하고, 접수 시각·처리 담당자·현재 상태·보류 "
        "또는 정정 이력이 보이는 전자문서 접수대장 처리안 한 장을 작성해 주십시오.",
        (
            "방금 1순위로 둔 문서와 처리 주체를 선택한 근거는 무엇이며, 그 선택으로 "
            "발생할 수 있는 접수 누락이나 권한 초과를 어떻게 막겠습니까?",
            "앞서 작성한 처리안에서 보류 또는 정정한 문서의 이전 기록과 새 기록을 "
            "어떻게 연결하여 변경 경위를 확인할 수 있게 하겠습니까?",
            "대장 기록과 전자문서시스템의 정보가 다를 때 어떤 항목을 서로 대조하고, "
            "일치 여부를 어떻게 확인하겠습니까?",
        ),
        (
            "문서별 마감과 권한 차이를 반영하여 처리 순서와 주체를 결정한다",
            "접수번호가 없거나 승인 권한이 없는 문서를 임의 처리하지 않고 상태를 구분한다",
            "정정 전후 기록을 연결하여 변경 사유와 처리 경위를 추적 가능하게 한다",
            "접수대장 처리안에 접수 시각·담당자·상태·보류 또는 정정 이력을 정확히 기재한다",
        ),
        "A",
        "마감·접수번호·승인권한이 다른 문서별 순서와 직접처리·위임·보고 주체를 정해 "
        "시각·담당·상태·이력 대장으로 남긴다.",
    ),
)


def _case(case_id: str) -> FinalV17Case:
    return next(case for case in FINAL_V17_CASES if case.case_id == case_id)


def _item(
    case: FinalV17Case,
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
        "question_evidence_id": f"ksa_final_v17_{case.case_id}",
        "question": question or case.question,
        "follow_ups": list(case.follow_ups),
        "evaluation_points": list(case.evaluation_points),
    }


def test_final_v17_oracle_is_report_independent_and_complete() -> None:
    assert RUNTIME_QUESTION_ORCHESTRATION_POLICY.endswith("_v21")
    assert len(FINAL_V17_CASES) == 16
    assert len({case.case_id for case in FINAL_V17_CASES}) == 16
    assert all(case.oracle == "A" for case in FINAL_V17_CASES)
    assert all(len(case.follow_ups) == 3 for case in FINAL_V17_CASES)
    assert all(len(case.evaluation_points) == 4 for case in FINAL_V17_CASES)
    assert all(len(case.rationale) >= 30 for case in FINAL_V17_CASES)


@pytest.mark.parametrize("case", FINAL_V17_CASES, ids=lambda case: case.case_id)
@pytest.mark.parametrize("source", ["openai_api", "codex_cli", "claude_code"])
def test_final_v17_human_a_cases_pass_every_provider(
    case: FinalV17Case,
    source: str,
) -> None:
    result = evaluate_ksa_measurement(_item(case, source=source))

    assert case.factor not in case.question
    assert result["passed"] is True, (case.rationale, result)
    assert all(result["checks"].values()), result


@pytest.mark.parametrize("target", FINAL_V17_CASES, ids=lambda case: case.case_id)
@pytest.mark.parametrize("source", FINAL_V17_CASES, ids=lambda case: case.case_id)
def test_final_v17_all_pairs_reject_cross_factor_substitution(
    target: FinalV17Case,
    source: FinalV17Case,
) -> None:
    if target.case_id == source.case_id:
        pytest.skip("identity pair is covered by the positive oracle")

    result = evaluate_ksa_measurement(
        _item(target, question=source.question, method=source.method)
    )

    assert result["checks"]["focus_visible"] is False, result
    assert result["checks"]["ksa_type_operationalized"] is False, result
    assert result["passed"] is False, result


@dataclass(frozen=True)
class RelationRemoval:
    case_id: str
    dimension: str
    replacements: tuple[tuple[str, str], ...]


RELATION_REMOVALS = (
    RelationRemoval(
        "indicator_definition",
        "operational-artifact-fields",
        (
            ("포함 범위와 중복 처리의 예외", "포함 범위와 중복 처리"),
            ("적용 기준·예외·수정 이력", "적용 기준"),
        ),
    ),
    RelationRemoval(
        "research_plan_review",
        "plan-content-dimensions",
        (
            (
                "공동연구 신청 마감이 오늘인데 신청서에는 참여기관이 세 곳으로, 연구 "
                "내용서에는 두 곳으로 기재되어 있고 한 기관의 역할과 예산 근거도 "
                "빠져 있습니다.",
                "공동연구 신청 마감이 오늘인데 신청서와 제출문서의 일반 설명이 서로 "
                "다릅니다.",
            ),
        ),
    ),
    RelationRemoval(
        "research_fund_rule",
        "opposed-authorities",
        (
            (
                "지원기관 지침상 제한되는 지출이 협약서에는 허용된 것으로 표시되고 "
                "내부 기준에는 별도 승인이 필요하다고 적힌 집행서류가 접수되었습니다.",
                "지원기관 지침상 제한되는 지출이 적힌 집행서류가 접수되었습니다.",
            ),
        ),
    ),
    RelationRemoval(
        "research_fund_rule",
        "exception-error-output",
        (("적용 근거·예외 인정 여부·오류 항목", "적용 근거·검토 결과"),),
    ),
    RelationRemoval(
        "agency_negotiation",
        "opposed-proposals",
        (
            (
                "수행기관은 검증되지 않은 자료 제출에 따른 책임을 이유로 전체 확인이 "
                "끝날 때까지 미루자고 합니다.",
                "수행기관도 핵심 결과를 먼저 내는 데 동의합니다.",
            ),
        ),
    ),
    RelationRemoval(
        "agency_negotiation",
        "negotiation-boundary",
        (
            (
                "양측이 교환할 조건과 양보할 수 없는 경계를 정하고",
                "양측 입장을 정리하고",
            ),
        ),
    ),
    RelationRemoval(
        "measurement_framework",
        "direct-action",
        (("결정하고 직접 반영하며", "결정안을 검토하며"),),
    ),
    RelationRemoval(
        "measurement_framework",
        "outcome-accountability",
        (("그 결과에 본인이 책임지는 ", "검토 내용을 적은 "),),
    ),
    RelationRemoval(
        "review_report_writing",
        "downstream-use-result",
        (
            (
                "차이 검토표 한 장과 실제 승인·정정 또는 의사결정 결과를 설명",
                "차이 검토표 한 장을 설명",
            ),
        ),
    ),
    RelationRemoval(
        "resource_allocation_fairness",
        "scarce-resource-constraint",
        (
            (
                "총자원은 동결되어 있고",
                "총자원은 모든 요구를 충족할 만큼 충분하고",
            ),
        ),
    ),
    RelationRemoval(
        "objective_evaluation",
        "direct-action",
        (("직접 관철할 조치", "추가로 검토할 쟁점"),),
    ),
    RelationRemoval(
        "objective_evaluation",
        "outcome-accountability",
        (("결과에 대한 책임 주체", "검토 참여 주체"),),
    ),
    RelationRemoval(
        "visualization_data_accuracy",
        "experiment-object-method",
        (
            ("직접 수행할 최소 검증", "직접 수행할 검토"),
            ("대상 항목·검증 가설·중단 기준", "검증 가설·중단 기준"),
        ),
    ),
    RelationRemoval(
        "data_use_ethics",
        "direct-action",
        (("직접 취한 조치", "검토한 내용"),),
    ),
    RelationRemoval(
        "data_use_ethics",
        "decision-artifact",
        (("승인·반려 기록 한 건", "구두 설명"),),
    ),
    RelationRemoval(
        "document_register",
        "actor-decision",
        (
            (
                "각 문서의 처리 순서와 직접처리·위임·보고 주체를 결정하고",
                "각 문서의 처리 순서만 결정하고",
            ),
            ("처리 담당자·", "문서 종류·"),
        ),
    ),
)


@pytest.mark.parametrize(
    "mutation",
    RELATION_REMOVALS,
    ids=lambda mutation: f"{mutation.case_id}-{mutation.dimension}",
)
def test_final_v17_rejects_relation_and_dimension_removals(
    mutation: RelationRemoval,
) -> None:
    case = _case(mutation.case_id)
    question = case.question
    for old, new in mutation.replacements:
        assert old in question, (mutation, old)
        question = question.replace(old, new, 1)

    result = evaluate_ksa_measurement(_item(case, question=question))

    assert result["passed"] is False, (mutation.dimension, question, result)


# v18 explicitly deprecates self-sacrifice and personal-liability wording as
# mandatory evidence.  These older questions remain semantically measurable
# after that leading language is removed because a concrete choice, action and
# checkable result/record still remain in the main task.
DEPRECATED_LEADING_REMOVALS = (
    RelationRemoval(
        "numeric_accuracy",
        "personal-cost",
        (
            (
                "일정 지연이나 관계 부서의 반발을 감수하면서 ",
                "관계 부서와 협의한 뒤 ",
            ),
        ),
    ),
    RelationRemoval(
        "measurement_framework",
        "personal-cost",
        (
            (
                "공시 지연이나 부서 반발 중 발생할 비용을 본인이 감수하면서 ",
                "기존 자료를 검토해 ",
            ),
        ),
    ),
    RelationRemoval(
        "objective_evaluation",
        "personal-cost",
        (
            (
                "일정 지연이나 관계 부서의 반발을 감수하고서라도 ",
                "관계 부서와 협의하여 ",
            ),
        ),
    ),
    RelationRemoval(
        "visualization_data_accuracy",
        "personal-directness",
        (("직접 수행할 최소 검증", "검토할 최소 검증"),),
    ),
    RelationRemoval(
        "visualization_data_accuracy",
        "personal-liability",
        (("그 결과에 대한 본인의 책임", "그 검토 결과"),),
    ),
)


@pytest.mark.parametrize(
    "mutation",
    DEPRECATED_LEADING_REMOVALS,
    ids=lambda mutation: f"{mutation.case_id}-{mutation.dimension}",
)
def test_v18_does_not_require_deprecated_self_sacrifice_language(
    mutation: RelationRemoval,
) -> None:
    case = _case(mutation.case_id)
    question = case.question
    for old, new in mutation.replacements:
        assert old in question, (mutation, old)
        question = question.replace(old, new, 1)

    result = evaluate_ksa_measurement(_item(case, question=question))

    assert result["passed"] is True, (mutation.dimension, question, result)


PARAPHRASED_CASES = (
    (
        "indicator_definition",
        "센터별 집계표에서 같은 참여자를 거듭 산입하고 이탈자를 달리 처리해 수치가 "
        "차이 납니다. 공식 산정 원칙을 근거로 포함 대상·중복 처리·관찰 기간·예외를 "
        "판정하고, 적용 규칙·범위·중복·예외·변경 이력을 적은 원칙 요약표를 작성해 "
        "주세요.",
    ),
    (
        "research_plan_review",
        "공동연구 신청문서의 수행기관 수와 과업서의 기관 수가 불일치하고 역할과 비용 "
        "근거도 누락된 채 제출 마감이 다가왔습니다. 제출 가능 여부를 판단한 뒤 "
        "차이·확인 근거·수정 요구를 담은 정정 조치표를 작성해 주세요.",
    ),
    (
        "numeric_accuracy",
        "보고 마감 압박 속 원자료와 집계표의 수치 오류를 발견했던 경험을 말씀해 "
        "주세요. 지연 부담을 감수해 스스로 정정 여부를 결정하고 직접 대조한 행동, "
        "결과 책임과 판단 근거·담당 책임·변경 전후 값을 담은 검증 내역을 작성해 "
        "주세요.",
    ),
    (
        "plan_actual_gap",
        "분기 계획·집행·성과 추이에서 집행은 늘었지만 산출량은 낮아지고 민원이 "
        "증가해 큰 차이가 생겼습니다. 핵심 격차의 원인을 분석하고 "
        "목표값·실적값·원인이 대응하는 "
        "원인 분석표로 발표해 주세요.",
    ),
    (
        "research_fund_rule",
        "관리기관 기준은 연구비 지출을 금지하지만 협약은 같은 비용을 인정하고 "
        "있습니다. 어느 근거를 우선 적용할지와 예외·보완 여부를 판단하고 적용 "
        "근거·판정·예외 조건을 담은 판정 기록을 작성해 주세요.",
    ),
    (
        "agency_negotiation",
        "[토론과제] 주관기관은 마감 때문에 자료를 먼저 공유하자고 하지만 검증기관은 "
        "확인 후 제출하자고 합니다. 사실 근거를 확인해 교환 조건과 양보 경계를 "
        "협의하고, 합의가 안 되면 남은 쟁점·결정권·이송 기준을 담은 조정 결과서를 "
        "도출해 주세요.",
    ),
    (
        "personal_data_protection",
        "외부기관의 개인정보 명단 요청과 잘못 첨부된 자료 회수 요청이 동시에 왔지만 "
        "제공 승인 권한은 없습니다. 목적·제공 근거·최소 열람 범위로 처리 순서와 각 "
        "처리 주체를 정하고 목적·허용 범위·보류 상태를 담은 제공 처리 대장을 작성해 "
        "주세요.",
    ),
    (
        "measurement_framework",
        "성과 집계에서 공동 활동이 분기마다 누락되지만 경영진은 공시 일정 때문에 "
        "현재 기준을 유지하라고 압박하고 부서도 변경에 반대합니다. 지연 비용을 "
        "감수해 스스로 기준 사용 보류 또는 변경을 결정하고 직접 반영한 뒤 후속 "
        "책임을 지며, 측정 단위·산입·미산입·집계 기간을 담은 결정 대장을 작성해 "
        "주세요.",
    ),
    (
        "closing_report_rules",
        "결산 초안의 미확인 금액과 증빙 보완 요청은 마감이 다르고 최종 승인 권한도 "
        "없습니다. 처리 순서와 담당 주체를 결정하고 확정·미확정 값, 본표·각주 위치, "
        "근거 자료 연계를 배치한 보고 판정표 한 장을 작성해 주세요.",
    ),
    (
        "review_report_writing",
        "예산계획과 회계 원장의 금액이 불일치했던 실제 경험을 설명해 주세요. 본인이 "
        "핵심 차이를 판별해 직접 만든 대조 기록과 그 기록으로 이루어진 정정 승인 "
        "결과를 함께 제시해 주세요.",
    ),
    (
        "civil_form_process",
        "기관 제출 양식의 종료일과 민원 회신 기록의 날짜가 다르고 제출 마감도 "
        "임박했습니다. 무엇을 먼저 확인해 보완할지 판단하고 두 기록과 일치하도록 "
        "고친 정정 양식을 제시해 주세요.",
    ),
    (
        "resource_allocation_fairness",
        "세 연구지원 사업이 인력과 예산을 늘려 달라고 요구하지만 총량은 고정되어 한 "
        "사업의 요청만 충족할 수 있습니다. 어느 사업을 우선 조정할지 공통 기준으로 "
        "결정하고 배분 원칙·사업별 조정량·예외 근거를 담은 지원 조정표를 발표해 "
        "주세요.",
    ),
    (
        "objective_evaluation",
        "[토론과제] 정량 수치 근거와 지역 현장 여건을 우선하자는 두 평가 입장이 "
        "충돌합니다. 지연 비용을 감수해 본인이 평가 기준과 적용 범위를 결정하고 "
        "직접 시행할 조치 및 결과 책임을 담은 평가 기록을 제시해 토론해 주세요.",
    ),
    (
        "visualization_data_accuracy",
        "같은 지표가 원자료와 시스템에서 다르지만 공개 일정은 임박했고 담당 인력도 "
        "한 명입니다. 공개 지연을 감수해 스스로 보류 범위를 결정하고 직접 실행해 "
        "결과를 책임지며, 원인 가설·대상 필드·간이 검증·중단 조건을 담은 정합 "
        "점검표를 작성해 주세요.",
    ),
    (
        "data_use_ethics",
        "보고 마감 때문에 이용 목적과 접근 권한이 불명확한 자료를 빨리 쓰라는 "
        "요청을 받은 실제 사례를 설명해 주세요. 본인이 지연을 감수해 사용 범위를 "
        "제한하고 직접 조치한 결과와 책임을 담은 사용 제한 대장을 작성해 주세요.",
    ),
    (
        "document_register",
        "회신 공문과 잘못 등록된 수신처 정정 요청, 미결재 보고서가 동시에 왔고 변경 "
        "승인 권한은 없습니다. 처리 순서와 각 처리 주체를 정하고 문서번호·등록 "
        "시각·현재 상태·변경 이력을 담은 수발신 기록표를 작성해 주세요.",
    ),
)


@pytest.mark.parametrize(
    ("case_id", "question"),
    PARAPHRASED_CASES,
    ids=[case_id for case_id, _question in PARAPHRASED_CASES],
)
def test_final_v17_accepts_relational_paraphrases_with_new_artifact_names(
    case_id: str,
    question: str,
) -> None:
    case = _case(case_id)
    result = evaluate_ksa_measurement(_item(case, question=question))

    assert case.factor not in question
    assert result["passed"] is True, (case.rationale, result)


NEGATED_OUTPUT_REPLACEMENTS = (
    (
        "indicator_definition",
        "적용 기준·예외·수정 이력을 담은 기준 정리표 한 장을 제시해 주십시오.",
        "적용 기준·예외·수정 이력이 있는 기준 정리표를 작성하지 말고 구두로 답해 "
        "주십시오.",
    ),
    (
        "research_plan_review",
        "문서별 불일치·확인 근거·요청할 수정 내용을 담은 보완요청서 한 장을 작성해 "
        "주십시오.",
        "문서별 불일치·확인 근거·수정 요구가 있는 보완 요청서 없이 구두로 설명해 "
        "주십시오.",
    ),
    (
        "research_fund_rule",
        "검토기록 한 장을 제시해 주십시오.",
        "검토기록 없이 구두로만 답해 주십시오.",
    ),
    (
        "agency_negotiation",
        "협의기록 한 장을 제시하십시오.",
        "협의기록 없이 구두로만 토론하십시오.",
    ),
    (
        "measurement_framework",
        "판단기록 한 장에 측정 차원·포함 및 제외 경계·관찰 기간을 명시해 주십시오.",
        "판단기록을 작성하지 말고 측정 차원·포함 및 제외 경계·관찰 기간을 구두로 "
        "답해 주십시오.",
    ),
    (
        "review_report_writing",
        "당시 본인이 어떤 차이를 보고서에 반영할 대상으로 판정해 직접 "
        "작성했으며, 그 판단이 담긴 차이 검토표 한 장과 실제 승인·정정 또는 "
        "의사결정 결과를 설명해 주십시오.",
        "당시 본인이 어떤 차이인지 직접 판정했으며, 차이 검토표 없이 실제 "
        "승인·정정 또는 의사결정 결과만 설명해 주십시오.",
    ),
    (
        "resource_allocation_fairness",
        "배분안 한 장을 발표해 주십시오.",
        "배분안 없이 구두로만 발표해 주십시오.",
    ),
    (
        "objective_evaluation",
        "평가원칙안 한 장을 제시해 토론해 주십시오.",
        "평가원칙안 없이 구두로만 토론해 주십시오.",
    ),
    (
        "visualization_data_accuracy",
        "판단 기록 한 장을 제시해 주십시오.",
        "판단 기록 없이 구두로만 설명해 주십시오.",
    ),
    (
        "data_use_ethics",
        "승인·반려 기록 한 건을 중심으로 말씀해 주십시오.",
        "승인·반려 기록 없이 기억만으로 말씀해 주십시오.",
    ),
    (
        "document_register",
        "전자문서 접수대장 처리안 한 장을 작성해 주십시오.",
        "접수대장 없이 구두로만 답해 주십시오.",
    ),
)


@pytest.mark.parametrize(
    ("case_id", "old", "new"),
    NEGATED_OUTPUT_REPLACEMENTS,
    ids=[case_id for case_id, _old, _new in NEGATED_OUTPUT_REPLACEMENTS],
)
def test_final_v17_does_not_count_negated_artifact_production(
    case_id: str,
    old: str,
    new: str,
) -> None:
    case = _case(case_id)
    assert old in case.question
    question = case.question.replace(old, new, 1)

    result = evaluate_ksa_measurement(_item(case, question=question))

    assert result["passed"] is False, result


def test_v19_numeric_experience_can_use_observed_result_without_named_artifact() -> None:
    case = _case("numeric_accuracy")
    question = case.question.replace(
        "판단 기록 한 건을 중심으로 설명해 주십시오.",
        "판단 기록 없이 기억만으로 설명해 주십시오.",
        1,
    )

    result = evaluate_ksa_measurement(_item(case, question=question))

    # The main task still requires an actual case, the candidate's selected
    # direct comparison/correction action, and its observed before/after
    # consequence.  v19 no longer makes a named record mandatory for that
    # experience evidence shape.
    assert result["passed"] is True, result


@pytest.mark.parametrize("case", FINAL_V17_CASES, ids=lambda case: case.case_id)
@pytest.mark.parametrize("source", ["openai_api", "codex_cli", "claude_code"])
def test_final_v17_rejects_raw_label_keyword_salad(
    case: FinalV17Case,
    source: str,
) -> None:
    salad = f"{case.factor} 상황 근거 판단 행동 결과 산출물 제시해 주세요."

    result = evaluate_ksa_measurement(_item(case, question=salad, source=source))

    assert result["passed"] is False, result
    assert result["checks"]["not_ksa_restatement"] is False or not all(
        result["checks"].values()
    )
