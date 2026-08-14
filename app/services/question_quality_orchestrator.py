from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Callable

from app.services.question_surface import public_task_object


RUNTIME_QUESTION_ORCHESTRATION_POLICY = "ncs_ksa_runtime_orchestration_v21"


_METHOD_OBSERVATION_GROUPS: dict[str, tuple[tuple[str, ...], ...]] = {
    "경험면접": (
        ("상황", "사례", "과제", "경험", "당시"),
        ("본인", "역할", "행동", "조치", "선택", "직접", "조율", "재배치"),
        ("결과", "성과", "영향", "학습", "개선"),
    ),
    "상황면접": (
        (
            "상황",
            "동시",
            "충돌",
            "제약",
            "문제",
            "요구",
            "부족",
            "어렵",
            "마감",
            "제출",
        ),
        ("판단", "기준", "근거", "확인", "선택", "조정", "비교", "확정"),
        ("순서", "행동", "조치", "보고", "실행", "위험", "설득", "반영", "결정"),
    ),
    "발표면접": (
        ("발표", "질의응답"),
        ("자료", "진단", "분석", "원인"),
        ("대안", "실행", "성과지표", "우선순위"),
    ),
    "토론면접": (
        ("토론", "입장", "충돌"),
        ("타당", "위험", "조정", "근거", "경청", "반대"),
        ("합의", "합의안", "공동", "실행"),
    ),
    "인바스켓면접": (
        ("인바스켓", "동시", "문서", "요청"),
        ("우선순위", "분류", "보류", "순서", "먼저", "기한"),
        ("보고", "위임", "직접처리", "첫", "조치", "처리", "결정", "기록"),
    ),
    "직무지식면접": (
        ("절차", "기준", "규정", "근거"),
        ("적용", "예외", "판단", "설명"),
        ("산출물", "품질", "오류", "점검", "예방"),
    ),
    "창의적 문제해결력면접": (
        ("문제", "정의", "미래예측", "원인", "가설"),
        ("대안", "창의", "검증", "실현가능성"),
        ("의사결정", "실행", "성과지표", "리스크"),
    ),
}


_SHALLOW_KSA_RESTATEMENT_RE = re.compile(
    r"(?:관련(?:하여|한|된|해)?|에\s*관한|에\s*대한)\s*"
    r"(?:본인의\s*)?(?:실제\s*)?(?:경험|사례)\s*"
    r"(?:(?:이|가)?\s*(?:있다면|있으시면|있으십니까|있습니까)\s*)?"
    r"(?:(?:에\s*대해|을|를)?\s*(?:구체적으로\s*)?)?"
    r"(?:말씀(?:해|하여)?\s*주세요|말(?:해|하여)?\s*주세요|"
    r"이야기(?:해|하여)?\s*주세요|공유(?:해|하여)?\s*주세요|"
    r"설명(?:해|하여)?\s*주세요|소개(?:해|하여)?\s*주세요|"
    r"서술(?:해|하여)?\s*주세요|답변(?:해|하여)?\s*주세요|"
    r"들려\s*주세요|"
    r"들어\s*(?:말씀|설명)(?:해|하여)?\s*주세요|"
    r"있으십니까|있습니까)",
    re.IGNORECASE,
)
_BARE_EXPERIENCE_RE = re.compile(
    r"^.{0,70}(?:실제\s*)?경험(?:이)?\s*(?:있으십니까|있습니까)\??\s*"
    r"(?:말씀(?:해|하여)\s*주세요\.?)?$",
    re.IGNORECASE,
)

# Observation keywords alone do not make an interview task.  A malformed
# model response such as "상황 본인 행동 결과" used to satisfy the method
# groups even though it neither asks a question nor gives the candidate an
# instruction.  Require an explicit response act instead of accepting keyword
# stuffing or heading-like fragments.
_RESPONSE_ELICITATION_RE = re.compile(
    r"(?:말씀|설명|제시|발표|작성|제출|답변|선택|판단|분류|도출|정리|수행|담|"
    r"검토|분석|행동|조치|밝히|밝혀|명시|토론|포함|답)(?:을|를)?\s*"
    r"(?:해|하여|하고|한\s*뒤|한\s*후|하되|하신\s*뒤|하신\s*후)?\s*"
    r"(?:주세요|주십시오|보세요|하십시오|십시오|하세요|세요|하시겠습니까|하겠습니까|으시겠습니까|시겠습니까)|"
    r"(?:무엇|어떻게|왜|어떤|어느|얼마)[^.!?]{0,180}"
    r"(?:하시겠습니까|하겠습니까|으시겠습니까|시겠습니까|입니까|인가요|합니까|할까요)",
    re.IGNORECASE,
)


_FREEFORM_FOCUS_STOPWORDS = frozenset(
    {
        "관련",
        "대한",
        "관한",
        "해당",
        "지식",
        "기술",
        "스킬",
        "능력",
        "태도",
        "업무",
        "수행",
        "있는",
        "있을",
        "있다",
        "가능",
        "가능한",
        "soft",
        "skill",
    }
)
_FREEFORM_FOCUS_ALIASES: tuple[frozenset[str], ...] = (
    frozenset(
        {
            "프로젝트",
            "사업",
            "과제",
            "용역",
        }
    ),
    frozenset(
        {
            "예산",
            "budget",
            "재원",
            "사업비",
            "금액",
            "배정액",
            "집행액",
        }
    ),
    frozenset(
        {
            "수립",
            "편성",
            "설계",
            "작성",
            "계획",
            "조정",
            "배분",
        }
    ),
    frozenset(
        {
            "계약",
            "협약",
            "발주",
            "제안요청서",
            "과업지시서",
        }
    ),
    frozenset(
        {
            "리스크",
            "위험",
            "누락",
            "불일치",
            "책임",
            "검수",
        }
    ),
    frozenset(
        {
            "식별",
            "발견",
            "확인",
            "검토",
            "대조",
            "진단",
        }
    ),
    frozenset(
        {
            "핵심성과지표",
            "성과지표",
            "성과관리표",
            "지표",
            "kpi",
            "목표값",
            "목표치",
        }
    ),
    frozenset(
        {
            "설정",
            "정의",
            "선정",
            "선택",
            "정하다",
            "정한",
            "산식",
            "측정항목",
        }
    ),
    frozenset(
        {
            "외국어",
            "영문",
            "영어",
            "해외",
            "외국",
        }
    ),
    frozenset(
        {
            "의사소통",
            "소통",
            "협의",
            "조율",
            "회신",
            "표현",
            "해석",
            "합의",
        }
    ),
    frozenset(
        {
            "대인관계",
            "대인관계기술",
            "softskill",
            "소프트스킬",
            "협업",
            "협조",
            "조율",
            "협의",
            "설득",
            "소통",
            "관계",
            "이해관계자",
            "조정",
            "갈등",
            "공방",
        }
    ),
    frozenset({"경영환경", "환경", "시장", "수요", "현황", "인력", "재원"}),
    frozenset({"분석", "검토", "조사", "비교", "진단", "도출", "판단"}),
    frozenset({"프로그램", "시스템", "전산", "소프트웨어", "도구", "애플리케이션"}),
    frozenset({"활용", "사용", "조회", "입력", "기록", "운영", "처리"}),
    frozenset({"검증", "확인", "점검", "대조", "검토"}),
    frozenset({"동향", "추세", "변화", "전망", "수요"}),
    frozenset(
        {
            "가능성",
            "가능",
            "실행가능",
            "현실성",
            "가용",
            "충족",
            "어렵",
            "불가",
            "초과",
            "부족",
            "제한",
            "제약",
        }
    ),
    frozenset({"승인", "결재", "허가"}),
    frozenset({"변경", "수정", "조정"}),
    frozenset({"당해년도", "해당연도", "금년도", "올해", "연차"}),
    frozenset({"사업계획", "연차계획", "운영계획", "계획서", "수정안"}),
)
_FREEFORM_COMPOUND_SUFFIXES = ("프로그램", "시스템", "소프트웨어", "동향")
_FREEFORM_GRAMMAR_SUFFIXES = (
    "으로부터",
    "에게서",
    "에서는",
    "으로",
    "에서",
    "에게",
    "까지",
    "부터",
    "하는",
    "하려는",
    "되는",
    "보다",
    "처럼",
    "하고",
    "된",
    "의",
    "을",
    "를",
    "은",
    "는",
    "이",
    "가",
    "에",
    "로",
    "과",
    "와",
    "만",
)
_FREEFORM_EVIDENCE_MARKERS = (
    "자료",
    "기록",
    "수치",
    "규정",
    "협약",
    "현황",
    "조사",
    "문서",
    "시스템",
    "원장",
    "공문",
    "표",
)
_FREEFORM_DECISION_MARKERS = (
    "판단",
    "결정",
    "선택",
    "조정",
    "도출",
    "확정",
    "반영",
    "합의",
    "우선",
    "보류",
    "수정",
    "제안",
    "배분",
)
_FREEFORM_ACTION_MARKERS = (
    "확인",
    "검토",
    "분석",
    "비교",
    "대조",
    "조회",
    "입력",
    "기록",
    "처리",
    "조치",
    "조율",
    "협의",
    "설득",
    "배치",
    "재배치",
    "작성",
    "변경",
)
_FREEFORM_OUTPUT_MARKERS = (
    "산출물",
    "결과",
    "보고",
    "계획서",
    "수정안",
    "일정",
    "예산",
    "목표",
    "지표",
    "기록",
    "문서",
    "배정",
    "배분",
    "보완안",
    "조정안",
    "관리표",
)
_FREEFORM_CONSTRAINT_MARKERS = (
    "요구",
    "충돌",
    "제약",
    "제한",
    "부족",
    "초과",
    "어렵",
    "불가",
    "위험",
    "마감",
    "기한",
    "반대",
    "철회",
)

# These bridges cover official factors whose candidate-facing translation is
# normally expressed as a work incident rather than a synonym of the factor
# label.  Each bridge is deliberately small: the authoritative factor must
# match every ``focus_group`` and the question must independently contain a
# domain incident, a KSA-distinct operation, and a job-specific output.  Newer
# bridges score bounded evidence concepts inside those three named slots;
# legacy bridges retain their all-groups contract.  This is stricter than
# adding ever-broader aliases (for example, treating every ``지표`` or ``안``
# as evidence of the target KSA).
_FREEFORM_KSA_MEANING_BRIDGES: tuple[dict[str, Any], ...] = (
    {
        "name": "project_budget_planning",
        "focus_groups": (
            ("예산", "budget"),
            ("수립", "편성", "계획", "작성", "배분"),
        ),
        "incident_groups": (
            (
                "예산요구안",
                "부서별요구안",
                "산출근거",
                "집행자료",
                "집행실적",
                "결산자료",
                "가용재원",
                "재원한도",
            ),
            ("차이", "맞지", "초과", "부족", "제한", "확정되지", "마감"),
        ),
        "action_groups": (("대조", "비교", "확인", "판단", "조정", "배분", "구분"),),
        "output_groups": (("예산조정표", "예산조정안", "예산안", "예산표", "배분안"),),
    },
    {
        "name": "contract_risk_identification",
        "focus_groups": (
            ("계약", "협약"),
            ("리스크", "위험"),
            ("식별", "확인", "검토", "진단"),
        ),
        "incident_groups": (
            (
                "계약서",
                "계약조항",
                "정산조항",
                "과업지시서",
                "제안요청서",
                "협약서",
            ),
            ("다르", "불일치", "누락", "위험", "리스크", "책임", "거부"),
        ),
        "action_groups": (("확인", "검토", "대조", "판단", "식별"),),
        "output_groups": (
            (
                "수정요청안",
                "보완안",
                "계약쟁점검토서",
                "체결여부",
                "착수진행여부",
                "회신",
            ),
        ),
    },
    {
        "name": "performance_indicator_setting",
        "focus_groups": (
            ("핵심성과지표", "성과지표", "kpi"),
            ("설정", "선정", "정의"),
        ),
        "incident_groups": (
            (
                "실적표",
                "성과지표표",
                "민원기록",
                "민원접수기록",
                "처리건수",
                "완료율",
                "만족도",
                "재참여율",
            ),
            (
                "하락",
                "낮아",
                "급증",
                "감소",
                "반복",
                "차이",
                "변동",
                "개선되지",
            ),
        ),
        "action_groups": (("진단", "분석", "원인", "개선안", "대안"),),
        "output_groups": (
            (
                "목표값",
                "측정자료",
                "측정항목",
                "확인지표",
                "달성여부",
                "성과관리표",
            ),
        ),
    },
    {
        "name": "indicator_operating_definition",
        "focus_groups": (("지표",), ("운영",), ("정의서", "정의")),
        "semantic_slots": (
            {
                "name": "incident",
                "required_groups": (
                    (
                        "맞지",
                        "다르",
                        "다릅",
                        "달라",
                        "상이",
                        "차이",
                        "불일치",
                        "중복",
                    ),
                ),
                "evidence_groups": (
                    ("실적표", "집계표"),
                    ("참여인원", "수료인원", "월말인원", "집계"),
                    ("부서", "사업"),
                ),
                "minimum_evidence_groups": 2,
            },
            {
                "name": "ksa_operation",
                "alternatives": (
                    {
                        "required_groups": (
                            (
                                "기준문서",
                                "지표정의서",
                                "운영정의서",
                                "집계규칙",
                                "집계원칙",
                                "산정원칙",
                                "데이터사전",
                                "근거문서",
                            ),
                        ),
                        "evidence_groups": (
                            (
                                "포함범위",
                                "포함대상",
                                "제외대상",
                                "포함조건",
                                "제외조건",
                            ),
                            ("중복처리", "중복"),
                            ("측정기간", "집계기간"),
                            ("예외항목", "예외"),
                            ("확정", "적용", "판단"),
                        ),
                        "minimum_evidence_groups": 3,
                        "relations": (
                            {
                                "object_groups": (
                                    (
                                        "기준문서",
                                        "지표정의서",
                                        "운영정의서",
                                        "집계규칙",
                                        "집계원칙",
                                        "산정원칙",
                                        "데이터사전",
                                        "근거문서",
                                    ),
                                ),
                                "action_groups": (("근거", "적용", "확정", "판단"),),
                                "maximum_distance": 90,
                            },
                        ),
                    },
                    {
                        # A definition can also be observed without naming its
                        # source document: the candidate must classify scope,
                        # duplicate treatment, and time boundary in one
                        # decision clause.
                        "required_groups": (
                            ("범위", "대상", "실적", "항목", "건"),
                            ("판정", "결정", "확정", "적용"),
                        ),
                        "evidence_groups": (
                            ("포함", "반영", "적용대상", "집계대상"),
                            ("제외", "미반영"),
                            ("중복", "중복산입"),
                            ("기준일", "관찰기간", "측정기간", "집계기간"),
                            ("예외", "사후확정"),
                        ),
                        "minimum_evidence_groups": 3,
                        "relations": (
                            {
                                "object_groups": (
                                    ("범위", "대상", "실적", "항목", "건"),
                                ),
                                "action_groups": (("판정", "결정", "확정", "적용"),),
                                "maximum_distance": 55,
                                "same_clause": True,
                            },
                        ),
                    },
                ),
            },
            {
                "name": "job_output",
                "alternatives": (
                    {
                        "required_groups": (
                            ("수정대조표", "정정대조표", "판정표", "검토기록"),
                        ),
                    },
                    {
                        "artifact_schema": {
                            "artifact_groups": (
                                (
                                    "기준표",
                                    "대조표",
                                    "정합성점검결과서",
                                    "점검결과서",
                                    "검토기록",
                                ),
                            ),
                            "field_groups": (
                                ("포함범위", "포함대상", "포함조건", "제외조건"),
                                ("중복처리", "중복"),
                                ("측정기간", "집계기간"),
                                ("예외사유", "예외항목", "예외"),
                                ("정정이력", "수정이력", "변경이력"),
                            ),
                            "minimum_field_groups": 4,
                        },
                    },
                    {
                        "artifact_schema": {
                            "artifact_groups": (
                                (
                                    "정정표",
                                    "수정표",
                                    "적용판정표",
                                    "집계기준표",
                                ),
                            ),
                            "field_groups": (
                                ("포함", "반영"),
                                ("제외", "미반영"),
                                ("중복", "중복처리"),
                                ("결론", "판정", "적용결과"),
                            ),
                            "minimum_field_groups": 3,
                            "field_same_clause": True,
                            "production_direction": "either",
                        },
                    },
                    {
                        # The artifact name is compositional rather than a
                        # corpus phrase: a correction/change role plus a
                        # record-like carrier, populated with before/rule/
                        # after fields.
                        "artifact_schema": {
                            "artifact_groups": (
                                ("정정", "수정", "변경"),
                                ("기록", "대장", "표"),
                            ),
                            "field_groups": (
                                ("기존", "종전", "변경전", "원값"),
                                ("기준", "규칙", "근거"),
                                ("수정", "정정", "변경후"),
                            ),
                            "minimum_field_groups": 3,
                            "field_same_clause": True,
                            "production_direction": "either",
                        },
                    },
                    {
                        # A working definition may be delivered as a
                        # principle/rule summary rather than a stock
                        # ``definition sheet`` title.  Bind that role to a
                        # table-like carrier and require the operational
                        # decisions that make the artefact auditable.
                        "artifact_schema": {
                            "artifact_groups": (
                                ("기준", "원칙", "규칙"),
                                ("정리표", "요약표", "기록", "대장", "표"),
                            ),
                            "field_groups": (
                                ("적용", "산정"),
                                ("포함", "제외", "범위"),
                                ("중복", "중복처리"),
                                ("예외", "경계"),
                                ("수정이력", "정정이력", "변경이력", "이력"),
                            ),
                            "minimum_field_groups": 4,
                            "field_same_clause": True,
                            "production_direction": "either",
                        },
                    },
                ),
            },
        ),
    },
    {
        "name": "research_plan_review",
        "focus_groups": (("연구",), ("계획서",), ("검토",)),
        "semantic_slots": (
            {
                "name": "incident",
                "alternatives": (
                    {
                        "required_groups": (
                            ("연구",),
                            ("신청서",),
                            (
                                "계획서",
                                "세부계획",
                                "연구계획",
                                "일정표",
                                "연구일정",
                            ),
                            ("다르", "불일치", "어긋", "누락", "변경"),
                        ),
                    },
                    {
                        # A plan is sometimes named by its substantive
                        # content (roles, budget, schedule) rather than by the
                        # literal document title.  Two independent content
                        # dimensions plus a discrepancy still make this a
                        # plan-review incident; a generic application error
                        # does not.
                        "required_groups": (
                            ("연구",),
                            ("신청서", "신청문서", "제출문서"),
                            (
                                "다르",
                                "불일치",
                                "어긋",
                                "누락",
                                "빠지",
                                "빠져",
                                "차이",
                            ),
                        ),
                        "clause_groups": (
                            ("신청서", "신청문서", "제출문서"),
                            (
                                "연구내용",
                                "수행내용",
                                "내용서",
                                "과업내용",
                                "과업서",
                                "수행안",
                            ),
                            (
                                "다르",
                                "불일치",
                                "어긋",
                                "누락",
                                "빠지",
                                "빠져",
                                "차이",
                            ),
                        ),
                        "evidence_groups": (
                            ("역할", "책임", "담당"),
                            ("예산", "비용", "금액"),
                            ("기간", "일정", "마감"),
                            ("참여기관", "수행기관", "협력기관"),
                        ),
                        "minimum_evidence_groups": 1,
                    },
                    {
                        # A named content document is not mandatory when the
                        # discrepancy clause itself ties two substantive plan
                        # dimensions (for example role and budget) to the
                        # application.
                        "required_groups": (("연구",),),
                        "clause_groups": (
                            ("신청서", "신청문서", "제출문서"),
                            (
                                "다르",
                                "불일치",
                                "어긋",
                                "누락",
                                "빠지",
                                "빠져",
                                "차이",
                            ),
                            ("역할", "책임", "담당"),
                            ("예산", "비용", "금액"),
                        ),
                    },
                ),
            },
            {
                "name": "ksa_operation",
                "required_groups": (
                    ("제출가능", "제출여부", "제출전", "제출"),
                    ("판단", "검토", "확인"),
                ),
                "evidence_groups": (
                    ("어긋난항목", "불일치항목", "보완항목", "차이"),
                    ("보완책임자", "책임자", "보완"),
                    ("점검", "수정"),
                ),
                "minimum_evidence_groups": 1,
            },
            {
                "name": "job_output",
                "alternatives": (
                    {
                        "required_groups": (("보완안", "수정안", "검토서", "점검표"),),
                    },
                    {
                        "artifact_schema": {
                            "artifact_groups": (
                                ("보완표", "검토목록", "제출점검서", "조치표"),
                            ),
                            "field_groups": (
                                ("불일치", "누락", "확인항목"),
                                ("근거", "출처"),
                                ("보완", "수정"),
                                ("상태", "완료여부"),
                            ),
                            "minimum_field_groups": 3,
                            "field_same_clause": True,
                            "production_direction": "either",
                        },
                    },
                    {
                        "artifact_schema": {
                            "artifact_groups": (
                                ("보완", "수정", "정정"),
                                ("요청서", "검토표", "목록", "기록", "조치표"),
                            ),
                            "field_groups": (
                                ("불일치", "차이", "누락"),
                                ("확인", "출처", "근거"),
                                ("요구", "보완", "수정"),
                            ),
                            "minimum_field_groups": 3,
                            "field_same_clause": True,
                            "production_direction": "either",
                        },
                    },
                ),
            },
        ),
    },
    {
        "name": "numeric_accuracy_attitude",
        "focus_groups": (("수리",), ("정확",), ("자세", "태도")),
        "semantic_slots": (
            {
                "name": "incident",
                "required_groups": (
                    ("원자료", "집계값", "집계표", "수치"),
                    (
                        "맞지",
                        "불일치",
                        "오류",
                        "다르",
                        "달랐",
                        "달라",
                        "차이",
                    ),
                    ("마감", "기한", "압박", "늦추지"),
                ),
            },
            {
                "name": "ksa_operation",
                "alternatives": (
                    {
                        # Compatibility path for earlier, otherwise
                        # observable experience questions.  v18 no longer
                        # requires this cost-bearing narrative; the neutral
                        # choice/action/outcome path below is equally valid.
                        "required_groups": (
                            (
                                "실제사례",
                                "실제",
                                "사례",
                                "경험",
                                "당시",
                                "했던",
                            ),
                            ("본인", "직접"),
                            ("판단", "선택", "결정", "택한", "고른"),
                            (
                                "행동",
                                "조치",
                                "실행",
                                "정정",
                                "수정",
                                "처리",
                                "대조",
                                "수행",
                            ),
                        ),
                    },
                    {
                        # Non-leading attitude evidence: the candidate may
                        # choose any defensible handling option, but must name
                        # an executable accuracy action and make its
                        # consequence checkable.  No self-sacrifice or
                        # personal-liability wording is required.
                        "reject_negated": True,
                        "required_groups": (
                            ("판단", "선택", "결정", "정할"),
                            (
                                "제출",
                                "잠정",
                                "보류",
                                "정정",
                                "수정",
                                "반려",
                                "재검증",
                            ),
                        ),
                        "clause_groups": (
                            ("원자료", "집계값", "집계표", "수치", "값"),
                            (
                                "대조할",
                                "대조해",
                                "검산할",
                                "검산해",
                                "재집계할",
                                "재집계해",
                                "정정할",
                                "수정할",
                                "확인할",
                                "실행",
                                "수행",
                            ),
                        ),
                        "evidence_groups": (
                            ("대조", "검산", "재집계", "정정", "수정", "확인"),
                            (
                                "수정전후",
                                "변경전후",
                                "확인결과",
                                "결과값",
                                "승인상태",
                                "처리상태",
                                "재검토조건",
                                "변경조건",
                            ),
                        ),
                        "minimum_evidence_groups": 2,
                        "relations": (
                            {
                                # The handling option must be the object of the
                                # candidate's decision.  A stray ``판단 근거``
                                # field in an output must not stand in for an
                                # actual choice among defensible responses.
                                "object_groups": (
                                    (
                                        "제출",
                                        "잠정",
                                        "보류",
                                        "정정",
                                        "수정",
                                        "반려",
                                        "재검증",
                                    ),
                                ),
                                "action_groups": (
                                    ("판단", "선택", "결정", "정할"),
                                ),
                                "maximum_distance": 75,
                                "same_clause": True,
                            },
                            {
                                "object_groups": (
                                    ("원자료", "집계값", "집계표", "수치", "값"),
                                ),
                                "action_groups": (
                                    ("대조", "검산", "재집계", "정정", "수정", "확인"),
                                ),
                                "maximum_distance": 95,
                                "same_clause": True,
                            },
                        ),
                    },
                ),
            },
            {
                "name": "job_output",
                "alternatives": (
                    {
                        "required_groups": (
                            ("대조기록", "정정기록", "검증기록", "승인기록"),
                        ),
                    },
                    {
                        "artifact_schema": {
                            "artifact_groups": (
                                ("판단", "검증", "대조", "정정"),
                                ("기록", "내역", "표"),
                            ),
                            "field_groups": (
                                ("근거", "이유"),
                                (
                                    "책임",
                                    "승인",
                                    "담당",
                                    "승인상태",
                                    "처리상태",
                                    "확인상태",
                                ),
                                (
                                    "결과",
                                    "수정전후",
                                    "변경전후",
                                    "확인값",
                                    "재검토조건",
                                    "변경조건",
                                ),
                            ),
                            "minimum_field_groups": 3,
                            "field_same_clause": True,
                            "production_markers": (
                                "제시",
                                "작성",
                                "남긴",
                                "기록",
                                "설명",
                                "말씀",
                            ),
                            "production_direction": "either",
                        },
                    },
                    {
                        # A concise experience question can elicit an
                        # observable consequence without prescribing a named
                        # administrative artifact.  Keep the selected
                        # accuracy action and its observed result together in
                        # the candidate's role clause.
                        "reject_negated": True,
                        "clause_groups": (
                            ("본인", "역할", "직접"),
                            ("선택", "결정", "택한", "고른"),
                            (
                                "대조",
                                "검산",
                                "재집계",
                                "정정",
                                "수정",
                                "확인",
                            ),
                            (
                                "관찰된결과",
                                "확인된결과",
                                "변화",
                                "개선",
                                "결과",
                            ),
                        ),
                    },
                ),
            },
        ),
    },
    {
        "name": "plan_actual_analysis",
        "focus_groups": (("계획",), ("실적",), ("분석",)),
        "semantic_slots": (
            {
                "name": "incident",
                "required_groups": (
                    ("집행", "집행액"),
                    ("성과", "실적", "산출량"),
                    ("정체", "차이", "급증", "지연", "웃도"),
                ),
                "evidence_groups": (
                    ("계획", "목표선", "목표"),
                    ("월별", "추이"),
                    ("민원", "서비스지연"),
                ),
                "minimum_evidence_groups": 1,
            },
            {
                "name": "ksa_operation",
                "alternatives": (
                    {
                        "required_groups": (
                            ("진단", "분석"),
                            ("대응안", "대안", "비교"),
                            ("우선", "조정", "결정", "배분"),
                        ),
                    },
                    {
                        "required_groups": (
                            ("차이", "격차"),
                            ("원인",),
                            ("판정", "진단", "분석"),
                        ),
                        "relations": (
                            {
                                "object_groups": (("차이", "격차"), ("원인",)),
                                "action_groups": (("판정", "진단", "분석"),),
                                "maximum_distance": 70,
                                "same_clause": True,
                            },
                        ),
                    },
                    {
                        # Attribution is a legitimate analytical judgment even
                        # when the task does not ask the candidate to assert a
                        # single causal explanation.
                        "reject_negated": True,
                        "required_groups": (
                            ("차이", "격차", "편차"),
                            (
                                "판정",
                                "진단",
                                "구분",
                                "선별",
                                "분석하",
                                "분석해",
                                "분석하여",
                            ),
                            (
                                "귀속근거",
                                "차이근거",
                                "발생근거",
                                "설명근거",
                                "판정근거",
                            ),
                        ),
                        "relations": (
                            {
                                "object_groups": (("차이", "격차", "편차"),),
                                "action_groups": (
                                    (
                                        "판정",
                                        "진단",
                                        "구분",
                                        "선별",
                                        "분석하",
                                        "분석해",
                                        "분석하여",
                                    ),
                                ),
                                "maximum_distance": 75,
                                "same_clause": True,
                            },
                        ),
                    },
                ),
            },
            {
                "name": "job_output",
                "alternatives": (
                    {
                        "required_groups": (("실행보고서", "조정안", "후속계획"),),
                    },
                    {
                        "artifact_schema": {
                            "artifact_groups": (
                                (
                                    "차이분석보고서",
                                    "실적분석보고서",
                                    "격차분석서",
                                    "차이분석표",
                                    "실적분석표",
                                    "비교분석표",
                                    "원인분석표",
                                    "분석표",
                                ),
                            ),
                            "field_groups": (
                                (
                                    "목표값",
                                    "계획값",
                                    "목표선",
                                    "계획기준",
                                    "기준값",
                                    "기준선",
                                ),
                                (
                                    "실적값",
                                    "실적치",
                                    "관측값",
                                    "관찰값",
                                    "측정값",
                                ),
                                ("차이", "격차"),
                                (
                                    "원인",
                                    "귀속근거",
                                    "차이근거",
                                    "발생근거",
                                    "설명근거",
                                    "판정근거",
                                ),
                                ("조치", "대응"),
                                ("담당", "책임"),
                                ("완료시점", "기한", "일정"),
                                ("확인지표", "후속지표", "측정치"),
                            ),
                            "minimum_field_groups": 3,
                            "maximum_field_distance": 120,
                            "production_direction": "either",
                        },
                    },
                ),
            },
        ),
    },
    {
        "name": "research_fund_rule_application",
        "focus_groups": (
            ("부처",),
            ("연구개발사업",),
            ("관리규정", "규정"),
        ),
        "semantic_slots": (
            {
                "name": "incident",
                "alternatives": (
                    {
                        "required_groups": (
                            (
                                "지원기관지침",
                                "지원기관기준",
                                "지원기관안내",
                                "사업별안내",
                                "사업안내",
                                "관리규정",
                            ),
                            (
                                "내부기준",
                                "내부지침",
                                "내부업무지침",
                                "연구원내부기준",
                                "공통지침",
                                "공통기준",
                                "협약서",
                                "협약",
                            ),
                            (
                                "다르",
                                "충돌",
                                "불일치",
                                "사전승인",
                                "하지만",
                                "있지만",
                                "반면",
                                "별도로",
                                "엇갈",
                            ),
                        ),
                        "evidence_groups": (
                            ("증빙", "정산"),
                            ("회의비", "출장비", "집행"),
                        ),
                        "minimum_evidence_groups": 1,
                    },
                    {
                        # Opposed normative predicates can state a conflict
                        # without using the noun ``conflict``.  Require two
                        # independent authorities and both sides of the
                        # permission relation.
                        "required_groups": (
                            (
                                "지원기관",
                                "관리기관",
                                "사업지침",
                                "공고지침",
                            ),
                            ("협약", "내부기준", "내규", "공통기준"),
                            ("제한", "금지", "불인정", "불가"),
                            ("허용", "인정", "가능"),
                        ),
                        "evidence_groups": (
                            ("집행", "지출", "연구비", "비용"),
                            ("승인", "예외", "보완", "반려"),
                        ),
                        "minimum_evidence_groups": 1,
                    },
                ),
            },
            {
                "name": "ksa_operation",
                "required_groups": (
                    ("근거",),
                    ("우선적용", "우선적용할지", "우선", "적용"),
                    ("인정", "보완", "반려", "판단"),
                ),
                "relations": (
                    {
                        "object_groups": (("근거", "경계", "예외", "집행"),),
                        "action_groups": (("판단", "결정", "구분"),),
                        "maximum_distance": 80,
                        "same_clause": True,
                    },
                ),
            },
            {
                "name": "job_output",
                "alternatives": (
                    {
                        "required_groups": (
                            (
                                "보완반려검토서",
                                "검토의견서",
                                "검토서",
                                "반려기록",
                                "보완기록",
                            ),
                        ),
                    },
                    {
                        "artifact_schema": {
                            "artifact_groups": (
                                ("검토기록", "판정기록", "집행검토표"),
                            ),
                            "field_groups": (
                                ("적용근거", "근거"),
                                ("판정", "인정", "보완", "반려"),
                                (
                                    "요구증빙",
                                    "필요증빙",
                                    "보완자료",
                                    "예외성립",
                                    "예외여부",
                                    "예외인정",
                                    "인정여부",
                                    "예외조건",
                                    "오류항목",
                                ),
                            ),
                            "minimum_field_groups": 3,
                            "field_same_clause": True,
                            "production_direction": "either",
                        },
                    },
                    {
                        "artifact_schema": {
                            "artifact_groups": (
                                ("검토", "판정", "적용"),
                                ("메모", "기록", "의견", "표"),
                            ),
                            "field_groups": (
                                ("적용근거", "판단근거", "근거"),
                                (
                                    "예외가능",
                                    "예외조건",
                                    "예외여부",
                                    "예외성립",
                                    "인정조건",
                                ),
                                (
                                    "보완요구",
                                    "보완사항",
                                    "보완자료",
                                    "필요증빙",
                                    "요청사항",
                                ),
                            ),
                            "minimum_field_groups": 3,
                            "field_same_clause": True,
                            "production_direction": "either",
                        },
                    },
                ),
            },
        ),
    },
    {
        "name": "agency_negotiation",
        "focus_groups": (("기관", "단체"), ("담당자",), ("협상",)),
        "semantic_slots": (
            {
                "name": "incident",
                "alternatives": (
                    {
                        "required_groups": (
                            ("관계기관", "양측", "주관기관", "검증기관", "기관"),
                            ("마감", "일정", "기한", "제출일"),
                            ("주장", "충돌", "입장", "요구"),
                        ),
                        "evidence_groups": (
                            ("자료", "제출"),
                            ("검증", "검토"),
                        ),
                        "minimum_evidence_groups": 1,
                    },
                    {
                        # Competing proposals may be expressed as verbs: one
                        # party wants an early hand-off while the other wants
                        # to wait for verification.
                        "required_groups": (
                            ("관계기관", "양측", "주관기관", "지원기관", "기관"),
                            ("마감", "일정", "기한", "제출일"),
                            (
                                "먼저",
                                "선제출",
                                "우선제출",
                                "선공유",
                                "일부자료",
                                "기한내",
                            ),
                            (
                                "미루",
                                "보류",
                                "검증후",
                                "확인후",
                                "완료후",
                                "검증완료",
                                "검증뒤",
                                "완료뒤",
                            ),
                        ),
                        "evidence_groups": (
                            ("자료", "제출", "결과"),
                            ("검증", "확인", "검토"),
                        ),
                        "minimum_evidence_groups": 1,
                    },
                ),
            },
            {
                "name": "ksa_operation",
                "alternatives": (
                    {
                        "required_groups": (
                            ("근거", "사실"),
                            ("경계", "범위", "양보"),
                            ("결정", "합의", "협의", "조율"),
                        ),
                        "relations": (
                            {
                                "object_groups": (("경계", "범위", "양보"),),
                                "action_groups": (
                                    ("결정", "합의", "협의", "조율"),
                                ),
                                "maximum_distance": 65,
                                "same_clause": True,
                            },
                        ),
                    },
                    {
                        # Negotiation evidence may be framed directly as the
                        # two sides' constraints and risks.  Those are the
                        # facts used to set a negotiable boundary, even when
                        # the prompt does not repeat the noun ``근거``.
                        "reject_negated": True,
                        "required_groups": (
                            ("한계", "제약", "조건", "요구"),
                            ("위험", "영향", "손실", "오류"),
                            ("경계", "범위", "양보", "수용"),
                            ("결정", "합의", "협의", "조율", "정할"),
                        ),
                        "relations": (
                            {
                                "object_groups": (
                                    ("경계", "범위", "양보", "수용"),
                                ),
                                "action_groups": (
                                    ("결정", "합의", "협의", "조율", "정할"),
                                ),
                                "maximum_distance": 75,
                                "same_clause": True,
                            },
                        ),
                    },
                ),
            },
            {
                "name": "job_output",
                "alternatives": (
                    {
                        "required_groups": (
                            ("공동합의안", "합의안", "협의안", "합의기록"),
                        ),
                    },
                    {
                        "artifact_schema": {
                            "artifact_groups": (
                                ("협의기록", "조정의사록", "협상결과서"),
                            ),
                            "field_groups": (
                                ("합의범위", "수용범위", "적용범위"),
                                ("유보", "미합의", "남은쟁점"),
                                ("책임주체", "담당주체", "이행주체"),
                            ),
                            "minimum_field_groups": 3,
                            "field_same_clause": True,
                            "production_direction": "either",
                        },
                    },
                    {
                        "artifact_schema": {
                            "artifact_groups": (
                                ("협의", "합의", "조정"),
                                ("기록", "의사록", "결과서", "합의문"),
                            ),
                            "field_groups": (
                                (
                                    "수용",
                                    "적용",
                                    "합의범위",
                                    "교환조건",
                                    "양보경계",
                                    "조건",
                                    "경계",
                                ),
                                ("예외", "유보", "쟁점", "미합의"),
                                ("책임", "결정권", "이송", "이행"),
                            ),
                            "minimum_field_groups": 3,
                            "field_same_clause": True,
                            "production_direction": "either",
                        },
                    },
                ),
            },
        ),
    },
    {
        "name": "personal_information_law",
        "focus_groups": (("개인정보보호법",),),
        "semantic_slots": (
            {
                "name": "incident",
                "required_groups": (
                    ("참여자명단", "연락처", "계좌번호", "급여파일", "개인정보"),
                    (
                        "외부기관",
                        "외부연구자",
                        "외부제공",
                        "이메일",
                        "자료회신",
                        "오첨부",
                        "잘못첨부",
                    ),
                    (
                        "승인권",
                        "승인권한",
                        "권한없",
                        "권한있는",
                        "담당자부재",
                        "제공근거",
                    ),
                ),
            },
            {
                "name": "ksa_operation",
                "required_groups": (
                    ("처리순서", "우선순위"),
                    ("처리주체", "담당주체", "각건"),
                ),
                "evidence_groups": (
                    ("목적", "이용목적"),
                    ("근거", "제공근거"),
                    ("권한", "처리주체"),
                    ("최소", "제공항목", "열람범위"),
                    ("열람", "외부제공", "제공"),
                    ("정정", "회수", "보류"),
                    ("포함", "보고"),
                ),
                "minimum_evidence_groups": 4,
            },
            {
                "name": "job_output",
                "alternatives": (
                    {
                        "required_groups": (
                            (
                                "처리결정표",
                                "권한검토서",
                                "외부제공검토서",
                                "유출대응기록",
                            ),
                        ),
                    },
                    {
                        "artifact_schema": {
                            "artifact_groups": (
                                ("처리대장", "요청처리표", "제공검토기록"),
                            ),
                            "field_groups": (
                                ("순서", "우선순위"),
                                ("주체", "담당자", "권한자"),
                                ("보류사유", "보류근거", "제한사유"),
                            ),
                            "minimum_field_groups": 3,
                            "field_same_clause": True,
                            "production_direction": "either",
                        },
                    },
                    {
                        "artifact_schema": {
                            "artifact_groups": (
                                ("처리", "제공", "요청"),
                                ("목록", "대장", "기록", "검토표"),
                            ),
                            "field_groups": (
                                ("목적", "용도"),
                                ("범위", "항목", "최소"),
                                ("상태", "보류", "조치"),
                            ),
                            "minimum_field_groups": 3,
                            "field_same_clause": True,
                            "production_direction": "either",
                        },
                    },
                ),
            },
        ),
    },
    {
        "name": "measurement_framework_attitude",
        "focus_groups": (("성과측정",), ("기준",), ("체계적",)),
        "semantic_slots": (
            {
                "name": "incident",
                "required_groups": (
                    (
                        "누락",
                        "빠지",
                        "오류",
                        "중복",
                        "흔들",
                        "기준모호",
                        "기준이모호",
                    ),
                    (
                        "마감",
                        "압박",
                        "반대",
                        "성과요구",
                        "요구",
                        "비교가능",
                        "인력",
                        "전담인력",
                        "즉시",
                    ),
                ),
            },
            {
                "name": "ksa_operation",
                "alternatives": (
                    {
                        "required_groups": (
                            ("측정기준", "성과기준", "기준체계", "운영기준"),
                        ),
                        "evidence_groups": (
                            ("정의", "산식", "측정차원"),
                            ("자료원", "데이터원천", "증빙"),
                            ("측정주기", "측정기간", "집계기간"),
                            (
                                "예외",
                                "누락처리",
                                "포함조건",
                                "제외조건",
                                "포함및제외조건",
                            ),
                            ("승인", "책임주체"),
                            ("검증절차", "검증방법", "시험절차"),
                        ),
                        "minimum_evidence_groups": 3,
                        "relations": (
                            {
                                "object_groups": (
                                    (
                                        "측정기준",
                                        "성과기준",
                                        "기준체계",
                                        "운영기준",
                                    ),
                                ),
                                "action_groups": (("채택", "수립", "정의", "판단"),),
                                "maximum_distance": 100,
                            },
                        ),
                    },
                    {
                        "required_groups": (
                            ("기준", "집계원칙", "측정원칙"),
                            ("선택", "판단", "결정", "수립", "정의", "채택", "정할"),
                        ),
                        "evidence_groups": (
                            ("측정차원", "집계단위", "측정단위"),
                            ("포함", "산입"),
                            ("제외", "미산입"),
                            ("관찰기간", "측정기간", "집계기간"),
                            ("표본", "대조", "검증"),
                            ("채택", "중단"),
                        ),
                        "minimum_evidence_groups": 4,
                        "relations": (
                            {
                                "object_groups": (("기준", "집계원칙", "측정원칙"),),
                                "action_groups": (
                                    (
                                        "선택",
                                        "판단",
                                        "결정",
                                        "수립",
                                        "정의",
                                        "채택",
                                        "정할",
                                    ),
                                ),
                                "maximum_distance": 55,
                                "same_clause": True,
                            },
                        ),
                    },
                    {
                        "reject_negated": True,
                        "required_groups": (
                            ("보정", "측정", "집계"),
                            ("방식", "규칙", "원칙"),
                            ("선택", "결정", "채택", "정할"),
                        ),
                        "evidence_groups": (
                            ("포함", "산입", "대상"),
                            ("중복", "중복처리", "중복산입"),
                            ("시험", "검증", "표본"),
                            ("채택", "중단", "수정"),
                        ),
                        "minimum_evidence_groups": 4,
                        "relations": (
                            {
                                "object_groups": (
                                    ("보정방식", "측정방식", "집계방식", "규칙"),
                                ),
                                "action_groups": (
                                    ("선택", "결정", "채택", "정할"),
                                ),
                                "maximum_distance": 75,
                                "same_clause": True,
                            },
                        ),
                    },
                ),
            },
            {
                "name": "attitude_commitment",
                "alternatives": (
                    {
                        "reject_negated": True,
                        "required_groups": (
                            (
                                "감수",
                                "불리",
                                "부담",
                                "지연",
                                "반발",
                                "추가업무",
                                "수치하락",
                                "압박",
                                "높은값",
                                "인력부족",
                            ),
                            ("결정", "채택", "수립", "정의"),
                            ("기준", "원칙"),
                        ),
                        "evidence_groups": (
                            (
                                "기준서",
                                "기준표",
                                "원칙서",
                                "원칙표",
                                "시험설계서",
                                "측정설계서",
                            ),
                        ),
                        "minimum_evidence_groups": 1,
                        "relations": (
                            {
                                "object_groups": (("기준", "원칙"),),
                                "action_groups": (("결정", "채택", "수립", "정의"),),
                                "maximum_distance": 65,
                                "same_clause": True,
                            },
                        ),
                    },
                    {
                        # Compatibility path for earlier personal-cost
                        # prompts.  It remains observable, but v18 does not
                        # make this narrative the required answer shape.
                        "reject_negated": True,
                        "clause_groups": (
                            ("본인", "스스로"),
                            ("직접",),
                            ("보류", "수정", "반영", "변경", "중단"),
                            ("책임", "결과책임", "후속책임"),
                        ),
                        "relations": (
                            {
                                "object_groups": (
                                    ("기준", "원칙", "방식", "사용", "집계"),
                                ),
                                "action_groups": (
                                    ("보류", "수정", "반영", "변경", "중단"),
                                ),
                                "maximum_distance": 85,
                                "same_clause": True,
                            },
                        ),
                    },
                    {
                        # A neutral prompt leaves maintain/adjust/redesign
                        # open.  What makes the attitude observable is the
                        # candidate's selected operation plus a stated way to
                        # inspect and revise its consequences.
                        "reject_negated": True,
                        "required_groups": (
                            ("선택", "결정", "채택", "판단", "정할"),
                            ("기준", "원칙", "방식"),
                        ),
                        "clause_groups": (
                            ("기준", "원칙", "방식", "집계"),
                            (
                                "적용할",
                                "적용해",
                                "반영할",
                                "반영해",
                                "시험할",
                                "시험해",
                                "검증할",
                                "검증해",
                                "집계할",
                                "시행할",
                                "실행",
                                "수행",
                            ),
                        ),
                        "evidence_groups": (
                            (
                                "유지",
                                "보류",
                                "수정",
                                "변경",
                                "재설계",
                                "보완",
                            ),
                            ("적용", "반영", "시험", "검증", "집계", "시행"),
                            (
                                "관찰결과",
                                "확인결과",
                                "후속점검",
                                "재검토조건",
                                "변경조건",
                                "중단기준",
                                "채택기준",
                            ),
                        ),
                        "minimum_evidence_groups": 3,
                        "relations": (
                            {
                                "object_groups": (("기준", "원칙", "방식"),),
                                "action_groups": (
                                    ("선택", "결정", "채택", "판단", "정할"),
                                ),
                                "maximum_distance": 70,
                                "same_clause": True,
                            },
                            {
                                "object_groups": (
                                    ("기준", "원칙", "방식", "집계"),
                                ),
                                "action_groups": (
                                    ("적용", "반영", "시험", "검증", "집계", "시행"),
                                ),
                                "maximum_distance": 90,
                                "same_clause": True,
                            },
                        ),
                    },
                    {
                        # A bounded pilot is observable attitude evidence when
                        # the candidate chooses the treatment, defines the
                        # accuracy rule it will exercise, and commits to a
                        # result-dependent adoption/stop boundary.
                        "reject_negated": True,
                        "required_groups": (
                            ("선택", "결정", "채택", "정할"),
                            ("보정방식", "측정방식", "집계방식", "규칙"),
                            ("시험", "검증", "적용"),
                            ("채택", "중단", "수정"),
                        ),
                        "evidence_groups": (
                            ("포함", "산입", "대상"),
                            ("중복", "중복처리"),
                            ("조건", "기준", "경계"),
                            (
                                "검증안",
                                "시험안",
                                "측정안",
                                "기록표",
                                "보정시험표",
                                "측정시험표",
                            ),
                        ),
                        "minimum_evidence_groups": 4,
                        "relations": (
                            {
                                "object_groups": (
                                    ("보정방식", "측정방식", "집계방식", "규칙"),
                                ),
                                "action_groups": (
                                    ("선택", "결정", "채택", "정할"),
                                ),
                                "maximum_distance": 75,
                                "same_clause": True,
                            },
                        ),
                    },
                ),
            },
            {
                "name": "job_output",
                "alternatives": (
                    {
                        "required_groups": (
                            (
                                "측정정의서",
                                "기준개정안",
                                "성과측정기준안",
                                "기준체계안",
                                "시험설계서",
                                "측정설계서",
                            ),
                        ),
                    },
                    {
                        "artifact_schema": {
                            "artifact_groups": (
                                ("기준표", "집계원칙표", "측정규칙표"),
                            ),
                            "field_groups": (
                                ("측정차원", "집계단위", "측정단위"),
                                ("포함", "산입"),
                                ("제외", "미산입"),
                                ("관찰기간", "측정기간", "집계기간"),
                            ),
                            "minimum_field_groups": 4,
                            "field_same_clause": True,
                            "production_direction": "either",
                        },
                    },
                    {
                        "artifact_schema": {
                            "artifact_groups": (
                                ("기준서", "원칙서", "규칙서", "설계문서"),
                            ),
                            "field_groups": (
                                ("측정차원", "집계단위", "측정단위"),
                                ("포함", "산입"),
                                ("제외", "미산입"),
                                ("관찰기간", "측정기간", "집계기간"),
                            ),
                            "minimum_field_groups": 4,
                            "field_same_clause": True,
                            "production_direction": "either",
                        },
                    },
                    {
                        "artifact_schema": {
                            "artifact_groups": (
                                ("판단", "결정", "기준"),
                                ("기록", "대장", "표"),
                            ),
                            "field_groups": (
                                ("측정차원", "집계단위", "측정단위"),
                                ("포함", "산입"),
                                ("제외", "미산입"),
                                ("관찰기간", "측정기간", "집계기간"),
                            ),
                            "minimum_field_groups": 4,
                            "field_same_clause": True,
                            "production_markers": (
                                "명시",
                                "제시",
                                "작성",
                                "담은",
                                "표시",
                            ),
                            "production_direction": "either",
                        },
                    },
                    {
                        "artifact_schema": {
                            "artifact_groups": (
                                ("검증", "시험", "보정", "측정"),
                                ("안", "표", "기록", "계획"),
                            ),
                            "field_groups": (
                                ("원인가설", "가설", "원인"),
                                ("포함", "대상", "산입"),
                                ("중복", "중복처리"),
                                ("채택", "중단", "수정", "판정"),
                            ),
                            "minimum_field_groups": 4,
                            "field_same_clause": True,
                            "production_direction": "either",
                        },
                    },
                ),
            },
        ),
    },
    {
        "name": "closing_report_rules",
        "focus_groups": (
            ("회계보고서",),
            ("분석", "검토보고서"),
            ("작성요령", "작성"),
        ),
        "semantic_slots": (
            {
                "name": "incident",
                "required_groups": (
                    ("결산초안", "회계보고서", "분석검토보고서", "검토보고서"),
                    ("금액", "증빙", "수치", "값", "근거자료"),
                    (
                        "맞지",
                        "불일치",
                        "누락",
                        "미확정",
                        "확인되지않",
                        "확정값처럼",
                        "증빙없",
                        "증빙이없",
                        "근거없",
                        "근거가없",
                        "자료없",
                        "자료가없",
                        "잠정",
                        "검토중",
                        "미승인",
                        "권한없",
                        "권한이없",
                    ),
                ),
            },
            {
                "name": "ksa_operation",
                "alternatives": (
                    {
                        "required_groups": (
                            (
                                "작성기준",
                                "회계기준",
                                "작성요령",
                                "검토기준",
                                "작성원칙",
                            ),
                        ),
                        "evidence_groups": (
                            ("목차", "구성", "항목"),
                            ("표현", "주석", "공시"),
                            ("범위", "예외"),
                            ("근거", "증빙연결"),
                        ),
                        "minimum_evidence_groups": 2,
                    },
                    {
                        "required_groups": (
                            ("완결성", "완성도", "누락여부", "자료충족"),
                            ("승인가능성", "결재가능", "승인여부", "결재여부"),
                        ),
                        "evidence_groups": (
                            ("확정값", "확정수치"),
                            ("잠정값", "잠정수치", "미확정값"),
                            ("근거자료", "연결증빙", "증빙자료"),
                            ("결재필요", "승인필요"),
                        ),
                        "minimum_evidence_groups": 4,
                        "relations": (
                            {
                                "object_groups": (
                                    ("완결성", "완성도", "누락여부", "자료충족"),
                                    (
                                        "승인가능성",
                                        "결재가능",
                                        "승인여부",
                                        "결재여부",
                                    ),
                                ),
                                "action_groups": (
                                    ("처리순서", "우선순위", "분류", "결정"),
                                ),
                                "maximum_distance": 110,
                            },
                        ),
                    },
                    {
                        "required_groups": (
                            ("미확인", "확인되지않", "잠정"),
                            ("표시", "표현", "기재"),
                        ),
                        "relations": (
                            {
                                "object_groups": (
                                    (
                                        "미확인금액",
                                        "확인되지않",
                                        "잠정",
                                    ),
                                ),
                                "action_groups": (
                                    (
                                        "표시방식",
                                        "표현",
                                        "기재",
                                    ),
                                ),
                                "maximum_distance": 45,
                                "same_clause": True,
                            },
                        ),
                    },
                    {
                        "required_groups": (
                            ("확정", "승인완료"),
                            ("잠정", "미확정", "검토중"),
                            ("배치", "표시", "구분", "기재"),
                            ("연결", "연계", "추적"),
                        ),
                        "evidence_groups": (
                            ("본문", "본표", "주요표"),
                            ("주석", "각주", "별도표시"),
                        ),
                        "minimum_evidence_groups": 2,
                        "relations": (
                            {
                                "object_groups": (("근거", "증빙", "자료"),),
                                "action_groups": (("연결", "연계", "추적"),),
                                "maximum_distance": 30,
                                "same_clause": True,
                            },
                            {
                                "object_groups": (
                                    ("확정", "승인완료"),
                                    ("잠정", "미확정", "검토중"),
                                ),
                                "action_groups": (("배치", "표시", "구분", "기재"),),
                                "maximum_distance": 85,
                                "same_clause": True,
                            },
                        ),
                    },
                ),
            },
            {
                "name": "job_output",
                "alternatives": (
                    {
                        "required_groups": (
                            ("결산검토서", "분석검토보고서", "회계보고서"),
                        ),
                    },
                    {
                        "artifact_schema": {
                            "artifact_groups": (
                                ("처리결정표", "보고판정표", "검토결정표"),
                            ),
                            "field_groups": (
                                ("확정값", "확정수치"),
                                ("잠정값", "잠정수치", "미확정값"),
                                ("근거자료", "연결증빙", "증빙자료"),
                                ("결재필요", "승인필요"),
                            ),
                            "minimum_field_groups": 4,
                        },
                    },
                    {
                        "artifact_schema": {
                            "artifact_groups": (
                                ("처리결정표", "문서판정표", "보고처리표"),
                            ),
                            "field_groups": (
                                ("처리순서", "우선순위", "순서"),
                                ("처리주체", "담당주체", "주체"),
                                ("미확인", "잠정", "확인되지않"),
                                ("표시", "표현", "기재"),
                            ),
                            "minimum_field_groups": 4,
                            "field_same_clause": True,
                            "production_direction": "either",
                        },
                    },
                    {
                        "artifact_schema": {
                            "artifact_groups": (
                                ("처리", "검토", "보고"),
                                (
                                    "결정표",
                                    "판정표",
                                    "기록",
                                    "대장",
                                    "보고서",
                                    "의견서",
                                    "명세서",
                                ),
                            ),
                            "field_groups": (
                                ("확정", "승인완료"),
                                ("잠정", "미확정", "검토중"),
                                ("본문", "본표", "주요표"),
                                ("주석", "각주", "별도표시"),
                                ("연결", "연계", "추적"),
                            ),
                            "minimum_field_groups": 4,
                            "field_same_clause": True,
                            "production_direction": "either",
                        },
                    },
                ),
            },
        ),
    },
    {
        "name": "review_report_writing",
        "focus_groups": (("분석", "검토"), ("보고서",), ("작성",)),
        "semantic_slots": (
            {
                "name": "incident",
                "required_groups": (
                    ("예산계획", "예산계획표"),
                    (
                        "결산실적",
                        "결산원장",
                        "회계원장",
                        "결산자료",
                        "결산내역",
                    ),
                    ("원자료", "자료", "원장", "부서제출실적", "실적자료"),
                    ("맞지", "불일치"),
                ),
            },
            {
                "name": "ksa_operation",
                "required_groups": (
                    (
                        "신뢰",
                        "대조",
                        "분석",
                        "판단",
                        "판별",
                        "재구성",
                        "반영",
                        "작성",
                        "구분",
                        "연결",
                    ),
                    ("직접", "본인"),
                ),
            },
            {
                "name": "job_output",
                "alternatives": (
                    {
                        "required_groups": (
                            ("의사결정용보고서", "검토보고서", "분석보고서"),
                            ("활용결과", "활용", "결과"),
                        ),
                    },
                    {
                        # Report-writing skill may yield a focused analytical
                        # table.  The role+carrier composition and a stated
                        # downstream decision keep this from accepting any
                        # generic spreadsheet.
                        "artifact_schema": {
                            "artifact_groups": (
                                ("차이", "대조", "검토", "분석"),
                                ("표", "기록", "보고서"),
                            ),
                            "field_groups": (
                                (
                                    "승인",
                                    "정정",
                                    "의사결정",
                                    "활용결과",
                                    "사용결과",
                                ),
                            ),
                            "minimum_field_groups": 1,
                            "field_same_clause": True,
                            "production_direction": "either",
                        },
                    },
                    {
                        # In an experience interview the work product may be
                        # evidenced by what the candidate put into a report
                        # and what happened when it was used; a stock report
                        # title is not necessary.
                        "reject_negated": True,
                        "clause_groups": (
                            ("보고서", "검토내용", "분석내용"),
                            ("직접", "본인", "역할"),
                            ("반영", "작성", "구분", "연결"),
                            (
                                "관찰된결과",
                                "확인된결과",
                                "활용과정",
                                "활용결과",
                                "사용결과",
                                "결과",
                            ),
                        ),
                    },
                ),
            },
        ),
    },
    {
        "name": "civil_form_process",
        "focus_groups": (("관공서",), ("서식",), ("민원프로세스", "민원")),
        "semantic_slots": (
            {
                "name": "incident",
                "required_groups": (
                    ("신청서", "제출양식", "제출서식", "양식", "서식"),
                    ("민원회신", "민원", "회신기록"),
                    (
                        "누락",
                        "비어",
                        "불일치",
                        "다르",
                        "다른",
                        "빠져",
                        "상충",
                        "엇갈",
                        "하지만",
                        "있지만",
                        "달리",
                        "반면",
                    ),
                ),
            },
            {
                "name": "ksa_operation",
                "required_groups": (
                    ("확인", "판단"),
                    ("보완", "수정", "일치"),
                ),
            },
            {
                "name": "job_output",
                "alternatives": (
                    {
                        "required_groups": (
                            (
                                "수정안",
                                "서식안",
                                "보완서식",
                                "제출본",
                                "신청서",
                            ),
                        ),
                    },
                    {
                        "clause_groups": (
                            ("수정", "보완", "정정"),
                            (
                                "서식",
                                "양식",
                                "신청서",
                                "제출본",
                            ),
                        ),
                    },
                    {
                        "artifact_schema": {
                            "artifact_groups": (
                                ("보완", "정정", "수정"),
                                ("본", "서식", "양식"),
                            ),
                            "field_groups": (
                                (
                                    "필수입력란",
                                    "필수항목",
                                    "확인란",
                                    "입력란",
                                ),
                                ("첨부증빙", "근거첨부", "증빙", "첨부"),
                                (
                                    "처리단계",
                                    "진행상태",
                                    "처리상태",
                                    "단계",
                                ),
                            ),
                            "minimum_field_groups": 3,
                            "field_same_clause": True,
                            "production_direction": "either",
                        },
                    },
                ),
            },
        ),
    },
    {
        "name": "fair_resource_allocation_attitude",
        "focus_groups": (("자원분배",), ("기준",), ("자세", "태도")),
        "semantic_slots": (
            {
                "name": "incident",
                "required_groups": (
                    ("사업", "과제", "연구지원사업"),
                    ("인력", "예산", "자원"),
                    ("요구", "요청"),
                    (
                        "부족",
                        "제한",
                        "한사업",
                        "하나만",
                        "충족할수",
                        "늘지않",
                        "고정된",
                        "한정된",
                        "동결",
                        "고정",
                        "총량제한",
                    ),
                ),
            },
            {
                "name": "ksa_operation",
                "required_groups": (
                    ("우선", "선택", "판단", "결정", "조정"),
                    (
                        "배분근거",
                        "조정근거",
                        "공통기준",
                        "배분원칙",
                        "공통원칙",
                        "어떤근거",
                        "기준",
                        "원칙",
                    ),
                ),
            },
            {
                "name": "attitude_commitment",
                "reject_negated": True,
                "required_groups": (
                    ("선택", "판단", "결정", "정할", "택할", "고를"),
                    ("배분", "배정", "조정", "지원"),
                ),
                "clause_groups": (
                    ("사업", "과제", "부서", "지원"),
                    (
                        "배분할",
                        "배분해",
                        "배분을",
                        "배분결정",
                        "배정할",
                        "배정해",
                        "배정을",
                        "조정할",
                        "조정해",
                        "조정을",
                        "지원할",
                        "지원해",
                        "지원을",
                        "우선할",
                        "우선순위",
                        "우선도를",
                        "직접",
                        "실행",
                        "수행",
                    ),
                ),
                "evidence_groups": (
                    (
                        "성과확인",
                        "결과확인",
                        "성과지표",
                        "확인지표",
                        "재조정",
                        "회수",
                        "유보조건",
                        "예외사유",
                        "예외근거",
                        "수정조건",
                        "책임범위",
                        "책임주체",
                        "적용기간",
                        "일정",
                        "영향",
                        "불리",
                        "서비스공백",
                        "조정경계",
                        "조정가능",
                        "변경경계",
                    ),
                    (
                        "배분안",
                        "배분계획",
                        "배분표",
                        "배정표",
                        "배정계획",
                        "조정표",
                        "실행표",
                        "지원계획",
                        "결정서",
                    ),
                ),
                "minimum_evidence_groups": 2,
                "relations": (
                    {
                        "object_groups": (("사업", "과제", "부서", "지원"),),
                        "action_groups": (
                            ("배분", "배정", "조정", "지원", "우선", "선택"),
                        ),
                        "maximum_distance": 95,
                        "same_clause": True,
                    },
                ),
            },
            {
                "name": "job_output",
                "alternatives": (
                    {
                        "required_groups": (
                            ("자원조정실행표", "배분안", "자원조정표", "실행표"),
                        ),
                        "evidence_groups": (
                            (
                                "배정량",
                                "지원수준",
                                "배분량",
                                "조정량",
                            ),
                            (
                                "유보조건",
                                "회수기준",
                                "조정근거",
                                "배분기준",
                                "공통기준",
                            ),
                            ("예외사유", "예외근거"),
                            (
                                "성과확인시점",
                                "성과확인지표",
                                "성과지표",
                                "적용기간",
                                "일정",
                            ),
                            ("담당자", "책임주체"),
                        ),
                        "minimum_evidence_groups": 2,
                    },
                    {
                        "artifact_schema": {
                            "artifact_groups": (
                                ("자원배정표", "지원조정표", "배분결정서"),
                            ),
                            "field_groups": (
                                ("공통기준", "배분원칙"),
                                ("사업별", "조정량", "배정량"),
                                ("예외사유", "예외근거"),
                            ),
                            "minimum_field_groups": 3,
                            "field_same_clause": True,
                            "production_direction": "either",
                        },
                    },
                    {
                        "artifact_schema": {
                            "artifact_groups": (
                                ("배분", "배정", "지원", "조정"),
                                ("안", "표", "기록", "계획"),
                            ),
                            "field_groups": (
                                (
                                    "공통기준",
                                    "배분기준",
                                    "공통원칙",
                                    "비교기준",
                                ),
                                (
                                    "영향",
                                    "불리",
                                    "서비스공백",
                                    "수혜영향",
                                    "접근영향",
                                ),
                                (
                                    "조정경계",
                                    "조정가능",
                                    "변경경계",
                                    "수정조건",
                                    "재검토조건",
                                    "이송조건",
                                ),
                            ),
                            "minimum_field_groups": 3,
                            "field_same_clause": True,
                            "production_direction": "either",
                        },
                    },
                ),
            },
        ),
    },
    {
        "name": "objective_evaluation_attitude",
        "focus_groups": (("평가",), ("객관",), ("자세", "태도")),
        "semantic_slots": (
            {
                "name": "incident",
                "alternatives": (
                    {
                        "required_groups": (
                            ("평가", "성과"),
                            ("수치", "율", "건수", "목표"),
                            (
                                "달성",
                                "낮",
                                "하락",
                                "미달",
                                "증가",
                                "감소",
                                "넘었",
                            ),
                            (
                                "예산",
                                "이해관계",
                                "양호",
                                "등급",
                                "압력",
                                "요구",
                            ),
                        ),
                    },
                    {
                        "required_groups": (
                            ("평가", "성과판정"),
                            ("정량", "수치증빙", "계량근거"),
                            (
                                "현장맥락",
                                "현장여건",
                                "참여자특성",
                                "장기효과",
                                "질적근거",
                                "지역여건",
                                "운영여건",
                                "운영조건",
                            ),
                            ("맞서", "충돌", "주장", "이견", "입장"),
                        ),
                    },
                    {
                        "required_groups": (
                            ("평가", "성과"),
                            ("정량", "계량", "수치", "목표"),
                            ("정성", "현장", "참여", "장기"),
                            ("주장", "입장", "요청", "반영"),
                        ),
                    },
                ),
            },
            {
                "name": "ksa_operation",
                "alternatives": (
                    {
                        "required_groups": (
                            (
                                "공동기준",
                                "평가기준",
                                "공동원칙",
                                "평가원칙",
                                "판정원칙",
                                "기준",
                                "원칙",
                            ),
                            ("확인된수치", "근거", "수치"),
                            (
                                "적용범위",
                                "예외범위",
                                "인정범위",
                                "인정경계",
                                "근거까지",
                                "까지인정",
                                "예외",
                                "범위",
                                "경계",
                            ),
                        ),
                    },
                    {
                        "reject_negated": True,
                        "required_groups": (
                            ("사실", "근거", "자료"),
                            (
                                "공통원칙",
                                "공동원칙",
                                "공동판정원칙",
                                "평가원칙",
                                "공통기준",
                                "판정원칙",
                            ),
                            ("선택", "결정", "채택", "정할"),
                            ("쟁점", "미합의", "합의되지않"),
                            (
                                "이송기준",
                                "이송조건",
                                "넘길기준",
                                "결정권자",
                                "상위결정자",
                                "상위권한자",
                            ),
                        ),
                        "relations": (
                            {
                                "object_groups": (
                                    (
                                        "공통원칙",
                                        "공동원칙",
                                        "공동판정원칙",
                                        "평가원칙",
                                        "공통기준",
                                        "판정원칙",
                                    ),
                                ),
                                "action_groups": (
                                    ("선택", "결정", "채택", "정할"),
                                ),
                                "maximum_distance": 80,
                                "same_clause": True,
                            },
                        ),
                    },
                ),
            },
            {
                "name": "attitude_commitment",
                "alternatives": (
                    {
                        "reject_negated": True,
                        "clause_groups": (
                            ("본인", "스스로"),
                            ("직접",),
                            (
                                "보류",
                                "수정",
                                "거절",
                                "유지",
                                "조정",
                                "중단",
                                "관철",
                                "시행",
                                "적용",
                                "집행",
                                "실천",
                            ),
                            ("책임", "결과책임", "후속책임"),
                        ),
                        "relations": (
                            {
                                "object_groups": (("적용", "판정", "평가", "지원"),),
                                "action_groups": (
                                    (
                                        "보류",
                                        "수정",
                                        "거절",
                                        "유지",
                                        "조정",
                                        "중단",
                                        "관철",
                                        "시행",
                                        "적용",
                                        "집행",
                                        "실천",
                                    ),
                                ),
                                "maximum_distance": 85,
                                "same_clause": True,
                            },
                        ),
                    },
                    {
                        # A deliberative panel can operationalize objectivity
                        # without a personal-cost narrative when the stem
                        # itself forces a neutral evidence boundary.  Keep
                        # that pathway distinct from personal commitment so a
                        # partially deleted self-accountability prompt cannot
                        # fall back to it.
                        "reject_negated": True,
                        "forbidden_groups": (
                            ("본인", "직접", "스스로", "감수", "책임"),
                        ),
                        "required_groups": (
                            ("공동",),
                            ("기준", "원칙"),
                            ("범위", "예외", "근거까지", "어느조건"),
                            ("판단", "결정", "정하", "토론"),
                        ),
                        "evidence_groups": (
                            ("수치", "정량", "계량"),
                            (
                                "현장",
                                "특수",
                                "참여자",
                                "장기",
                                "표본",
                                "운영여건",
                            ),
                        ),
                        "minimum_evidence_groups": 2,
                    },
                    {
                        # Neutral objectivity evidence does not prescribe
                        # personal sacrifice or unilateral authority.  It
                        # asks the candidate to select an operational
                        # treatment and state how later evidence can confirm
                        # or revise that treatment.
                        "reject_negated": True,
                        "required_groups": (
                            ("선택", "결정", "판단", "정할"),
                            (
                                "등급",
                                "지원결정",
                                "판정결과",
                                "평가결과",
                                "공개결과",
                            ),
                            (
                                "보류",
                                "수정",
                                "확정",
                                "유지",
                                "조정",
                                "적용",
                                "시행",
                            ),
                        ),
                        "clause_groups": (
                            (
                                "등급",
                                "지원결정",
                                "판정결과",
                                "평가결과",
                                "공개결과",
                            ),
                            (
                                "보류할",
                                "수정할",
                                "확정할",
                                "유지할",
                                "조정할",
                                "적용할",
                                "시행할",
                                "처리할",
                                "실행",
                            ),
                        ),
                        "evidence_groups": (
                            (
                                "결과확인",
                                "후속확인",
                                "재검토조건",
                                "수정조건",
                                "변경조건",
                                "오류확인",
                                "추가자료",
                                "후속자료",
                                "이송기준",
                            ),
                            (
                                "평가기록",
                                "판정기록",
                                "평가원칙안",
                                "판정원칙안",
                                "결정기록",
                            ),
                        ),
                        "minimum_evidence_groups": 2,
                        "relations": (
                            {
                                "object_groups": (
                                    (
                                        "등급",
                                        "지원결정",
                                        "판정결과",
                                        "평가결과",
                                        "공개결과",
                                    ),
                                ),
                                "action_groups": (
                                    ("선택", "결정", "판단", "정할"),
                                ),
                                "maximum_distance": 75,
                                "same_clause": True,
                            },
                            {
                                "object_groups": (
                                    (
                                        "등급",
                                        "지원결정",
                                        "판정결과",
                                        "평가결과",
                                        "공개결과",
                                    ),
                                ),
                                "action_groups": (
                                    (
                                        "보류",
                                        "수정",
                                        "확정",
                                        "유지",
                                        "조정",
                                        "적용",
                                        "시행",
                                    ),
                                ),
                                "maximum_distance": 75,
                                "same_clause": True,
                            },
                        ),
                    },
                    {
                        "reject_negated": True,
                        "required_groups": (
                            ("선택", "결정", "채택", "정할"),
                            (
                                "공통원칙",
                                "공동원칙",
                                "공동판정원칙",
                                "평가원칙",
                                "공통기준",
                                "판정원칙",
                            ),
                            ("쟁점", "미합의", "합의되지않"),
                            (
                                "결정권자",
                                "상위결정자",
                                "상위권한자",
                                "이송",
                            ),
                            ("기준", "경계", "범위", "조건"),
                        ),
                        "evidence_groups": (
                            ("정량", "수치", "계량"),
                            ("정성", "현장", "참여", "장기"),
                            ("평가원칙표", "판정원칙표", "평가기록", "합의안"),
                        ),
                        "minimum_evidence_groups": 3,
                        "relations": (
                            {
                                "object_groups": (
                                    (
                                        "공통원칙",
                                        "공동원칙",
                                        "공동판정원칙",
                                        "평가원칙",
                                        "공통기준",
                                        "판정원칙",
                                    ),
                                ),
                                "action_groups": (
                                    ("선택", "결정", "채택", "정할"),
                                ),
                                "maximum_distance": 80,
                                "same_clause": True,
                            },
                        ),
                    },
                ),
            },
            {
                "name": "job_output",
                "required_groups": (
                    (
                        "합의안",
                        "평가원칙안",
                        "판정원칙안",
                        "평가원칙표",
                        "판정원칙표",
                        "평가기록",
                        "판정기록",
                    ),
                ),
            },
        ),
    },
    {
        "name": "data_accuracy_attitude",
        "focus_groups": (("데이터",), ("정확",), ("태도", "자세")),
        "semantic_slots": (
            {
                "name": "incident",
                "required_groups": (
                    (
                        "출처",
                        "원자료",
                        "집계",
                        "추출본",
                        "제출표",
                        "게시본",
                        "시스템",
                        "제출파일",
                        "대시보드",
                        "성과화면",
                        "성과 화면",
                        "화면",
                    ),
                    ("다르", "불일치", "오류", "차이", "크게표시", "작게표시"),
                    (
                        "공개",
                        "게시일정",
                        "게시",
                        "일정",
                        "마감",
                        "압박",
                        "기한",
                        "담당자한명",
                        "한명의인력",
                        "인원은한명",
                        "인력한명",
                        "일부자료",
                        "내일보",
                        "추가예산없",
                        "담당인력은한명",
                        "화면갱신",
                    ),
                ),
            },
            {
                "name": "ksa_operation",
                "alternatives": (
                    {
                        "required_groups": (
                            (
                                "원인가설",
                                "가설",
                                "우선원인",
                                "원인으로판단",
                                "원인",
                                "가능성",
                                "갱신",
                                "업데이트",
                                "중복",
                            ),
                            ("검증", "점검", "검사", "대조", "시험"),
                        ),
                        "evidence_groups": (
                            (
                                "비교대상",
                                "원자료키",
                                "대조대상",
                                "대조항목",
                                "비교항목",
                                "대상항목",
                                "출처",
                                "갱신",
                                "중복",
                                "집계범위",
                                "기간",
                                "업데이트",
                                "중복산입",
                            ),
                            (
                                "실행절차",
                                "검증절차",
                                "대조방법",
                                "점검방법",
                                "검증방법",
                                "검사방법",
                                "표본점검",
                                "소규모시험",
                                "소규모검증",
                                "표본시험",
                                "최소검증",
                                "간이검증",
                                "최소확인",
                            ),
                            (
                                "판정값",
                                "판정기준",
                                "판정경계",
                                "채택기준",
                                "채택또는중단기준",
                            ),
                            ("중단조건", "중단기준"),
                            ("검증순서", "확인순서"),
                        ),
                        "minimum_evidence_groups": 3,
                    },
                    {
                        "reject_negated": True,
                        "required_groups": (
                            ("본인", "직접", "스스로"),
                            ("보류", "수정", "거부", "제한", "사용중단"),
                            ("결정", "선택", "실행", "조치"),
                            (
                                "검증되지않",
                                "불일치",
                                "오류",
                                "차이",
                                "다르",
                                "어긋",
                                "상충",
                            ),
                        ),
                        "relations": (
                            {
                                "object_groups": (
                                    (
                                        "데이터",
                                        "값",
                                        "항목",
                                        "수치",
                                        "게시",
                                        "공개",
                                        "공개결과",
                                    ),
                                ),
                                "action_groups": (
                                    ("보류", "수정", "거부", "제한", "사용중단"),
                                ),
                                "maximum_distance": 80,
                                "same_clause": True,
                            },
                        ),
                    },
                    {
                        "reject_negated": True,
                        "required_groups": (
                            ("원인", "가설", "설명"),
                            ("선택", "결정", "판단", "먼저검증"),
                            ("검증", "시험", "점검"),
                            ("사용범위", "사용가능범위", "적용범위"),
                            ("오류영향", "예상오류", "영향"),
                            ("채택", "중단", "수정"),
                        ),
                        "relations": (
                            {
                                "object_groups": (("원인", "가설", "설명"),),
                                "action_groups": (
                                    ("선택", "결정", "판단", "먼저검증"),
                                ),
                                "maximum_distance": 85,
                                "same_clause": True,
                            },
                        ),
                    },
                ),
            },
            {
                # Accuracy as an attitude is not established by a technically
                # sound experiment alone.  v18 accepts a neutral candidate
                # choice with an executable action and checkable consequence;
                # a cost-bearing personal-liability narrative remains only as
                # a compatibility path.
                "name": "attitude_commitment",
                "alternatives": (
                    {
                        "clause_groups": (
                            ("게시", "공개", "일정", "마감"),
                            (
                                "보류",
                                "연기",
                                "지연",
                                "거부",
                                "잠정",
                                "제한",
                            ),
                            ("결정", "선택", "감수", "책임"),
                        ),
                        "evidence_groups": (
                            ("정확", "검증", "대조", "불일치"),
                            ("지연", "불이익", "비용", "책임"),
                            (
                                "직접",
                                "실행",
                                "조치",
                                "게시연기",
                                "공개중단",
                                "사용중단",
                                "수정반영",
                            ),
                            (
                                "책임",
                                "결과확인",
                                "수정전후",
                                "후속조치",
                                "승인기록",
                            ),
                        ),
                        "minimum_evidence_groups": 4,
                        "relations": (
                            {
                                "object_groups": (
                                    (
                                        "보류",
                                        "연기",
                                        "지연",
                                        "거부",
                                        "잠정",
                                        "제한",
                                    ),
                                ),
                                "action_groups": (
                                    (
                                        "결정",
                                        "선택",
                                        "감수",
                                        "책임",
                                        "보류",
                                        "연기",
                                        "거부",
                                    ),
                                ),
                                "maximum_distance": 65,
                                "same_clause": True,
                            },
                        ),
                    },
                    {
                        "reject_negated": True,
                        "required_groups": (
                            ("선택", "결정", "판단", "정할"),
                            (
                                "게시",
                                "공개",
                                "보류",
                                "잠정",
                                "수정",
                                "정정",
                                "재검증",
                            ),
                            (
                                "확인결과",
                                "관찰결과",
                                "수정전후",
                                "승인상태",
                                "게시결과",
                                "재검토조건",
                                "변경조건",
                                "중단기준",
                            ),
                        ),
                        "clause_groups": (
                            ("데이터", "값", "항목", "수치", "게시", "공개"),
                            (
                                "대조할",
                                "대조해",
                                "검증할",
                                "검증해",
                                "재집계할",
                                "재집계해",
                                "정정할",
                                "수정할",
                                "표시할",
                                "요청할",
                                "실행",
                                "수행",
                            ),
                        ),
                        "evidence_groups": (
                            (
                                "대조",
                                "검증",
                                "재집계",
                                "정정",
                                "수정",
                                "표시",
                                "승인요청",
                            ),
                            (
                                "확인결과",
                                "관찰결과",
                                "수정전후",
                                "승인상태",
                                "게시결과",
                                "재검토조건",
                                "변경조건",
                                "중단기준",
                            ),
                            (
                                "판단기록",
                                "결정기록",
                                "검증기록",
                                "점검표",
                                "대조표",
                                "판정표",
                            ),
                        ),
                        # The concrete action is independently bound by the
                        # clause and relation checks, while ``job_output``
                        # owns the record schema.  Requiring only the action
                        # group here avoids coupling a valid neutral prompt to
                        # a stock artifact title.
                        "minimum_evidence_groups": 1,
                        "relations": (
                            {
                                "object_groups": (
                                    (
                                        "게시",
                                        "공개",
                                        "보류",
                                        "잠정",
                                        "수정",
                                        "정정",
                                        "재검증",
                                    ),
                                ),
                                "action_groups": (
                                    ("선택", "결정", "판단", "정할"),
                                ),
                                "maximum_distance": 75,
                                "same_clause": True,
                            },
                            {
                                "object_groups": (
                                    ("데이터", "값", "항목", "수치", "게시", "공개"),
                                ),
                                "action_groups": (
                                    (
                                        "대조",
                                        "검증",
                                        "재집계",
                                        "정정",
                                        "수정",
                                        "표시",
                                        "승인요청",
                                    ),
                                ),
                                "maximum_distance": 95,
                                "same_clause": True,
                            },
                        ),
                    },
                    {
                        # Accuracy attitude can be observed through a neutral
                        # verification-priority choice when that choice also
                        # fixes the usable data boundary, accounts for error
                        # impact, and has a result-dependent stop/revision rule.
                        "reject_negated": True,
                        "required_groups": (
                            ("원인", "가설", "설명"),
                            ("선택", "결정", "판단", "먼저검증"),
                            ("사용범위", "사용가능범위", "적용범위"),
                            ("오류영향", "예상오류", "영향"),
                            ("채택", "중단", "수정"),
                            ("검증기록", "검증표", "기록표", "시험표"),
                        ),
                        "relations": (
                            {
                                "object_groups": (("원인", "가설", "설명"),),
                                "action_groups": (
                                    ("선택", "결정", "판단", "먼저검증"),
                                ),
                                "maximum_distance": 85,
                                "same_clause": True,
                            },
                        ),
                    },
                ),
            },
            {
                "name": "job_output",
                "alternatives": (
                    {
                        "required_groups": (
                            (
                                "검증실험안",
                                "검증실험서",
                                "검증안",
                                "실험설계서",
                                "검증계획서",
                            ),
                        ),
                    },
                    {
                        "artifact_schema": {
                            "artifact_groups": (
                                ("검증실험표", "대조시험표", "정합성검사표"),
                            ),
                            "field_groups": (
                                ("대조항목", "비교항목", "대상필드"),
                                ("소규모시험", "표본시험", "검사절차"),
                                ("중단기준", "중단조건", "판정경계"),
                            ),
                            "minimum_field_groups": 3,
                            "field_same_clause": True,
                            "production_direction": "either",
                        },
                    },
                    {
                        "artifact_schema": {
                            "artifact_groups": (
                                ("검증", "대조", "정합", "점검"),
                                ("표", "기록", "설계서", "판정표"),
                            ),
                            "field_groups": (
                                (
                                    "대조항목",
                                    "비교항목",
                                    "비교대상",
                                    "대상필드",
                                    "대상항목",
                                ),
                                (
                                    "검증방법",
                                    "검사방법",
                                    "점검방법",
                                    "시험절차",
                                    "최소검증",
                                    "간이검증",
                                    "최소확인",
                                ),
                                ("중단기준", "중단조건", "판정경계"),
                            ),
                            "minimum_field_groups": 3,
                            "field_same_clause": True,
                            "production_direction": "either",
                        },
                    },
                    {
                        "artifact_schema": {
                            "artifact_groups": (
                                ("정확", "검증", "판단"),
                                ("기록", "대장", "판정표"),
                            ),
                            "field_groups": (
                                ("검증되지않", "불일치", "오류", "미확인"),
                                (
                                    "선택근거",
                                    "판단근거",
                                    "보류근거",
                                    "수정근거",
                                    "근거",
                                ),
                                ("본인", "후속책임", "결과책임", "담당책임"),
                            ),
                            "minimum_field_groups": 3,
                            "field_same_clause": True,
                            "production_direction": "either",
                        },
                    },
                    {
                        "artifact_schema": {
                            "artifact_groups": (
                                ("검증", "시험", "점검"),
                                ("기록표", "기록", "표", "판정표"),
                            ),
                            "field_groups": (
                                ("사용범위", "사용가능범위", "적용범위"),
                                ("오류영향", "예상오류", "영향"),
                                ("채택", "중단", "수정", "판정"),
                            ),
                            "minimum_field_groups": 3,
                            "field_same_clause": True,
                            "production_direction": "either",
                        },
                    },
                ),
            },
        ),
    },
    {
        "name": "ethical_data_use_attitude",
        "focus_groups": (
            ("데이터",),
            ("수집",),
            ("활용",),
            ("윤리",),
        ),
        "semantic_slots": (
            {
                "name": "incident",
                "required_groups": (
                    (
                        "마감",
                        "기한",
                        "일정",
                        "촉박",
                        "압박",
                        "업무편의",
                        "업무속도",
                        "빨라",
                    ),
                    ("목적", "용도"),
                    (
                        "열람권한",
                        "열람범위",
                        "접근권한",
                        "접근범위",
                        "권한",
                        "승인",
                    ),
                    ("불분명", "불명확", "맞지", "벗어난", "벗어날"),
                ),
            },
            {
                "name": "ksa_operation",
                "alternatives": (
                    {
                        "required_groups": (
                            ("실제사례", "실제", "경험", "가까운사례"),
                        ),
                        "clause_groups": (
                            ("본인", "스스로"),
                            ("판단", "보류", "거절", "제한", "행동", "조치"),
                        ),
                    },
                    {
                        "required_groups": (
                            ("실제사례", "실제", "경험", "가까운사례"),
                        ),
                        "clause_groups": (
                            ("직접",),
                            ("판단", "보류", "거절", "제한", "행동", "조치"),
                        ),
                    },
                    {
                        # A hypothetical alternative is valid when no direct
                        # experience exists.  The candidate still has to
                        # select and operationalize a data-use action.
                        "reject_negated": True,
                        "required_groups": (
                            ("선택", "결정", "판단", "정할"),
                            (
                                "사용",
                                "허용",
                                "제한",
                                "보류",
                                "거절",
                                "승인요청",
                                "익명",
                            ),
                        ),
                        "clause_groups": (
                            ("자료", "데이터", "이용", "사용", "접근", "공유"),
                            (
                                "축소하",
                                "차단하",
                                "요청하",
                                "익명처리하",
                                "삭제하",
                                "회수하",
                                "중단하",
                                "변경하",
                                "실행",
                                "수행",
                            ),
                        ),
                        "evidence_groups": (
                            (
                                "범위축소",
                                "접근차단",
                                "승인요청",
                                "익명처리",
                                "삭제",
                                "회수",
                                "공유중단",
                                "변경",
                            ),
                            (
                                "이용결정기록",
                                "사용결정기록",
                                "접근검토기록",
                                "승인기록",
                                "변경기록",
                            ),
                        ),
                        "minimum_evidence_groups": 1,
                        "relations": (
                            {
                                "object_groups": (
                                    (
                                        "사용",
                                        "허용",
                                        "제한",
                                        "보류",
                                        "거절",
                                        "승인요청",
                                        "익명",
                                    ),
                                ),
                                "action_groups": (
                                    ("선택", "결정", "판단", "정할"),
                                ),
                                "maximum_distance": 75,
                                "same_clause": True,
                            },
                            {
                                "object_groups": (
                                    ("자료", "데이터", "이용", "사용", "접근", "공유"),
                                ),
                                "action_groups": (
                                    (
                                        "범위축소",
                                        "접근차단",
                                        "승인요청",
                                        "익명처리",
                                        "삭제",
                                        "회수",
                                        "공유중단",
                                        "변경",
                                    ),
                                ),
                                "maximum_distance": 95,
                                "same_clause": True,
                            },
                        ),
                    },
                ),
            },
            {
                "name": "attitude_commitment",
                "alternatives": (
                    {
                        "reject_negated": True,
                        "required_groups": (
                            (
                                "마감",
                                "기한",
                                "일정",
                                "촉박",
                                "편의",
                                "빨리",
                                "빨라",
                                "지연",
                                "불이익",
                                "비용",
                            ),
                            ("결과", "승인", "회수", "삭제", "차단"),
                        ),
                        "clause_groups": (
                            ("본인", "스스로"),
                            ("행동", "실행", "조치", "보류", "거절", "제한"),
                        ),
                    },
                    {
                        "reject_negated": True,
                        "required_groups": (
                            (
                                "마감",
                                "기한",
                                "일정",
                                "촉박",
                                "편의",
                                "빨리",
                                "빨라",
                                "지연",
                                "불이익",
                                "비용",
                            ),
                            ("결과", "승인", "회수", "삭제", "차단"),
                        ),
                        "clause_groups": (
                            ("직접",),
                            ("행동", "실행", "조치", "보류", "거절", "제한"),
                        ),
                    },
                    {
                        # Neutral ethics evidence keeps every defensible
                        # option open.  It observes the selected intervention
                        # and a verifiable consequence or revision condition,
                        # not a promise to absorb personal cost.
                        "reject_negated": True,
                        "required_groups": (
                            ("선택", "결정", "판단", "정할"),
                            (
                                "사용",
                                "허용",
                                "제한",
                                "보류",
                                "거절",
                                "승인요청",
                                "익명",
                            ),
                            (
                                "확인결과",
                                "승인상태",
                                "변경내역",
                                "재검토조건",
                                "허용조건",
                                "후속확인",
                                "삭제확인",
                                "회수결과",
                            ),
                        ),
                        "clause_groups": (
                            ("자료", "데이터", "이용", "사용", "접근", "공유"),
                            (
                                "축소하",
                                "차단하",
                                "요청하",
                                "익명처리하",
                                "삭제하",
                                "회수하",
                                "중단하",
                                "변경하",
                                "실행",
                                "수행",
                            ),
                        ),
                        "evidence_groups": (
                            (
                                "범위축소",
                                "접근차단",
                                "승인요청",
                                "익명처리",
                                "삭제",
                                "회수",
                                "공유중단",
                                "변경",
                            ),
                            (
                                "확인결과",
                                "승인상태",
                                "변경내역",
                                "재검토조건",
                                "허용조건",
                                "후속확인",
                                "삭제확인",
                                "회수결과",
                            ),
                            (
                                "이용결정기록",
                                "사용결정기록",
                                "접근검토기록",
                                "승인기록",
                                "변경기록",
                            ),
                        ),
                        "minimum_evidence_groups": 1,
                        "relations": (
                            {
                                "object_groups": (
                                    (
                                        "사용",
                                        "허용",
                                        "제한",
                                        "보류",
                                        "거절",
                                        "승인요청",
                                        "익명",
                                    ),
                                ),
                                "action_groups": (
                                    ("선택", "결정", "판단", "정할"),
                                ),
                                "maximum_distance": 75,
                                "same_clause": True,
                            },
                            {
                                "object_groups": (
                                    ("자료", "데이터", "이용", "사용", "접근", "공유"),
                                ),
                                "action_groups": (
                                    (
                                        "범위축소",
                                        "접근차단",
                                        "승인요청",
                                        "익명처리",
                                        "삭제",
                                        "회수",
                                        "공유중단",
                                        "변경",
                                    ),
                                ),
                                "maximum_distance": 95,
                                "same_clause": True,
                            },
                        ),
                    },
                ),
            },
            {
                "name": "job_output",
                "alternatives": (
                    {
                        # An experience interview's observable product is the
                        # bounded incident account itself.  Role, selected
                        # action, and observed consequence must be elicited
                        # together; a generic ethical intention is insufficient.
                        "reject_negated": True,
                        "required_groups": (
                            ("실제사례", "실제", "경험", "가까운사례"),
                        ),
                        "clause_groups": (
                            (
                                "본인역할",
                                "본인의역할",
                                "당시역할",
                                "맡은역할",
                                "내역할",
                            ),
                            (
                                "선택한행동",
                                "선택한조치",
                                "취한행동",
                                "실행한조치",
                                "어떻게행동",
                            ),
                            (
                                "관찰된결과",
                                "확인한결과",
                                "행동결과",
                                "어떤결과",
                                "결과는",
                            ),
                        ),
                    },
                    {
                        "required_groups": (
                            (
                                "승인변경기록",
                                "승인기록",
                                "변경기록",
                                "사용기록",
                                "사용범위판단기록",
                                "범위판단기록",
                            ),
                        ),
                    },
                    {
                        "required_groups": (
                            (
                                "이용판단기록",
                                "사용검토기록",
                                "접근검토기록",
                            ),
                        ),
                        "relations": (
                            {
                                "object_groups": (
                                    (
                                        "이용판단기록",
                                        "사용검토기록",
                                        "접근검토기록",
                                    ),
                                ),
                                "action_groups": (("제시", "작성", "남긴", "기록"),),
                                "maximum_distance": 70,
                                "same_clause": True,
                            },
                        ),
                    },
                    {
                        "artifact_schema": {
                            "artifact_groups": (
                                ("이용", "사용", "접근", "공유"),
                                ("결정", "판단", "검토"),
                                ("기록", "내역", "대장"),
                            ),
                            "field_groups": (
                                ("목적", "용도"),
                                ("접근", "주체", "권한"),
                                ("공유범위", "허용범위", "제한범위"),
                            ),
                            "minimum_field_groups": 3,
                            "field_same_clause": True,
                            "production_direction": "either",
                        },
                    },
                    {
                        # Approval/rejection is itself the decision role of
                        # the evidence artefact.  Compose it with a record
                        # carrier and require the owned outcome in the same
                        # clause instead of enumerating compound file names.
                        "artifact_schema": {
                            "artifact_groups": (
                                ("승인", "반려", "거절", "제한", "허용"),
                                ("기록", "내역", "대장"),
                            ),
                            "field_groups": (("결과", "책임", "후속"),),
                            "minimum_field_groups": 1,
                            "field_same_clause": True,
                            "production_markers": (
                                "제시",
                                "작성",
                                "설명",
                                "말씀",
                                "남긴",
                                "기록",
                            ),
                            "production_direction": "either",
                        },
                    },
                ),
            },
        ),
    },
    {
        "name": "document_register_skill",
        "focus_groups": (("문서",), ("대장",), ("기록",)),
        "semantic_slots": (
            {
                "name": "incident",
                "required_groups": (
                    ("공문", "결재문서", "보고서"),
                    ("수신처", "발신번호", "등록"),
                    ("정정", "잘못등록", "오등록", "변경"),
                    ("권한", "승인", "결재"),
                ),
            },
            {
                "name": "ksa_operation",
                "required_groups": (
                    ("처리순서", "우선순위"),
                    (
                        "처리주체",
                        "담당자배정",
                        "담당자지정",
                        "각처리주체",
                        "주체",
                        "담당자",
                        "담당부서",
                    ),
                ),
                "evidence_groups": (
                    (
                        "등록번호",
                        "접수번호",
                        "문서번호",
                        "발신번호",
                        "문서식별정보",
                        "식별정보",
                    ),
                    (
                        "수발신구분",
                        "발신수신구분",
                        "발신처",
                        "수신처",
                        "발신기관",
                    ),
                    ("담당자", "담당부서", "처리주체"),
                    ("기한", "접수시각", "등록시각", "회신시각"),
                    ("현재상태", "처리상태", "등록상태", "상태"),
                    ("다음조치", "후속조치", "예정조치", "처리예정"),
                    ("보류사유", "이관사유"),
                    ("변경이력", "정정이력", "처리이력", "보류이력", "이력"),
                ),
                "minimum_evidence_groups": 3,
                "relations": (
                    {
                        "object_groups": (
                            (
                                "처리주체",
                                "담당자",
                                "담당부서",
                                "주체",
                            ),
                        ),
                        "action_groups": (("결정", "지정", "배정", "정하", "구분"),),
                        "maximum_distance": 70,
                        "same_clause": True,
                    },
                ),
            },
            {
                "name": "job_output",
                "alternatives": (
                    {
                        "required_groups": (
                            (
                                "접수처리대장",
                                "접수대장",
                                "문서대장",
                                "처리대장",
                                "대장초안",
                            ),
                        ),
                    },
                    {
                        "artifact_schema": {
                            "artifact_groups": (
                                ("등록표", "문서등록부", "수발신기록표"),
                            ),
                            "field_groups": (
                                ("접수시각", "등록시각"),
                                ("현재상태", "처리상태"),
                                ("보류사유", "이관사유"),
                                ("접수번호", "문서번호"),
                            ),
                            "minimum_field_groups": 3,
                            "field_same_clause": True,
                            "production_direction": "either",
                        },
                    },
                ),
            },
        ),
    },
)

_SCENARIO_SIGNATURE_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("deadline_missing_evidence", ("마감시간", "자료일부", "자료 일부", "누락")),
    (
        "stakeholder_rule_conflict",
        ("관련부서", "관련 부서", "현장처리기준", "현장 처리 기준"),
    ),
    ("exception_quality_risk", ("예외요청", "예외 요청", "품질오류", "품질 오류")),
    (
        "resource_constraint",
        ("인력과시간", "인력과 시간", "우선순위를다시", "우선순위를 다시"),
    ),
    ("speed_error_prevention", ("처리속도", "처리 속도", "오류예방", "오류 예방")),
    ("procedure_insufficient", ("기존절차", "기존 절차", "추가확인", "추가 확인")),
    ("handover_rule_mismatch", ("인수인계", "최신규정", "최신 규정")),
    ("complaint_approval_delay", ("민원확대", "민원 확대", "승인지연", "승인 지연")),
    ("security_urgent_request", ("안전보안", "안전·보안", "긴급요청", "긴급 요청")),
    ("vendor_repeat_defect", ("협력업체", "반복오류", "반복 오류")),
    ("metric_evidence_gap", ("성과지표", "원인자료", "원인 자료", "불충분")),
    ("workload_surge", ("업무량", "급증", "처리순서", "처리 순서")),
    (
        "system_outage_manual_record",
        ("시스템장애", "시스템 장애", "수기기록", "수기 기록"),
    ),
    ("rule_change_mixed_form", ("규정변경", "규정 변경", "구버전", "양식")),
    (
        "approval_change_notice_gap",
        ("승인기준", "승인 기준", "변경공지", "변경 공지", "전달되지"),
    ),
    ("stakeholder_success_risk", ("성공기준", "성공 기준", "허용위험", "허용 위험")),
    ("ineffective_initial_action", ("초기조치", "초기 조치", "재설계")),
    ("low_trust_cross_validation", ("신뢰성", "교차검증")),
    ("short_long_term_tradeoff", ("단기성과", "단기 성과", "장기재발", "장기 재발")),
    ("requester_urgent_pressure", ("요청부서", "요청 부서", "즉시처리", "즉시 처리")),
    (
        "reviewer_evidence_pressure",
        ("검토담당자", "검토 담당자", "증빙완결", "증빙 완결"),
    ),
    (
        "service_delay_alternative",
        (
            "서비스담당자",
            "서비스 담당자",
            "지연안내",
            "지연 안내",
            "대안제시",
            "대안 제시",
        ),
    ),
    (
        "quality_verification_hold",
        ("품질담당자", "품질 담당자", "추가검증", "추가 검증", "처리보류", "처리 보류"),
    ),
    ("decision_cost_pressure", ("의사결정자", "비용최소화", "비용 최소화")),
    (
        "external_liability_dispute",
        ("외부협력자", "외부 협력자", "오류책임", "오류 책임", "이견"),
    ),
    ("new_owner_support", ("신규담당자", "신규 담당자", "지원요청", "지원 요청")),
    ("approver_absent", ("승인권자", "자리를비운", "자리를 비운")),
)


def _compact(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", str(value or "")).lower()


def _question_text(item: dict[str, Any]) -> str:
    return str((item or {}).get("question") or "").strip()


def _question_dedup_text(item: dict[str, Any]) -> str:
    """Compare the authored incident, excluding shared STAR completion text."""

    source = str((item or {}).get("question_source") or "").strip()
    raw = str((item or {}).get("model_question_raw") or "").strip()
    if raw and source.startswith(("openai_api", "codex_cli", "claude_code")):
        return raw
    return _question_text(item)


def _question_focus(item: dict[str, Any]) -> str:
    focus = str((item or {}).get("question_focus") or "").strip()
    if focus:
        return focus
    refs = (item or {}).get("ksa_refs")
    if isinstance(refs, list) and refs:
        return str(refs[0] or "").strip()
    return ""


def _operational_focus(item: dict[str, Any]) -> str:
    """Return the action noun used when an official factor has a type suffix."""
    surface = str((item or {}).get("question_focus_surface") or "").strip()
    if surface:
        return surface
    focus = _question_focus(item)
    kind = _ksa_type(item)
    public_focus, _source = public_task_object(
        factor_name=focus,
        ksa_type=kind,
        element_name=(item or {}).get("elementName") or "",
        competency_name=(item or {}).get("competency")
        or (item or {}).get("compeUnitName")
        or "",
        competency_definition=(item or {}).get("compeUnitDef") or "",
    )
    if public_focus:
        return public_focus
    candidate = re.sub(
        r"\s*(?:관련\s*)?(?:능력|기술|스킬|지식)\s*$",
        "",
        focus,
    ).strip()
    if re.search(
        r"(?:에대한|에관한|을위한|를위한|하는|되는|대한|위한|의)$", _compact(candidate)
    ):
        return focus
    return candidate if len(_compact(candidate)) >= 2 else focus


def _uses_freeform_evidence_translation(item: dict[str, Any]) -> bool:
    source = str((item or {}).get("question_source") or "").strip()
    evidence_id = str((item or {}).get("question_evidence_id") or "").strip()
    if not evidence_id:
        return False
    return any(
        source == prefix or source.startswith(f"{prefix}_")
        for prefix in ("openai_api", "codex_cli", "claude_code")
    )


def _strip_focus_grammar(token: str) -> str:
    value = _compact(token)
    # Official Korean KSA factors are commonly written as predicates such as
    # ``수립할 수 있는 능력``.  ``할`` is grammatical here, not a concept that
    # a candidate-facing incident should have to repeat verbatim.
    if value.endswith("할") and len(value) >= 3:
        value = value[:-1]
    for suffix in _FREEFORM_GRAMMAR_SUFFIXES:
        compact_suffix = _compact(suffix)
        if value.endswith(compact_suffix) and len(value) - len(compact_suffix) >= 2:
            return value[: -len(compact_suffix)]
    return value


def _focus_aliases(token: str) -> frozenset[str]:
    compact_token = _compact(token)
    for aliases in _FREEFORM_FOCUS_ALIASES:
        if compact_token in {_compact(alias) for alias in aliases}:
            return frozenset(_compact(alias) for alias in aliases)
    return frozenset({compact_token}) if compact_token else frozenset()


def _all_marker_groups_hit(text: str, groups: tuple[tuple[str, ...], ...]) -> bool:
    compact_text = _compact(text)
    return all(
        any(_compact(marker) in compact_text for marker in group) for group in groups
    )


_SEMANTIC_NEGATION_TAIL_RE = re.compile(
    r"^(?:은|는|이|가|을|를|도|만|으로|로)?"
    r"(?:전혀|일절)?"
    r"(?:(?:작성|제시|만들|생성|도출|적용|판단|검토|확정)?"
    r"(?:하지않|하지말|하지못|할수없|하지않기로)|만들지말|쓰지말|내지말|"
    r"없이|아니|배제하|생략하)"
)
_SEMANTIC_RELATIONAL_PARTICLE_RE = re.compile(
    r"[0-9A-Za-z가-힣]+(?:에게는|에서는|으로는|에서는|으로|에서|에게|까지|부터|"
    r"보다|마다|의|을|를|은|는|이|가|와|과|에)(?=\s|[·,.;:?!]|$)"
)
_SEMANTIC_PREDICATE_RE = re.compile(
    r"(?:있|없|받|보내|늘|줄|낮추|산입되|집계되|판정하|기록하|남기|기재하|"
    r"감수하|실행하|반영하|허용하|인정하|정정하|"
    r"회수하|처리하|사용하|제한하|거절하|행동하|유지하|요청하|주장하|평가하|"
    r"완화하|제출되|기재되|표시되|누락되|도착하|요구하|발견하|나타나|적혀|빠져|"
    r"다르|맞지않|충돌하|어긋나|판단하|결정하|선택하|확정하|적용하|구분하|"
    r"비교하|대조하|검토하|분석하|진단하|설계하|작성하|제시하|발표하|도출하|"
    r"보완하|배분하|조정하|확인하|정하|고르|담아|담은|표시한|포함한|밝히|"
    r"설명하|말씀하|공유하|활용하|"
    r"(?:요청|주장|판단|결정|선택|확정|적용|구분|비교|대조|검토|분석|진단|"
    r"설계|작성|제시|발표|도출|보완|배분|조정|확인|합의|설명|말씀)"
    r"(?:했|합|할|해|하고|하여|한|될))"
)
_SEMANTIC_OUTPUT_ACTIONS = (
    "제시",
    "작성",
    "발표",
    "도출",
    "제출",
    "만들",
    "구성",
    "담은",
    "표시한",
    "포함한",
)


def _semantic_marker_occurrences(text: str, marker: str) -> list[tuple[int, int]]:
    compact_text = _compact(text)
    compact_marker = _compact(marker)
    if not compact_marker:
        return []
    occurrences: list[tuple[int, int]] = []
    cursor = 0
    while True:
        start = compact_text.find(compact_marker, cursor)
        if start < 0:
            return occurrences
        end = start + len(compact_marker)
        occurrences.append((start, end))
        cursor = start + 1


def _semantic_marker_is_negated(compact_text: str, end: int) -> bool:
    """Return whether the matched act/artifact is explicitly withdrawn.

    Negative facts in the incident (for example, ``원자료를 받지 못했다``) are
    valid pressure signals.  This helper is therefore used only by operation,
    relation, and output slots: ``적용하지 말라`` or ``보고서 없이`` must not
    become positive evidence merely because the noun or verb is present.
    """

    return bool(_SEMANTIC_NEGATION_TAIL_RE.match(compact_text[end : end + 24]))


def _semantic_group_hit(
    text: str,
    group: tuple[str, ...],
    *,
    reject_negated: bool = False,
) -> bool:
    compact_text = _compact(text)
    for marker in group:
        for _start, end in _semantic_marker_occurrences(text, marker):
            if reject_negated and _semantic_marker_is_negated(compact_text, end):
                continue
            return True
    return False


_SEMANTIC_CLAUSE_SPLIT_RE = re.compile(
    r"(?:[.!?。！？]+|(?:,|;|:|\u00b7)\s*(?=(?:그리고|그러나|반면|다만|이후|그다음)))"
)


def _semantic_clauses(text: str) -> tuple[str, ...]:
    """Return non-empty sentence-like clauses used for relation checks.

    Korean interview prompts often put an incident in one sentence and the
    requested decision/output in the next.  We preserve those boundaries so
    evidence words from unrelated sentences cannot form a synthetic
    action-object pair, while still allowing compact comma-separated field
    lists inside the requested output clause.
    """

    return tuple(
        clause.strip()
        for clause in _SEMANTIC_CLAUSE_SPLIT_RE.split(str(text or ""))
        if clause.strip()
    )


def _semantic_clause_groups_hit(
    text: str,
    groups: tuple[tuple[str, ...], ...],
    *,
    reject_negated: bool = False,
) -> bool:
    """Require every marker group to be stated in one coherent clause."""

    if not groups:
        return True
    return any(
        all(
            _semantic_group_hit(
                clause,
                group,
                reject_negated=reject_negated,
            )
            for group in groups
        )
        for clause in _semantic_clauses(text)
    )


def _semantic_relation_hit(text: str, relation: dict[str, Any]) -> bool:
    """Require an unnegated action to operate on a domain-owned object.

    This is intentionally a bounded relation rather than a global keyword
    score.  It lets natural substitutes such as ``데이터 사전을 적용`` stand
    in for ``기준 문서를 근거로 판단`` while preventing an incident word, an
    unrelated action, and an output noun from accumulating across the prompt.
    """

    if relation.get("same_clause"):
        nested = {**relation, "same_clause": False}
        return any(
            _semantic_relation_hit(clause, nested) for clause in _semantic_clauses(text)
        )

    compact_text = _compact(text)
    object_groups = tuple(relation.get("object_groups") or ())
    action_groups = tuple(relation.get("action_groups") or ())
    maximum_distance = int(relation.get("maximum_distance", 100))
    if not object_groups or not action_groups or maximum_distance < 0:
        return False

    object_spans: list[tuple[int, int]] = []
    for group in object_groups:
        group_spans = [
            span
            for marker in group
            for span in _semantic_marker_occurrences(text, marker)
        ]
        if not group_spans:
            return False
        object_spans.extend(group_spans)

    action_spans: list[tuple[int, int]] = []
    for group in action_groups:
        group_spans = [
            span
            for marker in group
            for span in _semantic_marker_occurrences(text, marker)
            if not _semantic_marker_is_negated(compact_text, span[1])
        ]
        if not group_spans:
            return False
        action_spans.extend(group_spans)

    return any(
        min(abs(action_start - object_end), abs(object_start - action_end))
        <= maximum_distance
        for object_start, object_end in object_spans
        for action_start, action_end in action_spans
    )


def _semantic_artifact_schema_hit(text: str, schema: dict[str, Any]) -> bool:
    """Match a produced artefact by type, populated fields, and creation act."""

    compact_text = _compact(text)
    artifact_groups = tuple(schema.get("artifact_groups") or ())
    if not artifact_groups:
        return False
    artifact_spans: list[tuple[int, int]] = []
    for group in artifact_groups:
        group_spans = [
            span
            for marker in group
            for span in _semantic_marker_occurrences(text, marker)
            if not _semantic_marker_is_negated(compact_text, span[1])
        ]
        if not group_spans:
            return False
        artifact_spans.extend(group_spans)

    field_groups = tuple(schema.get("field_groups") or ())
    minimum_fields = int(schema.get("minimum_field_groups", len(field_groups)))
    if minimum_fields < 0 or minimum_fields > len(field_groups):
        return False
    maximum_field_distance = int(schema.get("maximum_field_distance", -1))
    require_same_clause = bool(schema.get("field_same_clause"))

    def field_group_near_artifact(group: tuple[str, ...]) -> bool:
        if require_same_clause:
            return any(
                _semantic_group_hit(clause, group, reject_negated=True)
                and all(
                    _semantic_group_hit(clause, artifact_group, reject_negated=True)
                    for artifact_group in artifact_groups
                )
                for clause in _semantic_clauses(text)
            )
        if maximum_field_distance < 0:
            return _semantic_group_hit(text, group, reject_negated=True)
        field_spans = [
            span
            for marker in group
            for span in _semantic_marker_occurrences(text, marker)
            if not _semantic_marker_is_negated(compact_text, span[1])
        ]
        return any(
            min(abs(field_start - artifact_end), abs(artifact_start - field_end))
            <= maximum_field_distance
            for artifact_start, artifact_end in artifact_spans
            for field_start, field_end in field_spans
        )

    if sum(field_group_near_artifact(group) for group in field_groups) < minimum_fields:
        return False

    production_markers = tuple(
        schema.get("production_markers") or _SEMANTIC_OUTPUT_ACTIONS
    )
    maximum_distance = int(schema.get("maximum_production_distance", 90))
    production_spans = [
        span
        for marker in production_markers
        for span in _semantic_marker_occurrences(text, marker)
        if not _semantic_marker_is_negated(compact_text, span[1])
    ]
    direction = str(schema.get("production_direction") or "after")
    if direction == "either":
        return any(
            min(abs(action_start - artifact_end), abs(artifact_start - action_end))
            <= maximum_distance
            for artifact_start, artifact_end in artifact_spans
            for action_start, action_end in production_spans
        )
    return any(
        0 <= action_start - artifact_end <= maximum_distance
        for _artifact_start, artifact_end in artifact_spans
        for action_start, _action_end in production_spans
    )


def _has_semantic_proposition_structure(text: str) -> bool:
    """Reject heading-like keyword salads before factor-slot evaluation.

    Real interview prompts state relations (case particles), at least two
    predicates, and a response act.  A flat list such as ``실적표 중복 기준
    기간 수정표`` cannot pass simply by ending with ``제시해 주세요``.
    """

    relational_tokens = _SEMANTIC_RELATIONAL_PARTICLE_RE.findall(text)
    predicate_hits = _SEMANTIC_PREDICATE_RE.findall(_compact(text))
    return (
        len(relational_tokens) >= 3
        and len(predicate_hits) >= 2
        and _elicits_candidate_response(text)
    )


def _semantic_slot_hit(text: str, slot: dict[str, Any]) -> bool:
    """Match one factor-owned semantic slot without requiring stock wording.

    ``required_groups`` protects the meaning that makes a slot distinctive
    (for example, an authority source for a knowledge task).  ``evidence_groups``
    describe independently observable details and are scored by a bounded
    threshold.  A threshold is deliberately attached to one named slot rather
    than to a global bag of aliases, so an unrelated but detailed administrative
    question cannot accumulate its way into a pass.
    """

    alternatives = tuple(slot.get("alternatives") or ())
    if alternatives:
        return any(
            _semantic_slot_hit(
                text,
                {
                    **alternative,
                    "name": alternative.get("name", slot.get("name", "")),
                },
            )
            for alternative in alternatives
        )

    reject_negated = bool(
        slot.get("reject_negated", slot.get("name") in {"ksa_operation", "job_output"})
    )

    def group_hit(group: tuple[str, ...]) -> bool:
        return _semantic_group_hit(
            text,
            group,
            reject_negated=reject_negated,
        )

    required_groups = tuple(slot.get("required_groups") or ())
    if any(not group_hit(group) for group in required_groups):
        return False

    forbidden_groups = tuple(slot.get("forbidden_groups") or ())
    if any(group_hit(group) for group in forbidden_groups):
        return False

    clause_groups = tuple(slot.get("clause_groups") or ())
    if clause_groups and not _semantic_clause_groups_hit(
        text,
        clause_groups,
        reject_negated=reject_negated,
    ):
        return False

    evidence_groups = tuple(slot.get("evidence_groups") or ())
    minimum = int(slot.get("minimum_evidence_groups", len(evidence_groups)))
    if minimum < 0 or minimum > len(evidence_groups):
        return False
    if sum(group_hit(group) for group in evidence_groups) < minimum:
        return False

    relations = tuple(slot.get("relations") or ())
    if any(not _semantic_relation_hit(text, relation) for relation in relations):
        return False

    artifact_schema = slot.get("artifact_schema")
    if artifact_schema and not _semantic_artifact_schema_hit(text, artifact_schema):
        return False
    return True


def _freeform_meaning_bridge_match(item: dict[str, Any], question: str) -> bool | None:
    """Evaluate a known official-factor-to-work-evidence translation.

    ``None`` means that no narrow bridge owns this factor, so the general
    semantic-token path may still evaluate it. A boolean means the factor is
    known and the full incident -> action -> output chain either did or did not
    appear. Candidate copy never has to repeat the raw official label.
    """

    if not _uses_freeform_evidence_translation(item):
        return None
    focus = _question_focus(item)
    matching_bridges = [
        bridge
        for bridge in _FREEFORM_KSA_MEANING_BRIDGES
        if _all_marker_groups_hit(focus, bridge["focus_groups"])
    ]
    if not matching_bridges:
        return None

    # A broad factor such as 핵심성과지표 설정 can share words with a more
    # specific official factor such as 지표 운영 정의서에 대한 개념.  Let the
    # bridge with the greatest number of independently required focus concepts
    # own that factor; declaration order must not silently choose the broader
    # interpretation.
    bridge = max(
        matching_bridges,
        key=lambda candidate: (
            len(candidate["focus_groups"]),
            sum(
                max(len(_compact(marker)) for marker in group)
                for group in candidate["focus_groups"]
            ),
        ),
    )
    semantic_slots = tuple(bridge.get("semantic_slots") or ())
    if semantic_slots:
        return _has_semantic_proposition_structure(question) and all(
            _semantic_slot_hit(question, slot) for slot in semantic_slots
        )
    return all(
        _all_marker_groups_hit(question, bridge[group_name])
        for group_name in ("incident_groups", "action_groups", "output_groups")
    )


def _freeform_focus_concepts(
    item: dict[str, Any],
) -> tuple[list[frozenset[str]], list[frozenset[str]]]:
    """Return semantic focus concepts and mandatory domain qualifiers.

    Subscription CLI providers deliberately translate the official KSA into an
    incident instead of repeating its label. The bridge therefore works from
    the authoritative internal factor, not the model-supplied public surface. Tool compounds
    such as ``예산프로그램`` keep their domain prefix mandatory so an unrelated
    question about merely using *some* system cannot pass.
    """

    raw_focus = _question_focus(item)
    concepts: list[frozenset[str]] = []
    mandatory: list[frozenset[str]] = []
    seen: set[tuple[str, ...]] = set()

    def append_concept(aliases: frozenset[str], *, required: bool = False) -> None:
        clean_aliases = frozenset(alias for alias in aliases if len(alias) >= 2)
        key = tuple(sorted(clean_aliases))
        if not clean_aliases or key in seen:
            return
        seen.add(key)
        concepts.append(clean_aliases)
        if required:
            mandatory.append(clean_aliases)

    for raw_token in re.findall(r"[0-9A-Za-z가-힣]{2,}", raw_focus):
        token = _strip_focus_grammar(raw_token)
        if not token or token in _FREEFORM_FOCUS_STOPWORDS:
            continue
        compound_matched = False
        for suffix in _FREEFORM_COMPOUND_SUFFIXES:
            compact_suffix = _compact(suffix)
            if (
                not token.endswith(compact_suffix)
                or len(token) - len(compact_suffix) < 2
            ):
                continue
            prefix = token[: -len(compact_suffix)]
            append_concept(_focus_aliases(compact_suffix))
            if compact_suffix in {"프로그램", "시스템", "소프트웨어"}:
                append_concept(frozenset({prefix}), required=True)
            compound_matched = True
            break
        if not compound_matched:
            append_concept(_focus_aliases(token))
    return concepts, mandatory


def _freeform_focus_grounded(item: dict[str, Any], question: str) -> bool:
    if not _uses_freeform_evidence_translation(item):
        return False
    bridge_match = _freeform_meaning_bridge_match(item, question)
    if bridge_match is not None:
        return bridge_match
    concepts, mandatory = _freeform_focus_concepts(item)
    if not concepts:
        return False
    compact_question = _compact(question)

    def concept_hit(aliases: frozenset[str]) -> bool:
        return any(alias and alias in compact_question for alias in aliases)

    if any(not concept_hit(aliases) for aliases in mandatory):
        return False
    hit_count = sum(concept_hit(aliases) for aliases in concepts)
    required_hits = 1 if len(concepts) == 1 else 2
    return hit_count >= required_hits


def _has_freeform_marker(question: str, markers: tuple[str, ...]) -> bool:
    compact_question = _compact(question)
    return any(_compact(marker) in compact_question for marker in markers)


def _freeform_ksa_type_operationalized(item: dict[str, Any], question: str) -> bool:
    bridge_match = _freeform_meaning_bridge_match(item, question)
    if bridge_match is not None:
        return bridge_match
    # Focus grounding is a separate top-level contract (`focus_visible`).  Do
    # not repeat that check here: this function answers only whether the
    # candidate is asked to demonstrate knowledge, perform a skill, or reveal
    # an attitude through an observable choice.  Narrow factor-owned bridges
    # above still bind the shape to its domain and therefore reject
    # cross-domain substitutions before this generic fallback is reached.
    kind = _ksa_type(item)
    has_evidence = _has_freeform_marker(question, _FREEFORM_EVIDENCE_MARKERS)
    has_decision = _has_freeform_marker(question, _FREEFORM_DECISION_MARKERS)
    has_action = _has_freeform_marker(question, _FREEFORM_ACTION_MARKERS)
    has_output = _has_freeform_marker(question, _FREEFORM_OUTPUT_MARKERS)
    has_constraint = _has_freeform_marker(question, _FREEFORM_CONSTRAINT_MARKERS)
    if kind == "지식":
        return has_evidence and has_decision
    if kind == "기술":
        # A technical KSA may be observed through an explicit design/selection
        # decision that produces a verifiable artefact (for example, choosing
        # an indicator and submitting a performance table), or through an
        # evidence-backed action followed by a consequential decision (for
        # example, checking conflicting contract terms before approval).
        # Requiring a narrow execution verb plus a named file would reject
        # both kinds of real work.
        return bool(
            (has_action and (has_output or has_decision))
            or (has_decision and has_output)
        )
    if kind == "태도":
        return (has_action or has_decision) and has_constraint
    return (has_action or has_decision) and (has_evidence or has_output)


def _freeform_observable_task(item: dict[str, Any], question: str) -> bool:
    if not _uses_freeform_evidence_translation(item):
        return False
    bridge_match = _freeform_meaning_bridge_match(item, question)
    if bridge_match is not None:
        return bridge_match
    has_response_action = _has_freeform_marker(
        question,
        (*_FREEFORM_ACTION_MARKERS, *_FREEFORM_DECISION_MARKERS),
    )
    has_evidence_or_output = _has_freeform_marker(
        question,
        (*_FREEFORM_EVIDENCE_MARKERS, *_FREEFORM_OUTPUT_MARKERS),
    )
    return has_response_action and has_evidence_or_output


def _focus_visible(item: dict[str, Any], question: str) -> bool:
    focus = _question_focus(item)
    if not focus:
        return False
    compact_question = _compact(question)
    surface_focus = str((item or {}).get("question_focus_surface") or "").strip()
    operational_focus = _operational_focus(item)
    visible_candidates = [surface_focus, operational_focus]
    # Older callers do not provide a separately rendered public surface.  In
    # that compatibility path, an exact appearance of the supplied focus is
    # still valid evidence that the task is grounded.  Runtime-generated rows
    # always carry ``question_focus_surface`` and therefore remain governed by
    # the safer candidate-facing wording.
    if not surface_focus:
        visible_candidates.append(focus)
    if any(
        compact_candidate and compact_candidate in compact_question
        for compact_candidate in (_compact(value) for value in visible_candidates)
    ):
        return True
    tokens = [
        token
        for token in re.findall(
            r"[0-9A-Za-z가-힣]{2,}", surface_focus or operational_focus or focus
        )
        if token not in {"능력", "기술", "지식", "태도", "관련", "업무", "수행"}
    ]
    if not tokens:
        return _freeform_focus_grounded(item, question)
    hits = sum(_compact(token) in compact_question for token in tokens)
    if hits >= (1 if len(tokens) == 1 else 2):
        return True
    return _freeform_focus_grounded(item, question)


def _observation_group_hits(method: str, question: str) -> list[bool]:
    compact = _compact(question)
    return [
        any(_compact(marker) in compact for marker in group)
        for group in _METHOD_OBSERVATION_GROUPS.get(method, ())
    ]


def _elicits_candidate_response(question: str) -> bool:
    return bool(_RESPONSE_ELICITATION_RE.search(str(question or "").strip()))


def _has_sufficient_task_detail(question: str) -> bool:
    """Reject label-like prompts that merely append a polite request.

    Forty compact characters still allows a concise job-knowledge question,
    while requiring enough room to state a context, the evidence-producing
    action, and an outcome/constraint rather than a list of rubric words.
    """

    return len(_compact(question)) >= 40


def _generic_focus_self_report(item: dict[str, Any], question: str) -> bool:
    """Catch shallow synonyms placed immediately after the KSA label.

    A concrete task may still use words such as ``활용`` or ``경험`` later in
    the sentence.  Only the short label-adjacent form is rejected, leaving room
    for an intervening action, object, output, or decision constraint.
    """

    compact_question = _compact(question)
    focus_candidates = list(
        dict.fromkeys(
            value
            for value in (
                _compact(
                    (item or {}).get("question_focus_surface") or _question_focus(item)
                ),
                _compact(_operational_focus(item)),
            )
            if value
        )
    )
    if not compact_question or not focus_candidates:
        return False
    for compact_focus in focus_candidates:
        focus_at = compact_question.find(compact_focus)
        if focus_at < 0:
            continue
        tail = compact_question[
            focus_at + len(compact_focus) : focus_at + len(compact_focus) + 45
        ]
        if re.match(
            r"^(?:"
            r"(?:을|를|이|가|으로|로)?(?:에대해|에관해|관련하여|관련한)?"
            r"(?:직접)?(?:발휘|활용|사용|적용|수행|실천|구사|보유|보여)"
            r"(?:한|했던|하신|해본|준|주신|주었던)?(?:실제)?(?:경험|사례)"
            r"|(?:에대한|에관한)(?:실제)?(?:경험|사례)"
            r")",
            tail,
        ):
            return True
    return False


def _ksa_type(item: dict[str, Any]) -> str:
    raw = _compact((item or {}).get("question_focus_type"))
    if raw in {"k", "knowledge", "지식"} or "지식" in raw:
        return "지식"
    if raw in {"s", "skill", "skills", "기술"} or any(
        token in raw for token in ("기술", "스킬")
    ):
        return "기술"
    if raw in {"a", "attitude", "태도"} or "태도" in raw:
        return "태도"
    focus = _compact(_question_focus(item))
    if any(
        token in focus for token in ("태도", "자세", "의지", "의식", "성실", "책임감")
    ):
        return "태도"
    if any(
        token in focus for token in ("지식", "개념", "원리", "이론", "법규", "법령")
    ):
        return "지식"
    if any(
        token in focus
        for token in ("능력", "기술", "작성", "수립", "분석", "산정", "점검", "파악")
    ):
        return "기술"
    return ""


def _ksa_type_operationalized(item: dict[str, Any], question: str) -> bool:
    kind = _ksa_type(item)
    compact = _compact(question)
    if not kind:
        return True
    if _uses_freeform_evidence_translation(item):
        return _freeform_ksa_type_operationalized(item, question)
    compact_focus = _compact(
        (item or {}).get("question_focus_surface") or _question_focus(item)
    )
    compact_operational_focus = _compact(_operational_focus(item))
    evidence_text = compact
    for focus_value in (compact_focus, compact_operational_focus):
        if focus_value:
            evidence_text = evidence_text.replace(focus_value, "")
    marker_groups = {
        "지식": (
            ("활용", "근거", "기준", "절차", "분석", "적용", "확인", "검토", "조회"),
            (
                "판단",
                "예외",
                "오류",
                "위험",
                "산출",
                "품질",
                "범위",
                "해결",
                "결과",
                "성과",
            ),
        ),
        "기술": (
            (
                "발휘",
                "수행",
                "행동",
                "조치",
                "실행",
                "작성",
                "처리",
                "검증",
                "점검",
                "조율",
                "협의",
                "배치",
                "재배치",
                "소통",
                "설득",
                "조정",
                "분석",
                "검토",
                "조사",
                "도출",
                "배분",
                "제안",
            ),
            (
                "산출",
                "결과",
                "성과",
                "품질",
                "검증",
                "점검",
                "오류",
                "해결",
                "완료",
                "지표",
                "우선순위",
                "분류",
                "판단",
                "확인",
                "기록",
                "대조",
                "입력",
                "결정",
            ),
        ),
        "태도": (
            (
                "행동",
                "선택",
                "유지",
                "준수",
                "대응",
                "조정",
                "통제",
                "우선",
                "책임",
                "결정",
                "설득",
            ),
            (
                "압박",
                "제약",
                "충돌",
                "위험",
                "마감",
                "예외",
                "반대",
                "우선",
                "유지",
                "준수",
                "정확",
                "안전",
                "품질",
                "요구",
                "부족",
                "어렵",
            ),
        ),
    }[kind]
    if not all(
        any(_compact(marker) in evidence_text for marker in group)
        for group in marker_groups
    ):
        return False
    if kind == "기술" and (compact_focus or compact_operational_focus):
        linked_markers = (
            "발휘",
            "수행",
            "행동",
            "조치",
            "실행",
            "작성",
            "검증",
            "점검",
            "산출",
            "결과",
            "성과",
            "품질",
            "적용",
            "해결",
        )
        linked_patterns = [
            re.escape(focus_value)
            + r".{0,120}(?:"
            + "|".join(re.escape(_compact(marker)) for marker in linked_markers)
            + r")"
            for focus_value in dict.fromkeys((compact_focus, compact_operational_focus))
            if focus_value
        ]
        if not any(re.search(pattern, compact) for pattern in linked_patterns):
            return False
    if kind == "태도":
        if compact_focus and re.search(
            re.escape(compact_focus) + r".{0,10}적용", compact
        ):
            return False
    return True


def evaluate_ksa_measurement(item: dict[str, Any]) -> dict[str, Any]:
    """Decide whether the main task elicits observable evidence of its KSA.

    Merely repeating a factor name and asking whether the applicant has related
    experience is intentionally rejected.  The main task must include the
    observable structure of its selected interview method.
    """

    question = _question_text(item)
    method = str((item or {}).get("type") or (item or {}).get("method") or "").strip()
    group_hits = _observation_group_hits(method, question)
    shallow_restatement = bool(
        _SHALLOW_KSA_RESTATEMENT_RE.search(question)
        or _BARE_EXPERIENCE_RE.search(question)
        or _generic_focus_self_report(item, question)
    )
    type_operationalized = _ksa_type_operationalized(item, question)
    observable_task = bool(group_hits and all(group_hits)) or _freeform_observable_task(
        item, question
    )
    evidence_required = bool((item or {}).get("question_evidence_required"))
    evidence_linked = bool(str((item or {}).get("question_evidence_id") or "").strip())
    checks = {
        "has_question": bool(question),
        "has_supported_method": method in _METHOD_OBSERVATION_GROUPS,
        "focus_visible": _focus_visible(item, question),
        "ksa_type_operationalized": type_operationalized,
        "observable_task": observable_task,
        "elicits_response": _elicits_candidate_response(question),
        "sufficient_task_detail": _has_sufficient_task_detail(question),
        "not_ksa_restatement": not shallow_restatement,
        "evidence_linked": evidence_linked if evidence_required else True,
    }
    issues = [name for name, passed in checks.items() if not passed]
    return {
        "passed": not issues,
        "method": method,
        "focus": _question_focus(item),
        "checks": checks,
        "issues": issues,
        "observation_group_hits": group_hits,
    }


def normalize_history_key(value: Any) -> str:
    return _compact(value)


def question_text_similarity(left: Any, right: Any) -> float:
    left_key = normalize_history_key(left)
    right_key = normalize_history_key(right)
    if not left_key or not right_key:
        return 0.0
    sequence = SequenceMatcher(None, left_key, right_key).ratio()
    if len(left_key) < 3 or len(right_key) < 3:
        return sequence
    left_grams = {left_key[index : index + 3] for index in range(len(left_key) - 2)}
    right_grams = {right_key[index : index + 3] for index in range(len(right_key) - 2)}
    union = left_grams | right_grams
    jaccard = len(left_grams & right_grams) / len(union) if union else 0.0
    return max(sequence, jaccard)


def question_scenario_signature(value: Any) -> str:
    compact = _compact(value)
    if not compact:
        return ""
    matches = [
        name
        for name, markers in _SCENARIO_SIGNATURE_MARKERS
        if any(_compact(marker) in compact for marker in markers)
    ]
    return "+".join(matches)


def is_history_duplicate(
    question: Any, history: list[str], threshold: float = 0.86
) -> bool:
    text = str(question or "").strip()
    key = normalize_history_key(text)
    if not key:
        return True
    for previous in history:
        previous_text = str(previous or "").strip()
        if not previous_text:
            continue
        if key == normalize_history_key(previous_text):
            return True
        current_scenario = question_scenario_signature(text)
        previous_scenario = question_scenario_signature(previous_text)
        if current_scenario != previous_scenario and (
            current_scenario or previous_scenario
        ):
            continue
        if question_text_similarity(text, previous_text) >= threshold:
            return True
    return False


RepairQuestion = Callable[[dict[str, Any], int, list[str], int], dict[str, Any] | None]
AuditQuestion = Callable[[dict[str, Any]], dict[str, Any]]


def orchestrate_question_set(
    questions: list[dict[str, Any]],
    *,
    avoid_questions: list[str] | None = None,
    repair_question: RepairQuestion | None = None,
    audit_question: AuditQuestion = evaluate_ksa_measurement,
    max_repair_attempts: int = 6,
    required_repair_reasons: dict[int, list[str]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Audit, repair and re-audit a question set without failing the full set.

    Repair exceptions are isolated to the affected item and returned as stage
    evidence.  This prevents a second-generation repair error from turning an
    otherwise valid response into an HTTP 500.
    """

    history = [
        str(value or "").strip()
        for value in (avoid_questions or [])
        if str(value or "").strip()
    ]
    accepted_texts: list[str] = []
    output: list[dict[str, Any]] = []
    item_events: list[dict[str, Any]] = []
    initial_failure_count = 0
    repaired_count = 0
    repair_error_count = 0

    for index, raw in enumerate(questions):
        original = dict(raw) if isinstance(raw, dict) else {}
        measurement = audit_question(original)
        reasons = list(measurement["issues"])
        reasons.extend(
            str(reason or "").strip()
            for reason in (required_repair_reasons or {}).get(index, [])
            if str(reason or "").strip()
        )
        if is_history_duplicate(
            _question_dedup_text(original),
            [*history, *accepted_texts],
        ):
            reasons.append("history_duplicate")
        reasons = list(dict.fromkeys(reasons))
        if reasons:
            initial_failure_count += 1

        selected = original
        attempts = 0
        errors: list[str] = []
        final_reasons = list(reasons)
        last_candidate_reasons: list[str] = []
        if reasons and repair_question is not None:
            for attempt in range(1, max(1, int(max_repair_attempts)) + 1):
                attempts = attempt
                try:
                    candidate = repair_question(
                        dict(original), index, list(reasons), attempt
                    )
                except Exception as exc:  # item-level isolation is intentional
                    repair_error_count += 1
                    errors.append(f"{type(exc).__name__}: {exc}")
                    continue
                if not isinstance(candidate, dict):
                    break
                candidate_measurement = audit_question(candidate)
                candidate_reasons = list(candidate_measurement["issues"])
                if is_history_duplicate(
                    _question_dedup_text(candidate),
                    [*history, *accepted_texts],
                ):
                    candidate_reasons.append("history_duplicate")
                candidate_reasons = list(dict.fromkeys(candidate_reasons))
                if candidate_reasons:
                    last_candidate_reasons = candidate_reasons
                    continue
                selected = candidate
                final_reasons = []
                last_candidate_reasons = []
                repaired_count += 1
                break
            if selected is original:
                # The returned item is still the original candidate. Preserve
                # its audit reasons instead of accidentally reporting only the
                # last discarded repair candidate's issues.
                final_reasons = list(dict.fromkeys([*reasons, "repair_exhausted"]))

        output.append(selected)
        selected_text = _question_dedup_text(selected)
        if selected_text:
            accepted_texts.append(selected_text)
        item_events.append(
            {
                "index": index + 1,
                "initial_issues": reasons,
                "repair_attempts": attempts,
                "repaired": selected is not original,
                "final_issues": final_reasons,
                "last_candidate_issues": last_candidate_reasons,
                "errors": errors,
            }
        )

    unresolved_count = sum(bool(event["final_issues"]) for event in item_events)
    metadata = {
        "policy": RUNTIME_QUESTION_ORCHESTRATION_POLICY,
        "status": "passed" if not unresolved_count else "needs_review",
        "stages": [
            {
                "name": "ksa_measurement_audit",
                "status": "passed" if not initial_failure_count else "repair_required",
                "failed_count": initial_failure_count,
            },
            {
                "name": "history_dedup_and_repair",
                "status": "passed" if not unresolved_count else "partial",
                "repaired_count": repaired_count,
                "repair_error_count": repair_error_count,
            },
            {
                "name": "final_recheck",
                "status": "passed" if not unresolved_count else "needs_review",
                "unresolved_count": unresolved_count,
            },
        ],
        "question_count": len(output),
        "history_count": len(history),
        "initial_failure_count": initial_failure_count,
        "repaired_count": repaired_count,
        "repair_error_count": repair_error_count,
        "unresolved_count": unresolved_count,
        "items": item_events,
    }
    return output, metadata
