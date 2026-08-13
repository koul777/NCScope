from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import pytest

from app.services.question_evaluation_alignment import (
    EVALUATION_ELICITATION_POLICY,
    evaluate_evaluation_elicitation_alignment,
)


METHOD_FIXTURES: tuple[dict[str, Any], ...] = (
    {
        "id": "experience",
        "type": "경험면접",
        "question": (
            "해외기관 요구를 서로 다르게 해석해 일정 차질을 막았던 실제 상황에서 "
            "본인 역할, 판단과 직접 행동, 확인 가능한 결과를 설명하십시오."
        ),
        "follow_ups": [
            "그 해석과 대응을 선택한 이유는 무엇입니까?",
            "합의 결과가 반영됐음을 어떤 기록으로 확인했습니까?",
            "다시 맡는다면 소통 절차를 어떻게 바꾸겠습니까?",
        ],
        "evaluation_points": [
            "당시 상황과 본인 역할",
            "선택 근거와 직접 행동",
            "합의 결과를 입증하는 기록",
            "유사 상황에 적용할 소통 절차 개선",
        ],
        "negative": "갈등을 중재한 리더십",
        "negative_family": "mediation_leadership",
    },
    {
        "id": "situation",
        "type": "상황면접",
        "question": (
            "집행 자료와 요구안이 맞지 않을 때 무엇을 먼저 확인하고 어느 항목을 "
            "조정할지 판단한 조정안을 제시하십시오."
        ),
        "follow_ups": [
            "먼저 확인할 사실과 자료는 무엇입니까?",
            "조건이 바뀌면 조정 기준을 어떻게 수정합니까?",
            "결정 뒤 편차를 무엇으로 재검산합니까?",
        ],
        "evaluation_points": [
            "사실과 자료 확인",
            "조정 기준과 선택 근거",
            "조건 변화에 따른 판단 수정",
            "결정 뒤 편차 재검산",
        ],
        "negative": "연간 보고 서식과 직원 재발방지 교육",
        "negative_family": "report_format",
    },
    {
        "id": "presentation",
        "type": "발표면접",
        "question": (
            "누적 실적표와 민원 자료로 원인을 진단하고 두 대안을 비교하여 목표값, "
            "측정자료, 확인주기가 담긴 실행안을 발표하십시오."
        ),
        "follow_ups": [
            "반대 자료가 나오면 진단을 어떻게 바꾸겠습니까?",
            "자원 제약이 생기면 어느 대안을 우선할지 이유는 무엇입니까?",
            "목표 미달이면 어떤 지표를 바꾸겠습니까?",
        ],
        "evaluation_points": [
            "자료를 연결한 원인 진단",
            "대안 비교와 우선순위 근거",
            "자원 제약을 반영한 실행계획",
            "목표값·측정자료·확인주기의 측정 설계",
        ],
        "negative": "발표 뒤 부서장 결재를 받는 승인 절차",
        "negative_family": "approval_process",
    },
    {
        "id": "discussion",
        "type": "토론면접",
        "question": (
            "일정 준수와 자료 검증이 충돌합니다. 어느 쪽을 우선할지 근거를 "
            "검토하고 적용 범위와 이행 책임이 담긴 공동 합의안을 토론하십시오."
        ),
        "follow_ups": [
            "본인의 입장과 그 근거를 먼저 제시하십시오.",
            "상대 근거에서 무엇을 수용하고 무엇은 수용하지 않을지 경계를 설명하십시오.",
            "합의가 되지 않으면 공동안의 범위와 실행 책임을 어떻게 정합니까?",
        ],
        "evaluation_points": [
            "근거 있는 입장",
            "상대 근거의 요약 및 검토",
            "수용·불수용 경계 조정",
            "공동 합의안의 적용 범위와 이행 책임",
        ],
        "negative": "미합의 쟁점의 상급위원회 이송 문서 결재선",
        "negative_family": "approval_process",
    },
    {
        "id": "inbasket",
        "type": "인바스켓면접",
        "question": (
            "마감, 업무 영향도, 민감정보, 처리 권한이 서로 다른 세 요청의 순서와 "
            "처리 주체를 정해 상태와 보류 사유가 담긴 처리표를 작성하십시오."
        ),
        "follow_ups": [
            "첫 문서를 직접 처리할지, 위임할지, 보류할지 정하고 이유를 설명하십시오.",
            "과도한 개인정보는 무엇을 제외합니까?",
            "권한자가 회신하지 않으면 보류, 보고, 후속 시각을 무엇으로 기록합니까?",
        ],
        "evaluation_points": [
            "마감·영향도·권한에 따른 우선순위",
            "직접처리·위임·보류의 주체",
            "정보 최소화와 유출 위험 통제",
            "보류·보고·후속시각의 기록",
        ],
        "negative": "처리 뒤 전 직원 보안 재교육 계획",
        "negative_family": "prevention_training",
    },
    {
        "id": "job_knowledge",
        "type": "직무지식면접",
        "question": (
            "지침과 계약의 기준이 충돌할 때 어느 근거를 우선 적용해 인정, 보완, "
            "반려를 판단하고 검토서를 제시하십시오."
        ),
        "follow_ups": [
            "적용 대상이 다르면 효력 범위를 어떻게 확인합니까?",
            "예외를 인정하면 어떤 증빙을 검토서에 기재합니까?",
            "반복 오류를 무엇으로 재검산하고 예방합니까?",
        ],
        "evaluation_points": [
            "근거의 적용 범위와 근거 우선 적용",
            "조건별 인정·보완·반려 판단",
            "예외 증빙과 검토서 기재",
            "반복 오류 재검산 방식",
        ],
        "negative": "규정 반대자를 설득하는 갈등 중재 리더십",
        "negative_family": "mediation_leadership",
    },
    {
        "id": "creative",
        "type": "창의적 문제해결력면접",
        "question": (
            "데이터 값이 반복해 달라지는 원인 가설 하나를 선택하고 제한 자원 안에서 "
            "수행할 최소 실험의 일정, 측정자료, 중단 기준을 설계하십시오."
        ),
        "follow_ups": [
            "반증 자료가 나오면 가설을 어떻게 수정합니까?",
            "일정 압박이 생기면 실험을 어떻게 조정합니까?",
            "중단한 뒤 어떤 조건에서 다른 대안으로 전환하고 다음 검증을 진행합니까?",
        ],
        "evaluation_points": [
            "검증 가능한 원인 가설",
            "제한 자원 안의 실험 설계",
            "반증자료에 따른 가설 수정",
            "중단·전환 및 다음 검증 조건",
        ],
        "negative": "기관 전체 예산 일정과 해당 부서 인력 편성",
        "negative_family": "organization_staffing",
    },
)


# Frozen from the post-fix 2026-08-14 generation.  The runtime report is not a
# test dependency; these selected cases lock the semantic boundaries that were
# responsible for the calibration delta.
POST_FIX_SELECTED_CASES: tuple[tuple[str, str, dict[str, Any]], ...] = (
    (
        "indicator_definition",
        "pass",
        {
            "type": "직무지식면접",
            "question": (
                "부서마다 같은 참여 인원을 누적 인원, 순인원, 월말 인원으로 다르게 "
                "집계해 기관 실적표의 값이 서로 맞지 않습니다. 원자료에 수치나 산식이 "
                "제시되지 않은 상황에서 어떤 기준 문서를 근거로 포함 범위·중복 처리·"
                "측정 기간을 확정할지 판단하고, 예외 항목까지 표시한 수정 대조표를 "
                "제시해 주세요."
            ),
            "follow_ups": [
                "방금 적용하겠다고 한 근거가 연도 중간에 개정되었다면, 개정일 전후 "
                "자료를 어떻게 구분하고 수정 대조표에 어떤 오류 위험을 표시하겠습니까?",
                "앞서 설명한 예외 가운데 하나가 기준 문서에 명시되지 않았다면, 임의 "
                "집계를 피하기 위해 누구의 어떤 확인을 받아 어떤 값으로 반영하겠습니까?",
                "수정 완료 여부를 모든 지원자에게 동일하게 검증한다면 원자료, 집계 "
                "결과, 승인 흔적 사이에서 무엇을 대조하겠습니까?",
            ],
            "evaluation_points": [
                "포함 대상과 제외 대상을 구분하는 근거를 제시함",
                "동일 대상의 중복 여부와 측정 시점을 일관되게 판정함",
                "기준 변경 또는 미규정 사례의 처리 원칙과 오류 위험을 설명함",
                "수정 전후 값과 근거를 추적할 수 있는 대조표를 구성함",
            ],
        },
    ),
    (
        "numeric_accuracy",
        "pass",
        {
            "type": "경험면접",
            "question": (
                "보고 마감을 늦추지 말라는 요구가 있었지만 부서 원자료와 집계표의 "
                "수치가 맞지 않아 그대로 제출할 수 없었던 실제 사례를 설명해 주세요. "
                "당시 본인이 감수한 불이익이나 부담에도 불구하고 어떤 결정을 실행했으며, "
                "수정 전후 수치와 승인 결과가 담긴 검증 기록을 제시했는지 말씀해 주세요."
            ),
            "follow_ups": [
                "방금 언급한 불일치를 발견하게 한 대조 항목은 무엇이었으며, 그 항목을 "
                "다시 계산하거나 원자료까지 추적한 본인의 행동을 구체적으로 설명해 주세요.",
                "앞서 말씀하신 결정으로 일정 지연이나 관계 부서의 반발이 발생했다면, "
                "본인이 감수한 부담과 최종 결과를 보여 주는 수치 또는 승인 흔적은 "
                "무엇이었습니까?",
                "직접 경험이 없다면 유사한 자료 검증 경험을 설명해 주시고, 그것도 "
                "없다면 마감 직전 불일치를 발견한 경우 제출과 정정 중 무엇을 선택하고 "
                "그 결과를 어떻게 책임질지 답해 주세요.",
            ],
            "evaluation_points": [
                "실제 불일치의 대상과 발견 경위를 구체적으로 설명함",
                "마감 압박 속에서도 오류를 그대로 넘기지 않은 본인의 선택과 행동을 제시함",
                "선택으로 발생한 일정상·관계상 비용과 이를 감수한 이유를 설명함",
                "수정 전후 수치와 승인 흔적으로 결과 및 후속 책임을 입증함",
            ],
        },
    ),
    (
        "plan_actual_gap",
        "fail",
        {
            "type": "발표면접",
            "question": (
                "제공된 월별 자료에서 한 사업은 집행액이 목표선을 계속 웃도는데 핵심 "
                "산출량은 정체되어 있고, 관련 민원은 최근 두 달간 증가했습니다. 제한된 "
                "잔여 재원을 어느 조치에 먼저 배분할지 진단하고, 원인별 두 가지 대안과 "
                "선택 근거·후속 측정치를 담은 한 장짜리 조정안을 발표해 주세요."
            ),
            "follow_ups": [
                "발표에서 핵심 근거로 든 수치가 일회성 지출이나 집계 시점 차이의 영향을 "
                "받았다면, 어떤 원자료로 재분류하고 진단을 어떻게 수정하겠습니까?",
                "앞서 우선한다고 선택한 대안보다 다른 대안이 민원을 더 빨리 줄일 수 "
                "있다는 반론이 나오면, 비용·산출량·민원 추이를 어떻게 비교해 선택을 "
                "방어하거나 변경하겠습니까?",
                "모든 지원자에게 동일하게 묻겠습니다. 조정안 시행 후 어느 기간의 어떤 "
                "측정치를 기준으로 유지·확대·중단을 결정하겠습니까?",
            ],
            "evaluation_points": [
                "목표선, 실제 집행, 산출량, 민원 추이의 차이를 연결해 원인을 진단함",
                "일회성 요인과 집계 시점 차이를 분리할 자료 및 대조 방법을 제시함",
                "두 대안의 효과와 자원 소요를 비교해 우선 조치를 선택함",
                "실행 책임, 후속 측정치, 유지·변경 기준이 포함된 조정안을 구성함",
            ],
        },
    ),
    (
        "personal_data_protection",
        "review",
        {
            "type": "인바스켓면접",
            "question": (
                "오전 10시에 세 건이 동시에 도착했습니다. 외부기관은 오늘 정오까지 "
                "연구 참여자 명단 전체를 이메일로 요구했고, 인사팀은 잘못 기재된 "
                "계좌번호의 즉시 정정을 요청했으며, 부서장은 오후 1시 결재회의용 "
                "현황보고서에 참여자 연락처를 포함하라고 지시했습니다. 본인에게 외부 "
                "제공 승인권이 없는 상황에서 처리 순서와 각 건의 처리 주체를 결정한 "
                "인바스켓 처리결정표를 작성해 주세요."
            ),
            "follow_ups": [
                "방금 1순위로 정한 문서와 담당 주체를 기준으로, 직접 처리·위임·보류를 "
                "나눈 이유와 가장 큰 누락 위험을 설명해 주세요.",
                "앞서 외부 제공 또는 내부 보고에 포함하겠다고 한 항목 중 업무 목적에 "
                "비해 과도한 정보가 있다면 무엇을 제외하거나 대체하겠습니까?",
                "요청자의 동의가 없거나 긴급하다는 이유만 제시된 경우, 제공 가능 여부를 "
                "판단하기 위해 목적, 권한, 보유 기한 중 무엇을 어떤 기록으로 "
                "확인하겠습니까?",
            ],
            "evaluation_points": [
                "세 요청의 마감, 정보 민감도, 본인 권한을 반영해 처리 순서를 정한다",
                "직접 처리·위임·보류의 주체와 사유를 건별로 명확히 제시한다",
                "요청 목적에 필요한 정보만 남기고 과도한 항목을 제외하거나 대체한다",
                "제공 근거와 보유 기한을 확인할 문서 및 승인 기록을 제시한다",
            ],
        },
    ),
    (
        "closing_report_rules",
        "pass",
        {
            "type": "인바스켓면접",
            "question": (
                "오늘 오전 결산 초안의 미확정 수치를 바로 경영진 자료에 반영해 달라는 "
                "요청, 증빙이 빠진 지출의 보완 요청, 오후 결재가 필요한 정정안이 동시에 "
                "도착했습니다. 본인에게는 최종 승인 권한이 없고 결재자는 한 시간 뒤 "
                "자리를 비웁니다. 문서의 용도와 수치 확정 상태를 기준으로 처리 순서와 "
                "담당 주체를 결정하고, 각 문서의 보고 가능 범위와 보류 사유가 표시된 "
                "처리목록을 제시해 주십시오."
            ),
            "follow_ups": [
                "방금 1순위로 정한 문서와 처리 주체를 기준으로, 직접 처리·위임·상신 중 "
                "그 방식을 택한 이유와 뒤로 미룬 문서에서 발생할 수 있는 누락 위험을 "
                "설명해 주십시오.",
                "앞서 제시한 처리목록에서 잠정 수치와 확정 수치를 어떻게 구분하고, "
                "증빙이 끝내 도착하지 않을 경우 본문·주석·제외 항목 가운데 어디에 "
                "반영할지 말씀해 주십시오.",
                "검토 과정과 변경 내용을 다음 담당자가 재현할 수 있도록 어떤 항목을 "
                "기록하겠습니까?",
            ],
            "evaluation_points": [
                "문서의 사용 목적과 수치 확정 상태를 구별해 처리 순서를 정한다",
                "권한과 마감을 고려해 직접 처리·위임·상신의 주체를 명확히 정한다",
                "미확정 또는 증빙 누락 항목의 보고 범위와 표시 방식을 구체적으로 제시한다",
                "처리 근거와 수정 이력을 재현 가능한 항목으로 남긴다",
            ],
        },
    ),
    (
        "resource_allocation_fairness",
        "pass",
        {
            "type": "발표면접",
            "question": (
                "세 연구지원 사업의 월별 집행률·성과 달성률·대기 과제 수를 정리한 "
                "표에서, 한 사업은 집행률만 급증했고 다른 사업은 성과가 높지만 인력이 "
                "부족하며 세 번째 사업은 민원이 반복되고 있습니다. 추가 인력과 예산은 "
                "한 사업의 요구만 충족할 수 있고 각 책임자는 자기 사업의 전액 지원을 "
                "요구합니다. 어떤 근거로 지원 수준을 조정할지 결정하고, 사업별 배정량·"
                "유보 조건·성과 확인 시점이 담긴 자원 조정표를 발표해 주십시오."
            ),
            "follow_ups": [
                "발표에서 가장 중요하게 사용한 수치와 배정 기준을 짚고, 그 기준 때문에 "
                "요구를 덜 반영하게 된 사업의 반발을 감수할 이유와 본인이 질 책임을 "
                "설명해 주십시오.",
                "방금 선택한 조정안과 달리 집행률 급증이 일회성 선집행이었다는 자료가 "
                "확인되면, 어느 배정값과 유보 조건을 어떻게 바꾸겠습니까?",
                "각 사업 책임자가 수용할 수 있는 공통 기준을 적용하되 사업 특성상 "
                "예외를 허용할 범위와 다음 점검에서 유지·회수할 기준을 제시해 주십시오.",
            ],
            "evaluation_points": [
                "집행·성과·수요 자료의 의미를 구분하고 특정 지표 하나에 치우치지 않은 "
                "배정 근거를 제시한다",
                "상충하는 요구 속에서 불이익과 반발을 감수할 선택 및 본인의 결과 책임을 "
                "명확히 밝힌다",
                "반대 자료가 확인될 때 배정량과 유보 조건을 일관된 논리로 수정한다",
                "사업별 배정량·조건·확인 시점과 유지 또는 회수 기준을 실행 가능한 표로 "
                "제시한다",
            ],
        },
    ),
    (
        "visualization_data_accuracy",
        "fail",
        {
            "type": "창의적 문제해결력면접",
            "question": (
                "다음 날 공개할 성과 화면에서 월별 참여자 수가 업무시스템 추출본, 부서 "
                "제출표, 전월 게시본마다 반복해서 다르고, 확인 작업에는 담당자 한 명만 "
                "투입할 수 있습니다. 가장 먼저 검증할 원인 가설 하나를 선택하고, 비교 "
                "대상·실행 절차·판정값·중단 조건을 담은 최소 검증 실험안을 제시하십시오."
            ),
            "follow_ups": [
                "방금 선택한 가설보다 다른 원인이 유력하다는 반증 자료가 발견되면 실험 "
                "순서와 공개 여부를 어떻게 바꾸겠습니까?",
                "앞서 제시한 판정값이 충족되지 않았는데도 공개 일정 준수를 요구받는다면, "
                "어떤 불이익을 감수하고 무엇을 보류하거나 표시하겠습니까?",
                "실험 결과를 바탕으로 원자료 정제 규칙, 오류 표시, 수정 이력을 어떤 "
                "서식으로 남겨 같은 불일치를 줄이겠습니까?",
            ],
            "evaluation_points": [
                "세 출처의 생성 시점·집계 범위·중복 여부를 바탕으로 검증할 원인을 "
                "특정하는지",
                "한 명이 수행할 수 있는 비교 절차와 명확한 판정값·중단 조건을 설계하는지",
                "공개 압박 속에서도 검증되지 않은 수치를 구분하고 그 선택의 비용을 "
                "설명하는지",
                "검증 결과를 정제 규칙과 수정 이력이 포함된 재발 방지 서식으로 "
                "전환하는지",
            ],
        },
    ),
)


POST_V11_BOUNDARY_CASES: tuple[tuple[str, dict[str, Any], set[str]], ...] = (
    (
        "measurement_framework",
        {
            "type": "창의적 문제해결력면접",
            "question": (
                "최근 세 분기 성과보고에서 국제행사 실적이 반복 누락됐지만, 행사부서는 "
                "등록자 수를, 평가부서는 실제 참석자 수를 실적으로 인정해야 한다고 "
                "주장합니다. 담당 인력은 한 명뿐이고 이번 분기 수치를 높여 달라는 압박도 "
                "있는 상황에서, 단기 수치 하락이나 추가 업무를 감수하더라도 어떤 측정 "
                "기준을 채택할지 판단하고 그 기준의 측정 차원·포함 및 제외 조건·관찰 "
                "기간과 소규모 검증 절차가 담긴 시험설계서를 제시해 주세요."
            ),
            "follow_ups": [
                "방금 제시한 원인 가설을 반박할 수 있는 자료는 무엇이며, 그 자료가 "
                "확인되면 시험설계서의 어느 부분을 바꾸겠습니까?",
                "앞서 선택한 기준 때문에 보고 수치가 낮아지거나 현업 업무가 늘어날 "
                "때도 그 선택을 유지할 경계는 무엇이며, 본인이 어떤 결과 책임을 "
                "지겠습니까?",
                "한정된 인력으로 시험을 운영할 때 채택 기준과 중단 기준, 다음 분기 "
                "확대 여부를 판단할 관찰값을 구체적으로 제시해 주세요.",
            ],
            "evaluation_points": [
                "누락 원인을 분모·중복·시점 등 측정 구조의 문제로 구체화하고 반증 "
                "자료를 제시함",
                "측정 차원과 포함·제외 조건 및 관찰 기간이 서로 일관된 기준을 설계함",
                "단기 실적 저하나 업무 증가를 감수하는 선택의 경계와 본인의 책임을 "
                "설명함",
                "제한된 인력으로 실행 가능한 시험 절차와 채택·중단 판단값을 제시함",
            ],
        },
        {"measurement_denominator", "duplicate_handling"},
    ),
    (
        "closing_report_rules",
        {
            "type": "인바스켓면접",
            "question": (
                "오늘 들어온 문서는 정오까지 답해야 하는 결산 초안의 증빙 누락 정정 "
                "요청, 오후 결재 예정인 경영진 설명자료, 내일까지 회신할 연구책임자의 "
                "집행액 이의 제기입니다. 본인에게는 자료 수정 권한만 있고 최종 승인 "
                "권한은 없을 때, 문서의 완결성과 승인 가능성을 기준으로 처리 순서와 "
                "처리 주체를 결정하고, 각 문서의 확정값·잠정값·근거자료·결재 필요 여부를 "
                "구분한 처리결정표를 제시해 주십시오."
            ),
            "follow_ups": [
                "방금 1순위로 정한 문서와 처리 주체를 기준으로, 직접 처리·위임·상급자 "
                "보고 중 그 방식을 택한 이유와 뒤로 미룬 문서의 누락 위험을 설명해 "
                "주십시오.",
                "앞서 제시한 처리결정표에서 잠정값이나 증빙 미확보 항목이 있다면, "
                "본문과 주석에 각각 어떻게 표시하고 어떤 조건에서 확정값으로 "
                "바꾸겠습니까?",
                "정정 전후의 수치와 승인 경로를 나중에 재현할 수 있도록 어떤 항목을 "
                "변경 기록에 남기겠습니까?",
            ],
            "evaluation_points": [
                "마감, 승인 권한, 수치 확정 가능성을 함께 고려해 문서별 순서와 처리 "
                "주체를 구분한다.",
                "선택한 처리 방식과 후순위 문서에서 발생할 수 있는 구체적 누락 위험을 "
                "연결해 설명한다.",
                "처리결정표에서 확정값과 잠정값, 근거자료, 결재 필요 여부를 명확히 "
                "구획한다.",
                "정정 전후 값, 수정 사유, 근거, 작성자와 승인자를 추적할 수 있는 기록 "
                "항목을 제시한다.",
            ],
        },
        {"record_author"},
    ),
    (
        "resource_allocation_fairness",
        {
            "type": "발표면접",
            "question": (
                "세 연구지원 사업의 월별 집행률·성과 달성률·민원 건수 자료에서 한 "
                "사업의 집행률만 급등했고, 각 책임자는 다음 분기 인력과 예산의 증액을 "
                "동시에 요구하고 있습니다. 총자원이 늘지 않는 조건에서 급등 원인을 "
                "진단하고 두 가지 조정 대안을 비교한 뒤 하나를 선택하여, 배분 기준·"
                "포함 및 제외 대상·적용 기간·사업별 조정량·성과 확인 지표가 담긴 "
                "실행표를 발표해 주십시오."
            ),
            "follow_ups": [
                "발표에서 선택한 대안으로 인해 요구가 줄어드는 사업과 본인이 "
                "감수하겠다고 한 반발 또는 성과 위험을 밝히고, 그 선택을 유지할 근거를 "
                "설명해 주십시오.",
                "방금 근거로 사용한 수치가 일시적 집행 시점 차이이거나 중복 집계로 "
                "확인된다면, 배분 기준과 사업별 조정량을 어떻게 수정하겠습니까?",
                "실행 후 특정 사업의 성과가 악화될 경우 책임 있게 재조정할 수 있도록 "
                "측정 기간, 중단 기준, 재배분 권한을 어떻게 정하겠습니까?",
            ],
            "evaluation_points": [
                "급등 수치의 집계 범위와 시점 문제를 구분하여 원인을 진단한다.",
                "두 대안의 사업별 편익과 손실을 비교하고 제한된 자원 아래 선택 근거를 "
                "제시한다.",
                "배분 기준, 포함·제외 대상, 적용 기간, 조정량과 확인 지표가 연결된 "
                "실행표를 구성한다.",
                "불이익을 받는 사업의 반발과 성과 위험을 감수하는 선택 및 악화 시 "
                "재조정 책임을 구체화한다.",
            ],
        },
        {"aggregation_scope"},
    ),
)


def _item(fixture: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": fixture["type"],
        "question": fixture["question"],
        "follow_ups": list(fixture["follow_ups"]),
        "evaluation_points": list(fixture["evaluation_points"]),
    }


@pytest.mark.parametrize(
    ("case_id", "expected", "item"),
    POST_FIX_SELECTED_CASES,
    ids=[row[0] for row in POST_FIX_SELECTED_CASES],
)
def test_frozen_post_fix_cases_preserve_alignment_decisions(
    case_id: str,
    expected: str,
    item: dict[str, Any],
) -> None:
    result = evaluate_evaluation_elicitation_alignment(item)

    assert result["decision"] == expected, (case_id, result)
    if case_id == "plan_actual_gap":
        assert [
            (issue["point_index"], issue["semantic_family"])
            for issue in result["issues"]
        ] == [(4, "execution_owner")]
    elif case_id == "visualization_data_accuracy":
        assert {issue["semantic_family"] for issue in result["issues"]} == {
            "data_generation_time",
            "aggregation_scope",
            "duplicate_handling",
        }
    elif case_id == "personal_data_protection":
        assert [issue["code"] for issue in result["issues"]] == [
            "ambiguous_quantifier_scope"
        ]
        assert result["issues"][0]["semantic_family"] == "record_retention"


@pytest.mark.parametrize(
    ("case_id", "item", "expected_families"),
    POST_V11_BOUNDARY_CASES,
    ids=[row[0] for row in POST_V11_BOUNDARY_CASES],
)
def test_frozen_v11_cases_keep_hidden_scoring_atoms_closed(
    case_id: str,
    item: dict[str, Any],
    expected_families: set[str],
) -> None:
    result = evaluate_evaluation_elicitation_alignment(item)

    assert result["decision"] == "fail", (case_id, result)
    assert {
        issue["semantic_family"]
        for issue in result["issues"]
        if issue["code"] == "unelicited_evaluation_atom"
    } == expected_families


@pytest.mark.parametrize("fixture", METHOD_FIXTURES, ids=lambda row: f"P-{row['id']}")
def test_all_seven_method_paraphrases_pass(fixture: dict[str, Any]) -> None:
    result = evaluate_evaluation_elicitation_alignment(_item(fixture))

    assert result["policy"] == EVALUATION_ELICITATION_POLICY
    assert result["decision"] == "pass", (fixture["id"], result)
    assert result["passed"] is True
    assert result["checks"]["exact_four"] is True
    assert result["checks"]["all_point_atoms_elicited"] is True
    assert result["metrics"]["point_count"] == 4
    assert (
        result["metrics"]["matched_atom_count"] == result["metrics"]["point_atom_count"]
    )


@pytest.mark.parametrize("fixture", METHOD_FIXTURES, ids=lambda row: f"N-{row['id']}")
def test_all_seven_hidden_criterion_mutations_fail(
    fixture: dict[str, Any],
) -> None:
    item = _item(fixture)
    item["evaluation_points"][3] = fixture["negative"]

    result = evaluate_evaluation_elicitation_alignment(item)

    assert result["decision"] == "fail", (fixture["id"], result)
    assert result["passed"] is False
    assert any(
        issue["code"] == "unelicited_evaluation_atom"
        and issue["point_index"] == 4
        and issue["semantic_family"] == fixture["negative_family"]
        for issue in result["issues"]
    )


def test_compound_point_fails_only_the_unasked_owner_atom() -> None:
    item = {
        "type": "창의적 문제해결력면접",
        "question": ("두 대안을 검증하고 성과지표와 중단 기준을 제시하십시오."),
        "follow_ups": [
            "대안 검증 결과는 무엇입니까?",
            "성과지표는 어떻게 측정합니까?",
            "중단 기준에 이르면 무엇을 바꾸겠습니까?",
        ],
        "evaluation_points": [
            "대안 검증",
            "성과지표",
            "중단 기준",
            "성과지표·중단 기준·실행 책임",
        ],
    }

    result = evaluate_evaluation_elicitation_alignment(item)
    point_four_issues = [
        issue for issue in result["issues"] if issue.get("point_index") == 4
    ]

    assert result["decision"] == "fail"
    assert point_four_issues == [
        {
            "code": "unelicited_evaluation_atom",
            "point_index": 4,
            "atom_index": 3,
            "semantic_family": "execution_owner",
        }
    ]


def test_scenario_premise_does_not_elicit_stakeholder_response() -> None:
    item = {
        "type": "상황면접",
        "question": (
            "민원인 요구에 관한 부서장과의 협의 방식을 확인한 상황입니다. "
            "제출자료의 사실관계를 확인하고 확인한 사실을 어떤 판단 기준으로 "
            "적용할지 정한 뒤 처리 순서를 제시하세요."
        ),
        "follow_ups": [
            "가장 먼저 확인할 자료는 무엇입니까?",
            "그 판단 기준은 왜 적절합니까?",
            "처리 순서를 언제 바꾸겠습니까?",
        ],
        "evaluation_points": [
            "자료와 사실 확인",
            "판단 기준",
            "처리 순서",
            "부서장과의 협의 방식",
        ],
    }

    result = evaluate_evaluation_elicitation_alignment(item)

    assert result["decision"] == "fail"
    assert any(
        issue["code"] == "unelicited_evaluation_atom"
        and issue["point_index"] == 4
        and issue["semantic_family"] == "escalation_stakeholder"
        for issue in result["issues"]
    )


def test_ambiguous_quantifier_scope_routes_to_review() -> None:
    item = {
        "type": "직무지식면접",
        "question": ("적용 범위, 예외, 반복 오류 중 무엇을 확인할지 설명하세요."),
        "follow_ups": [],
        "evaluation_points": [
            "적용 범위",
            "예외 조건",
            "반복 오류",
            "적용 범위와 예외 조건",
        ],
    }

    result = evaluate_evaluation_elicitation_alignment(item)

    assert result["decision"] == "review"
    assert result["passed"] is False
    assert result["metrics"]["review_atom_count"] == 2
    assert {issue["code"] for issue in result["issues"]} == {
        "ambiguous_quantifier_scope"
    }


def test_explicit_single_choice_cannot_support_all_compound_atoms() -> None:
    item = {
        "type": "직무지식면접",
        "question": ("적용 범위, 예외, 반복 오류 중 하나를 확인해 설명하세요."),
        "follow_ups": [],
        "evaluation_points": [
            "적용 범위",
            "예외 조건",
            "반복 오류",
            "적용 범위와 예외 조건",
        ],
    }

    result = evaluate_evaluation_elicitation_alignment(item)

    assert result["decision"] == "fail"
    assert any(
        issue["code"] == "quantifier_scope_mismatch" for issue in result["issues"]
    )


def test_answer_adaptive_pronouns_inherit_selected_response_object() -> None:
    item = {
        "type": "창의적 문제해결력면접",
        "question": "두 대안을 비교해 하나를 선택하고 최소 실험을 설계하세요.",
        "follow_ups": [
            "앞서 선택한 것은 왜 적합하다고 보셨습니까?",
            "그 선택은 어떤 조건에서 바꾸겠습니까?",
            "방금 제시한 실험은 어떻게 검증합니까?",
        ],
        "evaluation_points": [
            "대안 비교",
            "선택 근거",
            "실험 검증",
            "선택 대안의 전환 조건",
        ],
    }

    result = evaluate_evaluation_elicitation_alignment(item)

    assert result["decision"] == "pass", result
    assert result["metrics"]["adaptive_demand_count"] == 3


def test_trait_is_not_independently_scoreable_but_behavior_is() -> None:
    base = {
        "type": "인바스켓면접",
        "question": "문서의 우선순위를 정하고 처리 결과를 보고서에 기록하세요.",
        "follow_ups": [
            "무엇을 먼저 처리합니까?",
            "처리 결과를 어떻게 기록합니까?",
            "그 보고는 누구에게 전달합니까?",
        ],
        "evaluation_points": [
            "우선순위",
            "처리 결과 기록",
            "보고 방식",
            "책임감",
        ],
    }

    failed = evaluate_evaluation_elicitation_alignment(base)
    assert failed["decision"] == "fail"
    assert any(
        issue["code"] == "unobservable_trait_criterion" for issue in failed["issues"]
    )

    observable = deepcopy(base)
    observable["evaluation_points"][3] = "책임감 있는 결과 보고"
    passed = evaluate_evaluation_elicitation_alignment(observable)
    assert passed["decision"] == "pass", passed


def test_generic_artifact_does_not_imply_hidden_approval_or_retention() -> None:
    item = {
        "type": "직무지식면접",
        "question": "예외 적용 근거를 검토서에 기재해 제시하십시오.",
        "follow_ups": [
            "어떤 예외를 적용합니까?",
            "그 근거는 무엇입니까?",
            "검토서에는 무엇을 기재합니까?",
        ],
        "evaluation_points": [
            "예외 적용",
            "적용 근거",
            "검토서 기재",
            "검토서 결재선과 보존 기간",
        ],
    }

    result = evaluate_evaluation_elicitation_alignment(item)

    assert result["decision"] == "fail"
    assert {
        issue["semantic_family"]
        for issue in result["issues"]
        if issue["code"] == "unelicited_evaluation_atom"
    } == {"approval_process", "record_retention"}


def test_exact_four_is_fail_closed_and_diagnostics_do_not_copy_raw_text() -> None:
    fixture = METHOD_FIXTURES[0]
    item = _item(fixture)
    item["evaluation_points"] = item["evaluation_points"][:3]

    result = evaluate_evaluation_elicitation_alignment(item)
    serialized = json.dumps(result, ensure_ascii=False)

    assert result["decision"] == "fail"
    assert result["checks"]["exact_four"] is False
    assert any(
        issue["code"] == "evaluation_point_count_mismatch" for issue in result["issues"]
    )
    assert item["question"] not in serialized
    assert all(point not in serialized for point in item["evaluation_points"])


def test_guide_dimensions_and_scope_are_closed_when_guide_is_present() -> None:
    fixture = METHOD_FIXTURES[2]
    item = _item(fixture)
    item["assessment_guide"] = {
        "dimensions": list(item["evaluation_points"]),
        "rating_levels": [
            {"score": 5, "anchor": "자료 진단과 대안 비교 및 성과지표를 구체화한다."}
        ],
    }

    passed = evaluate_evaluation_elicitation_alignment(item)
    assert passed["decision"] == "pass", passed

    item["assessment_guide"]["rating_levels"][0]["anchor"] = (
        "자료 진단 뒤 부서장 결재선까지 구체화한다."
    )
    failed = evaluate_evaluation_elicitation_alignment(item)
    assert failed["decision"] == "fail"
    assert failed["checks"]["guide_scope_closed"] is False
    assert any(issue["code"] == "guide_scope_drift" for issue in failed["issues"])
