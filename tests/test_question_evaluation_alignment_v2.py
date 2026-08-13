from __future__ import annotations

from typing import Any

import pytest

from app.services.question_evaluation_alignment import (
    EVALUATION_ELICITATION_POLICY,
    evaluate_evaluation_elicitation_alignment,
)


# Frozen, report-independent copy of the sixteen questions generated from the
# real recruitment notice and NCS job-description PDFs after the v12 prompt
# changes.  The runtime report is intentionally not read by these tests.
LATEST_V12_ORACLE: tuple[dict[str, Any], ...] = (
    {
        "case_id": "indicator_definition",
        "type": "직무지식면접",
        "question": (
            "같은 참여자가 두 사업에 중복 집계되고 일부 실적은 기준일 뒤에 확정되어 "
            "부서별 월간 실적표가 서로 다릅니다. 이번 보고에 반영할 실적의 범위를 "
            "어떻게 판정하겠습니까? 포함·제외·중복 처리 결론이 기록된 정정표 한 "
            "장을 제시해 설명해 주세요."
        ),
        "follow_ups": (
            "방금 적용한 기준 가운데 기준일 뒤 확정된 실적을 처리한 근거는 무엇이며, "
            "그 근거가 적용되지 않는 예외는 무엇입니까?",
            "앞서 제시한 정정표에서 중복 실적을 놓칠 가능성이 있다면 어떤 원자료를 "
            "대조하고 어느 항목을 수정하시겠습니까?",
            "수정 완료 여부를 다른 담당자가 재확인할 수 있도록 어떤 출처와 변경 "
            "흔적을 남기겠습니까?",
        ),
        "evaluation_points": (
            "측정 대상과 관찰 기간을 구분하여 반영 범위를 판정함",
            "중복 집계와 사후 확정 건에 서로 구별되는 처리 규칙을 적용함",
            "판정 결과와 수정 내용을 대응시킨 정정표를 제시함",
            "재확인에 필요한 원자료 출처와 변경 흔적을 설명함",
        ),
        "mutation_follow_up": 0,
        "mutation_family": "unclassified_response_object",
    },
    {
        "case_id": "research_plan_review",
        "type": "상황면접",
        "question": (
            "공동연구 신청 마감일에 신청서의 총연구비와 세부 계획의 합계가 다르고, "
            "참여기관 한 곳의 역할 설명도 비어 있습니다. 연구책임자는 우선 제출해 "
            "달라고 요청하고 확인 가능한 기관 담당자는 오후에만 연락됩니다. 제출 "
            "가능 여부에 대한 첫 판단을 내리고, 불일치 항목·확인 근거·보완 상태가 "
            "담긴 제출 전 보완표를 제시해 주세요."
        ),
        "follow_ups": (
            "방금 말씀하신 첫 판단에서 금액 불일치와 역할 누락 중 하나를 먼저 확인한 "
            "이유는 무엇입니까?",
            "앞서 선택한 조치 후 기관 담당자의 설명이 기존 계획과 다르다면, 어느 "
            "문서를 어떻게 고치고 보완표의 상태를 어떻게 바꾸시겠습니까?",
            "연구책임자가 수정 없이 제출을 계속 요구할 경우 제출 품질을 지키기 위한 "
            "최소 보완 범위와 보고 경로를 설명해 주세요.",
        ),
        "evaluation_points": (
            "제출 가능 여부를 문서 간 불일치의 중요도에 근거해 판단함",
            "신청서와 세부 계획을 항목별로 대조하는 순서를 설명함",
            "불일치 내용과 근거 및 보완 상태를 연결한 보완표를 제시함",
            "추가 확인 결과에 따라 수정 대상과 보고 경로를 조정함",
        ),
        "mutation_follow_up": 1,
        "mutation_family": "conditional_revision",
    },
    {
        "case_id": "numeric_accuracy",
        "type": "경험면접",
        "question": (
            "마감 지연이나 관계 부서의 반발을 감수하더라도 부서 원자료와 집계표의 "
            "불일치를 바로잡기로 결정했던 실제 사례를 설명해 주세요. 당시 본인이 "
            "내린 결정과 직접 수행한 대조 작업, 수정 전후 값과 승인 흔적이 연결된 "
            "검증 기록을 중심으로 결과까지 말씀해 주세요."
        ),
        "follow_ups": (
            "방금 언급한 불일치를 발견했을 때 어떤 원자료를 다시 확인했고, 본인이 "
            "맡아 수행한 검산은 무엇이었습니까?",
            "앞서 말씀하신 결정으로 실제 감수한 일정상 또는 관계상의 부담은 "
            "무엇이었으며, 그대로 진행하지 않은 이유는 무엇입니까?",
            "수정 전후 결과를 입증할 수치나 승인 흔적이 없다면, 당시 결과를 "
            "객관적으로 확인할 수 있는 다른 기록은 무엇입니까?",
        ),
        "evaluation_points": (
            "마감 압박이나 반발 속에서 오류 수정을 선택한 본인의 결정을 설명함",
            "원자료와 집계표를 직접 대조하고 검산한 행동을 구체적으로 제시함",
            "결정으로 감수한 부담과 그 선택의 이유를 밝힘",
            "수정 전후 값과 승인 또는 대체 증거를 연결해 결과를 입증함",
        ),
        "mutation_follow_up": 1,
        "mutation_family": "decision_criteria",
    },
    {
        "case_id": "plan_actual_gap",
        "type": "발표면접",
        "question": (
            "세 사업의 월별 계획·집행·성과 추이표와 관련 민원 요약을 검토한 결과, "
            "한 사업은 집행액이 계획에 근접하지만 성과가 두 달 연속 크게 낮아졌습니다. "
            "이 사업에서 가장 중요한 차이의 원인을 하나로 판정하고, 계획값·실적값·"
            "원인 근거가 보이는 분석표 한 장으로 발표해 주세요."
        ),
        "follow_ups": (
            "발표에서 원인 근거로 선택한 수치가 단순한 시기 차이가 아니라는 점을 어떤 "
            "자료와 대조해 확인하시겠습니까?",
            "앞서 판정한 원인과 반대되는 자료가 추가로 확인된다면 분석표의 어느 "
            "부분을 어떻게 수정하시겠습니까?",
            "판정한 원인에 대응할 수 있는 조정 대안 두 가지를 비교하고, 제한된 "
            "예산에서 먼저 시행할 하나와 그 이유를 설명해 주세요.",
        ),
        "evaluation_points": (
            "계획과 집행 및 성과의 차이를 구분하여 핵심 원인 하나를 판정함",
            "계획값과 실적값 및 원인 근거가 대응되는 분석표를 구성함",
            "선택한 근거를 다른 자료와 대조하여 판정의 타당성을 설명함",
            "반대 자료나 예산 제약에 따라 분석 또는 후속 선택을 조정함",
        ),
        "mutation_follow_up": 0,
        "mutation_family": "evidence_basis",
    },
    {
        "case_id": "research_fund_rule",
        "type": "직무지식면접",
        "question": (
            "연구책임자가 제출한 회의비 집행서에는 사업 공통지침상 인정되는 증빙은 "
            "갖춰져 있지만, 지원기관의 해당 사업 안내에서는 참석자와 연구과제의 "
            "관련성을 추가로 확인하도록 정하고 있습니다. 공통지침과 사업별 안내의 "
            "목적·적용 대상·특례 여부를 따져 이 건의 인정, 보완 또는 반려 중 하나를 "
            "판단하고, 적용 근거·판정·요구 증빙이 담긴 검토기록 한 건을 제시해 주세요."
        ),
        "follow_ups": (
            "방금 적용 우선순위를 정할 때 언급한 근거가 이 과제에는 적용되지 않는다는 "
            "사실이 확인되면, 어떤 조건을 확인해 판정을 바꾸시겠습니까?",
            "앞서 제시한 검토기록에서 연구책임자가 가장 이의를 제기할 부분은 "
            "무엇이며, 그 오류나 누락을 어떤 원자료와 대조해 확인하시겠습니까?",
            "같은 유형의 집행서류가 다시 접수될 때 검토 편차를 줄이기 위한 완료 확인 "
            "기록에는 무엇을 남기겠습니까?",
        ),
        "evaluation_points": (
            "공통 기준과 사업별 기준의 목적·대상·특례를 구분해 적용 순서를 설명한다",
            "인정·보완·반려 중 하나의 판정과 그 판정을 바꾸는 예외 조건을 제시한다",
            "집행서류와 대조할 원자료 및 오류 확인 방법을 구체화한다",
            "적용 근거·판정·요구 증빙이 서로 대응하는 검토기록을 제시한다",
        ),
        "mutation_follow_up": 1,
        "mutation_family": "verification_method",
    },
    {
        "case_id": "agency_negotiation",
        "type": "토론면접",
        "question": (
            "[토론과제] 공동 연구사업의 중간보고가 임박한 가운데 주관기관은 일정 "
            "준수를 위해 핵심 결과만 먼저 제출하자는 입장이고, 검증기관은 원자료와 "
            "산출 과정까지 함께 받아야 검토를 시작할 수 있다는 입장입니다. 양측 "
            "주장의 근거를 확인한 뒤 이번 제출에서 수용할 경계를 결정하고, 합의 "
            "범위·유보 사항·책임 주체가 담긴 협의기록을 제시하세요. 합의가 어렵다면 "
            "남은 쟁점과 결정권자에게 넘길 기준을 기록하세요."
        ),
        "follow_ups": (
            "방금 수용한 상대 측 요구와 받아들이지 않은 요구를 나눈 기준은 무엇이며, "
            "그 기준을 확인하려면 어떤 일정·자료·검증 사실이 필요합니까?",
            "앞서 남겨 둔 유보 사항이 상대 기관의 필수 조건이라고 확인되면, 어디까지 "
            "양보하고 어느 지점부터 결정권자에게 넘기시겠습니까?",
            "협의 결과의 적용 대상과 예외, 이행 책임자 및 후속 확인 시점을 어떻게 "
            "정해야 같은 갈등이 반복되지 않겠습니까?",
        ),
        "evaluation_points": (
            "양측 입장을 판단하기 위해 확인할 일정·자료·검증 사실을 구체적으로 제시한다",
            "수용 범위와 불수용 범위를 일관된 교환 조건으로 구분한다",
            "합의 실패 시 남은 쟁점과 상위 결정권자 이송 기준을 명확히 한다",
            "합의 범위·유보 사항·책임 주체가 추적되는 협의기록을 제시한다",
        ),
        "mutation_follow_up": 0,
        "mutation_family": "unclassified_response_object",
    },
    {
        "case_id": "personal_data_protection",
        "type": "인바스켓면접",
        "question": (
            "오전 중 세 건이 동시에 도착했습니다. 한 시간 안에 보내 달라는 외부 "
            "연구자의 참여자 명단 요청에는 연락처까지 포함되어 있고 이용 목적과 제공 "
            "근거가 적혀 있지 않습니다. 오늘 결재가 필요한 인사 현황 보고서에는 "
            "다른 직원의 평가자료가 잘못 첨부되었으며, 오후까지 처리해야 하는 당사자의 "
            "정보 정정 요청은 담당자가 부재 중입니다. 업무 목적에 필요한 최소 항목만 "
            "권한 있는 사람에게 제공하고, 근거가 불명확한 제공은 보류한다는 원칙에 "
            "따라 처리 순서와 처리 주체를 결정한 뒤, 순서·주체·보류 사유가 담긴 "
            "처리대장을 제시해 주세요."
        ),
        "follow_ups": (
            "방금 1순위로 둔 건과 그 처리 주체를 선택한 이유를 목적의 명확성, 제공 "
            "근거, 피해 가능성 중 어느 기준으로 설명하시겠습니까?",
            "앞서 보류하거나 위임한 요청에서 권한 또는 이용 목적이 추가로 확인되면, "
            "제공 항목과 열람 범위를 어떻게 다시 정하시겠습니까?",
            "긴급한 외부 요청이라도 본인 동의 없이 처리할 수 있는 별도 근거가 "
            "제시된다면, 적용 대상과 최소 제공 범위를 어떤 기록으로 확인하겠습니까?",
        ),
        "evaluation_points": (
            "각 요청의 목적·제공 근거·권한 유무를 구분한다",
            "노출 및 권리 침해 위험을 반영해 처리 순서를 정한다",
            "직접 처리·위임·보류의 주체와 조건을 명확히 설명한다",
            "순서·주체·보류 사유가 남는 처리대장을 제시한다",
        ),
        "mutation_follow_up": 0,
        "mutation_family": "risk_control",
    },
    {
        "case_id": "measurement_framework",
        "type": "창의적 문제해결력면접",
        "question": (
            "사업 성과 집계에서 동일 참여자가 여러 프로그램에 참여할 때마다 중복 "
            "산입되어 분기별 결과가 흔들리고 있습니다. 부서장은 이번 분기의 높은 "
            "수치를 유지하길 원하지만, 실무팀은 이전 기간과 비교 가능한 기준을 "
            "요구하며 전담 인력은 한 명뿐입니다. 어느 한쪽에 불리한 결과가 나올 수 "
            "있음을 감수하고 이번 집계에 적용할 기준을 하나로 결정한 뒤, 측정 차원·"
            "포함 및 제외 조건·관찰 기간이 담긴 기준표를 제시해 주세요."
        ),
        "follow_ups": (
            "방금 정한 포함 및 제외 조건이 실제 누락 원인이라는 근거가 부족하다면, "
            "한 명의 인력으로 어떤 표본을 대조해 가설을 확인하시겠습니까?",
            "앞서 선택한 기준 때문에 보고 수치가 낮아지거나 기존 실적과 단절될 때 "
            "어떤 비용을 감수하고, 그 결과를 누구에게 어떤 근거로 설명하시겠습니까?",
            "검증 결과에 따라 기준을 채택하거나 중단할 판정 조건과 다음 집계부터 "
            "적용할 최소 실행 순서를 제시해 주세요.",
        ),
        "evaluation_points": (
            "중복 산입 문제를 측정 차원과 집계 단위의 불일치로 구체화한다",
            "측정 차원·포함 및 제외 조건·관찰 기간이 일관된 기준표를 제시한다",
            "불리한 수치가 예상되어도 비교 가능성을 위한 선택과 감수할 비용을 설명한다",
            "제한된 인력으로 수행할 표본 대조와 채택·중단 조건을 제시한다",
        ),
        "mutation_follow_up": 2,
        "mutation_family": "decision_stop",
    },
    {
        "case_id": "closing_report_rules",
        "type": "인바스켓면접",
        "question": (
            "오늘 결재 가능한 범위가 팀장 전결까지인 상황에서, 정오까지 답해야 하는 "
            "증빙 보완 요청, 오후 결재 예정인 결산 초안, 내일 기관장 회의에 쓰일 "
            "요약자료가 동시에 도착했습니다. 결산 초안에는 증빙이 확인되지 않은 "
            "금액이 확정값처럼 기재되어 있고 요약자료도 이를 인용하고 있습니다. "
            "무엇을 먼저 처리할지 판단하고, 각 문서의 처리 순서·처리 주체·미확인 "
            "금액의 표시 방식을 담은 처리결정표 한 장을 제시해 주십시오."
        ),
        "follow_ups": (
            "방금 1순위로 정한 문서와 처리 주체를 기준으로, 직접 처리·위임·상급자 "
            "보고 중 그 방식을 택한 이유와 가장 큰 누락 위험을 설명해 주십시오.",
            "앞서 제시한 미확인 금액 표시 방식이 결산 초안과 경영진용 요약자료에 "
            "동일하게 적용되기 어렵다면, 각 문서에서 본문·주석·잠정값을 어떻게 "
            "구분하겠습니까?",
            "확인되지 않은 증빙이 결재 시점까지 도착하지 않을 경우, 확정 보고와 "
            "잠정 보고의 경계를 어떤 기준으로 정하겠습니까?",
        ),
        "evaluation_points": (
            "마감과 결재 권한을 반영한 문서별 처리 순서",
            "직접 처리·위임·보고를 구분한 처리 주체 결정",
            "미확인 금액을 본문·주석·잠정값으로 구별하는 기록 방식",
            "증빙 미도착 시 확정과 잠정 보고를 나누는 적용 기준",
        ),
        "mutation_follow_up": 1,
        "mutation_family": "status_certainty",
    },
    {
        "case_id": "review_report_writing",
        "type": "경험면접",
        "question": (
            "예산계획표와 결산 원장의 실적이 서로 맞지 않았지만 보고 마감은 임박했던 "
            "실제 사례를 말씀해 주십시오. 본인이 가장 중요하다고 판정한 차이 한 건, "
            "그 판단을 위해 취한 행동, 그리고 계획값·실적값·차이 근거가 드러나도록 "
            "직접 작성한 검토보고서가 어떻게 활용되었는지 결과 증거와 함께 설명해 "
            "주십시오."
        ),
        "follow_ups": (
            "방금 중요하다고 판단한 차이에 대해, 어떤 원자료를 서로 대조했으며 다른 "
            "차이보다 먼저 보고할 만하다고 본 근거는 무엇이었습니까?",
            "앞서 언급한 검토보고서에서 본인이 직접 작성하거나 수정한 부분을 짚고, "
            "잠정값·증빙 부족·담당자 의견을 어떻게 구분해 독자가 오해하지 않게 "
            "했는지 설명해 주십시오.",
            "직접 경험이 없다면, 유사한 자료 불일치 상황을 가정하여 보고서의 핵심 "
            "표와 품질 확인 방법을 제시해 주십시오.",
        ),
        "evaluation_points": (
            "의사결정에 중요한 차이 한 건을 선별한 근거",
            "계획표·원장·증빙 등 원자료를 대조한 구체적 행동",
            "차이와 근거 및 불확실성을 구별한 보고서 구성",
            "승인·수정·후속 결정 등 보고서 활용 결과의 증거",
        ),
        "mutation_follow_up": 1,
        "mutation_family": "report_treatment",
    },
    {
        "case_id": "civil_form_process",
        "type": "상황면접",
        "question": (
            "관계기관 제출 서식에는 접수일과 처리 결과가 필수인데 접수일이 비어 있고, "
            "내부 민원 기록에는 날짜가 있으나 같은 건임을 확인할 식별정보 일부가 "
            "다릅니다. 제출 마감은 오늘이고 담당 기관에는 한 차례만 확인할 수 "
            "있습니다. 내부 기록의 날짜를 서식에 반영할 수 있는지 먼저 판단하고, "
            "항목별 출처·미확정 항목·수정 이력이 표시된 보완 서식 한 부를 제시해 "
            "주십시오."
        ),
        "follow_ups": (
            "방금 내린 첫 판단에서 두 기록을 같은 민원으로 본 근거가 부족하다면, "
            "담당 기관에 무엇을 우선 확인하고 답변에 따라 서식을 어떻게 바꾸겠습니까?",
            "앞서 제시한 보완 서식에 누락되었거나 출처가 불분명한 항목이 있다면, "
            "임의 기입을 막으면서 마감을 관리할 표시와 회신 기록을 어떻게 남기겠습니까?",
            "담당 기관의 답변이 마감 전에 오지 않을 때 제출·보완 예정 통지·기한 "
            "조정 요청 중 하나를 고르는 기준은 무엇입니까?",
        ),
        "evaluation_points": (
            "서식과 내부 기록의 동일 민원 여부를 가리는 확인 기준",
            "기관 확인 결과에 따라 기재 내용을 바꾸는 조건부 판단",
            "항목 출처와 미확정 상태 및 수정 이력이 남는 보완 서식",
            "기관 회신이 지연될 때 제출 절차를 선택하는 기준",
        ),
        "mutation_follow_up": 0,
        "mutation_family": "conditional_revision",
    },
    {
        "case_id": "resource_allocation_fairness",
        "type": "발표면접",
        "question": (
            "다음 분기 총인력과 예산이 동결된 가운데, 연구지원 세 사업이 모두 증액을 "
            "요구하고 있습니다. 한 사업은 최근 실적이 급감했지만 의무 지원 대상이 "
            "많고, 다른 사업은 실적이 높으나 특정 부서만 이용하며, 나머지 사업은 "
            "신규 과제라 비교 실적이 없습니다. 성과가 높은 사업에 몰아 달라는 "
            "경영진의 요구와 최소 서비스 유지를 요구하는 현장 의견이 충돌할 때 배분을 "
            "하나로 결정하고, 공통 기준·사업별 조정량·예외 사유가 보이는 배분안 한 "
            "장을 발표해 주십시오."
        ),
        "follow_ups": (
            "방금 적용한 공통 기준 때문에 가장 큰 감축을 받는 사업의 반발이 "
            "예상됩니다. 그 기준을 유지하며 본인이 감수할 불이익과 결정 결과에 대한 "
            "책임을 어떻게 설명하겠습니까?",
            "앞서 선택한 배분안과 비교해 탈락시킨 대안 하나를 제시하고, 어느 조건이 "
            "바뀌면 그 대안으로 전환할지 설명해 주십시오.",
            "배분 후 성과를 확인하려면 측정 대상의 포함·제외 범위와 관찰 기간을 "
            "어떻게 정하며, 누가 후속 점검을 맡아야 합니까?",
        ),
        "evaluation_points": (
            "서로 다른 사업에 일관되게 적용되는 배분 기준",
            "공통 기준과 조정량 및 예외가 연결된 배분안",
            "압박 속에서도 기준을 유지하며 감수할 비용과 결과 책임",
            "대안 전환 조건과 사후 측정 범위를 명확히 한 후속 판단",
        ),
        "mutation_follow_up": 0,
        "mutation_family": "outcome_accountability",
    },
    {
        "case_id": "objective_evaluation",
        "type": "토론면접",
        "question": (
            "[토론과제] 신규 문화사업의 성과를 판정하려는데, 기획부서는 모든 사업에 "
            "정량 증빙을 엄격히 적용해야 한다고 주장하고 현업부서는 참여자 특성과 장기 "
            "효과를 반영하지 않으면 성과가 왜곡된다고 맞섭니다. 두 입장을 검토한 뒤 "
            "어느 조건에서 어떤 근거를 우선할지 하나로 판단하고, 확인할 사실과 적용 "
            "범위가 담긴 공동 평가 원칙안 한 장을 제시하십시오. 합의가 어렵다면 남은 "
            "쟁점과 결정권자에게 넘길 기준을 원칙안에 표시하십시오."
        ),
        "follow_ups": (
            "방금 말씀하신 원칙을 정하기 전에 반드시 확인해야 할 자료나 사실은 "
            "무엇이며, 그 사실이 달라지면 판단을 어떻게 바꾸겠습니까?",
            "앞서 일부 수용한 상대 측 주장과 남겨 둔 예외를 기준으로, 수용할 수 없는 "
            "경계와 그로 인해 감수할 일정 지연 또는 내부 반발을 설명해 주십시오.",
            "공통 원칙의 적용 대상과 제외 대상을 어떻게 구분하고, 적용 결과를 누가 "
            "어떤 기록으로 점검해야 합니까?",
        ),
        "evaluation_points": (
            "정량 증빙과 현장 맥락의 우선 조건을 구별해 하나의 판정 원칙을 제시한다",
            "판단 전에 확인할 자료와 사실이 결론에 미치는 영향을 설명한다",
            "일정 지연이나 내부 반발을 감수하는 선택과 그 책임을 밝힌다",
            "적용 대상·예외·미합의 쟁점의 이송 기준이 드러나는 원칙안을 제시한다",
        ),
        "mutation_follow_up": 1,
        "mutation_family": "outcome_accountability",
    },
    {
        "case_id": "visualization_data_accuracy",
        "type": "창의적 문제해결력면접",
        "question": (
            "월간 성과 화면을 만들 때마다 같은 사업의 건수가 설문 추출본, 업무시스템 "
            "내보내기 파일, 부서 집계표에서 서로 다르게 나타납니다. 담당자는 게시 "
            "일정을 지키기 위해 한 출처만 쓰자고 하지만, 검증에 투입할 수 있는 인원은 "
            "한 명뿐입니다. 가장 가능성이 높은 원인 하나를 판단하고, 대조 항목·소규모 "
            "시험·중단 기준이 담긴 검증 실험표 한 장을 제시하십시오."
        ),
        "follow_ups": (
            "방금 선택한 원인 가설을 반박할 수 있는 자료는 무엇이며, 그 자료가 "
            "확인되면 다음 가설을 어떻게 정하겠습니까?",
            "앞서 제시한 시험 결과가 어느 모습이면 계속 진행하고 어느 모습이면 즉시 "
            "중단하겠습니까? 답변에 판정 경계가 없다면 이를 구체화해 주십시오.",
            "같은 불일치의 재발을 막기 위해 원자료 인수 단계에 남길 최소 확인 기록은 "
            "무엇입니까?",
        ),
        "evaluation_points": (
            "출처별 생성 시점·식별자·중복 여부와 연결되는 원인 하나를 제시한다",
            "제한된 인력으로 원인을 구별할 수 있는 작은 대조 시험을 설계한다",
            "반증 자료와 계속·중단 판정 경계를 명확히 설명한다",
            "재발 여부를 추적할 수 있는 원자료 인수 확인 기록을 제안한다",
        ),
        "mutation_follow_up": 2,
        "mutation_family": "metric_source",
    },
    {
        "case_id": "data_use_ethics",
        "type": "경험면접",
        "question": (
            "성과 보고 마감이 임박한 상황에서, 승인받은 이용 목적이나 접근 범위를 "
            "벗어날 수 있는 내부 자료를 쓰면 업무가 빨라지는 유혹을 받은 실제 사례를 "
            "설명해 주십시오. 당시 본인이 내린 사용 여부 판단과 직접 취한 행동, 감수한 "
            "일정상 불이익, 최종 결과를 말하고, 그 판단을 남긴 이용 판단 기록의 핵심 "
            "내용을 제시하십시오."
        ),
        "follow_ups": (
            "방금 언급한 자료의 당초 이용 목적과 본인이 허용 가능하다고 본 범위는 "
            "각각 무엇이었으며, 그 경계를 어떤 근거로 판단했습니까?",
            "앞서 말씀하신 협의 상대나 승인 기록 가운데 본인의 선택과 결과를 가장 "
            "분명히 입증하는 것은 무엇입니까? 결과 증거가 없다면 당시 확인할 수 "
            "있었던 변화를 설명해 주십시오.",
            "직접 경험이 없다면, 유사한 정보 취급 경험을 답하고 그것도 없다면 같은 "
            "상황에서 자료의 최소 사용 범위와 공유 대상을 어떻게 기록할지 설명하십시오.",
        ),
        "evaluation_points": (
            "자료의 당초 이용 목적과 허용 가능한 사용 범위를 구별한다",
            "편의를 포기하며 감수한 지연이나 추가 업무와 본인의 행동을 설명한다",
            "협의·승인·접근 변경 등 선택의 결과를 확인할 증거를 제시한다",
            "사용 여부·최소 범위·공유 대상을 담은 판단 기록을 제시한다",
        ),
        "mutation_follow_up": 0,
        "mutation_family": "document_purpose",
    },
    {
        "case_id": "document_register",
        "type": "인바스켓면접",
        "question": (
            "오늘 회신해야 하는 외부기관 협조 공문, 이미 접수번호가 부여됐지만 "
            "발신기관명이 잘못된 정정 요청, 부서장 결재가 끝나지 않은 내일 보고 문서가 "
            "동시에 도착했습니다. 본인은 접수와 등록은 할 수 있지만 정정 승인과 최종 "
            "결재 권한은 없습니다. 세 문서의 처리 순서와 각 처리 주체를 하나로 "
            "결정하고, 접수시각·현재 상태·보류 또는 이관 사유가 보이는 접수 등록표를 "
            "제시하십시오."
        ),
        "follow_ups": (
            "방금 1순위로 둔 문서와 처리 주체를 선택한 근거는 무엇이며, 직접 처리·"
            "보고·이관 가운데 다른 방식을 택하면 어떤 누락 위험이 생깁니까?",
            "앞서 보류하거나 이관한다고 한 문서가 기한 안에 돌아오지 않으면, 등록표의 "
            "상태와 변경 이력을 어떻게 갱신하고 누구에게 알리겠습니까?",
            "접수번호 중복, 첨부 누락, 발신정보 오류를 발견하기 위한 등록 완료 전 "
            "확인 절차를 순서대로 설명하십시오.",
        ),
        "evaluation_points": (
            "마감·현재 상태·권한 범위를 근거로 세 문서의 처리 순서를 정한다",
            "각 문서에 대해 직접 처리·보고·이관할 주체와 누락 위험을 설명한다",
            "접수시각·상태·보류 또는 이관 사유가 포함된 등록표를 작성한다",
            "중복 번호·첨부·발신정보와 변경 이력을 확인하는 절차를 제시한다",
        ),
        "mutation_follow_up": 2,
        "mutation_family": "duplicate_handling",
    },
)


def _item(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": case["type"],
        "question": case["question"],
        "follow_ups": list(case["follow_ups"]),
        "evaluation_points": list(case["evaluation_points"]),
    }


@pytest.mark.parametrize(
    "case",
    LATEST_V12_ORACLE,
    ids=lambda case: case["case_id"],
)
def test_frozen_latest_real_pdf_oracle_passes(case: dict[str, Any]) -> None:
    result = evaluate_evaluation_elicitation_alignment(_item(case))

    assert result["policy"] == EVALUATION_ELICITATION_POLICY
    assert result["decision"] == "pass", (case["case_id"], result)
    assert (
        result["metrics"]["matched_atom_count"] == result["metrics"]["point_atom_count"]
    )


@pytest.mark.parametrize(
    "case",
    LATEST_V12_ORACLE,
    ids=lambda case: case["case_id"],
)
def test_removing_eliciting_follow_up_exposes_hidden_atom(
    case: dict[str, Any],
) -> None:
    item = _item(case)
    item["follow_ups"].pop(case["mutation_follow_up"])

    result = evaluate_evaluation_elicitation_alignment(item)

    assert result["decision"] == "fail", (case["case_id"], result)
    assert any(
        issue["code"] == "unelicited_evaluation_atom"
        and issue["semantic_family"] == case["mutation_family"]
        for issue in result["issues"]
    ), (case["case_id"], result)


_CROSS_CASE_PAIRS = (
    ("indicator_definition", "data_use_ethics"),
    ("research_plan_review", "objective_evaluation"),
    ("numeric_accuracy", "document_register"),
    ("plan_actual_gap", "personal_data_protection"),
    ("research_fund_rule", "visualization_data_accuracy"),
    ("agency_negotiation", "review_report_writing"),
    ("measurement_framework", "civil_form_process"),
    ("closing_report_rules", "resource_allocation_fairness"),
)
_CASES_BY_ID = {case["case_id"]: case for case in LATEST_V12_ORACLE}


@pytest.mark.parametrize(("prompt_id", "criteria_id"), _CROSS_CASE_PAIRS)
@pytest.mark.parametrize("reverse", (False, True), ids=("forward", "reverse"))
def test_unrelated_cross_case_evaluation_swap_fails(
    prompt_id: str,
    criteria_id: str,
    reverse: bool,
) -> None:
    if reverse:
        prompt_id, criteria_id = criteria_id, prompt_id
    item = _item(_CASES_BY_ID[prompt_id])
    item["evaluation_points"] = list(_CASES_BY_ID[criteria_id]["evaluation_points"])

    result = evaluate_evaluation_elicitation_alignment(item)

    assert result["decision"] == "fail", (prompt_id, criteria_id, result)


@pytest.mark.parametrize(
    ("criterion", "family"),
    (
        ("개선안의 실행 책임자", "execution_owner"),
        ("검토서의 부서장 결재선", "approval_process"),
        ("처리 기록의 보존 기간", "record_retention"),
        ("재발 방지를 위한 전 직원 교육", "prevention_training"),
    ),
)
def test_generic_plan_does_not_open_protected_hidden_roles(
    criterion: str,
    family: str,
) -> None:
    item = {
        "type": "상황면접",
        "question": (
            "실적표와 원장이 맞지 않을 때 대조할 자료와 판단 기준을 정하고 수정 "
            "결과가 담긴 개선안을 제시하십시오."
        ),
        "follow_ups": [
            "어떤 자료를 먼저 확인합니까?",
            "그 판단 기준을 택한 이유는 무엇입니까?",
            "수정 결과를 무엇으로 검증합니까?",
        ],
        "evaluation_points": [
            "대조할 자료",
            "판단 기준",
            "수정 결과",
            criterion,
        ],
    }

    result = evaluate_evaluation_elicitation_alignment(item)

    assert result["decision"] == "fail"
    assert any(
        issue["code"] == "unelicited_evaluation_atom"
        and issue["semantic_family"] == family
        for issue in result["issues"]
    )


def test_protected_roles_in_scenario_premise_are_not_candidate_demands() -> None:
    item = {
        "type": "상황면접",
        "question": (
            "기존 계획에는 실행 담당자, 부서장 결재선, 5년 보존 기간, 전 직원 교육이 "
            "기재된 상황입니다. 두 자료의 불일치를 확인하고 적용할 판단 기준과 수정 "
            "순서를 제시하십시오."
        ),
        "follow_ups": [
            "먼저 볼 자료는 무엇입니까?",
            "그 기준을 선택한 이유는 무엇입니까?",
            "수정 결과를 어떻게 확인합니까?",
        ],
        "evaluation_points": [
            "실행 담당자",
            "부서장 결재선",
            "기록 보존 기간",
            "전 직원 재발 방지 교육",
        ],
    }

    result = evaluate_evaluation_elicitation_alignment(item)

    assert result["decision"] == "fail"
    assert {
        issue["semantic_family"]
        for issue in result["issues"]
        if issue["code"] == "unelicited_evaluation_atom"
    } == {
        "execution_owner",
        "approval_process",
        "record_retention",
        "prevention_training",
    }


def test_semantic_role_paraphrase_matches_without_latest_phrase_copy() -> None:
    item = {
        "type": "상황면접",
        "question": (
            "협약 신청액과 산출 내역의 합이 다릅니다. 신청 금액과 산출 내역을 "
            "항목별로 대조한 뒤 제출 가능성을 먼저 결정하고, 차이가 난 칸·확인 자료·보완 "
            "상태가 연결된 점검표를 제시하십시오."
        ),
        "follow_ups": [
            "첫 결정을 내린 까닭은 무엇입니까?",
            "담당자의 추가 회신이 기존 설명과 어긋나면 어느 칸을 고치고 상태를 "
            "어떻게 갱신하겠습니까?",
            "보완 없이 내 달라는 요구가 계속되면 누구에게 어떤 보고 경로로 "
            "알리겠습니까?",
        ],
        "evaluation_points": [
            "문서 차이의 중요도에 따른 제출 판단",
            "신청 금액과 산출 내역을 대조하는 절차",
            "차이와 확인 자료 및 보완 상태를 연결한 점검표",
            "추가 회신에 따른 수정 대상과 보고 경로 조정",
        ],
    }

    result = evaluate_evaluation_elicitation_alignment(item)

    assert result["policy"] == EVALUATION_ELICITATION_POLICY
    assert result["decision"] == "pass", result
