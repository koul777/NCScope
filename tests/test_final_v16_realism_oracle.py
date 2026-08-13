"""Human-realism oracle for the final full-16 actual run.

The initial local read marked all questions non-leading.  The later blinded
Codex comparison, whose source labels were hidden until validation, found seven
answer-direction defects.  Those adjudicated labels supersede the initial read
and are frozen here as a regression corpus.
"""

from __future__ import annotations

import json

import pytest

from app.services.question_realism import evaluate_question_realism


FINAL_V16_REALISM_CASES: list[dict[str, object]] = json.loads(
    r"""
[
    {
        "case_id":  "indicator_definition",
        "type":  "직무지식면접",
        "question":  "두 부서가 같은 참여자를 각각 연인원과 실인원으로 집계했고, 중도 이탈자와 공동행사 참여자의 처리도 서로 다른 월별 실적표가 제출되었습니다. 공식 산정 원칙을 확정할 때 포함 범위와 중복 처리의 예외를 어떻게 판정할지 설명하고, 적용 기준·예외·수정 이력을 담은 기준 정리표 한 장을 제시해 주십시오.",
        "follow_ups":  [
                           "방금 적용하겠다고 한 기준이 공동행사 참여자에게도 타당하다는 근거는 무엇이며, 그 근거가 불명확하면 어떻게 표시하겠습니까?",
                           "앞서 제시한 예외 가운데 원래 집계 목적을 왜곡할 위험이 가장 큰 것은 무엇이고, 기준 정리표에서 그 위험을 어떻게 드러내겠습니까?",
                           "수정된 실적표가 같은 기준으로 다시 산출되었는지 확인할 때 어떤 원자료와 변경 기록을 대조하겠습니까?"
                       ],
        "question_source":  "codex_cli",
        "human_field_realism":  true,
        "human_nonleading":  true,
        "human_concrete_dilemma":  null,
        "human_adaptive_count":  2,
        "human_overload_warning":  false
    },
    {
        "case_id":  "research_plan_review",
        "type":  "상황면접",
        "question":  "공동연구 신청 마감이 오늘인데 신청서에는 참여기관이 세 곳으로, 연구 내용서에는 두 곳으로 기재되어 있고 한 기관의 역할과 예산 근거도 빠져 있습니다. 연구책임자는 우선 제출을 요구하고 담당 기관에는 연락할 수 있는 상황입니다. 제출 가능 여부에 관한 첫 판단을 내리고, 문서별 불일치·확인 근거·요청할 수정 내용을 담은 보완요청서 한 장을 작성해 주십시오.",
        "follow_ups":  [
                           "방금 첫 판단에서 우선 확인하겠다고 한 항목이 해결되지 않는다면 제출 여부와 보완요청 내용을 어떻게 바꾸겠습니까?",
                           "앞서 선택한 조치에서 참여기관의 역할과 비용 간 연결을 빠뜨렸다면, 어느 자료를 대조해 어떤 문구를 고치겠습니까?",
                           "수정본을 받은 뒤 신청서와 연구 내용서가 일치한다고 판정할 최소 확인 항목은 무엇입니까?"
                       ],
        "question_source":  "codex_cli",
        "human_field_realism":  true,
        "human_nonleading":  true,
        "human_concrete_dilemma":  true,
        "human_adaptive_count":  2,
        "human_overload_warning":  false
    },
    {
        "case_id":  "numeric_accuracy",
        "type":  "경험면접",
        "question":  "성과 집계 마감 직전에 부서 원자료와 취합표의 값이 맞지 않는 사실을 직접 발견했던 사례를 말씀해 주십시오. 일정 지연이나 관계 부서의 반발을 감수하면서 본인이 보류 또는 수정 중 무엇을 선택해 직접 처리했는지, 그 선택에 대한 결과 책임과 수정 전후 값·근거 자료·승인 흔적이 남은 판단 기록 한 건을 중심으로 설명해 주십시오.",
        "follow_ups":  [
                           "방금 언급한 불일치를 발견하게 한 대조 과정에서 어떤 단위·기간·합계 관계를 확인했으며, 본인이 직접 한 검산은 무엇입니까?",
                           "앞서 말씀하신 선택으로 실제 감수한 일정 또는 관계상의 비용은 무엇이었고, 그럼에도 그 행동을 택한 이유는 무엇입니까?",
                           "답변에 수정 전후 결과나 승인 증거가 분명하지 않다면, 당시 기록 중 무엇으로 오류 정정과 본인의 책임 이행을 입증할 수 있습니까?"
                       ],
        "question_source":  "codex_cli",
        "human_field_realism":  true,
        "human_nonleading":  true,
        "human_concrete_dilemma":  null,
        "human_adaptive_count":  2,
        "human_overload_warning":  false
    },
    {
        "case_id":  "plan_actual_gap",
        "type":  "발표면접",
        "question":  "세 사업의 월별 계획액·집행액·성과량 추이에서 한 사업만 집행은 늘었지만 성과량은 하락했고, 관련 민원도 같은 달에 증가한 자료가 제시되었습니다. 이 사업에서 가장 중요한 차이의 원인을 하나로 판정하고, 계획값·실적값·원인 근거가 보이는 분석표 한 장으로 발표해 주십시오.",
        "follow_ups":  [
                           "방금 핵심 근거로 사용한 수치가 일시적 변동일 가능성을 배제하려면 어떤 기간과 원자료를 추가로 대조하겠습니까?",
                           "앞서 판정한 원인과 반대되는 자료가 확인된다면 어떤 다른 설명을 우선 검토하고 분석표를 어떻게 수정하겠습니까?",
                           "확인된 차이에 대응할 조정안 두 가지 중 하나를 먼저 시행한다면, 선택 기준과 효과를 확인할 지표는 무엇입니까?"
                       ],
        "question_source":  "codex_cli",
        "human_field_realism":  true,
        "human_nonleading":  true,
        "human_concrete_dilemma":  true,
        "human_adaptive_count":  2,
        "human_overload_warning":  false
    },
    {
        "case_id":  "research_fund_rule",
        "type":  "직무지식면접",
        "question":  "지원기관 지침상 제한되는 지출이 협약서에는 허용된 것으로 표시되고 내부 기준에는 별도 승인이 필요하다고 적힌 집행서류가 접수되었습니다. 세 근거의 적용 대상과 효력 범위를 따져 보완과 반려 중 하나를 판단하고, 적용 근거·예외 인정 여부·오류 항목이 담긴 검토기록 한 장을 제시해 주십시오.",
        "follow_ups":  [
                           "방금 적용 우선순위로 제시한 근거가 해당 사업이나 집행 시점에 적용된다고 확인할 자료는 무엇입니까?",
                           "앞서 예외를 인정하거나 인정하지 않은 판단에서, 지출 목적이나 승인 시점이 달라지면 결론이 어떻게 바뀝니까?",
                           "수정된 서류가 다시 제출되었을 때 같은 오류가 해소되었음을 어떤 기록끼리 대조해 확인하시겠습니까?"
                       ],
        "question_source":  "codex_cli",
        "human_field_realism":  true,
        "human_nonleading":  true,
        "human_concrete_dilemma":  null,
        "human_adaptive_count":  2,
        "human_overload_warning":  false
    },
    {
        "case_id":  "agency_negotiation",
        "type":  "토론면접",
        "question":  "[토론과제] 공동 연구사업의 중간보고 마감이 임박한 가운데 지원기관은 일정 준수를 위해 핵심 결과만 먼저 받겠다고 하고, 수행기관은 검증되지 않은 자료 제출에 따른 책임을 이유로 전체 확인이 끝날 때까지 미루자고 합니다. 양측이 교환할 조건과 양보할 수 없는 경계를 정하고, 확인할 사실을 근거로 공동안을 도출하되 합의가 어렵다면 남은 쟁점과 결정권자 이송 기준이 드러나는 협의기록 한 장을 제시하십시오.",
        "follow_ups":  [
                           "방금 수용한 상대 측 요구는 어떤 자료나 사실을 확인했을 때만 유지할 수 있습니까?",
                           "앞서 양보하지 않겠다고 정한 경계 중 상대가 다시 조정을 요구한다면, 수용 여부를 가를 교환 조건은 무엇입니까?",
                           "공동안의 적용 대상과 예외, 이행 확인 기준, 담당 주체와 후속 점검 시점을 어떻게 정하시겠습니까?"
                       ],
        "question_source":  "codex_cli",
        "human_field_realism":  true,
        "human_nonleading":  true,
        "human_concrete_dilemma":  true,
        "human_adaptive_count":  2,
        "human_overload_warning":  false
    },
    {
        "case_id":  "personal_data_protection",
        "type":  "인바스켓면접",
        "question":  "오전 중 외부기관의 연구 참여자 명단 긴급 회신 요청, 오늘 결재가 필요한 인사자료 오류 정정안, 내일까지 제출할 민원 처리현황 보고서가 동시에 도착했습니다. 외부 요청에는 이용 목적과 제공 근거가 빠져 있고, 본인에게는 외부 제공 승인 권한이 없습니다. 목적·권한·필요 범위를 기준으로 세 건의 처리 순서와 직접처리·보고·보류 주체를 결정하고, 건별 순서·담당 주체·보류 사유가 담긴 처리결정표 한 장을 제시해 주십시오.",
        "follow_ups":  [
                           "방금 첫 순위로 정한 문서와 처리 주체를 선택한 근거는 무엇이며, 그 선택으로 뒤로 밀린 업무의 위험은 어떻게 통제하겠습니까?",
                           "앞서 외부 요청을 처리할 수 있다고 답했다면 제공 범위를 어디까지 줄일 것이며, 보류한다고 답했다면 어떤 정보가 보완되어야 판단을 바꾸겠습니까?",
                           "오발송이나 과다 제공을 막기 위해 회신 전 확인할 권한 기록, 수신자 정보, 보관 기한을 어떤 방식으로 점검하시겠습니까?"
                       ],
        "question_source":  "codex_cli",
        "human_field_realism":  true,
        "human_nonleading":  true,
        "human_concrete_dilemma":  true,
        "human_adaptive_count":  2,
        "human_overload_warning":  false
    },
    {
        "case_id":  "measurement_framework",
        "type":  "창의적 문제해결력면접",
        "question":  "사업 성과 집계에서 일부 부서의 활동이 매 분기 빠지지만, 경영진은 공시 일정을 이유로 기존 방식의 즉시 사용을 요구하고 해당 부서는 기준 변경에 반대하고 있습니다. 공시 지연이나 부서 반발 중 발생할 비용을 본인이 감수하면서 기존 기준의 사용 보류 또는 수정을 하나로 결정하고 직접 반영하며, 그 결과에 본인이 책임지는 판단기록 한 장에 측정 차원·포함 및 제외 경계·관찰 기간을 명시해 주십시오.",
        "follow_ups":  [
                           "방금 정한 포함·제외 경계가 반복 누락의 원인이라는 설명과 맞지 않는 반증 자료가 나온다면 어떤 부분을 다시 판단하시겠습니까?",
                           "앞서 선택한 기준을 제한된 인력으로 시험한다면 어떤 소규모 검증을 직접 수행하고, 어떤 관찰 결과에서 채택을 중단하겠습니까?",
                           "수정된 기준을 실제 집계에 적용하기 위한 담당 범위와 첫 점검 시점을 어떻게 정하시겠습니까?"
                       ],
        "question_source":  "codex_cli",
        "human_field_realism":  true,
        "human_nonleading":  true,
        "human_concrete_dilemma":  true,
        "human_adaptive_count":  2,
        "human_overload_warning":  false
    },
    {
        "case_id":  "closing_report_rules",
        "type":  "인바스켓면접",
        "question":  "결산 초안의 미확인 금액 정정 요청은 오늘 정오, 증빙 보완 요청은 오늘 퇴근 전, 경영진 보고자료는 내일 오전까지 처리해야 하지만 본인에게는 초안 수정 권한만 있고 최종 승인 권한은 없습니다. 세 문서의 처리 순서와 직접 처리·위임·보고 대상을 결정하고, 확정값과 잠정값의 본문·주석 배치 및 증빙 연결이 드러나는 검토기록 한 장을 제시해 주십시오.",
        "follow_ups":  [
                           "방금 1순위로 정한 문서와 처리 주체를 기준으로, 다른 문서를 뒤로 미뤘을 때 생길 수 있는 가장 큰 오류를 어떻게 통제하겠습니까?",
                           "답변에서 잠정값이나 증빙이 빠졌다면 어느 항목을 어떻게 고쳐 기록하시겠습니까? 빠지지 않았다면 해당 표기 방식이 오해를 막는 이유를 설명해 주십시오.",
                           "최종 승인권자가 마감 전 연락되지 않을 경우, 권한을 넘지 않으면서 보고자료에 반영할 수 있는 범위와 제외할 범위를 어떻게 나누겠습니까?"
                       ],
        "question_source":  "codex_cli",
        "human_field_realism":  true,
        "human_nonleading":  true,
        "human_concrete_dilemma":  true,
        "human_adaptive_count":  2,
        "human_overload_warning":  false
    },
    {
        "case_id":  "review_report_writing",
        "type":  "경험면접",
        "question":  "예산계획표와 결산 원장의 금액이 맞지 않은 상태에서 보고 마감이 임박했던 실제 사례를 떠올려 주십시오. 당시 본인이 어떤 차이를 보고서에 반영할 대상으로 판정해 직접 작성했으며, 그 판단이 담긴 차이 검토표 한 장과 실제 승인·정정 또는 의사결정 결과를 설명해 주십시오.",
        "follow_ups":  [
                           "방금 언급한 원자료 가운데 그 차이를 판정하는 데 가장 결정적이었던 자료는 무엇이며, 다른 자료보다 신뢰한 이유는 무엇입니까?",
                           "앞서 설명한 차이 검토표에서 본인이 직접 작성한 부분과 다른 담당자에게 확인받은 부분을 구분하고, 활용 결과를 입증할 기록을 제시해 주십시오.",
                           "직접 경험이 없다면 서로 다른 집행자료를 대조해 보고용 문서를 만든 유사 경험을 답하고, 그마저 없다면 같은 상황에서 작성할 표의 구성과 검산 방법을 설명해 주십시오."
                       ],
        "question_source":  "codex_cli",
        "human_field_realism":  true,
        "human_nonleading":  true,
        "human_concrete_dilemma":  null,
        "human_adaptive_count":  2,
        "human_overload_warning":  false
    },
    {
        "case_id":  "civil_form_process",
        "type":  "상황면접",
        "question":  "관계기관 제출 서식에는 사업기간 종료일이 비어 있고 첨부된 내부 문서와 민원 회신 기록에는 서로 다른 종료일이 적혀 있으며 제출 마감은 오늘입니다. 가장 먼저 어떤 값의 기재 가능 여부를 판단할지 정하고, 필수 항목·확인 출처·보완 상태가 표시된 수정 서식 한 장을 제시해 주십시오.",
        "follow_ups":  [
                           "방금 첫 확인 대상으로 고른 기록에서 담당자 확인이나 작성 근거가 발견되지 않는다면, 기재·공란 유지·제출 보류 중 어떤 쪽으로 판단을 바꾸겠습니까?",
                           "답변에서 민원인에게 이미 안내된 내용과의 불일치를 다루지 않았다면 어떻게 보완하시겠습니까? 다뤘다면 어떤 변경 흔적을 남길지 설명해 주십시오.",
                           "기관 담당자의 구두 확인만 마감 전에 확보된 경우, 서식 제출과 민원 회신을 각각 어디까지 진행하고 어떤 후속 확인을 남기겠습니까?"
                       ],
        "question_source":  "codex_cli",
        "human_field_realism":  true,
        "human_nonleading":  true,
        "human_concrete_dilemma":  true,
        "human_adaptive_count":  2,
        "human_overload_warning":  false
    },
    {
        "case_id":  "resource_allocation_fairness",
        "type":  "발표면접",
        "question":  "세 연구지원 사업의 인력·예산 요구는 모두 늘었지만 총자원은 동결되어 있고, 실적이 낮은 사업을 줄이면 해당 부서의 강한 반발과 핵심 일정 지연이 예상됩니다. 그 비용을 감수할 사업별 배분을 본인이 직접 하나로 결정하고 결과가 목표에 못 미칠 경우의 책임까지 본인이 지겠다는 전제에서, 공통 기준·사업별 배분량·책임 범위가 표시된 배분안 한 장을 발표해 주십시오.",
        "follow_ups":  [
                           "방금 적용한 공통 기준 때문에 불리해진 사업이 제시한 반대 자료 중 어떤 사실이 확인되면 배분 결정을 수정하겠습니까?",
                           "발표에서 본인이 감수하겠다고 한 비용이 실제로 발생한다면, 선택한 배분을 유지하거나 철회할 경계를 무엇으로 정하겠습니까?",
                           "동일한 기준을 적용해도 사업의 필수 의무를 수행할 수 없는 경우, 예외를 허용할 범위와 그 결과를 확인할 지표를 어떻게 정하겠습니까?"
                       ],
        "question_source":  "codex_cli",
        "human_field_realism":  true,
        "human_nonleading":  true,
        "human_concrete_dilemma":  true,
        "human_adaptive_count":  2,
        "human_overload_warning":  false
    },
    {
        "case_id":  "objective_evaluation",
        "type":  "토론면접",
        "question":  "[토론과제] 시범사업의 성과를 확정해야 하는데, 기획부서는 예정된 보고 일정을 지키기 위해 정량 실적을 우선 적용하자는 입장이고 현업부서는 지역별 운영 여건을 반영하지 않은 평가는 수용할 수 없다는 입장입니다. 두 입장의 근거를 확인한 뒤 일정 지연이나 관계 부서의 반발을 감수하고서라도 본인이 채택할 평가 기준을 하나로 결정하고, 직접 관철할 조치와 결과에 대한 책임 주체, 적용 범위, 미합의 시 이송 기준이 담긴 평가원칙안 한 장을 제시해 토론해 주십시오.",
        "follow_ups":  [
                           "방금 말씀하신 결정에서 상대 입장 중 수용한 근거와 배제한 근거는 무엇이며, 이를 확인하기 위해 어떤 성과자료와 현장 사실을 대조하겠습니까?",
                           "앞서 제시한 평가원칙안에서 예외로 남긴 대상은 무엇이며, 그 예외가 자의적으로 확대되지 않도록 누가 어떤 결과를 책임져야 합니까?",
                           "공동안에 이르지 못할 경우 결정권자에게 이송할 핵심 쟁점과 재검토 조건을 어떻게 정하겠습니까?"
                       ],
        "question_source":  "codex_cli",
        "human_field_realism":  true,
        "human_nonleading":  true,
        "human_concrete_dilemma":  true,
        "human_adaptive_count":  2,
        "human_overload_warning":  false
    },
    {
        "case_id":  "visualization_data_accuracy",
        "type":  "창의적 문제해결력면접",
        "question":  "내일 공개할 성과 화면에서 같은 지표가 원천 시스템별로 반복해서 다르게 나타나지만, 검증에 투입할 수 있는 인력은 한 명뿐입니다. 공개 지연을 감수하고 본인이 우선 보류하거나 수정할 데이터 범위를 하나로 결정한 뒤, 직접 수행할 최소 검증과 그 결과에 대한 본인의 책임이 드러나도록 대상 항목·검증 가설·중단 기준을 담은 판단 기록 한 장을 제시해 주십시오.",
        "follow_ups":  [
                           "방금 선택한 검증 가설을 틀렸다고 판단하게 만들 반증 자료는 무엇이며, 그 자료가 나오면 보류 범위를 어떻게 바꾸겠습니까?",
                           "앞서 제시한 최소 검증에서 관찰 결과가 중단 기준에 정확히 걸칠 경우 어떤 결정을 내리고 그 결과를 어떻게 설명하겠습니까?",
                           "같은 불일치를 다음 갱신 때 조기에 발견하도록 원천별 대조 규칙과 변경 이력을 어떻게 남기겠습니까?"
                       ],
        "question_source":  "codex_cli",
        "human_field_realism":  true,
        "human_nonleading":  true,
        "human_concrete_dilemma":  true,
        "human_adaptive_count":  2,
        "human_overload_warning":  false
    },
    {
        "case_id":  "data_use_ethics",
        "type":  "경험면접",
        "question":  "마감이나 업무 편의를 위해 확보한 자료를 그대로 분석에 쓰자는 요구를 받았지만, 수집 목적·접근권한·공유 대상 중 하나가 맞지 않아 사용 범위를 제한하거나 사용을 거절했던 실제 사례를 설명해 주십시오. 본인이 감수한 일정 지연이나 관계자의 반발, 직접 취한 조치와 그 결과에 대한 책임이 함께 드러나도록 당시의 판단을 남긴 승인·반려 기록 한 건을 중심으로 말씀해 주십시오.",
        "follow_ups":  [
                           "방금 언급한 자료에서 실제 업무 목적과 맞지 않았던 이용 범위는 무엇이었으며, 그 판단을 뒷받침한 확인 내용은 무엇입니까?",
                           "앞서 말씀하신 조치로 일정이나 관계자에게 어떤 영향이 생겼고, 본인이 책임진 결과를 승인 기록이나 변경 내역으로 어떻게 확인할 수 있습니까?",
                           "직접 경험이 없다면 유사한 자료 이용 상황을 제시하고, 사용을 허용할 범위와 승인 또는 반려 기록에 남길 내용을 설명해 주십시오."
                       ],
        "question_source":  "codex_cli",
        "human_field_realism":  true,
        "human_nonleading":  true,
        "human_concrete_dilemma":  null,
        "human_adaptive_count":  2,
        "human_overload_warning":  false
    },
    {
        "case_id":  "document_register",
        "type":  "인바스켓면접",
        "question":  "오늘 회신해야 하지만 공식 접수번호가 없는 협조 공문, 이미 결재된 문서의 수신처 정정 요청, 오후 결재 마감이 임박했지만 본인에게 승인 권한이 없는 보고 문서가 동시에 도착했습니다. 각 문서의 처리 순서와 직접처리·위임·보고 주체를 결정하고, 접수 시각·처리 담당자·현재 상태·보류 또는 정정 이력이 보이는 전자문서 접수대장 처리안 한 장을 작성해 주십시오.",
        "follow_ups":  [
                           "방금 1순위로 둔 문서와 처리 주체를 선택한 근거는 무엇이며, 그 선택으로 발생할 수 있는 접수 누락이나 권한 초과를 어떻게 막겠습니까?",
                           "앞서 작성한 처리안에서 보류 또는 정정한 문서의 이전 기록과 새 기록을 어떻게 연결하여 변경 경위를 확인할 수 있게 하겠습니까?",
                           "대장 기록과 전자문서시스템의 정보가 다를 때 어떤 항목을 서로 대조하고, 일치 여부를 어떻게 확인하겠습니까?"
                       ],
        "question_source":  "codex_cli",
        "human_field_realism":  true,
        "human_nonleading":  true,
        "human_concrete_dilemma":  true,
        "human_adaptive_count":  2,
        "human_overload_warning":  false
    }
]
"""
)


BLIND_NONLEADING_NEGATIVE_CASE_IDS = frozenset(
    {
        "numeric_accuracy",
        "plan_actual_gap",
        "measurement_framework",
        "resource_allocation_fairness",
        "objective_evaluation",
        "visualization_data_accuracy",
        "data_use_ethics",
    }
)

# The JSON block also preserves the initial human-read fields as generated.
# Apply the later blind adjudication in one auditable mapping rather than
# silently rewriting the source wording of the 16 questions.
for _item in FINAL_V16_REALISM_CASES:
    _item["human_nonleading"] = (
        _item["case_id"] not in BLIND_NONLEADING_NEGATIVE_CASE_IDS
    )


@pytest.mark.parametrize(
    "item",
    FINAL_V16_REALISM_CASES,
    ids=lambda item: str(item["case_id"]),
)
def test_final_v16_actual_generation_matches_independent_human_realism(
    item: dict[str, object],
) -> None:
    result = evaluate_question_realism(item)

    assert item["human_field_realism"] is True
    expected_nonleading = item["case_id"] not in BLIND_NONLEADING_NEGATIVE_CASE_IDS
    assert item["human_nonleading"] is expected_nonleading
    assert item["human_adaptive_count"] == 2
    assert item["human_overload_warning"] is False

    assert result["policy_version"] == "field-realism-v3.14"
    assert result["passed"] is expected_nonleading, (
        item["case_id"],
        result["issues"],
    )
    assert result["checks"]["no_prescribed_answer"] is expected_nonleading
    if not expected_nonleading:
        assert "candidate_answer_prescribed" in result["issue_codes"]
        assert result["metrics"]["leading_or_assumptive_exposure_count"] >= 1
    assert result["metrics"]["adaptive_follow_up_count"] >= item["human_adaptive_count"]
    assert result["metrics"]["overload_warning"] is item["human_overload_warning"]

    if item["human_concrete_dilemma"] is True:
        assert result["checks"]["concrete_scenario"] is True
        assert result["metrics"]["scenario_signals"]["concrete_fact"] is True
        assert result["metrics"]["scenario_signals"]["dilemma"] is True
    else:
        assert "concrete_scenario" not in result["applicable_checks"]


def test_final_v16_human_realism_oracle_is_complete() -> None:
    assert len(FINAL_V16_REALISM_CASES) == 16
    assert len({item["case_id"] for item in FINAL_V16_REALISM_CASES}) == 16
    assert sum(item["human_field_realism"] is True for item in FINAL_V16_REALISM_CASES) == 16
    assert sum(item["human_nonleading"] is True for item in FINAL_V16_REALISM_CASES) == 9
    assert sum(item["human_nonleading"] is False for item in FINAL_V16_REALISM_CASES) == 7
    assert sum(item["human_concrete_dilemma"] is True for item in FINAL_V16_REALISM_CASES) == 11
    assert sum(item["human_adaptive_count"] >= 2 for item in FINAL_V16_REALISM_CASES) == 16
    assert sum(item["human_overload_warning"] is True for item in FINAL_V16_REALISM_CASES) == 0
