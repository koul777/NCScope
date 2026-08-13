from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

import pytest

from app.services.question_evaluation_alignment import (
    EVALUATION_ELICITATION_POLICY,
    evaluate_evaluation_elicitation_alignment,
)


# Report-independent frozen candidate-visible oracle. No dated report is read.
FINAL_V18_NEUTRAL_ALIGNMENT_ORACLE: tuple[dict[str, Any], ...] = tuple(
    json.loads(r'''[{"case_id":"indicator_definition","type":"직무지식면접","question":"부서 실적표에는 같은 교육 프로그램의 참여자가 월별로 중복 합산되어 있고, 기관 집계표에는 연간 고유 참여자로 계산되어 두 수치가 다릅니다. 어느 집계가 공식 보고에 적합한지 판정하고, 적용 대상·측정 기간·중복 처리 규칙이 표시된 판정표 한 장을 제시해 주세요.","follow_ups":["방금 적용한 집계 규칙에서 한 사람이 서로 다른 프로그램에 참여한 경우에는 어떤 자료를 확인해 포함 범위를 정하겠습니까?","앞서 제시한 판정표대로 수정했는데도 부서 실적표와 기관 집계표가 다르다면 어떤 오류 가능성을 먼저 확인하겠습니까?","연도 중 프로그램 명칭이나 집계 방식이 바뀐 경우, 전후 실적의 비교 가능성을 유지하기 위한 예외 처리와 기록 방법을 설명해 주세요."],"evaluation_points":["보고 목적에 맞게 집계 대상과 포함 범위를 구분한다.","측정 기간을 기준으로 월별 합계와 연간 고유값의 차이를 설명한다.","중복 참여를 식별하고 처리할 수 있는 판정 규칙을 제시한다.","기준 변경이나 복수 프로그램 참여에 대한 예외와 오류 위험을 설명한다."]},{"case_id":"research_plan_review","type":"상황면접","question":"공동연구 과제의 신청서에는 참여기관이 세 곳으로 적혀 있지만 연구계획서와 예산 설명에는 두 곳만 반영되어 있으며 제출 마감은 오늘입니다. 현재 확인 가능한 신청 안내, 기관별 역할 자료, 예산 내역을 대조해 제출 가능 여부를 판단하고, 불일치 항목·확인 근거·수정 내용을 담은 보완표 한 장을 제시해 주세요.","follow_ups":["방금 제출 가능 여부를 판단할 때 가장 결정적으로 사용한 자료는 무엇이며, 그 자료와 다른 문서가 충돌하면 어떤 기준으로 확정하겠습니까?","앞서 선택한 수정 내용에서 참여기관의 역할이나 비용 근거가 빠졌다는 사실이 추가로 확인되면 최초 판단을 어떻게 바꾸겠습니까?","연구책임자의 확인을 마감 전 받기 어려운 경우, 본인의 수정 권한과 승인권자의 결정 영역을 어떻게 구분해 처리하겠습니까?"],"evaluation_points":["신청서·연구계획서·예산 내역 사이의 연결 관계를 정확히 대조한다.","불일치가 제출 요건과 연구 수행에 미치는 영향을 근거로 제출 가능 여부를 판단한다.","보완표에 오류 위치와 근거 문서 및 수정 내용을 추적 가능하게 기록한다.","수정 권한을 벗어나는 사항을 식별하고 적절한 승인 경계를 제시한다."]},{"case_id":"numeric_accuracy","type":"경험면접","question":"제출 마감이 임박한 상황에서 원자료와 집계표의 수치가 맞지 않았던 실제 경험을 들려주세요. 당시 역할과 승인 권한 안에서 어떤 대응을 선택하고 직접 무엇을 했으며 결과를 어떻게 확인했는지 설명한 뒤, 선택한 조치·예상 영향·재확인 조건과 담당 역할이 담긴 판단 기록 한 장을 제시해 주세요. 직접 경험이 없다면 학업·프로젝트·봉사활동의 가장 가까운 사례로 답해도 됩니다.","follow_ups":["방금 말씀하신 원자료와 집계표의 불일치 중 본인의 선택을 좌우한 근거는 무엇이었으며, 다른 대응을 택했을 때 예상한 영향은 무엇이었습니까?","앞서 언급한 결과가 실제로 수정되었음을 보여 주는 수치 변화, 승인 기록 또는 문서 이력은 무엇이었습니까?","최초 판단과 다른 결과가 확인된다면 어느 시점에 다시 검증하고, 누구의 권한으로 수정하거나 상위 결정권자에게 넘기겠습니까?"],"evaluation_points":["마감과 자료 불일치가 동시에 존재했던 구체적 상황과 본인의 권한을 구분한다.","복수의 가능한 대응 가운데 선택한 조치와 그에 따른 영향을 설명한다.","원자료 대조와 수정 과정에서 본인이 수행한 행동을 구체적으로 제시한다.","수정 전후 수치나 승인·변경 기록으로 결과를 확인하고 재검증 조건을 제시한다."]},{"case_id":"plan_actual_gap","type":"발표면접","question":"한 사업의 월별 계획치는 매월 100건이고 실제치는 첫째 달 98건, 둘째 달 101건, 셋째 달 99건, 넷째 달 62건입니다. 같은 기간 민원은 월 3건 수준에서 넷째 달 18건으로 늘었고, 예산 집행률은 넷째 달 말 95퍼센트입니다. 가장 중요한 계획과 실적의 차이 한 건과 그 원인을 판정하고, 계획·실적값, 차이, 원인 근거가 보이는 분석표 한 장으로 발표해 주세요.","follow_ups":["방금 원인 근거로 사용한 수치가 다른 요인의 영향일 가능성은 어떤 추가 자료와 비교해 확인하겠습니까?","앞서 판정한 원인과 반대되는 자료가 확인된다면 분석표의 어느 부분을 어떻게 수정하겠습니까?","확인된 차이에 대응할 수 있는 방안 두 가지 중 하나를 먼저 시행한다면 선택 기준과 이후 성과 확인 방법은 무엇입니까?"],"evaluation_points":["월별 계획치와 실제치를 같은 기간과 단위로 맞추어 핵심 차이를 식별한다.","실적 급감, 민원 증가, 높은 예산 집행 사이의 관계를 구분해 원인을 판정한다.","분석표에 계획·실적값과 차이 및 원인 근거를 명확히 연결한다.","반대 자료가 나타날 경우 판정을 수정하고 후속 대응의 성과 확인 방법을 제시한다."]},{"case_id":"research_fund_rule","type":"직무지식면접","question":"한 연구과제의 장비 수리비 집행신청서가 제출됐는데, 지원기관 지침은 과제 수행과 직접 관련된 비용만 인정하고 내부 지침은 공용 장비의 유지비도 허용하며, 해당 장비는 여러 과제가 함께 사용하고 있습니다. 두 기준의 효력과 비용의 직접 관련성, 공용 사용에 따른 예외 가능성을 따져 승인·보완·반려 중 하나를 판단하고, 적용 근거·적용 범위·수정 완료 확인란이 있는 검토기록 한 장을 제시해 주세요.","follow_ups":["방금 적용한다고 말씀하신 근거가 공용 장비에도 미친다고 본 이유와, 그 범위를 벗어나는 사례를 설명해 주세요.","앞서 선택한 처리 결과에서 신청 금액의 일부만 인정할 수 있다면 어떤 증빙을 대조하고 검토기록을 어떻게 고치시겠습니까?","서로 다른 지원기관의 과제에 같은 비용이 배분됐을 때 중복 집행을 예방하고 수정 완료를 확인하는 방법은 무엇입니까?"],"evaluation_points":["상충하는 기준의 효력과 적용 순서를 구분한 근거","비용의 직접 관련성과 공용 사용 범위를 토대로 한 예외 판단","증빙 대조와 중복 집행 위험을 반영한 처리 결정","적용 근거와 수정 확인이 추적되는 검토기록의 완결성"]},{"case_id":"agency_negotiation","type":"토론면접","question":"[토론과제] 공동 연구사업의 중간자료 제출일이 임박한 가운데 지원기관은 일정 유지를 위해 현재 자료를 우선 접수하자는 입장이고, 참여기관은 검증되지 않은 수치가 공식 기록으로 굳어질 수 있어 확인 후 제출해야 한다는 입장입니다. 양측의 일정상 한계와 자료 사용 위험을 검토해 수용 가능한 협의 경계를 하나로 정하고, 합의 범위·유보 항목·미합의 시 이송 기준이 담긴 협의기록 한 장을 제시해 주세요.","follow_ups":["방금 수용한 상대 측 요구와 수용하지 않은 요구를 나눈 기준은 무엇이며, 그 판단에 어떤 자료를 확인하겠습니까?","앞서 남겨 둔 유보 항목이 전체 일정에 영향을 준다면 적용 범위와 예외를 어디까지 조정할 수 있습니까?","합의가 이뤄지지 않을 경우 어느 쟁점을 누구에게 이송하고, 합의된 내용의 이행 여부를 누가 어떤 기록으로 점검해야 합니까?"],"evaluation_points":["양측 주장과 제약을 확인할 자료 및 사실의 구체성","상대 요구를 교환조건과 허용 한계로 전환하는 협상 논리","합의 범위와 예외 또는 잔여 쟁점을 구분한 조정 결과","이송 기준과 실행 책임이 추적되는 협의기록의 명확성"]},{"case_id":"personal_data_protection","type":"인바스켓면접","question":"오늘 안에 처리해야 할 세 건이 동시에 도착했습니다. 외부 평가기관은 연구 참여자 명단과 연락처를 요청했으나 제공 근거가 적혀 있지 않고, 참여자는 잘못 기재된 계좌정보의 즉시 정정을 요구했으며, 부서장은 인사 현황 원본을 첨부한 보고문서의 결재 상신을 지시했지만 본인에게는 외부 제공과 원본 첨부를 승인할 권한이 없습니다. 각 건의 목적·처리 근거·필요 최소 범위를 구분해 처리 순서와 담당 주체를 결정하고, 건별 조치·보류 사유·승인 또는 이송 대상을 담은 처리목록 한 장을 제시해 주세요.","follow_ups":["방금 첫 순위로 둔 요청과 처리 주체를 선택한 이유를 정보주체의 권리, 외부 제공의 근거, 피해 가능성과 연결해 설명해 주세요.","앞서 보류하거나 이송한 건에서 목적과 권한을 확인할 자료가 추가되면 제공 범위와 처리 주체를 어떻게 다시 정하겠습니까?","긴급 요청이라도 원본 전체가 불필요한 경우 제공·열람·보관 범위를 정하고 처리 이력을 남기는 기준은 무엇입니까?"],"evaluation_points":["각 요청의 목적과 적법한 처리 근거를 구별한 분류","정보 노출과 정정 지연의 위험을 반영한 처리 순서","필요 최소 범위와 승인 권한에 맞춘 담당 주체 결정","조치와 보류 및 이송 경계가 드러나는 처리목록의 추적성"]},{"case_id":"measurement_framework","type":"창의적 문제해결력면접","question":"분기별 국제학술행사 성과에서 공동 참가자가 행사마다 중복 집계되거나 일부 온라인 참가자가 빠지는 현상이 반복되고 있습니다. 기존 산식을 유지하면 전년 비교가 쉽지만 누락이 계속되고, 산식을 즉시 바꾸면 비교 연속성이 약해지며 이번 달에는 담당자 한 명만 투입할 수 있습니다. 현재 권한 안에서 다음 분기에 시험할 보정 방식을 하나 선택하고, 원인 가설·참가자 포함 및 중복 처리 규칙·채택 또는 중단 조건이 담긴 검증안 한 장을 제시해 주세요.","follow_ups":["방금 선택한 보정 방식이 기존 추세 비교나 현장 입력 부담에 줄 수 있는 불리한 영향을 어떻게 확인하겠습니까?","앞서 제시한 원인 가설과 맞지 않는 결과가 나오면 어떤 관찰값을 근거로 시험을 중단하거나 수정하겠습니까?","한 명의 담당자가 수행할 수 있도록 시험 대상과 관찰 기간을 어디까지 제한하고, 결과 검토와 최종 변경 승인은 누가 맡아야 합니까?"],"evaluation_points":["반복 누락과 중복 현상을 설명하는 검증 가능한 원인 가설","포함 대상과 중복 처리 및 관찰 기간이 구별되는 측정 규칙","비교 연속성과 정확성 및 업무 부담의 상충효과를 반영한 선택","관찰 결과에 따라 채택·중단·수정할 수 있는 검증안의 실행 가능성"]},{"case_id":"closing_report_rules","type":"인바스켓면접","question":"오늘 정오까지 보완해야 하는 결산 초안, 오후 결재가 예정된 증빙 보완 요청, 내일 경영진에게 제공할 보고자료가 동시에 도착했지만 본인은 최종 결재 권한이 없습니다. 가장 먼저 처리할 문서와 처리 주체를 결정하고, 확정값과 잠정값의 구분 및 본문·주석 배치를 적용해 증빙 연결 상태까지 표시한 검토기록 한 장을 제시해 주십시오.","follow_ups":["방금 1순위로 정한 문서와 처리 주체를 기준으로, 다른 문서를 먼저 처리했을 때 생길 수 있는 누락 위험을 설명해 주십시오.","앞서 잠정값으로 표시한 항목 중 증빙이 마감 전 확보되지 않는다면 보고 범위와 결재 요청을 어떻게 바꾸겠습니까?","검토 후 오류가 발견된 경우 수정 이력, 승인자, 재보고 여부를 어떤 기준으로 기록하겠습니까?"],"evaluation_points":["마감과 결재 권한을 근거로 최초 처리 대상과 처리 주체를 명확히 정한다.","확정된 금액과 확인 중인 금액을 구별하여 기록한다.","핵심 수치와 보충 설명을 본문과 주석에 적절히 배치한다.","각 판단을 증빙 및 수정·승인 이력과 연결한다."]},{"case_id":"review_report_writing","type":"경험면접","question":"예산계획표와 결산 원장의 실적이 맞지 않는 상태에서 보고 마감이 임박했던 실제 경험을 설명해 주십시오. 당시 본인이 어떤 원자료를 선택해 차이를 판정하고 어떤 조치를 했는지, 그 판단과 수정 전후 값 또는 승인 흔적이 담긴 검토보고서 한 장을 중심으로 결과까지 말씀해 주십시오. 직접 경험이 없다면 학업·프로젝트·봉사활동 중 가장 가까운 실제 경험으로 답해도 됩니다.","follow_ups":["방금 말씀하신 원자료를 신뢰할 기준으로 선택한 이유와 다른 자료와 대조한 방법은 무엇이었습니까?","앞서 언급한 본인의 조치가 보고서 이용자의 결정에 어떤 변화를 만들었는지 수치, 승인 기록 또는 후속 요청으로 입증해 주십시오.","같은 유형의 차이가 다시 발생한다면 보고서의 어떤 부분을 먼저 점검하고 품질을 어떻게 확인하겠습니까?"],"evaluation_points":["마감 압박과 자료 불일치가 있었던 실제 상황 및 본인 역할을 구체적으로 밝힌다.","차이 판정에 사용한 원자료와 대조 절차를 설명한다.","본인이 작성한 보고서에 판단 근거와 수정 결과를 식별 가능하게 남긴다.","수정 전후 값, 승인 기록 또는 활용 변화로 결과를 입증한다."]},{"case_id":"civil_form_process","type":"상황면접","question":"관계기관 제출 서식에는 사업 책임자의 확인 표시가 빠져 있고, 같은 건의 민원 회신 기록에는 서로 다른 처리 상태가 적혀 있으며 제출 기한은 오늘입니다. 기관 담당자에게 확인할 수 있으나 본인에게 최종 승인 권한은 없을 때, 최초 보완 방향을 결정하고 필수 입력란·첨부 증빙·처리 단계가 표시된 보완본 한 장을 제시해 주십시오.","follow_ups":["방금 선택한 최초 보완 방향에서 사실 확인이 끝나지 않은 항목은 어떤 상태로 표시하고 어느 단계에서 승인권자에게 넘기겠습니까?","앞서 제시한 보완본에 민원인의 요청 내용이나 기관의 접수 기준이 빠졌다면 처리 방향을 어떻게 수정하겠습니까?","제출 직전 서식 누락과 회신 기록의 불일치를 각각 확인할 검수 방법을 설명해 주십시오."],"evaluation_points":["서식의 필수 입력 요건과 민원 처리 기록의 불일치를 구별해 최초 보완 방향을 정한다.","미확인 정보와 승인 권한의 경계를 반영해 처리 단계를 제시한다.","필수 입력란과 첨부 증빙이 연결된 보완본을 구성한다.","제출 전 서식 누락 및 처리 상태 불일치를 확인하는 검수 방법을 제시한다."]},{"case_id":"resource_allocation_fairness","type":"발표면접","question":"다음 분기 총예산과 투입 인력은 동결되어 있습니다. 한 연구지원 사업은 최근 실적이 높지만 추가 인력을 요구하고, 다른 사업은 실적은 낮으나 이용자 접근성 개선 필요와 민원이 확인되었습니다. 현재 역할과 승인 권한 안에서 사업별 배분을 하나로 결정하고, 공통 기준·사업별 배분량·재검토 조건과 승인 역할이 보이는 배분안 한 장을 발표해 주십시오.","follow_ups":["방금 제시한 공통 기준이 특정 사업에 불리하게 작용하는 지점은 무엇이며, 그 영향을 어느 범위까지 허용하겠습니까?","앞서 선택한 배분량과 다른 방향을 지지하는 자료가 추가된다면 어떤 조건에서 안을 수정하거나 승인권자에게 다시 올리겠습니까?","배분 후 결과가 예상과 달랐을 때 확인할 지표와 관찰 기간, 후속 점검 담당 역할을 제시해 주십시오."],"evaluation_points":["서로 다른 사업에 일관되게 적용할 수 있는 공통 기준을 제시한다.","제한된 예산과 인력 범위 안에서 사업별 배분 결정을 명확히 수치화한다.","선택으로 불리해질 수 있는 영향을 인식하고 수정 또는 재승인 경계를 설명한다.","성과 확인 지표와 관찰 기간 및 후속 점검 역할을 구체화한다."]},{"case_id":"objective_evaluation","type":"토론면접","question":"[토론과제] 신규 문화사업의 성과를 평가하는 과정에서 수치 목표는 충족했지만 현업 부서는 장기적 참여 기반이 형성됐다는 정성 자료도 반영해 달라고 요청했습니다. 한쪽은 사업 간 비교 가능성을 위해 사전에 정한 정량 기준을 동일하게 적용해야 한다고 주장하고, 다른 쪽은 사업 특성을 반영해 현장 자료의 비중을 높여야 한다고 주장합니다. 양측이 확인해야 할 사실을 짚은 뒤 공통으로 적용할 평가 원칙 하나를 선택하고, 합의가 어렵다면 남은 쟁점과 결정권자에게 넘길 기준을 포함한 평가 원칙표 한 장을 제시해 토론해 주세요.","follow_ups":["방금 수용한 상대 입장의 근거와 받아들이지 않은 범위를 구분하고, 그 경계가 특정 사업에만 유리하게 작용하지 않는지 어떤 자료로 확인하겠습니까?","앞서 제시한 평가 원칙을 적용했을 때 불리해질 수 있는 사업 유형은 무엇이며, 그 영향이 예상과 다르면 누가 어떤 기준으로 원칙을 조정해야 합니까?","공통 원칙을 시범 적용한다면 적용 대상과 예외 범위, 확인 지표, 후속 점검 담당을 어떻게 정하겠습니까?"],"evaluation_points":["정량 자료와 현장 자료의 신뢰도·비교 가능성을 구분해 검토하는가","어느 한 이해관계자의 요구에 치우치지 않는 공통 판정 기준을 제시하는가","선택한 원칙으로 불리해질 수 있는 사업과 조정 권한의 경계를 설명하는가","합의 적용 범위와 미합의 쟁점의 이송 기준을 평가 원칙표에 명확히 기록하는가"]},{"case_id":"visualization_data_accuracy","type":"창의적 문제해결력면접","question":"월별 성과 화면에서 같은 사업의 참여자 수가 원천 시스템보다 반복적으로 크게 나타나며, 수작업 병합 오류라는 설명과 집계 기간 차이라는 설명이 맞서고 있습니다. 담당 인력은 한 명이고 화면 갱신은 내일입니다. 두 설명 중 먼저 검증할 원인 하나를 선택하고, 사용 가능 범위와 예상 오류 영향, 채택 또는 중단 기준이 담긴 소규모 검증 기록표 한 장을 제시해 주세요.","follow_ups":["방금 선택한 원인과 맞지 않는 값이 검증 표본에서 발견된다면, 최초 판단을 유지하거나 바꿀 기준은 무엇입니까?","앞서 정한 중단 기준에 도달했을 때 화면 갱신 여부와 상위 권한자에게 넘길 내용을 어떻게 구분하겠습니까?","검증 뒤 같은 불일치가 다시 생기지 않도록 원자료의 식별값·집계 기간·중복 처리 중 무엇을 통제 항목으로 남기겠습니까?"],"evaluation_points":["상충하는 설명 중 우선 검증할 원인을 관찰된 불일치와 연결하는가","제한된 인력과 마감 아래 오류 영향을 확인할 수 있는 작은 검증 범위를 정하는가","반증 결과에 따라 판단을 수정할 채택·중단 기준을 명시하는가","검증 기록표에 사용 범위와 오류 영향 및 판정 기준을 구별해 기록하는가"]},{"case_id":"data_use_ethics","type":"경험면접","question":"성과 분석 마감이 임박한 상황에서 접근 권한이나 당초 수집 목적이 불분명한 내부 자료를 활용해 달라는 요청을 받은 실제 경험을 설명해 주세요. 당시 본인의 역할과 승인 범위 안에서 어떤 사용 결정을 내리고 어떤 조치를 했는지, 그 결정의 결과를 확인할 수 있도록 사용 목적·허용 범위·승인 또는 이송 주체가 담긴 판단 기록 한 장을 함께 제시해 주세요. 직접 경험이 없다면 학업·프로젝트·봉사에서 가장 가까운 실제 사례로 답해 주세요.","follow_ups":["방금 언급한 자료 가운데 실제로 사용하거나 제외한 항목 하나를 골라, 그 경계를 정한 근거와 업무 결과를 설명해 주세요.","앞서 말씀한 승인 또는 협의 과정에서 본인의 권한 밖이었던 부분은 무엇이었으며, 답변에 결과 증거가 없다면 어떤 기록이나 변화로 확인할 수 있습니까?","결정 후 자료의 목적 외 이용이나 과도한 공유가 확인됐다면 어떤 사용을 멈추고 누구의 권한으로 기록을 수정하거나 사안을 이송하겠습니까?"],"evaluation_points":["마감 편의와 자료 이용의 적정성이 충돌한 구체적 상황과 본인 역할을 설명하는가","자료별 사용 목적과 접근·공유 범위를 구분해 실제 행동으로 옮겼는가","승인 범위를 넘는 판단을 적절한 주체에게 확인하거나 이송했는가","결정 결과를 승인 기록·사용 범위 변화·산출물 수정 등 확인 가능한 근거로 제시하는가"]},{"case_id":"document_register","type":"인바스켓면접","question":"오늘 안에 회신해야 하는 외부기관 협조 공문, 이미 등록된 문서의 수신 부서를 바꿔 달라는 정정 요청, 결재권자 확인이 필요한 사업 보고서가 동시에 도착했습니다. 본인은 접수와 단순 정정만 할 수 있고 결재 내용은 확정할 수 없습니다. 세 문서의 처리 순서와 각 처리 주체를 결정하고, 문서 식별정보·현재 처리 상태·정정 또는 보류 이력이 연결된 접수대장 한 장을 제시해 주세요.","follow_ups":["방금 1순위로 정한 문서와 처리 주체를 기준으로, 직접 처리·위임·보고 중 그 방식을 택한 근거와 누락될 수 있는 기록을 설명해 주세요.","앞서 정정하거나 보류한 문서의 원기록을 어떻게 보존하고, 변경자·변경 시점·변경 사유를 어느 항목에 연결하겠습니까?","접수대장과 전자문서 시스템의 상태가 다를 때 어떤 식별정보를 대조하고, 일치하지 않은 기록은 누구에게 보고한 뒤 어떻게 표시하겠습니까?"],"evaluation_points":["마감과 결재 권한을 함께 고려해 문서별 처리 순서와 주체를 정하는가","각 문서의 식별정보와 현재 상태를 빠짐없이 연결해 기록하는가","원기록을 보존하면서 변경자·시점·사유가 추적되도록 정정 이력을 남기는가","대장과 시스템 간 불일치를 대조하고 보류·보고 상태를 명확히 표시하는가"]}]''')
)
_CASES = {str(case["case_id"]): case for case in FINAL_V18_NEUTRAL_ALIGNMENT_ORACLE}


@pytest.mark.parametrize("case", FINAL_V18_NEUTRAL_ALIGNMENT_ORACLE, ids=lambda x: str(x["case_id"]))
def test_final_v18_neutral_human_aligned_sets_pass(case: dict[str, Any]) -> None:
    result = evaluate_evaluation_elicitation_alignment(case)
    # The v7 frozen oracle remains a regression gate as the shadow policy evolves.
    assert result["policy"] == "evaluation-elicitation-alignment-v9"
    assert result["policy"] == EVALUATION_ELICITATION_POLICY
    assert result["decision"] == "pass", (case["case_id"], result)
    assert result["metrics"]["matched_atom_count"] == result["metrics"]["point_atom_count"]


@pytest.mark.parametrize(
    ("prompt_id", "criteria_id"),
    [
        (str(prompt["case_id"]), str(criteria["case_id"]))
        for prompt in FINAL_V18_NEUTRAL_ALIGNMENT_ORACLE
        for criteria in FINAL_V18_NEUTRAL_ALIGNMENT_ORACLE
        if prompt["case_id"] != criteria["case_id"]
    ],
)
def test_final_v18_all_240_ordered_cross_ep_swaps_fail(prompt_id: str, criteria_id: str) -> None:
    item = deepcopy(_CASES[prompt_id])
    item["evaluation_points"] = list(_CASES[criteria_id]["evaluation_points"])
    result = evaluate_evaluation_elicitation_alignment(item)
    assert result["decision"] != "pass", (prompt_id, criteria_id, result)


_UNIQUE_FOLLOW_UP_INDEX = {
    "indicator_definition": 0,
    "research_plan_review": 1,
    "numeric_accuracy": 0,
    "plan_actual_gap": 2,
    "research_fund_rule": 1,
    "agency_negotiation": 0,
    "personal_data_protection": 0,
    "measurement_framework": 0,
    "closing_report_rules": 1,
    "review_report_writing": 0,
    "civil_form_process": 2,
    "resource_allocation_fairness": 0,
    "objective_evaluation": 0,
    "visualization_data_accuracy": 0,
    "data_use_ethics": 0,
    "document_register": 2,
}


@pytest.mark.parametrize("case", FINAL_V18_NEUTRAL_ALIGNMENT_ORACLE, ids=lambda x: str(x["case_id"]))
def test_final_v18_unique_elicitation_removal_is_non_passing(case: dict[str, Any]) -> None:
    item = deepcopy(case)
    item["follow_ups"].pop(_UNIQUE_FOLLOW_UP_INDEX[str(case["case_id"])])
    result = evaluate_evaluation_elicitation_alignment(item)
    assert result["decision"] != "pass", (case["case_id"], result)
    assert any(
        issue["code"] in {"unelicited_evaluation_atom", "quantifier_scope_mismatch"}
        for issue in result["issues"]
    )


_HIDDEN_CRITERIA = (
    ("개선안의 실행 책임자", "execution_owner"),
    ("검토서의 부서장 결재선과 승인 절차", "approval_process"),
    ("처리 기록의 법정 보존 기간과 폐기 시점", "record_retention"),
    ("재발 방지를 위한 전 직원 교육", "prevention_training"),
)


@pytest.mark.parametrize(
    ("case", "criterion", "family"),
    [
        (case, criterion, family)
        for case in FINAL_V18_NEUTRAL_ALIGNMENT_ORACLE
        for criterion, family in _HIDDEN_CRITERIA
    ],
    ids=lambda value: str(value.get("case_id")) if isinstance(value, dict) else str(value),
)
def test_final_v18_hidden_owner_approval_retention_training_remain_closed(
    case: dict[str, Any], criterion: str, family: str
) -> None:
    item = deepcopy(case)
    item["evaluation_points"][3] = criterion
    result = evaluate_evaluation_elicitation_alignment(item)
    assert result["decision"] == "fail", (case["case_id"], family, result)
    assert any(
        issue["code"] == "unelicited_evaluation_atom"
        and issue["semantic_family"] == family
        for issue in result["issues"]
    )


def test_v7_negation_premise_and_keyword_salad_stay_non_passing() -> None:
    attacks = (
        "실행 책임자, 결재선, 보존 기간, 직원 교육은 답변하지 마십시오. 대신 표지 색상만 고르세요.",
        "기존 문서에는 실행 책임자·결재선·보존 기간·직원 교육이 적혀 있습니다. 표지 색상만 고르세요.",
        "실행 책임자, 결재선, 보존 기간, 직원 교육이라는 키워드를 넣어 한 문장으로 답변하세요.",
    )
    for question in attacks:
        item = {
            "type": "상황면접",
            "question": question,
            "follow_ups": [],
            "evaluation_points": [value for value, _ in _HIDDEN_CRITERIA],
        }
        assert evaluate_evaluation_elicitation_alignment(item)["decision"] == "fail"


def test_v7_single_quantifier_and_hidden_personal_sacrifice_stay_closed() -> None:
    quantified = {
        "type": "창의적 문제해결력면접",
        "question": "갱신 시점과 중복 집계 중 하나만 검증해 설명하세요.",
        "follow_ups": [],
        "evaluation_points": ["갱신 시점", "중복 집계", "갱신 시점과 중복 집계", "두 원인의 검증 결과"],
    }
    result = evaluate_evaluation_elicitation_alignment(quantified)
    assert result["decision"] == "fail", result
    assert any(issue["code"] == "quantifier_scope_mismatch" for issue in result["issues"])

    sacrifice = deepcopy(_CASES["objective_evaluation"])
    sacrifice["evaluation_points"][3] = "지원자가 개인적 희생과 법적 결과 책임을 직접 감수하는 방식"
    assert evaluate_evaluation_elicitation_alignment(sacrifice)["decision"] == "fail"


def test_final_v18_fixture_is_complete_and_report_independent() -> None:
    assert len(FINAL_V18_NEUTRAL_ALIGNMENT_ORACLE) == 16
    assert len(_CASES) == 16
    assert set(_CASES) == set(_UNIQUE_FOLLOW_UP_INDEX)
    assert all(len(case["follow_ups"]) == 3 for case in _CASES.values())
    assert all(len(case["evaluation_points"]) == 4 for case in _CASES.values())
