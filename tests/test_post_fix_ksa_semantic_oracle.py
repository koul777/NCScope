from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.services.question_quality_orchestrator import evaluate_ksa_measurement


@dataclass(frozen=True)
class PostFixSemanticCase:
    case_id: str
    method: str
    ksa_type: str
    factor: str
    question: str
    oracle: str


# Frozen independently from the generated report.  These are the exact 16
# post-fix Codex questions adjudicated under the consistent attitude rule as
# A13/B3; tests must not read
# the report at runtime or inherit its automatic gate labels.
POST_FIX_CASES = (
    PostFixSemanticCase(
        "indicator_definition",
        "직무지식면접",
        "지식",
        "지표 운영 정의서에 대한 개념",
        "부서마다 같은 참여 인원을 누적 인원, 순인원, 월말 인원으로 다르게 집계해 "
        "기관 실적표의 값이 서로 맞지 않습니다. 원자료에 수치나 산식이 제시되지 않은 "
        "상황에서 어떤 기준 문서를 근거로 포함 범위·중복 처리·측정 기간을 확정할지 "
        "판단하고, 예외 항목까지 표시한 수정 대조표를 제시해 주세요.",
        "A",
    ),
    PostFixSemanticCase(
        "research_plan_review",
        "상황면접",
        "기술",
        "연구 계획서 검토 기술",
        "공동연구 신청 마감이 오늘인데 신청서의 연구기간은 24개월, 첨부된 연구 "
        "일정표는 18개월로 적혀 있고 참여기관은 예산 변경 요청까지 보내왔습니다. "
        "제출 가능 여부에 관한 첫 판단을 내리고, 서로 어긋난 항목과 보완 책임자를 "
        "표시한 제출 전 점검표를 제시해 주세요.",
        "A",
    ),
    PostFixSemanticCase(
        "numeric_accuracy",
        "경험면접",
        "태도",
        "수리적 정확도를 확보하려는 자세",
        "보고 마감을 늦추지 말라는 요구가 있었지만 부서 원자료와 집계표의 수치가 "
        "맞지 않아 그대로 제출할 수 없었던 실제 사례를 설명해 주세요. 당시 본인이 "
        "감수한 불이익이나 부담에도 불구하고 어떤 결정을 실행했으며, 수정 전후 "
        "수치와 승인 결과가 담긴 검증 기록을 제시했는지 말씀해 주세요.",
        "A",
    ),
    PostFixSemanticCase(
        "plan_actual_gap",
        "발표면접",
        "기술",
        "계획대비 실적 분석 능력",
        "제공된 월별 자료에서 한 사업은 집행액이 목표선을 계속 웃도는데 핵심 "
        "산출량은 정체되어 있고, 관련 민원은 최근 두 달간 증가했습니다. 제한된 잔여 "
        "재원을 어느 조치에 먼저 배분할지 진단하고, 원인별 두 가지 대안과 선택 "
        "근거·후속 측정치를 담은 한 장짜리 조정안을 발표해 주세요.",
        "A",
    ),
    PostFixSemanticCase(
        "research_fund_rule",
        "직무지식면접",
        "지식",
        "부처별 연구개발사업 관리규정에 대한 지식",
        "지원기관 지침에는 회의비를 허용하지만 협약서에는 사전승인 대상으로 "
        "기재되어 있고, 승인 기록 없이 집행된 회의비 증빙이 정산 직전에 "
        "제출되었습니다. 어떤 근거를 우선 적용해 인정·보완·반려 여부를 판단할지 "
        "설명하고, 그 판단을 반영한 보완·반려 검토서를 제시해 주세요.",
        "A",
    ),
    PostFixSemanticCase(
        "agency_negotiation",
        "토론면접",
        "기술",
        "관련기관ㆍ단체 담당자와의 협상 기술",
        "[토론과제] 공동 연구사업의 착수일은 임박했지만 관계기관이 제출 자료의 "
        "일부를 사후 보완하자고 요구합니다. 연구원 측은 검증이 끝난 자료만 접수하자는 "
        "입장이고, 관계기관은 일정 준수를 위해 핵심 자료만 먼저 확정하자는 "
        "입장입니다. 양측이 확인할 사실을 정하고, 선제출 범위와 보완 조건을 담은 "
        "공동 합의안을 도출해 주세요.",
        "A",
    ),
    PostFixSemanticCase(
        "personal_data_protection",
        "인바스켓면접",
        "지식",
        "개인정보보호법",
        "오전 10시에 세 건이 동시에 도착했습니다. 외부기관은 오늘 정오까지 연구 "
        "참여자 명단 전체를 이메일로 요구했고, 인사팀은 잘못 기재된 계좌번호의 즉시 "
        "정정을 요청했으며, 부서장은 오후 1시 결재회의용 현황보고서에 참여자 연락처를 "
        "포함하라고 지시했습니다. 본인에게 외부 제공 승인권이 없는 상황에서 처리 "
        "순서와 각 건의 처리 주체를 결정한 인바스켓 처리결정표를 작성해 주세요.",
        "A",
    ),
    PostFixSemanticCase(
        "measurement_framework",
        "창의적 문제해결력면접",
        "태도",
        "성과측정 기준 수립을 위한 체계적 사고",
        "최근 세 분기 실적표에서 국제교류 사업의 공동행사 성과가 반복 누락됐지만, "
        "사업부서는 입력 업무가 늘어난다며 새 항목 도입에 반대하고 경영진은 다음 "
        "분기부터 즉시 수치를 요구합니다. 추가 인력 없이 누락 원인 하나를 우선 "
        "검증할 방법을 결정하고, 기존 업무 일부를 줄이거나 늦추는 비용까지 명시한 "
        "소규모 검증안을 제시해 주세요.",
        "B",
    ),
    PostFixSemanticCase(
        "closing_report_rules",
        "인바스켓면접",
        "지식",
        "회계보고서 및 분석·검토보고서 작성 요령",
        "오늘 오전 결산 초안의 미확정 수치를 바로 경영진 자료에 반영해 달라는 요청, "
        "증빙이 빠진 지출의 보완 요청, 오후 결재가 필요한 정정안이 동시에 "
        "도착했습니다. 본인에게는 최종 승인 권한이 없고 결재자는 한 시간 뒤 자리를 "
        "비웁니다. 문서의 용도와 수치 확정 상태를 기준으로 처리 순서와 담당 주체를 "
        "결정하고, 각 문서의 보고 가능 범위와 보류 사유가 표시된 처리목록을 제시해 "
        "주십시오.",
        "B",
    ),
    PostFixSemanticCase(
        "review_report_writing",
        "경험면접",
        "기술",
        "분석·검토보고서 작성 능력",
        "예산계획표와 결산 원장의 실적이 서로 맞지 않는 상태에서 보고 마감이 "
        "임박했던 실제 사례를 떠올려 주십시오. 당시 본인이 어떤 차이를 핵심 쟁점으로 "
        "판단해 원자료를 재구성했는지, 본인이 작성한 의사결정용 보고서와 그 활용 "
        "결과까지 설명해 주십시오. 직접 경험이 없다면 유사한 재무·실적 자료를 다룬 "
        "경험으로 답해 주십시오.",
        "A",
    ),
    PostFixSemanticCase(
        "civil_form_process",
        "상황면접",
        "기술",
        "관공서 서식 작성과 민원프로세스 파악 기술",
        "관계기관 제출 양식에는 사업비 변경액과 담당자 확인란이 필수인데, 내부 민원 "
        "회신 기록에는 변경 사유만 있고 신청인의 동의 일자와 처리 단계가 빠져 "
        "있습니다. 제출 기한은 오늘이며 담당 기관에는 한 차례만 확인할 수 있습니다. "
        "어떤 누락을 먼저 확인해 보완할지 판단하고, 제출본과 회신 이력이 일치하도록 "
        "수정한 서식안을 제시해 주십시오.",
        "A",
    ),
    PostFixSemanticCase(
        "resource_allocation_fairness",
        "발표면접",
        "태도",
        "합리적인 자원분배 기준을 설정하려는 자세",
        "세 연구지원 사업의 월별 집행률·성과 달성률·대기 과제 수를 정리한 표에서, "
        "한 사업은 집행률만 급증했고 다른 사업은 성과가 높지만 인력이 부족하며 세 "
        "번째 사업은 민원이 반복되고 있습니다. 추가 인력과 예산은 한 사업의 요구만 "
        "충족할 수 있고 각 책임자는 자기 사업의 전액 지원을 요구합니다. 어떤 근거로 "
        "지원 수준을 조정할지 결정하고, 사업별 배정량·유보 조건·성과 확인 시점이 "
        "담긴 자원 조정표를 발표해 주십시오.",
        "A",
    ),
    PostFixSemanticCase(
        "objective_evaluation",
        "토론면접",
        "태도",
        "평가에 대한 객관적 자세의 유지",
        "[토론과제] 신규 교육사업의 중간평가에서 참여 인원은 목표를 달성했지만 "
        "재참여율과 만족도는 낮게 나타났고, 사업부서는 다음 연도 예산 확보를 이유로 "
        "현장 사정을 반영해 ‘양호’로 평가하자고 요구합니다. ‘확인된 수치에 따라 "
        "등급을 낮춰야 한다’는 입장과 ‘사업 초기의 특수성을 인정해 등급을 유지해야 "
        "한다’는 입장 중 공동 기준을 정하고, 그 기준과 적용 범위가 담긴 합의안을 "
        "제시하십시오.",
        "A",
    ),
    PostFixSemanticCase(
        "visualization_data_accuracy",
        "창의적 문제해결력면접",
        "태도",
        "데이터의 정확성을 추구하는 태도",
        "다음 날 공개할 성과 화면에서 월별 참여자 수가 업무시스템 추출본, 부서 "
        "제출표, 전월 게시본마다 반복해서 다르고, 확인 작업에는 담당자 한 명만 투입할 "
        "수 있습니다. 가장 먼저 검증할 원인 가설 하나를 선택하고, 비교 대상·실행 "
        "절차·판정값·중단 조건을 담은 최소 검증 실험안을 제시하십시오.",
        "B",
    ),
    PostFixSemanticCase(
        "data_use_ethics",
        "경험면접",
        "태도",
        "기업 내 데이터 수집 및 활용에 대한 윤리적 태도",
        "성과보고 마감이나 업무 편의를 이유로 원래 승인된 용도·접근 범위와 맞지 "
        "않는 자료를 분석 또는 공유해 달라는 요청을 받은 실제 사례를 설명해 "
        "주십시오. 당시 자료를 사용·제한·거절하는 판단 중 무엇을 택해 직접 "
        "행동했는지와 그 결과를 보여 주는 승인 또는 사용기록의 핵심 내용을 "
        "제시하십시오.",
        "A",
    ),
    PostFixSemanticCase(
        "document_register",
        "인바스켓면접",
        "기술",
        "문서 대장 기록 능력",
        "오전 업무함에 오늘 정오까지 회신해야 하는 외부기관 협조 공문, 이미 등록된 "
        "발신번호의 수신처 정정 요청, 오후 회의 전 결재가 필요한 보고서가 동시에 "
        "들어왔습니다. 본인은 접수 등록과 담당자 배정은 가능하지만 발신 취소와 최종 "
        "결재 권한은 없을 때 처리 순서와 처리 주체를 결정하고, 등록번호·수발신 "
        "구분·담당자·기한·현재 상태·보류 사유가 포함된 접수·처리대장 초안을 "
        "제시하십시오.",
        "A",
    ),
)


# Frozen from the independently reviewed post-v11 generation round.  The
# report itself is deliberately not read by the test: a regenerated report
# cannot silently relabel the oracle.  Fifteen cases are A; the data-accuracy
# prompt is B because it asks for an experiment but not a costly accuracy-
# protecting choice, direct action, or ownership of the consequence.
POST_V11_QUESTIONS = {
    "indicator_definition": (
        "두 부서가 같은 참여자를 각각 포함해 월간 참여 인원이 서로 다르게 집계된 "
        "실적표가 제출되었습니다. 원자료를 아직 받지 못한 상태에서 어떤 집계 원칙을 "
        "적용할지 판단하고, 포함 대상·중복 처리·측정 기간·예외 사유·정정 이력을 "
        "담은 수정 기준표를 제시해 주십시오."
    ),
    "research_plan_review": (
        "공동연구 신청 마감이 오늘인데 신청서에는 외부기관 참여자가 기재되어 있고 "
        "첨부된 연구계획서의 역할표와 예산 설명에는 해당 기관이 빠져 있습니다. "
        "연구책임자는 우선 제출해 달라고 요청하는 상황에서 제출 가능 여부를 어떻게 "
        "판단할지 밝히고, 항목별 불일치·보완 내용·확인 근거를 담은 보완안을 제시해 "
        "주십시오."
    ),
    "numeric_accuracy": (
        "보고 마감이나 상급자의 제출 요구가 임박한 상황에서 부서 원자료와 집계표의 "
        "수치 불일치를 직접 발견했던 실제 사례를 말씀해 주십시오. 당시 본인이 어떤 "
        "결정을 내리고 행동했는지, 그로 인해 감수한 부담과 수정 전후 결과를 보여 "
        "주는 대조 기록을 함께 설명해 주십시오."
    ),
    "plan_actual_gap": (
        "제공된 월별 자료에는 한 사업의 집행률이 목표보다 낮아지는 동안 성과 건수는 "
        "급증했고, 같은 기간 결과물 품질 관련 민원도 늘어난 것으로 나타납니다. 예산 "
        "증액이 어려운 조건에서 차이의 핵심 원인을 진단하고 두 가지 조정 대안을 "
        "비교해 우선안을 선택한 뒤, 목표값·실적값·차이·원인·조치·담당·완료 시점·확인 "
        "지표를 담은 차이분석 보고서를 발표해 주십시오."
    ),
    "research_fund_rule": (
        "교육부 지원 과제의 회의비 집행서에 참석자 명단이 빠져 있고, 협약서에는 "
        "집행 가능 항목으로 적혀 있지만 당해 연도 지원기관 지침은 증빙 요건을 별도로 "
        "두고 있습니다. 어떤 근거가 이 건에 우선 적용되는지 판단하고, 보완·승인·반려 "
        "중 결론과 근거를 담은 검토의견서 한 건을 제시해 주세요."
    ),
    "agency_negotiation": (
        "[토론과제] 해외 학술행사 지원사업의 개막이 임박했는데, 주관기관은 일정 "
        "준수를 위해 미확인 참가자 자료도 먼저 접수하자고 하고 연구원은 오류 방지를 "
        "위해 검증이 끝난 자료만 받자는 입장입니다. 양측의 근거를 검토해 어느 "
        "범위까지 선접수를 허용할지 합의하고, 수용 범위·유보 조건·상호 이행사항이 "
        "담긴 합의안을 도출해 주세요."
    ),
    "personal_data_protection": (
        "오전 중 세 건이 동시에 도착했습니다. 한 시간 뒤가 기한인 외부기관의 연구 "
        "참여자 연락처 전체 회신 요청, 오늘까지 처리해야 하는 퇴직자의 인사기록 오기 "
        "정정 요청, 부서장 결재 전인 참여자 현황 보고서가 있으며, 본인에게는 외부 "
        "제공 승인 권한이 없습니다. 각 건의 목적, 적법한 처리 근거, 필요한 정보의 "
        "최소 범위와 권한을 기준으로 처리 순서와 처리 주체를 결정하고, 제공·보류·보고 "
        "구분이 담긴 처리결정표를 작성해 주세요."
    ),
    "measurement_framework": (
        "최근 세 분기 성과보고에서 국제행사 실적이 반복 누락됐지만, 행사부서는 "
        "등록자 수를, 평가부서는 실제 참석자 수를 실적으로 인정해야 한다고 "
        "주장합니다. 담당 인력은 한 명뿐이고 이번 분기 수치를 높여 달라는 압박도 "
        "있는 상황에서, 단기 수치 하락이나 추가 업무를 감수하더라도 어떤 측정 기준을 "
        "채택할지 판단하고 그 기준의 측정 차원·포함 및 제외 조건·관찰 기간과 소규모 "
        "검증 절차가 담긴 시험설계서를 제시해 주세요."
    ),
    "closing_report_rules": (
        "오늘 들어온 문서는 정오까지 답해야 하는 결산 초안의 증빙 누락 정정 요청, "
        "오후 결재 예정인 경영진 설명자료, 내일까지 회신할 연구책임자의 집행액 이의 "
        "제기입니다. 본인에게는 자료 수정 권한만 있고 최종 승인 권한은 없을 때, "
        "문서의 완결성과 승인 가능성을 기준으로 처리 순서와 처리 주체를 결정하고, 각 "
        "문서의 확정값·잠정값·근거자료·결재 필요 여부를 구분한 처리결정표를 제시해 "
        "주십시오."
    ),
    "review_report_writing": (
        "보고 마감이 임박한 상황에서 예산계획표, 회계 원장, 부서 제출 실적의 금액이나 "
        "분류가 서로 맞지 않았던 실제 사례를 설명해 주십시오. 당시 본인이 "
        "의사결정자에게 반드시 알려야 할 차이를 어떻게 판별했으며, 직접 작성한 차이 "
        "검토보고서와 그 보고서가 활용된 결과를 제시해 주십시오."
    ),
    "civil_form_process": (
        "관계기관 제출 서식에는 사업기간 전체의 지급액을 적도록 안내되어 있지만, "
        "내부 민원 회신 기록에는 당해 연도 지급액만 기재하라는 담당자 통화 내용이 "
        "남아 있고 제출 기한은 오늘입니다. 어느 범위를 기재할지 처음 판단한 뒤, "
        "필수 기재란·첨부자료·수정 사유·확인 상태가 표시된 보완 서식을 제시해 "
        "주십시오."
    ),
    "resource_allocation_fairness": (
        "세 연구지원 사업의 월별 집행률·성과 달성률·민원 건수 자료에서 한 사업의 "
        "집행률만 급등했고, 각 책임자는 다음 분기 인력과 예산의 증액을 동시에 "
        "요구하고 있습니다. 총자원이 늘지 않는 조건에서 급등 원인을 진단하고 두 "
        "가지 조정 대안을 비교한 뒤 하나를 선택하여, 배분 기준·포함 및 제외 대상·적용 "
        "기간·사업별 조정량·성과 확인 지표가 담긴 실행표를 발표해 주십시오."
    ),
    "objective_evaluation": (
        "[토론과제] 신규 문화사업의 중간평가에서 이용자 수는 목표를 넘었지만, 현업은 "
        "취약계층 참여 저조와 조사 표본의 편중을 이유로 등급 조정을 요구하고 "
        "있습니다. 한쪽은 공표된 수치 기준대로 즉시 판정하자는 입장이고, 다른 쪽은 "
        "현장 사정을 반영해 기준을 완화하자는 입장입니다. 어떤 근거까지 인정할지 "
        "토론하여, 양쪽 사업에 일관되게 적용할 공동 판정 원칙안을 제시해 주십시오."
    ),
    "visualization_data_accuracy": (
        "월별 성과 화면에서 같은 사업의 참여자 수가 원장 자료보다 계속 크게 표시되며, "
        "담당 부서는 내일 보고를 위해 기존 수치를 유지하라고 요청합니다. 한 명의 "
        "인력으로 일부 자료만 점검할 수 있을 때 무엇을 우선 원인으로 판단할지 정하고, "
        "원자료 키 대조 방법·표본 범위·채택 또는 중단 기준이 담긴 최소 검증 실험서를 "
        "제시해 주십시오."
    ),
    "data_use_ethics": (
        "성과보고 마감이 임박한 상황에서 접근 권한이나 당초 이용 목적이 불분명한 내부 "
        "자료를 분석에 포함해 달라는 요청을 받은 실제 사례를 설명해 주십시오. 당시 "
        "본인이 사용 가능 범위를 어떻게 판단하고 행동했는지, 그로 인한 일정상 비용과 "
        "최종 결과를 보여 주는 사용범위 판단 기록을 함께 말씀해 주십시오."
    ),
    "document_register": (
        "오늘 오전, 정오까지 회신해야 하는 외부기관 협조 공문, 이미 잘못 등록된 "
        "발신번호의 정정 요청, 오후 회의 전 승인이 필요한 결재 문서가 동시에 "
        "도착했습니다. 본인에게는 접수와 담당자 지정 권한만 있고 정정 승인권과 최종 "
        "결재권은 없을 때 처리 순서를 결정하고, 접수시각·문서번호·발신처·담당자·처리 "
        "상태·보류 사유·변경 이력이 포함된 접수대장 초안을 제시해 주십시오."
    ),
}

POST_V11_ORACLE = {
    case.case_id: (
        "B" if case.case_id == "visualization_data_accuracy" else "A"
    )
    for case in POST_FIX_CASES
}


def _item(case: PostFixSemanticCase, source: str) -> dict[str, object]:
    return {
        "type": case.method,
        "question_focus": case.factor,
        "question_focus_type": case.ksa_type,
        "question_focus_surface": "내부 추적용 표면어",
        "question_source": source,
        "question_evidence_required": True,
        "question_evidence_id": f"ksa_{case.case_id}",
        "question": case.question,
    }


@pytest.mark.parametrize("case", POST_FIX_CASES, ids=lambda case: case.case_id)
@pytest.mark.parametrize("source", ["openai_api", "codex_cli", "claude_code"])
def test_post_fix_corpus_matches_independent_human_oracle(
    case: PostFixSemanticCase,
    source: str,
) -> None:
    result = evaluate_ksa_measurement(_item(case, source))

    assert case.factor not in case.question
    assert result["passed"] is (case.oracle == "A"), result
    if case.oracle == "A":
        assert all(result["checks"].values()), result
    else:
        assert result["checks"]["focus_visible"] is False, result
        assert result["checks"]["ksa_type_operationalized"] is False, result


@pytest.mark.parametrize(
    ("case_id", "missing_meaning"),
    [
        ("measurement_framework", "measurement-definition chain"),
        ("closing_report_rules", "report-writing-rule chain"),
        (
            "visualization_data_accuracy",
            "costly accuracy choice and responsibility chain",
        ),
    ],
)
def test_post_fix_b_cases_fail_the_factor_owned_chain(
    case_id: str,
    missing_meaning: str,
) -> None:
    del missing_meaning  # readable parametrized test id documents the semantic gap
    case = next(case for case in POST_FIX_CASES if case.case_id == case_id)

    result = evaluate_ksa_measurement(_item(case, "openai_api"))

    assert result["checks"]["focus_visible"] is False, result
    assert result["checks"]["ksa_type_operationalized"] is False, result
    assert result["passed"] is False, result


@pytest.mark.parametrize("case", POST_FIX_CASES, ids=lambda case: case.case_id)
@pytest.mark.parametrize("source", ["openai_api", "codex_cli", "claude_code"])
def test_post_v11_human_oracle_matches_all_provider_paths(
    case: PostFixSemanticCase,
    source: str,
) -> None:
    question = POST_V11_QUESTIONS[case.case_id]
    result = evaluate_ksa_measurement(
        {
            **_item(case, source),
            "question": question,
        }
    )

    assert case.factor not in question
    expected = POST_V11_ORACLE[case.case_id]
    assert result["passed"] is (expected == "A"), result
    if expected == "A":
        assert all(result["checks"].values()), result
    else:
        assert result["checks"]["focus_visible"] is False, result
        assert result["checks"]["ksa_type_operationalized"] is False, result


def test_indicator_bridge_accepts_natural_authority_and_artifact_substitution() -> None:
    case = next(case for case in POST_FIX_CASES if case.case_id == "indicator_definition")
    question = (
        "두 사업의 같은 참여자가 중복 산입되어 월간 참여 인원이 서로 다른 실적표가 "
        "제출되었습니다. 원자료가 아직 없는 상황에서 데이터 사전을 적용해 포함 대상, "
        "중복 처리, 측정 기간과 예외 사유를 판단하고, 변경 이력을 담은 정합성 점검 "
        "결과서를 제시해 주십시오."
    )

    result = evaluate_ksa_measurement(
        {**_item(case, "codex_cli"), "question": question}
    )

    assert result["passed"] is True, result


def test_indicator_bridge_rejects_keyword_salad() -> None:
    case = next(case for case in POST_FIX_CASES if case.case_id == "indicator_definition")
    question = (
        "실적표 불일치 참여인원 사업 데이터사전 포함대상 중복처리 측정기간 예외사유 "
        "판단 변경이력 정합성점검결과서 상황 행동 결과를 제시해 주십시오."
    )

    result = evaluate_ksa_measurement(
        {**_item(case, "openai_api"), "question": question}
    )

    assert result["checks"]["focus_visible"] is False, result
    assert result["checks"]["ksa_type_operationalized"] is False, result
    assert result["passed"] is False, result


def test_indicator_bridge_rejects_negated_operation_and_artifact() -> None:
    case = next(case for case in POST_FIX_CASES if case.case_id == "indicator_definition")
    question = (
        "두 사업의 같은 참여자가 중복 산입되어 월간 참여 인원이 서로 다른 실적표가 "
        "제출되었습니다. 데이터 사전은 적용하지 말고 포함 대상, 중복 처리, 측정 기간, "
        "예외 사유와 변경 이력만 검토하십시오. 정합성 점검 결과서는 만들지 말고 관련 "
        "용어를 설명해 주십시오."
    )

    result = evaluate_ksa_measurement(
        {**_item(case, "claude_code"), "question": question}
    )

    assert result["checks"]["focus_visible"] is False, result
    assert result["checks"]["ksa_type_operationalized"] is False, result
    assert result["passed"] is False, result


@pytest.mark.parametrize(
    ("target_case_id", "source_case_id"),
    [
        ("indicator_definition", "measurement_framework"),
        ("measurement_framework", "indicator_definition"),
    ],
)
def test_adjacent_metric_factors_reject_cross_domain_substitution(
    target_case_id: str,
    source_case_id: str,
) -> None:
    cases = {case.case_id: case for case in POST_FIX_CASES}
    target = cases[target_case_id]
    source = cases[source_case_id]

    result = evaluate_ksa_measurement(
        {
            **_item(target, "codex_cli"),
            "type": source.method,
            "question": POST_V11_QUESTIONS[source_case_id],
        }
    )

    assert result["checks"]["focus_visible"] is False, result
    assert result["checks"]["ksa_type_operationalized"] is False, result
    assert result["passed"] is False, result


@pytest.mark.parametrize(
    ("target", "source"),
    [
        (target, source)
        for target in POST_FIX_CASES
        for source in POST_FIX_CASES
        if target.case_id != source.case_id
    ],
    ids=[
        f"{target.case_id}-rejects-{source.case_id}"
        for target in POST_FIX_CASES
        for source in POST_FIX_CASES
        if target.case_id != source.case_id
    ],
)
def test_post_v11_questions_do_not_measure_a_different_planned_ksa(
    target: PostFixSemanticCase,
    source: PostFixSemanticCase,
) -> None:
    """Keep all sixteen factor-owned bridges disjoint, not only KPI neighbors."""

    result = evaluate_ksa_measurement(
        {
            **_item(target, "openai_api"),
            "type": source.method,
            "question": POST_V11_QUESTIONS[source.case_id],
        }
    )

    assert result["passed"] is False, {
        "target": target.case_id,
        "source": source.case_id,
        "result": result,
    }


@pytest.mark.parametrize("case", POST_FIX_CASES, ids=lambda case: case.case_id)
def test_post_v11_semantic_bridge_needs_both_incident_and_response_task(
    case: PostFixSemanticCase,
) -> None:
    """A scenario-only preface or an output-only command must not pass alone."""

    question = POST_V11_QUESTIONS[case.case_id]
    sentences = [part.strip() for part in question.split(". ") if part.strip()]
    assert len(sentences) >= 2, case.case_id

    incident_only = sentences[0]
    response_only = sentences[-1]

    assert evaluate_ksa_measurement(
        {**_item(case, "openai_api"), "question": incident_only}
    )["passed"] is False
    assert evaluate_ksa_measurement(
        {**_item(case, "openai_api"), "question": response_only}
    )["passed"] is False
