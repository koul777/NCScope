"""Deterministic alignment between interview demands and scoring criteria.

The evaluator in this module deliberately answers a narrow question: did the
candidate-facing prompt actually ask for every substantive response object
that appears in ``evaluation_points``?  A pass is correspondence, not approval
that a criterion is lawful, fair, safe, or appropriate; those concerns belong
to separate policy review.  In particular, this evaluator never invents a
self-sacrifice, personal-liability, or ownership criterion.  It does not use
embeddings or a model, and its diagnostics never copy candidate-facing text.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


EVALUATION_ELICITATION_POLICY = "evaluation-elicitation-alignment-v9"


_METHOD_ALIASES = {
    "behavior": "경험면접",
    "behavioral": "경험면접",
    "experience": "경험면접",
    "경험형": "경험면접",
    "행동면접": "경험면접",
    "경험면접": "경험면접",
    "situation": "상황면접",
    "situational": "상황면접",
    "상황형": "상황면접",
    "상황면접": "상황면접",
    "presentation": "발표면접",
    "pt": "발표면접",
    "pt면접": "발표면접",
    "발표면접": "발표면접",
    "discussion": "토론면접",
    "debate": "토론면접",
    "토의면접": "토론면접",
    "토론면접": "토론면접",
    "inbasket": "인바스켓면접",
    "in-basket": "인바스켓면접",
    "인바스켓면접": "인바스켓면접",
    "jobknowledge": "직무지식면접",
    "직무지식": "직무지식면접",
    "직무지식면접": "직무지식면접",
    "creative": "창의적 문제해결력면접",
    "창의적문제해결력면접": "창의적 문제해결력면접",
    "창의적 문제해결력면접": "창의적 문제해결력면접",
}


@dataclass(frozen=True)
class _FamilySpec:
    code: str
    expressions: tuple[re.Pattern[str], ...]


@dataclass(frozen=True)
class _Demand:
    location: str
    clause_index: int
    text: str
    families: frozenset[str]
    quantifier: str
    quantified_text: str
    quantified_families: frozenset[str]
    adaptive: bool


@dataclass(frozen=True)
class _Atom:
    point_index: int
    atom_index: int
    text: str
    family: str
    trait_only: bool


def _patterns(*values: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(value, re.IGNORECASE) for value in values)


# These high-specificity families are available to every method.  Keeping
# hidden requirements (owner, approval line, retention period, and training)
# separate is important: a generic request for a plan or report must not imply
# them.
_COMMON_SPECS = (
    _FamilySpec(
        "evidence_traceability",
        _patterns(
            r"(?:본문|주석|보고서).{0,48}(?:원자료|증빙|근거)"
            r".{0,36}(?:추적|역추적|참조|연결)",
            r"(?:원자료|증빙|근거).{0,48}(?:본문|주석|보고서)"
            r".{0,36}(?:추적|역추적|참조|연결)",
            r"(?:추적|역추적).{0,24}(?:참조|정보|근거|증빙)",
        ),
    ),
    _FamilySpec(
        "approval_process",
        _patterns(
            r"결재\s*(?:선|절차)",
            r"승인\s*(?:선|절차)",
            r"부서장\s*승인",
        ),
    ),
    _FamilySpec(
        "prevention_training",
        _patterns(r"(?:재발\s*방지|예방|직원)\s*교육", r"재교육"),
    ),
    _FamilySpec(
        "report_format",
        _patterns(r"보고\s*(?:서식|양식|형식)", r"연간\s*보고"),
    ),
    _FamilySpec(
        "record_retention",
        _patterns(r"(?:보유|보존|폐기)\s*(?:기간|기한|시점)"),
    ),
    _FamilySpec(
        "record_author",
        _patterns(r"(?:기록\s*)?작성자"),
    ),
    _FamilySpec(
        "execution_owner",
        _patterns(
            r"실행\s*(?:책임|담당|주체)",
            r"이행\s*(?:책임|담당|주체)",
            r"(?:실행|이행)\s*담당자",
            r"^책임$",
            r"(?:조치|보고서).{0,24}담당",
        ),
    ),
    _FamilySpec(
        "monitoring_owner",
        _patterns(
            r"누가.{0,24}(?:이행|점검|확인|추적)",
            r"(?:이행|점검|확인|추적).{0,24}(?:담당|주체|누가)",
            r"(?:실행|이행)\s*책임.{0,24}(?:협의|합의|점검|추적)",
            r"(?:협의|합의).{0,24}(?:실행|이행)\s*책임",
        ),
    ),
    _FamilySpec(
        "triage_ownership",
        _patterns(
            r"(?:마감|기한|권한).{0,60}처리\s*순서.{0,36}(?:주체|담당|위임|보고)",
            r"처리\s*순서.{0,36}(?:주체|담당|위임|보고)",
        ),
    ),
    _FamilySpec(
        "mediation_leadership",
        _patterns(
            r"갈등(?:을|을\s*)?\s*(?:중재|조정)(?:한|하는)?\s*리더십",
            r"중재\s*리더십",
        ),
    ),
    _FamilySpec(
        "organization_staffing",
        _patterns(
            r"(?:부서|조직)\s*(?:인력|정원|편성)",
            r"기관\s*전체\s*(?:예산|일정|정원)",
        ),
    ),
    _FamilySpec(
        "scope_inclusion",
        _patterns(
            r"(?:포함|제외)\s*(?:대상|범위|기준|조건)",
            r"(?:포함\s*[·ㆍ∙‧・,，;；、/]\s*제외|포함\s+및\s+제외)",
            r"(?:측정|집계|반영)\s*대상",
            r"(?:이용|사용|접근|공유|제공)\s*범위",
            r"(?:실적|항목|정보|자료|대상).{0,12}(?:포함|제외)(?:할|할지|하는|한)?",
            r"(?:포함|제외)(?:할|할지|하는|한).{0,8}(?:실적|항목|정보|자료|대상)",
            r"(?:포함|제외)\s*경계",
            r"(?:회신|제공|이용)\s*범위",
            r"(?:참가자|참여자|이용자|인원).{0,12}(?:포함|제외)",
            r"(?:공용|공동)\s*(?:사용|이용).{0,12}(?:범위|예외|적용)",
            r"^(?:포함|제외)$",
        ),
    ),
    _FamilySpec(
        "duplicate_handling",
        _patterns(r"중복\s*(?:처리|여부|제거|기준)?"),
    ),
    _FamilySpec(
        "rule_revision",
        _patterns(
            r"(?:기준|근거|규정|문서).{0,12}(?:개정|변경)",
            r"개정.{0,12}(?:기준|근거|규정|문서)",
        ),
    ),
    _FamilySpec(
        "undefined_rule_handling",
        _patterns(
            r"(?:미규정|규정되지|명시되지|기준이\s*없)",
            r"미확인\s*(?:정보|항목|내용|사실)",
            r"임의.{0,10}(?:처리|집계|기재|판단)",
            r"(?:접수번호|식별번호|승인\s*권한).{0,12}(?:없|누락)",
        ),
    ),
    _FamilySpec(
        "discrepancy_reconciliation",
        _patterns(
            r"(?:불일치|어긋난|맞지\s*않|차이).{0,14}(?:항목|자료|수치|일정|예산|원인|영향)?",
            r"(?:자료|수치|일정|예산).{0,14}(?:불일치|어긋|맞지\s*않|차이)",
            r"(?:자료|값|수치).{0,14}보다.{0,12}(?:크|작|다르)",
            r"(?:대장|시스템|등록표).{0,24}(?:다르|불일치|일치하지)",
            r"(?:다르|불일치|일치하지).{0,24}(?:대장|시스템|등록표)",
        ),
    ),
    _FamilySpec(
        "comparison_basis",
        _patterns(
            r"(?:단위|기간|시점|합계).{0,24}"
            r"(?:관계|대조|비교|확인|같|동일|다르|차이|맞추|조정)",
            r"(?:대조|비교|검산).{0,24}(?:단위|기간|시점|합계)",
            r"(?:다르|차이|맞추).{0,24}(?:단위|기간|시점|합계)",
        ),
    ),
    _FamilySpec(
        "data_change_evidence",
        _patterns(
            r"(?:수치|값|실적|성과).{0,12}(?:변화|추이).{0,24}(?:원인|근거|민원|기록)",
            r"(?:민원|기록).{0,24}(?:수치|값|실적|성과).{0,12}(?:변화|추이)",
            r"수치\s*변화",
            r"민원.{0,12}(?:증가|감소|늘|줄|변화)",
            r"(?:증가|감소|늘|줄|변화).{0,12}민원",
            r"실적\s*(?:급감|급증|감소|증가)",
        ),
    ),
    _FamilySpec(
        "document_comparison",
        _patterns(
            r"(?:자료|문서|신청서|첨부|원자료|산출\s*내역|값).{0,14}(?:대조|일치)",
            r"(?:대조|일치).{0,14}(?:자료|문서|신청서|첨부|원자료|산출\s*내역|값)",
            r"(?:동일|같은)\s*(?:민원|문서|건|대상).{0,16}(?:여부|확인|식별)",
            r"(?:식별\s*정보|식별자).{0,16}(?:다르|일치|확인)",
            r"^대조$",
            r"대조\s*(?:과정|흔적)",
            r"(?:수정본|재제출\s*서류|다시\s*제출된?\s*서류).{0,18}"
            r"(?:대조|확인|일치|오류|누락)?",
            r"(?:수정된|재제출할?)\s*서류.{0,20}(?:다시\s*)?제출",
        ),
    ),
    _FamilySpec(
        "evidence_basis",
        _patterns(
            r"(?:확인|자료상|사실|판단)\s*근거",
            r"(?:적용|판정|처리|선택)\s*근거",
            r"근거.{0,8}(?:확인|자료|사실)",
            r"확인.{0,8}(?:자료|항목)",
            r"(?:원자료\s*)?출처",
            r"(?:자료|수치).{0,14}(?:대조|교차\s*확인).{0,10}(?:타당|판정|확인)",
            r"(?:용도|상태).{0,12}기준",
            r"처리\s*근거",
            r"신뢰(?:할\s*수\s*있다고\s*본)?\s*근거",
            r"판단.{0,14}(?:뒷받침|지지).{0,12}(?:확인|자료|내용|기록)",
            r"어떤\s*자료(?:를|에서|로)?.{0,12}(?:확인|검토|검증)",
            r"자료(?:를|에서|로)?.{0,12}(?:검토|검증)",
        ),
    ),
    _FamilySpec(
        "conditional_revision",
        _patterns(
            r"(?:판단|진단|결정|조정(?:안|\s*기준)?).{0,10}(?:수정|변경|바꾸)",
            r"(?:수정|변경|바꾸).{0,10}(?:판단|진단|결정|조정(?:안|\s*기준)?)",
            r"(?:결과|설명|답변|사실|자료|조건).{0,18}(?:다르|달라|바뀌|확인|따라).{0,24}(?:고치|수정|변경|바꾸|조정)",
            r"(?:다르|달라|바뀌|확인).{0,24}(?:수정\s*대상|어느\s*문서|어느\s*부분).{0,16}(?:고치|수정|변경|바꾸|조정)",
            r"(?:추가\s*)?확인\s*결과.{0,16}(?:수정|변경|조정)",
            r"(?:사실|자료|답변).{0,18}(?:결론|판단).{0,12}(?:영향|바꾸|변경)",
        ),
    ),
    _FamilySpec(
        "cost_tradeoff",
        _patterns(
            r"(?:불이익|부담|비용|반발|관계\s*악화|일정\s*지연)",
            r"감수.{0,10}(?:이유|선택|결과|책임)?",
        ),
    ),
    _FamilySpec(
        "outcome_accountability",
        _patterns(
            r"(?:결과|후속|선택).{0,10}책임",
            r"책임(?!감).{0,10}(?:지|질|입증|결과)",
            r"(?:질|지는|지겠).{0,6}책임",
            r"책임(?:진|지는|질)",
            r"그\s*책임",
            r"책임.{0,8}(?:져|져야|지게)",
        ),
    ),
    _FamilySpec(
        "verification_method",
        _patterns(
            r"(?:대조|확인|검증|점검|재검증|판정).{0,8}(?:방법|방식|절차|값)",
            r"(?:방법|방식).{0,8}(?:대조|확인|검증|점검|재검증)",
            r"(?:원자료|자료|문서).{0,16}대조(?:해|하여|하고)?\s*확인",
            r"대조(?:해|하여|하고)?.{0,12}(?:판정|타당|오류|일치).{0,8}(?:확인|설명)",
            r"판정값",
            r"원자료.{0,12}(?:재분류|추적)",
            r"점검\s*기록.{0,12}(?:구성|항목)",
            r"(?:후속\s*)?점검(?:할|하겠|해|하는)",
        ),
    ),
    _FamilySpec(
        "priority_relation",
        _patterns(
            r"(?:우선\s*관계|우선관계)",
            r"(?:근거|규정|문서).{0,10}우선\s*적용",
            r"우선\s*적용.{0,10}(?:근거|규정|문서)",
        ),
    ),
    _FamilySpec(
        "stakeholder_interest",
        _patterns(
            r"(?:양측|상대|각\s*측).{0,12}(?:주장|입장|이해관계|요구)",
            r"이해관계.{0,10}(?:구분|조정|비교)",
            r"(?:특정\s*)?부서.{0,12}이해관계",
        ),
    ),
    _FamilySpec(
        "concession_boundary",
        _patterns(
            r"(?:양보|수용|불수용|받아들).{0,10}(?:범위|경계|부분|요소)",
            r"(?:범위|경계).{0,10}(?:양보|수용|불수용)",
            r"(?:배제|수용하지\s*않)",
            r"양보\s*(?:한계|선)",
            r"(?:허용|수용)\s*한계",
            r"교환\s*조건",
        ),
    ),
    _FamilySpec(
        "submission_scope",
        _patterns(
            r"(?:선제출|먼저\s*제출|사후\s*보완)",
            r"제출.{0,10}(?:범위|항목|보완\s*조건)",
            r"(?:제출\s*가능|보류)\s*범위",
        ),
    ),
    _FamilySpec(
        "noncompliance_response",
        _patterns(
            r"(?:불이행|지켜지지\s*않|미준수).{0,32}(?:조치|대응|처리)",
            r"(?:조치|대응).{0,32}(?:불이행|미준수)",
        ),
    ),
    _FamilySpec(
        "sensitive_information",
        _patterns(
            r"(?:정보\s*민감도|민감\s*정보|개인정보)",
            r"(?:참여자\s*명단|계좌번호|연락처)",
            r"(?:과도한\s*정보|정보.{0,8}(?:제외|대체))",
        ),
    ),
    _FamilySpec(
        "provision_basis",
        _patterns(
            r"제공.{0,24}(?:목적|권한|근거|승인)",
            r"(?:목적|권한|근거|승인).{0,24}제공",
        ),
    ),
    _FamilySpec(
        "adoption_decision",
        _patterns(r"(?:채택|도입).{0,10}(?:기준|결정|여부)?"),
    ),
    _FamilySpec(
        "decision_maintain_change",
        _patterns(
            r"(?:유지|확대|회수).{0,10}(?:기준|조건|결정)?",
            r"(?:기준|조건).{0,10}(?:유지|확대|회수)",
            r"변경\s*기준",
        ),
    ),
    _FamilySpec(
        "next_action",
        _patterns(
            r"다음\s*(?:(?:확인|대응|처리)\s*)?(?:조치|단계|실행|검증)",
            r"후속\s*(?:조치|단계|실행)",
        ),
    ),
    _FamilySpec(
        "document_purpose",
        _patterns(
            r"(?:문서|자료|보고서).{0,14}(?:사용|이용|보고)\s*목적",
            r"(?:자료별\s*)?사용\s*목적",
            r"문서의\s*용도",
        ),
    ),
    _FamilySpec(
        "status_certainty",
        _patterns(
            r"(?:수치|값|자료|금액).{0,8}(?:확정|미확정|잠정)\s*(?:상태|여부)?",
            r"(?:확정|미확정|잠정)(?:된)?\s*(?:수치|값|자료|금액)",
            r"^(?:확정|미확정|잠정)$",
        ),
    ),
    _FamilySpec(
        "report_content_placement",
        _patterns(
            r"(?:핵심\s*(?:수치|내용)|보충\s*설명).{0,40}(?:본문|주석)",
            r"(?:본문|주석).{0,40}(?:핵심\s*(?:수치|내용)|보충\s*설명)",
            r"(?:본문|주석).{0,24}(?:배치|위치|구분)",
        ),
    ),
    _FamilySpec(
        "certainty_placement",
        _patterns(
            r"(?:확정|미확정|잠정|확실성).{0,50}(?:본문|주석)"
            r".{0,20}(?:배치|위치|구분|표시)",
            r"(?:본문|주석).{0,30}(?:배치|위치|구분|표시)"
            r".{0,50}(?:확정|미확정|잠정|확실성)",
        ),
    ),
    _FamilySpec(
        "report_treatment",
        _patterns(
            r"(?:보고|반영).{0,10}(?:범위|표시|본문|주석|제외)",
            r"(?:본문|주석|제외\s*항목).{0,10}(?:반영|표시)",
            r"(?:본문|주석|잠정값).{0,18}(?:구분|구별|기록|보고)",
            r"(?:불확실|잠정|증빙\s*(?:부족|누락)).{0,18}(?:보고서|구성|구분|표시)",
            r"표시\s*방식",
        ),
    ),
    _FamilySpec(
        "revision_trace",
        _patterns(
            r"(?:수정|변경|정정).{0,12}(?:이력|기록|시각|내용|전후)",
            r"(?:이력|기록).{0,12}(?:수정|변경|정정|재현|추적)",
            r"(?:변경|수정|정정)\s*(?:흔적|내역)",
            r"(?:판정|처리)\s*결과.{0,16}(?:수정|정정|변경|표)",
            r"(?:수정|정정).{0,10}대조표",
            r"정정\s*완료.{0,16}(?:기록|추적|확인)",
            r"(?:수정\s*반영|작성\s*전후)",
            r"(?:기존|수정\s*전)\s*값.{0,24}수정\s*값.{0,16}(?:정정|변경)\s*기록",
            r"수정(?:\s*완료)?\s*(?:확인|확인란|검증)",
            r"(?:확인란|확인\s*기록).{0,16}수정",
            r"(?:변화|수정|변경|정정).{0,24}기록.{0,12}(?:확인|점검)",
            r"기록.{0,12}(?:확인|점검).{0,24}(?:변화|수정|변경|정정)",
            r"(?:다시|재)\s*점검",
        ),
    ),
    _FamilySpec("hold", _patterns(r"(?:처리\s*)?보류")),
    _FamilySpec(
        "responsibility_scope",
        _patterns(
            r"(?:본인|담당).{0,8}책임\s*범위",
            r"책임\s*범위",
            r"본인.{0,10}(?:직접\s*)?작성한\s*부분",
        ),
    ),
    _FamilySpec(
        "analysis_process",
        _patterns(
            r"(?:원자료|자료).{0,12}(?:재구성|정리|분석)\s*(?:과정|절차)?",
            r"(?:재구성|정리|분석).{0,8}(?:과정|절차)",
        ),
    ),
    _FamilySpec(
        "deliverable_revision",
        _patterns(r"(?:수정|보완).{0,8}(?:서식안|수정안|제출본)", r"(?:서식안|수정안)"),
    ),
    _FamilySpec(
        "submission_checklist",
        _patterns(
            r"(?:필수\s*(?:입력값|칸|항목)|첨부물|동의\s*일자)",
            r"(?:날짜|처리\s*이력).{0,8}(?:일치|검증|점검)",
            r"^날짜$",
        ),
    ),
    _FamilySpec(
        "allocation_evidence",
        _patterns(
            r"(?:집행|성과|수요|대기\s*과제).{0,50}(?:배정|조정|배분)(?:\s*근거)?",
            r"(?:배정|조정).{0,50}(?:집행|성과|수요|대기\s*과제)",
        ),
    ),
    _FamilySpec(
        "allocation_plan",
        _patterns(
            r"(?:배정량|배정값|배분량|유보\s*조건|회수\s*기준)",
            r"(?:자원|예산|인력).{0,16}(?:배정|배분|조정)",
            r"(?:사업|부서)별\s*(?:배정|배분)(?:량|값|안)?",
            r"(?:배정|배분)\s*(?:결정|수치|수량|비율)",
        ),
    ),
    _FamilySpec(
        "evaluation_basis",
        _patterns(
            r"(?:참여\s*(?:규모|인원)|성과의?\s*질|만족도|재참여율).{0,16}(?:등급|평가|근거)?",
            r"(?:등급|평가).{0,16}(?:참여|성과|만족도|재참여율)",
            r"(?:확인된\s*수치|등급).{0,18}(?:근거|결정|낮추|유지)",
            r"(?:정량\s*(?:결과|증빙)|수치\s*기준)",
            r"(?:정량\s*증빙|현장\s*맥락).{0,18}(?:우선|판정|평가)",
            r"정량\s*(?:성과|수치|지표|결과|증빙).{0,120}"
            r"(?:현장\s*(?:맥락|설명)|지역별\s*운영\s*여건|질적\s*성과)",
            r"(?:현장\s*(?:맥락|설명)|지역별\s*운영\s*여건|질적\s*성과)"
            r".{0,120}정량\s*(?:성과|수치|지표|결과|증빙)",
        ),
    ),
    _FamilySpec(
        "consistent_context_boundary",
        _patterns(
            r"(?:동일|공통|일관).{0,60}(?:현장|지역|상황|차이|예외)"
            r".{0,40}(?:경계|범위|조건|반영|조정)",
            r"(?:현장|지역|상황|차이|예외).{0,60}(?:동일|공통|일관)"
            r".{0,40}(?:경계|범위|조건|적용)",
            r"(?:공동|공통)\s*(?:판정|평가)?\s*원칙.{0,40}"
            r"(?:예외|적용\s*대상|적용\s*범위)",
        ),
    ),
    _FamilySpec(
        "exception_handling",
        _patterns(
            r"(?:예외|특례).{0,10}(?:인정|적용|처리|범위|조건|여부)?",
            r"적용되지\s*않는\s*(?:경계|조건|대상|범위)",
        ),
    ),
    _FamilySpec(
        "approval_authority",
        _patterns(
            r"(?:예외\s*)?승인(?:할|할\s*수\s*있는)?\s*(?:주체|책임|권자|권한)",
            r"재승인\s*(?:경계|조건|주체)",
        ),
    ),
    _FamilySpec(
        "access_audit",
        _patterns(
            r"(?:요청|승인|접근|공유|사용).{0,40}(?:내역|기록|증거|항목)",
            r"(?:내역|기록).{0,16}(?:요청|승인|접근|공유|사용)",
            r"(?:목적|권한|보유).{0,20}기록",
            r"(?:제공|보류|보고).{0,28}(?:항목|수신자|근거|추적|표)",
            r"(?:항목|수신자|근거).{0,28}(?:제공|추적|표)",
            r"승인(?:자|흔적|경로)",
        ),
    ),
    _FamilySpec(
        "register_field",
        _patterns(
            r"(?:대장|처리목록).{0,18}(?:문서\s*식별|담당|기한|상태|보류)",
            r"(?:등록번호|수발신\s*구분|현재\s*상태|보류\s*사유|임시\s*담당자)",
            r"(?:접수\s*시각|접수시각|발신\s*정보|첨부\s*(?:여부|누락)|이관\s*사유)",
            r"(?:필수\s*식별정보|진행상태).{0,30}(?:대장|처리목록)",
        ),
    ),
    _FamilySpec(
        "data_generation_time",
        _patterns(r"(?:자료|출처|데이터).{0,8}생성\s*시점", r"생성\s*시점"),
    ),
    _FamilySpec(
        "aggregation_scope",
        _patterns(r"집계\s*(?:대상|범위|기간|기준)"),
    ),
    _FamilySpec(
        "resource_requirement",
        _patterns(
            r"(?:자원|재원|예산|인력).{0,20}(?:소요|제약|배분|요구|범위)",
            r"제한된\s*(?:자원|재원|예산|인력)",
            r"(?:총예산|총인력|투입\s*인력).{0,12}(?:동결|제한|한\s*명)",
            r"(?:담당\s*)?인력.{0,8}한\s*명",
        ),
    ),
    _FamilySpec(
        "actual_spend",
        _patterns(r"(?:실제\s*집행|집행액|실집행)"),
    ),
    _FamilySpec(
        "application_document",
        _patterns(
            r"(?:신청서|연구계획서|첨부\s*문서|역할표)",
        ),
    ),
    _FamilySpec(
        "performance_responsibility",
        _patterns(
            r"(?:수행|역할)\s*책임",
            r"(?:참여기관|역할|예산).{0,24}(?:일치|확인|대조|책임)",
            r"(?:누락|빠진|비어\s*있).{0,12}역할",
            r"역할.{0,12}(?:누락|빠뜨|비어\s*있)",
            r"^역할$",
        ),
    ),
    _FamilySpec(
        "discovery_trace",
        _patterns(
            r"(?:발견|찾아낸).{0,12}(?:경위|단서|과정|대조)",
            r"(?:경위|단서).{0,12}(?:발견|찾아)",
        ),
    ),
    _FamilySpec(
        "implementation_assignment",
        _patterns(
            r"(?:조치|담당|책임|기한).{0,24}(?:보고서|실행표|조정안)",
            r"(?:보고서|실행표|조정안).{0,24}(?:조치|담당|책임|기한)",
            r"권고\s*조치",
        ),
    ),
    _FamilySpec(
        "performance_assessment",
        _patterns(
            r"(?:성과|효과).{0,10}(?:판정|확인|평가)",
            r"(?:판정|평가).{0,10}(?:성과|효과)",
            r"(?:성과|결과)\s*(?:확인|점검)\s*지표",
            r"(?:결과|성과).{0,24}(?:확인할|점검할)\s*지표",
        ),
    ),
    _FamilySpec(
        "lawful_processing_basis",
        _patterns(
            r"(?:동의|법적\s*의무|공적\s*임무|적법한\s*근거)",
            r"(?:처리|제공).{0,16}(?:법적|적법|동의|근거)",
            r"(?:법적|적법|동의).{0,16}(?:처리|제공|적용\s*범위)",
        ),
    ),
    _FamilySpec(
        "access_scope",
        _patterns(
            r"(?:접근\s*가능한\s*사람|접근\s*대상)",
            r"열람\s*(?:가능\s*)?(?:대상|범위)",
            r"(?:외부|타\s*부서).{0,12}(?:공유|제공|범위)",
            r"(?:허용|공유|접근).{0,10}범위",
            r"사용\s*가능\s*범위",
            r"공유\s*대상",
            r"(?:접근|공유)\s*범위",
        ),
    ),
    _FamilySpec(
        "guidance_scope",
        _patterns(
            r"(?:안내|서식|기록).{0,18}(?:적용\s*대상|적용\s*기간|기재\s*범위)",
            r"(?:적용\s*대상|적용\s*기간).{0,18}(?:안내|서식|기록|구분)",
        ),
    ),
    _FamilySpec(
        "schedule_requirement",
        _patterns(
            r"(?:일정|착수일|제출일).{0,12}(?:준수|임박|지연|차질|기한)?",
            r"^일정$",
            r"마감(?:이)?\s*(?:제약|임박)",
            r"마감\s*압박",
            r"마감(?:을)?\s*앞두",
            r"(?:제출\s*)?마감(?:이|은|은)?\s*(?:오늘|내일|당일)",
            r"마감.{0,12}(?:오늘|내일|당일)",
            r"연구\s*기간",
            r"^마감$",
            r"마감\s*편의",
        ),
    ),
    _FamilySpec(
        "metric_target",
        _patterns(
            r"목표\s*(?:값|치|선)",
            r"성과\s*지표",
            r"측정\s*(?:지표|치)",
            r"후속\s*측정치",
            r"^목표$",
        ),
    ),
    _FamilySpec(
        "metric_source",
        _patterns(
            r"측정\s*(?:자료|데이터)",
            r"자료\s*(?:출처|원천)",
            r"원자료",
        ),
    ),
    _FamilySpec(
        "metric_cadence",
        _patterns(
            r"측정\s*(?:주기|시점|기간)",
            r"확인\s*(?:주기|시점)",
            r"평가\s*주기",
            r"관찰\s*기간",
        ),
    ),
    _FamilySpec(
        "metric_formula",
        _patterns(r"측정\s*(?:산식|방법)", r"계산\s*산식", r"산식"),
    ),
    _FamilySpec(
        "measurement_denominator",
        _patterns(r"(?:측정\s*)?분모"),
    ),
    _FamilySpec(
        "decision_criteria",
        _patterns(
            r"판단\s*(?:기준|근거)",
            r"선택.{0,3}(?:기준|근거|이유)",
            r"조정\s*기준",
            r"우선\s*기준",
            r"(?:처리|구분|나눈|방식).{0,8}이유",
            r"(?:처리|선택|구분)?\s*사유",
            r"우선\s*적용",
            r"(?:유지|선택).{0,8}근거",
            r"그\s*근거",
            r"선택한\s*처리\s*방식",
            r"(?:이유|근거)(?:는|가)?\s*무엇",
            r"(?:그대로|달리|먼저).{0,12}(?:진행|처리|선택).{0,10}이유",
            r"(?:고르|택하|정하).{0,12}(?:기준|이유|근거)",
            r"(?:주체|처리|보류|위임).{0,12}조건",
            r"(?:정한|결정한|고른|판단한|본)\s*근거",
            r"왜\b",
            r"(?:근거|자료).{0,16}(?:없|부족|확보되지).{0,36}"
            r"(?:기재|공란|보류|제출).{0,16}(?:판단|선택|바꾸)",
        ),
    ),
    _FamilySpec(
        "decision_impact",
        _patterns(
            r"(?:선택|결정|조치|대응).{0,24}(?:예상\s*)?(?:영향|결과|불리)",
            r"(?:예상|불리한|미치는)\s*영향",
            r"그에\s*따른\s*(?:영향|결과)",
            r"불리하게.{0,24}(?:영향|작용)",
            r"(?:그\s*)?영향.{0,16}(?:허용|예상|달라|바뀌)",
        ),
    ),
    _FamilySpec(
        "measurement_quality_tradeoff",
        _patterns(
            r"(?:비교\s*연속성|정확성).{0,40}(?:비교\s*연속성|정확성|업무\s*부담)",
            r"(?:업무\s*부담).{0,40}(?:비교\s*연속성|정확성)",
            r"(?:비교\s*연속성|전년\s*비교).{0,150}(?:누락|중복|정확성)",
            r"(?:누락|중복|정확성).{0,150}(?:비교\s*연속성|전년\s*비교)",
        ),
    ),
    _FamilySpec(
        "intervention_choice",
        _patterns(
            r"(?:보류|수정|반려|제출|게시|사용\s*제한|사용\s*거절)"
            r".{0,18}(?:선택|결정|조치|범위)",
            r"(?:선택한|결정한|직접\s*취할).{0,12}"
            r"(?:보류|수정|반려|제출|게시|제한|거절)",
        ),
    ),
    _FamilySpec(
        "measurement_dimension",
        _patterns(r"측정\s*(?:차원|관점|축)"),
    ),
    _FamilySpec(
        "plan_actual_values",
        _patterns(
            r"(?:계획|예산\s*계획)(?:값|액|표)?.{0,24}"
            r"(?:실적|집행|결산)(?:값|액|표|원장)?",
            r"(?:실적|집행|결산)(?:값|액|표|원장)?.{0,24}"
            r"(?:계획|예산\s*계획)(?:값|액|표)?",
        ),
    ),
)


_METHOD_SPECS: dict[str, tuple[_FamilySpec, ...]] = {
    "경험면접": (
        _FamilySpec(
            "context_role",
            _patterns(
                r"본인(?:의)?\s*(?:역할|책임)",
                r"담당\s*(?:역할|업무)",
                r"맡(?:은|았던)\s*(?:역할|업무)",
                r"(?:당시|실제)\s*(?:상황|배경|과제)",
                r"구체적\s*(?:상황|배경|과제)",
                r"상황과?\s*본인",
                r"본인.{0,40}(?:직접\s*)?(?:판별|작성|수행|담당)",
            ),
        ),
        _FamilySpec(
            "judgment_action",
            _patterns(
                r"본인.{0,10}(?:판단|행동|조치|기여|대응)",
                r"직접\s*(?:판단|행동|조치|기여|대응)",
                r"(?:본인|직접).{0,18}(?:수행|대조|검산|작성|수정)",
                r"(?:수행|대조|검산|작성|수정).{0,12}(?:본인|직접|행동)",
                r"(?:판단|선택).{0,10}(?:행동|조치)",
                r"(?:판단|행동|조치|기여)",
            ),
        ),
        _FamilySpec(
            "outcome_evidence",
            _patterns(
                r"(?:확인\s*가능한\s*)?(?:결과|성과|변화|수치)",
                r"(?:결과|합의).{0,12}(?:기록|자료|회신|입증)",
                r"(?:보고서|산출물).{0,16}(?:활용|사용).{0,16}(?:결과|증거|결정)",
                r"(?:협의|승인|접근\s*변경).{0,24}(?:결과|증거|입증)",
                r"(?:기록|회신).{0,12}(?:확인|입증)",
                r"(?:승인|후속\s*변화).{0,8}(?:기록|결과|증거)?",
            ),
        ),
        _FamilySpec(
            "learning_transfer",
            _patterns(
                r"(?:교훈|학습|개선점|전이)",
                r"다시\s*맡는다면",
                r"다음\s*(?:상황|업무).{0,10}(?:적용|바꾸)",
                r"(?:절차|방식|행동).{0,8}바꾸",
            ),
        ),
    ),
    "상황면접": (
        _FamilySpec(
            "fact_rule",
            _patterns(
                r"^(?:사실|자료)(?:관계)?$",
                r"(?:사실|자료|규정).{0,10}(?:확인|검증)",
                r"확인할\s*(?:사실|자료|값)",
                r"(?:편차|값).{0,8}재검산",
                r"재검산",
            ),
        ),
        _FamilySpec(
            "option_risk",
            _patterns(
                r"(?:대안|위험|리스크)",
                r"조건.{0,8}(?:변화|바뀌)",
                r"(?:영향|부작용).{0,8}(?:비교|검토)",
            ),
        ),
        _FamilySpec(
            "action_sequence",
            _patterns(
                r"첫\s*조치",
                r"(?:처리|행동|대응|조치)\s*순서",
                r"조정안",
                r"(?:처리|대응)\s*절차",
            ),
        ),
        _FamilySpec(
            "escalation_stakeholder",
            _patterns(
                r"누구.{0,8}(?:보고|협의|알리|이송)",
                r"이해관계자",
                r"(?:보고|협의|이송)\s*(?:대상|방식|시점|경로)",
                r"(?:상급|담당).{0,8}(?:보고|이송)",
            ),
        ),
        _FamilySpec(
            "followup_prevention",
            _patterns(r"후속\s*조치", r"(?:재발|오류)\s*예방"),
        ),
    ),
    "발표면접": (
        _FamilySpec(
            "data_diagnosis",
            _patterns(
                r"자료.{0,12}(?:분석|연결|근거)",
                r"(?:현황|원인).{0,8}(?:분석|진단)",
                r"(?:원인|진단|분석)",
            ),
        ),
        _FamilySpec(
            "alternative_comparison",
            _patterns(r"대안.{0,10}비교", r"비교.{0,10}대안", r"(?:대안|방안)"),
        ),
        _FamilySpec(
            "priority_rationale",
            _patterns(
                r"우선\s*(?:대안|순위)",
                r"(?:대안|방안).{0,10}(?:선택\s*)?(?:근거|이유)",
            ),
        ),
        _FamilySpec(
            "implementation_resource",
            _patterns(
                r"실행\s*(?:계획|일정|자원|방안)",
                r"자원\s*(?:제약|배분)",
            ),
        ),
        _FamilySpec(
            "counterevidence_qa",
            _patterns(
                r"(?:반론|반대\s*자료|반증)",
                r"(?:선택한|제시한).{0,20}(?:달리|다른\s*결과)",
                r"질의\s*응답",
                r"질의응답",
                r"자료\s*한계",
            ),
        ),
    ),
    "토론면접": (
        _FamilySpec(
            "position_ground",
            _patterns(
                r"근거\s*있는\s*입장", r"(?:입장|주장).{0,10}(?:근거|논거)", r"입장"
            ),
        ),
        _FamilySpec(
            "other_view_listening",
            _patterns(
                r"상대.{0,12}(?:의견|주장|근거|입장)",
                r"(?:상대|다른\s*쪽).{0,12}(?:요약|검토)",
                r"(?:경청|요약|검토)",
            ),
        ),
        _FamilySpec(
            "tradeoff",
            _patterns(r"(?:상충|상쇄|장단점)", r"(?:비용|영향).{0,8}비교"),
        ),
        _FamilySpec(
            "adjustment_boundary",
            _patterns(
                r"(?:수용|불수용).{0,8}(?:경계|범위)",
                r"(?:수용|불수용|경계|조정|배제)",
            ),
        ),
        _FamilySpec(
            "consensus_escalation",
            _patterns(
                r"(?:공동\s*)?(?:합의안|공동안)",
                r"(?:최종|공동)\s*합의",
                r"미합의.{0,10}이송",
                r"(?:합의가\s*어렵|합의\s*실패|남은\s*쟁점).{0,28}(?:결정권자|상위|이송|넘길)",
                r"(?:결정권자|상위).{0,18}(?:넘길|이송)\s*(?:기준)?",
                r"(?:합의안|공동안).{0,12}적용\s*범위",
                r"합의\s*(?:내용|결과)",
                r"^적용\s*범위$",
            ),
        ),
    ),
    "인바스켓면접": (
        _FamilySpec(
            "triage_priority",
            _patterns(
                r"(?:우선순위|긴급도|영향도|마감)",
                r"(?:오늘|오전|오후|정오|\d+\s*시).{0,10}(?:까지|전|회의|요청)",
                r"처리\s*순서",
            ),
        ),
        _FamilySpec(
            "authority_owner",
            _patterns(
                r"(?:처리|업무|승인|결재)\s*권한",
                r"(?:처리|담당|이관)\s*주체",
                r"담당\s*부서",
                r"^권한$",
                r"^주체(?:를|가|는|를\s*)?",
            ),
        ),
        _FamilySpec("direct_process", _patterns(r"직접\s*처리")),
        _FamilySpec("delegate", _patterns(r"위임")),
        _FamilySpec("hold", _patterns(r"보류")),
        _FamilySpec(
            "initial_action_time",
            _patterns(r"첫\s*(?:조치|문서)", r"(?:처리|후속)\s*시각", r"(?:즉시|기한)"),
        ),
        _FamilySpec(
            "risk_control",
            _patterns(
                r"위험\s*통제",
                r"(?:노출|권리\s*침해|유출|누락|피해)\s*(?:위험|가능성)?",
                r"(?:정보|개인정보).{0,10}(?:최소|제외|보호)",
                r"과도한\s*정보",
                r"오제공",
                r"오류\s*영향",
            ),
        ),
        _FamilySpec(
            "record_handoff",
            _patterns(
                r"(?:처리|후속|보류|보고).{0,10}기록",
                r"(?:기록|인계|보고)",
                r"(?:담당|기한|상태|보류).{0,12}(?:필드|항목|대장)",
            ),
        ),
    ),
    "직무지식면접": (
        _FamilySpec(
            "rule_source_scope",
            _patterns(
                r"(?:규정|지침|계약|근거).{0,12}(?:적용|범위|효력|우선)",
                r"(?:지원기관|협약|내부)\s*(?:조건|절차|지침)"
                r".{0,20}(?:포함|적용|범위|효력|우선)",
                r"(?:포함|적용|범위|효력|우선).{0,20}"
                r"(?:지원기관|협약|내부)\s*(?:조건|절차|지침)",
                r"(?:지원기관|협약)\s*조건|내부\s*절차",
                r"(?:공통|사업별|기관별).{0,10}(?:기준|지침|안내).{0,20}(?:목적|대상|특례|적용|우선)",
                r"(?:목적|대상|특례).{0,18}(?:기준|지침|안내).{0,12}(?:적용|순서|우선)",
                r"적용\s*범위",
                r"(?:규정|지침|계약)\s*근거",
                r"^(?:협약서|지침|규정|계약서)$",
                r"(?:문서|기준|근거).{0,12}(?:효력|충돌|적용\s*순서)",
                r"(?:효력|충돌|적용\s*순서).{0,12}(?:문서|기준|근거)",
                r"(?:두\s*)?문서.{0,12}적용\s*관계",
                r"적용\s*관계",
            ),
        ),
        _FamilySpec(
            "procedure_order",
            _patterns(
                r"(?:업무|검토|처리)?\s*(?:절차|순서|단계)",
                r"(?:적용|검토)\s*우선순위",
                r"우선순위.{0,12}(?:적용|정하|판단)",
            ),
        ),
        _FamilySpec(
            "application_decision",
            _patterns(
                r"(?:인정|보완|승인|반려).{0,10}(?:판단|결정)?",
                r"적용\s*판단",
                r"^판정$",
            ),
        ),
        _FamilySpec("exception_conflict", _patterns(r"(?:예외|특례|충돌)")),
        _FamilySpec(
            "deliverable_quality",
            _patterns(
                r"(?:산출물|검토서|검토기록).{0,10}(?:품질|기재|작성|제시|대응)",
                r"(?:검토서|검토기록|산출물)",
            ),
        ),
        _FamilySpec(
            "error_prevention",
            _patterns(r"(?:반복|재발)?\s*오류", r"(?:재검산|오류\s*예방|재발\s*방지)"),
        ),
    ),
    "창의적 문제해결력면접": (
        _FamilySpec(
            "problem_reframe",
            _patterns(r"문제\s*(?:정의|재정의)", r"원인.{0,8}(?:규명|진단)"),
        ),
        _FamilySpec(
            "hypothesis",
            _patterns(r"가설", r"(?:가능성이\s*높은|핵심|검증할|연결되는)\s*원인"),
        ),
        _FamilySpec(
            "falsifiable_hypothesis",
            _patterns(
                r"반증\s*가능한.{0,16}(?:원인\s*)?가설",
                r"(?:원인\s*)?가설.{0,32}(?:반박|반증|뒤집)"
                r".{0,18}(?:자료|증거|결과)",
            ),
        ),
        _FamilySpec(
            "alternative_novelty",
            _patterns(r"(?:새로운|창의적)\s*(?:대안|아이디어)", r"(?:대안|아이디어)"),
        ),
        _FamilySpec(
            "test_counterevidence",
            _patterns(r"(?:실험|테스트|검증|반증|반대\s*자료)"),
        ),
        _FamilySpec(
            "feasibility_cost",
            _patterns(r"실현\s*가능", r"(?:비용|자원|제약).{0,10}(?:검토|반영|안)"),
        ),
        _FamilySpec("decision_stop", _patterns(r"(?:중단|포기)\s*(?:기준|조건)?")),
        _FamilySpec(
            "decision_switch", _patterns(r"(?:전환|변경|바꾸).{0,8}(?:기준|조건)?")
        ),
        _FamilySpec(
            "implementation_iteration_risk",
            _patterns(
                r"(?:실행|반복)\s*(?:계획|방안|검증)",
                r"다음\s*검증",
                r"실행\s*위험",
            ),
        ),
    ),
}


_TRAIT_RE = re.compile(
    r"(?:책임감|성실성|적극성|친화력|주인의식|열정|인내심|리더십|충성심)"
)
_OBSERVABLE_WITH_TRAIT_RE = re.compile(
    r"(?:보고|기록|작성|선택|판단|행동|조치|중재|조정|검증|설명|제시|실행)"
)
_DEMAND_RE = re.compile(
    r"(?:"
    r"십시오|세요|습니까|인가요|겠습니까|나요|까요|"
    r"해야\s*합니다|해\s*주시기\s*바랍니다|답변\s*바랍니다"
    r")\s*[.!?。！？]*$",
    re.IGNORECASE,
)
_NEGATED_REQUEST_RE = re.compile(
    r"(?:설명|제시|작성|기록|포함|답변|평가|요구|다루|묻|선정|판단)"
    r"(?:하|해|하지|하지는|하지도|하지\s*)?\s*"
    r"(?:마(?:십시오|세요|라)?|말(?:고|아|라)?|않(?:습니다|는다|도록)?|제외)",
    re.IGNORECASE,
)
_NEGATION_SCOPE_BOUNDARY_RE = re.compile(
    r"(?:대신|다만|그러나|그렇지만|반면|하되|해야\s*하지만)[,;:]?\s*"
)
_KEYWORD_INSERTION_RE = re.compile(
    r"(?:키워드|단어|표현).{0,24}(?:넣|포함|사용|언급).{0,16}"
    r"(?:답변|말|작성|제시)",
    re.IGNORECASE,
)
_ADAPTIVE_RE = re.compile(
    r"(?:앞서|방금|그\s*(?:선택|판단|대안|방안|답변|결과|기준)|"
    r"선택한\s*(?:것|항목|대안)|제시한\s*(?:것|안|대안|방안))",
    re.IGNORECASE,
)
_SINGLE_QUANTIFIER_RE = re.compile(
    r"(?:중(?:에서)?\s*(?:하나|한\s*가지)|하나만|한\s*가지만)", re.IGNORECASE
)
_AMBIGUOUS_QUANTIFIER_RE = re.compile(
    r"(?:중(?:에서)?\s*(?:무엇|어느|어떤\s*것)|"
    r"가운데\s*(?:무엇|어느|어떤\s*것))",
    re.IGNORECASE,
)
_QUANTIFIER_SCOPE_BOUNDARY_RE = re.compile(
    r"(?:따져|검토한\s*뒤|확인한\s*뒤|비교한\s*뒤|판단하고|결정하고|정하고|"
    r"근거로|토대로|바탕으로)\s*"
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s+|[\r\n]+")
_PREMISE_PREFIX_RE = re.compile(
    r"^.{0,180}?(?:상황|조건|전제)(?:입니다|이다|가\s*주어졌습니다)[,;:]\s*"
)
_POINT_SPLIT_RE = re.compile(r"\s*(?:[·ㆍ∙‧・,，;；、/]|\s+(?:및|그리고|또는)\s+)\s*")
_PARTICLE_CONJUNCTION_RE = re.compile(
    r"(?:(?<!이유)(?<=[가-힣])와|(?<!결)(?<!성)(?<!효)(?<!인)(?<!학)과)"
    r"\s+(?=[가-힣A-Za-z0-9])"
)
# These forms describe arguments or examples of one scored relation, rather
# than several independent response objects.  For example, ``A·B·C 등 결과의
# 증거`` asks for outcome evidence, while ``A·B·C 중 하나`` really does limit
# the selectable objects and must remain split for quantifier checking.
_RELATIONAL_POINT_RE = re.compile(
    r"(?:"
    r"\b등\b.{0,40}(?:결과|증거|행동|절차|구성|기록)|"
    r"(?:와|과)\s*연결되는\s*(?:원인|결과|위험)|"
    r"(?:와|과|및).{0,24}(?:차이|불일치)(?:를|의|로)|"
    r"(?:정량\s*증빙|현장\s*맥락).{0,28}(?:우선|판정)|"
    r"(?:본문|주석).{0,40}(?:배치|위치|구분).{0,40}"
    r"(?:확실성|확정|잠정).{0,24}(?:일치|맞)|"
    r"(?:동일|공통|일관).{0,60}(?:현장|지역|상황|차이|예외)"
    r".{0,40}(?:경계|범위|조건)|"
    r"(?:목적|권한|대상|범위).{0,56}가운데.{0,30}(?:충돌|위반|문제)\s*지점|"
    r"(?:변화|기록|결정|수치|승인).{0,60}(?<!등)(?<!등으)(?:으로|로).{0,24}"
    r"(?:활용\s*)?결과.{0,16}(?:입증|확인)|"
    r"(?:또는|및|와|과).{0,48}(?:반증\s*가능한\s*)?(?:원인\s*)?가설"
    r"|(?:우선\s*)?검증할\s*원인.{0,48}(?:불일치|관찰).{0,16}연결"
    r"|(?:핵심\s*(?:수치|내용)|보충\s*설명).{0,48}(?:본문|주석)"
    r".{0,24}(?:배치|위치|구분)"
    r"|(?:본문|주석|보고서).{0,48}(?:원자료|증빙|근거)"
    r".{0,36}(?:추적|역추적|참조|연결)"
    r")"
)
_TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9]{2,}")
_TOKEN_SUFFIXES = (
    "으로부터",
    "에서는",
    "에게서",
    "까지의",
    "으로",
    "에서",
    "에게",
    "부터",
    "까지",
    "처럼",
    "보다",
    "하고",
    "하며",
    "하여",
    "해야",
    "하는",
    "한",
    "된",
    "되는",
    "인지",
    "의",
    "을",
    "를",
    "이",
    "가",
    "은",
    "는",
    "에",
    "로",
    "와",
    "과",
    "도",
)
_TOKEN_STOPWORDS = {
    "평가",
    "여부",
    "정도",
    "능력",
    "적절",
    "구체",
    "명확",
    "타당",
    "일관",
    "설명",
    "제시",
    "작성",
    "확인",
    "방식",
    "방법",
    "대한",
    "관련",
}
_SHORT_SEMANTIC_TOKENS = {
    "계획",
    "집행",
    "성과",
    "실적",
    "자료",
    "서식",
    "상태",
    "첨부",
    "판정",
    "협의",
    "주석",
    "대상",
    "출처",
    "변경",
    "순서",
    "주체",
    "근거",
    "결과",
    "위험",
    "기록",
    "기한",
    "권한",
    "목적",
    "예외",
    "특례",
    "승인",
    "수정",
    "보고",
    "이관",
    "누락",
    "증빙",
    "원인",
    "대조",
    "검산",
    "단위",
    "기간",
    "후속",
    "민원",
    "조치",
}

_CONTEXT_SLOT_PATTERNS: dict[str, re.Pattern[str]] = {
    "access_audit": re.compile(
        r"^(?:요청|승인|접근|공유|사용|제공|수신자|근거)(?:\s*내역)?$"
    ),
    "access_scope": re.compile(
        r"^(?:외부|타\s*부서|허용\s*범위)$|"
        r"^(?:접근|공유|열람)(?:\s*(?:가능\s*)?(?:대상|범위))?$"
    ),
    "allocation_evidence": re.compile(r"^(?:집행|성과|수요)(?:\s*자료)?$"),
    "allocation_plan": re.compile(r"^(?:조건|배정|유보|회수)(?:\s*기준)?$"),
    "evaluation_basis": re.compile(
        r"^(?:참여\s*규모|성과의?\s*질|등급\s*근거|정량\s*증빙|현장\s*맥락)$"
    ),
    "exception_handling": re.compile(
        r"^(?:예외|특례)(?:\s*(?:조건|여부))?$|^(?:인정\s*)?한계.*$|"
        r"^(?:그\s*)?한계.*$|^불성립\s*위험.*$"
    ),
    "guidance_scope": re.compile(
        r"^(?:상충하는\s*안내의\s*적용\s*대상|기간(?:을\s*구분.*)?)$"
    ),
    "implementation_assignment": re.compile(r"^(?:조치|책임|담당|기한|완료\s*시점)$"),
    "lawful_processing_basis": re.compile(
        r"^(?:동의|법적\s*의무|공적\s*임무|그\s*적용\s*범위(?:를\s*구별함)?)$"
    ),
    "register_field": re.compile(
        r"^(?:문서\s*식별|필수\s*식별정보|담당|기한|상태|진행상태|"
        r"보류\s*근거|변경\s*내용|접수\s*시각|접수시각|첨부|발신\s*정보)$"
    ),
    "revision_trace": re.compile(
        r"^(?:수정|변경|정정)(?:\s*(?:시각|내용|이력|전후|흔적))?$|"
        r"^(?:판정|처리)\s*결과$|"
        r"^(?:기존|수정\s*전)\s*값$|^적용\s*기준$|^수정\s*값$|"
        r"^(?:변경\s*)?시점$"
    ),
    "status_certainty": re.compile(
        r"^(?:확정|미확정|잠정)(?:된)?\s*(?:수치|값|금액)?$"
    ),
    "report_treatment": re.compile(r"^(?:표시|반영)(?:\s*방식)?$"),
    "rule_source_scope": re.compile(
        r"^(?:공통\s*기준|사업별\s*기준(?:의\s*목적)?|목적|대상|특례|"
        r"사업|기간|지출\s*유형)$"
    ),
    "scope_inclusion": re.compile(
        r"^(?:측정|집계|반영)\s*대상$|^(?:포함|제외)(?:\s*(?:대상|조건))?$|"
        r"^(?:접근|공유)(?:\s*범위)?$"
    ),
    "evidence_basis": re.compile(
        r"^(?:재확인에\s*필요한\s*)?(?:원자료\s*)?출처$|^검증\s*사실$|"
        r"^(?:자료|사실)(?:의\s*구체성|을\s*구체적으로\s*제시.*)?$|"
        r"^신뢰\s*근거.*$"
    ),
    "conditional_revision": re.compile(
        r"^(?:추가\s*확인\s*결과에\s*따라\s*)?수정\s*대상$|"
        r"^사실이\s*결론에\s*미치는\s*영향.*$|"
        r"^(?:추가|새로운)\s*(?:정보|사실|자료).{0,20}(?:제출|판단)$"
    ),
    "escalation_stakeholder": re.compile(r"^보고\s*경로(?:를\s*조정함)?$"),
    "judgment_action": re.compile(
        r"^(?:집계표를\s*)?(?:직접\s*)?(?:대조|검산|수행).*(?:행동|작업).*$|"
        r"^(?:보류|수정|반려)\s*결정.*$"
    ),
    "decision_criteria": re.compile(
        r"^(?:그\s*)?(?:선택|판정|처리|제출).*(?:이유|기준|타당성).*$|"
        r"^조건(?:을\s*명확히\s*설명.*)?$"
    ),
    "actual_spend": re.compile(r"^(?:집행|실집행|집행액)$"),
    "data_diagnosis": re.compile(r"^(?:계획|집행|성과)(?:값)?$"),
    "procedure_order": re.compile(
        r"^(?:특례를\s*구분해\s*)?적용\s*(?:순서|우선순위).*$"
    ),
    "verification_method": re.compile(
        r"^(?:대조|검산|확인).*(?:판정|타당성|방법|절차).*$|^오류\s*확인\s*방법.*$"
    ),
    "application_decision": re.compile(r"^(?:판정|결론)$"),
    "risk_control": re.compile(r"^(?:노출|권리\s*침해)(?:\s*위험)?$"),
    "authority_owner": re.compile(r"^(?:처리|이관|담당)?\s*주체$"),
    "record_handoff": re.compile(r"^(?:순서|주체|상태|접수시각|주석|첨부|발신정보)$"),
    "outcome_evidence": re.compile(
        r"^(?:협의|승인|접근\s*변경|수정|후속\s*결정)(?:\s*결과|\s*증거)?$"
    ),
    "document_comparison": re.compile(
        r"^(?:서식|내부\s*기록|계획표|원장|증빙)(?:\s*자료)?$"
    ),
    "consensus_escalation": re.compile(
        r"^(?:합의\s*실패\s*시\s*)?(?:남은|미합의)\s*쟁점$|"
        r"^(?:(?:상위\s*)?결정권자(?:에게)?\s*)?(?:넘길|이송)\s*기준.*$"
    ),
    "decision_stop": re.compile(r"^(?:계속|중단)(?:\s*(?:조건|판정|경계))?$"),
    "triage_priority": re.compile(r"^(?:마감\s*시각|외부\s*영향|영향)$"),
    "schedule_requirement": re.compile(
        r"^(?:마감|마감\s*편의|마감\s*압박|연구\s*기간|세부\s*일정)$"
    ),
    "discrepancy_reconciliation": re.compile(
        r"^(?:금액|수량)\s*단위$|^(?:사업|과제|기관)명$|^신청\s*주체$"
    ),
    "comparison_basis": re.compile(r"^(?:단위|기간|시점|합계)(?:\s*관계)?$"),
    "plan_actual_values": re.compile(
        r"^(?:계획|실적|집행|결산)(?:값|액|표|원장)?$"
    ),
    "intervention_choice": re.compile(
        r"^(?:제한된\s*(?:인력|자원|예산)\s*(?:아래|에서)\s*)?"
        r"(?:선택한\s*)?(?:보류|수정|반려|제출|게시|제한|거절)(?:\s*조치)?$"
    ),
    "resource_requirement": re.compile(
        r"^(?:제한된\s*)?(?:인력|예산|자원|재원)(?:\s*범위)?$"
    ),
    "undefined_rule_handling": re.compile(
        r"^미확인\s*(?:정보|항목|내용|사실)$"
    ),
}

# Scenario facts remain facts, not demands.  A small set of bounded decision
# inputs may nevertheless be inherited when the same prompt asks the candidate
# to decide from that scenario.  Response actions such as execution ownership,
# approval workflow, escalation, and stakeholder contact are intentionally
# absent, which preserves the premise-only negative boundary.
_CONTEXT_INPUT_FAMILIES = frozenset(
    {
        "actual_spend",
        "access_scope",
        "allocation_evidence",
        "application_document",
        "approval_authority",
        "consistent_context_boundary",
        "data_generation_time",
        "discrepancy_reconciliation",
        "document_comparison",
        "duplicate_handling",
        "evaluation_basis",
        "initial_action_time",
        "metric_cadence",
        "metric_source",
        "risk_control",
        "schedule_requirement",
        "scope_inclusion",
        "sensitive_information",
        "stakeholder_interest",
    }
)


def _text(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def _compact(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", _text(value)).lower()


def _method(value: Any) -> str:
    normalized = re.sub(r"\s+", "", _text(value)).lower()
    return _METHOD_ALIASES.get(normalized, _text(value))


def _specs_for(method: str) -> tuple[_FamilySpec, ...]:
    return (*_COMMON_SPECS, *_METHOD_SPECS.get(method, ()))


def _detect_families(text: str, method: str) -> frozenset[str]:
    return frozenset(
        spec.code
        for spec in _specs_for(method)
        if any(expression.search(text) for expression in spec.expressions)
    )


def _negated_families(text: str, method: str) -> frozenset[str]:
    """Return response objects explicitly excluded from the answer request.

    Negation is scoped to the material before the negative request verb and
    after the last contrast boundary.  This keeps a sentence such as "A는
    묻지 말고 B를 설명하세요" from accidentally eliciting A while retaining
    B as a positive demand.
    """

    families: set[str] = set()
    for match in _NEGATED_REQUEST_RE.finditer(text):
        prefix = text[: match.end()]
        boundaries = list(_NEGATION_SCOPE_BOUNDARY_RE.finditer(prefix))
        if boundaries:
            prefix = prefix[boundaries[-1].end() :]
        families.update(_detect_families(prefix, method))
    return frozenset(families)


def _primary_family(text: str, method: str) -> str:
    families = _detect_families(text, method)
    if "allocation_plan" in families and re.search(r"(?:배정|배분)", text):
        return "allocation_plan"
    if "monitoring_owner" in families:
        return "monitoring_owner"
    if "report_content_placement" in families:
        return "report_content_placement"
    if "triage_ownership" in families:
        return "triage_ownership"
    if "certainty_placement" in families:
        return "certainty_placement"
    if "consistent_context_boundary" in families:
        return "consistent_context_boundary"
    if "falsifiable_hypothesis" in families:
        return "falsifiable_hypothesis"
    # In a relational criterion the head after ``등`` or ``연결되는`` carries
    # the scored response role.  Choosing the first modifier family would turn
    # examples such as approval/access changes into hidden mandatory atoms.
    if (
        "등" in text
        and "outcome_evidence" in families
        and re.search(r"(?:결과|증거|입증)", text)
    ):
        return "outcome_evidence"
    if "outcome_evidence" in families and re.search(
        r"(?<!등)(?<!등으)(?:으로|로).{0,24}(?:활용\s*)?결과.{0,16}(?:입증|확인)",
        text,
    ):
        return "outcome_evidence"
    if re.search(r"(?:와|과)\s*연결되는\s*원인", text) and "hypothesis" in families:
        return "hypothesis"
    if re.search(r"(?:와|과|및).{0,24}(?:차이|불일치)", text):
        if "discrepancy_reconciliation" in families:
            return "discrepancy_reconciliation"
    if "evaluation_basis" in families and re.search(
        r"(?:정량\s*증빙|현장\s*맥락).{0,28}(?:우선|판정)", text
    ):
        return "evaluation_basis"
    if "data_generation_time" in families and re.search(r"생성\s*시점", text):
        return "data_generation_time"
    if "judgment_action" in families and re.search(
        r"(?:본인|직접).{0,20}(?:결정|선택|수행|대조|검산|행동)", text
    ):
        return "judgment_action"
    for spec in _specs_for(method):
        if any(expression.search(text) for expression in spec.expressions):
            return spec.code
    return "unclassified_response_object"


def _prompt_rows(item: Mapping[str, Any]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    question = _text(item.get("question"))
    if question:
        rows.append(("question", question))
    follow_ups = item.get("follow_ups")
    if isinstance(follow_ups, Sequence) and not isinstance(
        follow_ups, (str, bytes, bytearray)
    ):
        for index, raw in enumerate(follow_ups):
            if isinstance(raw, Mapping):
                value = raw.get("question") or raw.get("text") or raw.get("prompt")
            else:
                value = raw
            text = _text(value)
            if text:
                rows.append((f"follow_up:{index + 1}", text))
    return rows


def _quantifier_scope(text: str, method: str) -> tuple[str, str, frozenset[str]]:
    match = _SINGLE_QUANTIFIER_RE.search(text)
    mode = "single"
    if match is None:
        match = _AMBIGUOUS_QUANTIFIER_RE.search(text)
        mode = "ambiguous"
    if match is None:
        return "all", "", frozenset()
    prefix = text[: match.start()]
    boundaries = list(_QUANTIFIER_SCOPE_BOUNDARY_RE.finditer(prefix))
    if boundaries:
        prefix = prefix[boundaries[-1].end() :]
    quantified_text = f"{prefix}{text[match.start() : match.end()]}".strip()
    return mode, quantified_text, _detect_families(quantified_text, method)


def _demand_clauses(item: Mapping[str, Any], method: str) -> list[_Demand]:
    demands: list[_Demand] = []
    previous_families: frozenset[str] = frozenset()
    for location, text in _prompt_rows(item):
        row_families = _detect_families(text, method)
        row_context_families = row_families & _CONTEXT_INPUT_FAMILIES
        clauses = [
            part.strip() for part in _SENTENCE_SPLIT_RE.split(text) if part.strip()
        ]
        for clause_index, raw_clause in enumerate(clauses, start=1):
            if not ("?" in raw_clause or _DEMAND_RE.search(raw_clause)):
                continue
            clause = _PREMISE_PREFIX_RE.sub("", raw_clause)
            if _KEYWORD_INSERTION_RE.search(clause):
                continue
            negated_families = _negated_families(clause, method)
            explicit_families = frozenset(
                _detect_families(clause, method) - negated_families
            )
            # A missing identifier or absent rule is only inherited from the
            # scenario when the same demand asks for a record-state handling
            # decision.  Merely mentioning the defect in a premise must not
            # make it a scored response object.
            gated_context_families: set[str] = set()
            if (
                "undefined_rule_handling" in row_families
                and explicit_families
                & {
                    "authority_owner",
                    "hold",
                    "record_handoff",
                    "register_field",
                    "triage_ownership",
                }
            ):
                gated_context_families.add("undefined_rule_handling")
            # Resource limits are decision inputs only when the same demand
            # asks the candidate to allocate, test, or implement under them.
            # A premise that merely mentions a small team or frozen budget
            # must not open a hidden resource-scoring criterion.
            if (
                "resource_requirement" in row_families
                and explicit_families
                & {
                    "allocation_plan",
                    "allocation_evidence",
                    "feasibility_cost",
                    "implementation_resource",
                    "intervention_choice",
                    "test_counterevidence",
                }
            ):
                gated_context_families.add("resource_requirement")
            if (
                "data_change_evidence" in row_families
                and explicit_families
                & {
                    "data_diagnosis",
                    "discrepancy_reconciliation",
                    "performance_assessment",
                    "plan_actual_values",
                }
            ):
                gated_context_families.add("data_change_evidence")
            if (
                "measurement_quality_tradeoff" in row_families
                and explicit_families
                & {
                    "adoption_decision",
                    "decision_stop",
                    "hypothesis",
                    "test_counterevidence",
                }
            ):
                gated_context_families.add("measurement_quality_tradeoff")
            families = frozenset(
                set(explicit_families)
                | (set(row_context_families) - set(negated_families))
                | gated_context_families
            )
            adaptive = bool(_ADAPTIVE_RE.search(clause))
            if adaptive:
                # Pronouns can inherit the response object, not an unasked
                # action or quality.  Any explicit families in this probe are
                # retained alongside the previous object's families.
                families = frozenset((*families, *previous_families))
            quantifier, quantified_text, quantified_families = _quantifier_scope(
                clause, method
            )
            demands.append(
                _Demand(
                    location=location,
                    clause_index=clause_index,
                    text=clause,
                    families=families,
                    quantifier=quantifier,
                    quantified_text=quantified_text,
                    quantified_families=quantified_families,
                    adaptive=adaptive,
                )
            )
            previous_families = families or previous_families
    return demands


def _point_values(item: Mapping[str, Any]) -> tuple[list[str], bool]:
    raw = item.get("evaluation_points")
    if not isinstance(raw, list):
        return [], False
    points = [_text(value) for value in raw]
    return points, len(points) == 4 and all(points)


def _point_fragments(point: str) -> list[str]:
    if _RELATIONAL_POINT_RE.search(point) and not (
        _SINGLE_QUANTIFIER_RE.search(point) or _AMBIGUOUS_QUANTIFIER_RE.search(point)
    ):
        return [point.strip(" -:()[]")]
    pieces: list[str] = []
    for rough in _POINT_SPLIT_RE.split(point):
        pieces.extend(_PARTICLE_CONJUNCTION_RE.split(rough))
    return [piece.strip(" -:()[]") for piece in pieces if piece.strip(" -:()[]")]


def _atoms(points: Sequence[str], method: str) -> list[_Atom]:
    atoms: list[_Atom] = []
    for point_index, point in enumerate(points, start=1):
        fragments = _point_fragments(point)
        point_families = _detect_families(point, method)
        for atom_index, fragment in enumerate(fragments, start=1):
            family = _primary_family(fragment, method)
            if family == "unclassified_response_object":
                fragment_key = re.sub(
                    r"(?:을|를|이|가|은|는|의|에|로|으로)$", "", fragment
                ).strip()
                for candidate, expression in _CONTEXT_SLOT_PATTERNS.items():
                    if candidate in point_families and expression.fullmatch(
                        fragment_key
                    ):
                        family = candidate
                        break
            trait_only = bool(
                _TRAIT_RE.search(fragment)
                and not _OBSERVABLE_WITH_TRAIT_RE.search(fragment)
            )
            atoms.append(
                _Atom(
                    point_index=point_index,
                    atom_index=atom_index,
                    text=fragment,
                    family="unobservable_trait" if trait_only else family,
                    trait_only=trait_only,
                )
            )
    return atoms


def _stem_token(value: str) -> str:
    stem = value
    changed = True
    while changed:
        changed = False
        for suffix in _TOKEN_SUFFIXES:
            if stem.endswith(suffix) and len(stem) - len(suffix) >= 2:
                stem = stem[: -len(suffix)]
                changed = True
                break
    return stem


def _tokens(value: str) -> set[str]:
    return {
        stem
        for token in _TOKEN_RE.findall(value.lower())
        if (stem := _stem_token(token)) not in _TOKEN_STOPWORDS and len(stem) >= 2
    }


def _lexically_matches(atom_text: str, demand_text: str) -> bool:
    atom_key = _compact(atom_text)
    demand_key = _compact(demand_text)
    if len(atom_key) >= 4 and atom_key in demand_key:
        return True
    atom_tokens = _tokens(atom_text)
    demand_tokens = _tokens(demand_text)
    anchors = {
        (atom_token, demand_token)
        for atom_token in atom_tokens
        for demand_token in demand_tokens
        if atom_token == demand_token
        or (
            min(len(atom_token), len(demand_token)) >= 2
            and (
                atom_token.startswith(demand_token)
                or demand_token.startswith(atom_token)
            )
        )
    }
    atom_anchor_tokens = {atom_token for atom_token, _ in anchors}
    if len(atom_anchor_tokens) >= 2:
        return True
    return bool(
        len(atom_anchor_tokens) == 1
        and (
            len(next(iter(atom_anchor_tokens))) >= 3
            or next(iter(atom_anchor_tokens)) in _SHORT_SEMANTIC_TOKENS
        )
    )


def _relationally_matches(atom: _Atom, demand: _Demand) -> bool:
    """Match a scored role to a semantically equivalent demand relation.

    These rules deliberately require a role plus its operation.  A generic
    noun such as ``보고서`` or a scenario-only stakeholder mention therefore
    cannot open hidden owner, approval, retention, or training criteria.
    """

    text = demand.text
    families = demand.families
    if atom.family == "comparison_basis":
        return bool(
            (
                "comparison_basis" in families
                and re.search(r"(?:단위|기간|시점|합계|범위|비교|대조)", text)
            )
            or (
                re.search(r"단위", atom.text)
                and
                "plan_actual_values" in families
                and "discrepancy_reconciliation" in families
                and re.search(r"(?:계획\s*기준|계획(?:값|치)?)", text)
                and re.search(r"(?:실적\s*관측값|실적(?:값|치)?|집행|결산)", text)
                and re.search(r"(?:표|비교|차이|판정)", text)
            )
        )
    if atom.family == "schedule_requirement":
        return bool(
            "triage_priority" in families
            and (
                re.search(r"(?:마감|기한|오늘|내일|정오|오전|오후)", text)
                or re.search(r"처리\s*순서|우선", text)
            )
        )
    if atom.family == "discrepancy_reconciliation":
        if (
            {"metric_cadence", "duplicate_handling"} <= families
            and re.search(r"(?:측정\s*기간|월별|연간|집계)", atom.text)
        ):
            return True
    if atom.family == "decision_impact":
        return bool(
            (
                "decision_impact" in families
                and re.search(r"(?:영향|불리|결과)", text)
            )
            or (
                {"conditional_revision", "performance_responsibility"}
                <= families
                and re.search(r"(?:역할|비용|수행|빠졌|누락)", text)
            )
        )
    if atom.family == "measurement_quality_tradeoff":
        return bool(
            "cost_tradeoff" in families
            and re.search(r"(?:비교|추세|연속성)", text)
            and re.search(r"(?:부담|불리|누락|중복|정확)", text)
        )
    if atom.family == "resource_requirement":
        return bool(
            "test_counterevidence" in families
            and re.search(r"(?:표본|작은|소규모|대조|시험|검증)", atom.text)
        )
    if atom.family == "rule_revision":
        return bool(
            (
                "exception_handling" in families
                and "decision_maintain_change" in families
                and re.search(
                    r"(?:기준|집계|산식|방식).{0,18}(?:바뀌|변경|전후)", text
                )
            )
            or (
                "decision_maintain_change" in families
                and re.search(r"(?:반대|상충|충돌).{0,16}(?:자료|근거|기준)", text)
                and re.search(r"(?:유지|변경|수정).{0,12}기준", text)
            )
        )
    if atom.family == "rule_source_scope":
        return bool(
            "priority_relation" in families
            and {"lawful_processing_basis", "evidence_basis"} & families
            and re.search(r"(?:지원기관|협약|내부|지침|규정|근거)", text)
            and re.search(r"(?:포함|적용|범위|우선|바꾸|판단)", text)
        )
    if atom.family == "hold":
        return bool(
            "application_decision" in families
            and (
                re.search(r"처리\s*(?:여부|경계|보류)", text)
                or (
                    re.search(r"보완(?:을|\s*)?(?:요청|요구)", text)
                    and re.search(r"(?:이송|승인권자)", text)
                )
            )
        )
    if atom.family == "scope_inclusion":
        return bool(
            {"access_scope", "aggregation_scope", "exception_handling"} & families
            and _lexically_matches(atom.text, text)
        )
    if atom.family == "context_role":
        return bool(
            "judgment_action" in families
            and re.search(r"(?:당시|실제|경험)", text)
            and re.search(r"(?:본인|맡(?:은|았던)\s*역할)", text)
        )
    if atom.family == "conditional_revision":
        return bool(
            {"decision_maintain_change", "decision_switch"} & families
            and {"test_counterevidence", "hypothesis", "conditional_revision"}
            & families
            and re.search(r"(?:반증|맞지\s*않|다른|달라|바꿀|수정)", text)
        )
    if atom.family == "discrepancy_reconciliation":
        return bool(
            "document_comparison" in families
            and re.search(r"(?:다르|불일치|일치하지)", text)
            and re.search(r"(?:대장|시스템|문서|자료|기록)", text)
        )
    if atom.family == "report_content_placement":
        return bool(
            {"certainty_placement", "report_treatment", "report_content_placement"}
            & families
            and re.search(r"(?:본문|주석)", text)
            and re.search(r"(?:배치|구분|표시)", text)
        )
    if atom.family == "document_purpose":
        return bool(
            {"lawful_processing_basis", "provision_basis"} & families
            and re.search(r"목적", text)
        )
    if atom.family == "error_prevention":
        return bool(
            {"exception_handling", "risk_control", "discrepancy_reconciliation"}
            & families
            and re.search(r"(?:오류|누락|왜곡|위험|재발|틀리)", text)
            and re.search(r"(?:막|통제|드러내|확인|해소|설명)", text)
        )
    if atom.family == "data_change_evidence":
        return bool(
            {"data_diagnosis", "discrepancy_reconciliation"} & families
            and re.search(r"(?:수치|값|실적|성과|민원|원인|변동|추이)", text)
        )
    if atom.family == "decision_criteria":
        return bool(
            (
                "evidence_basis" in families
                and {
                    "application_decision",
                    "deliverable_quality",
                    "intervention_choice",
                    "judgment_action",
                }
                & families
                and re.search(r"(?:근거|이유|뒷받침)", text)
            )
            or (
                "conditional_revision" in families
                and re.search(r"(?:없|않|부족|미확인|달라|바뀌|경우|조건)", text)
                and re.search(r"(?:판단|선택|결론|보류|기재|제출)", text)
            )
        )
    if atom.family == "tradeoff":
        return bool(
            {"position_ground", "stakeholder_interest", "other_view_listening"}
            & families
            and re.search(r"(?:두|양측|상대|서로|수용|배제)", text)
            and re.search(r"(?:입장|근거|자료|사실|요구)", text)
        )
    if atom.family == "verification_method":
        return bool(
            (
                "document_comparison" in families
                and (
                    re.search(
                        r"(?:대조|검산|확인)(?:한|해|하여|하고)?.{0,18}"
                        r"(?:오류|일치|판정|결정|타당|방법)",
                        text,
                    )
                    or re.search(
                        r"(?:오류|일치|판정|타당).{0,18}(?:대조|검산|확인)",
                        text,
                    )
                )
            )
            or (
                "revision_trace" in families
                and re.search(
                    r"(?:출처|변경\s*흔적|수정\s*완료).{0,28}(?:확인|입증|검증)",
                    text,
                )
            )
            or (
                "test_counterevidence" in families
                and re.search(
                    r"(?:표본|대조\s*자료|작은|소규모|최소).{0,24}"
                    r"(?:대조|검증|시험)",
                    text,
                )
            )
            or bool(re.search(r"(?:작성\s*)?오류.{0,24}(?:어떻게\s*)?확인", text))
        )
    if atom.family == "evidence_basis":
        if "decision_criteria" in families:
            return True
        return "document_comparison" in families and bool(
            re.search(r"(?:자료|수치|문서).{0,18}(?:대조|확인)", text)
        )
    if atom.family == "outcome_accountability":
        return bool(
            (
                "cost_tradeoff" in families
                and (
                    re.search(
                        r"(?:본인|그로\s*인해|선택).{0,24}"
                        r"(?:감수|불이익|부담|반발|지연)",
                        text,
                    )
                    or re.search(
                        r"(?:감수|불이익|부담|반발|지연).{0,24}(?:본인|선택)",
                        text,
                    )
                )
            )
            or (
                "outcome_evidence" in families
                and re.search(
                    r"(?:승인|제한|거절|보류).{0,18}(?:결과|증거).{0,18}책임\s*이행",
                    atom.text,
                )
                and re.search(r"(?:승인|제한|삭제|결과|기록)", text)
            )
        )
    if atom.family == "direct_process":
        return "authority_owner" in families and bool(
            re.search(r"처리\s*(?:순서와\s*)?(?:처리\s*)?주체", text)
        )
    if atom.family == "discrepancy_reconciliation":
        return "duplicate_handling" in families and bool(
            re.search(
                r"(?:측정\s*차원|집계\s*단위|포함|제외|관찰\s*기간|집계\s*기준)", text
            )
        )
    if atom.family == "hypothesis":
        return bool(
            {"data_diagnosis", "test_counterevidence"} & families
            and re.search(r"(?:원인|가설)", text)
        )
    if atom.family == "report_treatment":
        return "status_certainty" in families and bool(
            re.search(
                r"(?:본문|주석|잠정|미확정|확정).{0,20}(?:표시|구분|보고|기록)", text
            )
        )
    if atom.family == "register_field":
        return bool(
            {"record_handoff", "access_audit"} & families
            and re.search(r"(?:대장|등록표|처리표|처리목록|기록)", text)
        )
    if atom.family == "revision_trace":
        return bool(
            (
                "outcome_evidence" in families
                and re.search(
                    r"(?:수정|변경|정정).{0,16}(?:전후|변화|이력)", atom.text
                )
                and re.search(
                    r"(?:수정|변경|정정).{0,18}(?:전후|변화|이력|흔적|내역)",
                    text,
                )
                and re.search(r"(?:활용|결과|입증|객관|후속|확인)", text)
            )
            or (
                "outcome_evidence" in families
                and re.search(r"(?:변경\s*기록|재점검\s*결과)", atom.text)
                and re.search(
                    r"(?:변화.{0,20}기록.{0,12}확인|기록.{0,12}확인|"
                    r"다시\s*점검|재점검)",
                    text,
                )
            )
        )
    return False


def _matching_demands(atom: _Atom, demands: Sequence[_Demand]) -> list[_Demand]:
    matches: list[_Demand] = []
    protected_role_families = {
        "approval_process",
        "execution_owner",
        "prevention_training",
        "record_retention",
    }
    for demand in demands:
        family_match = (
            atom.family != "unclassified_response_object"
            and atom.family in demand.families
            and (
                atom.family not in protected_role_families
                or _lexically_matches(atom.text, demand.text)
            )
        )
        lexical_match = (
            atom.family == "unclassified_response_object"
            and _lexically_matches(atom.text, demand.text)
        )
        if family_match or lexical_match or _relationally_matches(atom, demand):
            matches.append(demand)
    return matches


def _quantifier_applies(atom: _Atom, demand: _Demand) -> bool:
    if demand.quantifier == "all":
        return False
    if atom.family != "unclassified_response_object":
        return atom.family in demand.quantified_families
    return _lexically_matches(atom.text, demand.quantified_text)


def _issue(
    code: str,
    *,
    point_index: int | None = None,
    atom_index: int | None = None,
    semantic_family: str | None = None,
    prompt_locations: Sequence[str] = (),
    guide_level_index: int | None = None,
) -> dict[str, Any]:
    issue: dict[str, Any] = {"code": code}
    if point_index is not None:
        issue["point_index"] = point_index
    if atom_index is not None:
        issue["atom_index"] = atom_index
    if semantic_family is not None:
        issue["semantic_family"] = semantic_family
    if prompt_locations:
        issue["prompt_locations"] = list(dict.fromkeys(prompt_locations))
    if guide_level_index is not None:
        issue["guide_level_index"] = guide_level_index
    return issue


def _location(demand: _Demand) -> str:
    return f"{demand.location}:clause:{demand.clause_index}"


def _guide_checks(
    item: Mapping[str, Any],
    *,
    method: str,
    points: Sequence[str],
    demands: Sequence[_Demand],
    atoms: Sequence[_Atom],
) -> tuple[bool, bool, list[dict[str, Any]], int]:
    guide = item.get("assessment_guide")
    if not isinstance(guide, Mapping):
        return True, True, [], 0

    raw_dimensions = guide.get("dimensions")
    dimensions = (
        [_text(value) for value in raw_dimensions]
        if isinstance(raw_dimensions, list)
        else []
    )
    dimensions_exact = len(dimensions) == len(points) == 4 and all(
        _compact(left) == _compact(right)
        for left, right in zip(dimensions, points, strict=True)
    )
    issues: list[dict[str, Any]] = []
    if not dimensions_exact:
        issues.append(_issue("guide_dimensions_mismatch"))

    allowed_families = {
        *(atom.family for atom in atoms if not atom.trait_only),
        *(family for demand in demands for family in demand.families),
    }
    guide_family_count = 0
    scope_closed = True
    rating_levels = guide.get("rating_levels")
    if isinstance(rating_levels, list):
        for level_index, raw_level in enumerate(rating_levels, start=1):
            if not isinstance(raw_level, Mapping):
                continue
            anchor = _text(raw_level.get("anchor"))
            families = _detect_families(anchor, method)
            guide_family_count += len(families)
            for family in sorted(families - allowed_families):
                scope_closed = False
                issues.append(
                    _issue(
                        "guide_scope_drift",
                        semantic_family=family,
                        guide_level_index=level_index,
                    )
                )
    return dimensions_exact, scope_closed, issues, guide_family_count


def evaluate_evaluation_elicitation_alignment(
    item: Mapping[str, Any] | Any,
) -> dict[str, Any]:
    """Evaluate whether all four scoring points are elicited by the prompt.

    ``review`` is intentionally non-passing.  It is reserved for genuinely
    ambiguous quantifier scope (for example, a prompt asking which of A/B/C to
    inspect while a point appears to require A and B).  Explicitly asking for
    only one item while scoring several is a hard failure.
    """

    source: Mapping[str, Any] = item if isinstance(item, Mapping) else {}
    method = _method(source.get("type") or source.get("method"))
    points, exact_four = _point_values(source)
    demands = _demand_clauses(source, method)
    atoms = _atoms(points, method)
    point_atom_counts: dict[int, int] = {}
    for atom in atoms:
        point_atom_counts[atom.point_index] = (
            point_atom_counts.get(atom.point_index, 0) + 1
        )

    issues: list[dict[str, Any]] = []
    if not exact_four:
        issues.append(_issue("evaluation_point_count_mismatch"))
    if not demands:
        issues.append(_issue("missing_prompt_demand"))

    matched_atom_count = 0
    review_atom_count = 0
    unmatched_atom_count = 0
    for atom in atoms:
        if atom.trait_only:
            unmatched_atom_count += 1
            issues.append(
                _issue(
                    "unobservable_trait_criterion",
                    point_index=atom.point_index,
                    atom_index=atom.atom_index,
                    semantic_family=atom.family,
                )
            )
            continue

        matches = _matching_demands(atom, demands)
        if not matches:
            unmatched_atom_count += 1
            issues.append(
                _issue(
                    "unelicited_evaluation_atom",
                    point_index=atom.point_index,
                    atom_index=atom.atom_index,
                    semantic_family=atom.family,
                )
            )
            continue

        compound_point = point_atom_counts.get(atom.point_index, 0) > 1
        quantifiers = {
            demand.quantifier if _quantifier_applies(atom, demand) else "all"
            for demand in matches
        }
        locations = [_location(demand) for demand in matches]
        if compound_point and quantifiers == {"single"}:
            unmatched_atom_count += 1
            issues.append(
                _issue(
                    "quantifier_scope_mismatch",
                    point_index=atom.point_index,
                    atom_index=atom.atom_index,
                    semantic_family=atom.family,
                    prompt_locations=locations,
                )
            )
            continue
        if compound_point and quantifiers <= {"ambiguous"}:
            review_atom_count += 1
            issues.append(
                _issue(
                    "ambiguous_quantifier_scope",
                    point_index=atom.point_index,
                    atom_index=atom.atom_index,
                    semantic_family=atom.family,
                    prompt_locations=locations,
                )
            )
            continue
        matched_atom_count += 1

    dimensions_exact, guide_scope_closed, guide_issues, guide_atom_count = (
        _guide_checks(
            source,
            method=method,
            points=points,
            demands=demands,
            atoms=atoms,
        )
    )
    issues.extend(guide_issues)

    hard_issue_codes = {
        "evaluation_point_count_mismatch",
        "missing_prompt_demand",
        "unelicited_evaluation_atom",
        "unobservable_trait_criterion",
        "quantifier_scope_mismatch",
        "guide_dimensions_mismatch",
        "guide_scope_drift",
    }
    has_hard_issue = any(issue["code"] in hard_issue_codes for issue in issues)
    has_review_issue = any(
        issue["code"] == "ambiguous_quantifier_scope" for issue in issues
    )
    if has_hard_issue:
        decision = "fail"
    elif has_review_issue:
        decision = "review"
    else:
        decision = "pass"

    all_atoms_elicited = bool(atoms) and not any(
        issue["code"]
        in {
            "unelicited_evaluation_atom",
            "unobservable_trait_criterion",
            "quantifier_scope_mismatch",
            "ambiguous_quantifier_scope",
        }
        for issue in issues
    )
    checks = {
        "exact_four": exact_four,
        "has_prompt_demands": bool(demands),
        "all_point_atoms_elicited": all_atoms_elicited,
        "no_unobservable_trait_criteria": not any(
            issue["code"] == "unobservable_trait_criterion" for issue in issues
        ),
        "guide_dimensions_exact": dimensions_exact,
        "guide_scope_closed": guide_scope_closed,
    }
    return {
        "policy": EVALUATION_ELICITATION_POLICY,
        "decision": decision,
        "passed": decision == "pass",
        "checks": checks,
        "issues": issues,
        "metrics": {
            "point_count": len(points),
            "point_atom_count": len(atoms),
            "matched_atom_count": matched_atom_count,
            "review_atom_count": review_atom_count,
            "unmatched_atom_count": unmatched_atom_count,
            "demand_clause_count": len(demands),
            "adaptive_demand_count": sum(demand.adaptive for demand in demands),
            "guide_atom_count": guide_atom_count,
        },
    }


__all__ = [
    "EVALUATION_ELICITATION_POLICY",
    "evaluate_evaluation_elicitation_alignment",
]
