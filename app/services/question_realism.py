"""Field-realism checks for generated interview questions.

The existing quality checks primarily verify NCS traceability and method
shape.  This module answers a different question: could a panel member read
the prompt aloud and use the follow-ups *after* hearing the candidate's
answer?  It is deliberately dependency-free so a question item can be checked
immediately before it is marked ready or persisted.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


REALISM_POLICY_VERSION = "field-realism-v3.14"

CHECK_WEIGHTS: dict[str, int] = {
    "no_generic_template_scaffolding": 20,
    "no_candidate_checklist": 20,
    "natural_ksa_surface": 15,
    "answer_adaptive_follow_ups": 15,
    "concrete_scenario": 20,
    "not_raw_deterministic_provenance": 10,
    "no_label_like_metadata_exposure": 15,
    "no_presumed_experience": 15,
    "no_prescribed_answer": 15,
    "no_instruction_injection_artifact": 25,
}

DETERMINISTIC_QUESTION_SOURCES = frozenset(
    {
        "template_fallback",
        "rule_fallback",
        "simulation_candidate",
        "quality_orchestrator_repair",
        "model_main_template_followups",
        "deterministic",
        "deterministic_template",
    }
)

_TASK_METHODS = frozenset(
    {
        "상황면접",
        "발표면접",
        "토론면접",
        "인바스켓면접",
        "창의적 문제해결력면접",
    }
)

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
    "토론면접": "토론면접",
    "토의면접": "토론면접",
    "inbasket": "인바스켓면접",
    "in-basket": "인바스켓면접",
    "인바스켓면접": "인바스켓면접",
    "creative": "창의적 문제해결력면접",
    "창의적문제해결력면접": "창의적 문제해결력면접",
    "창의적 문제해결력면접": "창의적 문제해결력면접",
}

_GENERIC_SCAFFOLD_PATTERNS = (
    re.compile(
        r"\[(?:경험|상황|발표|토론|인바스켓|직무지식|창의적\s*문제해결력)(?:면접|과제)\]",
        re.IGNORECASE,
    ),
    re.compile(r"\{\{?[^{}\n]{1,60}\}?\}|<<[^<>\n]{1,60}>>"),
    re.compile(
        r"\[(?:직무명|역량명|능력단위|KSA|세부능력|상황을\s*입력)\]", re.IGNORECASE
    ),
    re.compile(
        r"(?:[가-힣A-Za-z0-9()·\s]{1,45})(?:와|과)\s*관련해\s*"
        r"본인이\s*판단하고\s*행동한\s*실제\s*경험",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:관련된?|유사한|해당)\s*(?:업무|직무|과업|역량).{0,30}"
        r"(?:경험|사례)(?:을|를)?\s*(?:말씀|설명|선택)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:직무\s*)?경험이\s*없다면.{0,100}(?:프로젝트|교육\s*실습|사례).{0,30}가능",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:다음|아래)(?:의)?\s*(?:항목|내용|사항).{0,80}(?:포함|구분|답변)",
        re.IGNORECASE,
    ),
)

_CHECKLIST_DIRECTIVE_RE = re.compile(
    r"(?:모두\s*)?포함(?:해|하여|하세요|하십시오)|구분(?:해|하여|하세요|하십시오)|"
    r"각각|순서대로|빠짐없이|중심으로\s*(?:답변|설명)|(?:밝혀|제시|설명)\s*주세요",
    re.IGNORECASE,
)
_ENUMERATOR_RE = re.compile(r"(?:^|\s)(?:\(?\d+[.)]|[①②③④⑤⑥⑦⑧⑨⑩]|[가나다라마][.)])")
_CHECKLIST_COMPONENTS: dict[str, re.Pattern[str]] = {
    "상황·과제": re.compile(r"당시\s*상황|상황과\s*(?:문제|과제)|구체적\s*상황|배경"),
    "본인 역할": re.compile(r"본인(?:이\s*맡은)?\s*역할|담당\s*역할"),
    "판단 근거": re.compile(r"판단\s*(?:근거|기준)|선택\s*(?:근거|기준)|확인할\s*사실"),
    "행동·조치": re.compile(
        r"실제\s*(?:판단[·ㆍ/]?)?행동|행동\s*순서|구체적\s*(?:행동|조치)|수행\s*단계"
    ),
    "근거·산출물": re.compile(
        r"사용한\s*근거|근거\s*또는\s*산출물|산출물(?:과|을|의)?"
    ),
    "결과·성과": re.compile(r"(?:확인\s*가능한\s*)?결과|성과(?:\s*지표)?|결과\s*지표"),
    "학습·전이": re.compile(r"학습(?:과\s*전이)?|교훈|개선점|전이"),
    "위험·이해관계자": re.compile(r"위험\s*요인|이해관계자|상충\s*비용"),
    "보고·후속조치": re.compile(
        r"보고(?:와|\s*및)?\s*(?:실행|후속)|후속\s*조치|예방\s*조치"
    ),
}

_MECHANICAL_KSA_PATTERNS = (
    re.compile(
        r"관련\s*실무\s*(?:적용|수행)\s*[·ㆍ∙‧・/\-]?\s*검증\s*(?:절차|기준)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:확인|분석|계산|산정|예측|적용|활용|조정|수행)\s*[·ㆍ∙‧・/]\s*"
        r"(?:검증|확인)\s*(?:절차|기준)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:절차|기준)(?:이|가)?\s*(?:요구된|요구되는|필요했던|필요한)\s*"
        r"(?:장면|상황)(?:을|에서)?\s*(?:골라|선택)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:NCS|KSA|능력단위(?:요소)?|지식[·ㆍ/]기술[·ㆍ/]태도).{0,35}"
        r"(?:적용|검증|평가|드러나)",
        re.IGNORECASE,
    ),
)

_PRESUMED_EXPERIENCE_PATTERNS = (
    re.compile(
        r"경험(?:이)?\s*(?:있으실|있을)\s*(?:것입니다|겁니다)",
        re.IGNORECASE,
    ),
    re.compile(
        r"경험(?:을)?\s*(?:해|하여)\s*보셨을\s*(?:것입니다|겁니다)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:해|하여)\s*보신\s*경험이\s*(?:있으실|있을)\s*(?:것입니다|겁니다)",
        re.IGNORECASE,
    ),
)

# A work sample may provide facts or a short rule excerpt, but it must not give
# the candidate the complete decision policy and then ask them merely to enact
# it.  Require two solution clauses plus an explicit "원칙/기준에 따라" bridge
# so ordinary mentions such as "개인정보 보호 원칙을 고려" remain valid.
_PRESCRIBED_ANSWER_PATTERNS = (
    re.compile(
        r"(?:필요한|최소)\s*(?:항목|정보|범위)?만.{0,45}(?:제공|수집|공유|사용)"
        r".{0,80}(?:근거|권한|목적).{0,30}(?:불명확|없|확인되지).{0,30}"
        r"(?:보류|거절|제외).{0,35}(?:원칙|기준)(?:에\s*따라|을\s*적용)"
        r".{0,80}(?:판단|결정|처리)",
        re.IGNORECASE,
    ),
)

# A realistic dilemma leaves the action, escalation boundary, and consequence
# ownership for the candidate to decide.  The v3.11 policy caught an explicitly
# supplied privacy rule, but it did not catch prompts that made a personally
# costly action the morally correct answer before the candidate spoke.  Keep
# these signals compositional: none of ``cost``, ``direct action``, or
# ``responsibility`` is objectionable by itself.
_PERSONAL_COST_OBJECT_RE = re.compile(
    r"(?:일정|보고|게시|공개|공시|업무|처리)\s*(?:의\s*)?지연|"
    r"(?:관계\s*부서|담당\s*부서|현업|관계자|부서)(?:의|가)?\s*"
    r"(?:반발|반대)|불이익|손실|비용",
    re.IGNORECASE,
)
_MANDATED_COST_ACCEPTANCE_RE = re.compile(
    r"(?:감수|감내|떠안)(?:하|해|할|한|했|고|면서|하고|하더라도|하고서라도|겠)",
    re.IGNORECASE,
)
_CANDIDATE_DIRECT_ACTION_RE = re.compile(
    r"(?:본인(?:이|은|의)?|지원자(?:가|는)?).{0,85}"
    r"직접\s*(?:처리|반영|관철|수행|시행|결정|선택|작성|조치|취|보류|수정|확정)|"
    r"직접\s*(?:처리|반영|관철|수행|시행|결정|선택|작성|조치|취|보류|수정|확정)"
    r".{0,55}(?:본인(?:이|은|의)?|지원자(?:가|는)?)",
    re.IGNORECASE,
)
_CANDIDATE_FORCED_DECISION_RE = re.compile(
    r"(?:본인(?:이|은)?|지원자(?:가|는)?).{0,65}"
    r"(?:하나로\s*)?(?:결정|선택|채택)|"
    r"(?:결정|선택|채택).{0,45}(?:본인(?:이|은)?|지원자(?:가|는)?)",
    re.IGNORECASE,
)
_DIRECT_ENFORCEMENT_RE = re.compile(
    r"직접\s*(?:처리|반영|관철|수행|시행|결정|작성|조치|취|보류|수정|확정)",
    re.IGNORECASE,
)
_PERSONAL_OUTCOME_LIABILITY_RE = re.compile(
    r"(?:결과|선택|결정).{0,35}(?:본인(?:이|의)?|지원자(?:가|의)?)"
    r".{0,20}(?:책임|책임지)|"
    r"(?:본인(?:이|의)?|지원자(?:가|의)?).{0,35}(?:결과|선택|결정)"
    r".{0,25}(?:책임|책임지)|"
    r"책임(?:까지|을|은|도)?\s*(?:본인(?:이|의)?|지원자(?:가|의)?)\s*"
    r"(?:지|부담|귀속)|"
    r"(?:그\s*)?결과(?:에|의).{0,20}(?:질\s*)?책임",
    re.IGNORECASE,
)
_EXPLICIT_PERSONAL_LIABILITY_PRECONDITION_RE = re.compile(
    r"(?:책임(?:까지|을)?\s*)?(?:본인(?:이|은)?|지원자(?:가|는)?)\s*"
    r"(?:전적으로\s*)?(?:책임을?\s*)?(?:지|떠안|부담)"
    r".{0,18}(?:겠다는|한다는)\s*전제|"
    r"(?:본인(?:이|은)?|지원자(?:가|는)?).{0,45}"
    r"(?:결과에\s*)?(?:전적으로\s*)?책임지겠다는\s*전제",
    re.IGNORECASE,
)
_EXPERIENCE_FALLBACK_RE = re.compile(
    r"(?:직접\s*)?(?:관련\s*)?경험(?:이)?\s*없(?:다면|으면|을\s*경우)|"
    r"그(?:마저|것도)\s*없(?:다면|으면)|"
    r"경험이\s*부족(?:하다면|한\s*경우)",
    re.IGNORECASE,
)
_FORCED_COSTLY_DISCOVERY_EXPERIENCE_RE = re.compile(
    r"(?:직접|스스로).{0,25}(?:발견|찾아|확인|포착)|"
    r"(?:불일치|오류|문제).{0,30}(?:발견|찾아|확인|포착)"
    r"(?:했|한|했던).{0,15}(?:실제\s*)?(?:사례|경험)",
    re.IGNORECASE,
)
_ONE_SIDED_DATA_CONTROL_EXPERIENCE_RE = re.compile(
    r"(?:수집\s*목적|이용\s*목적|접근\s*권한|공유\s*대상).{0,120}"
    r"(?:맞지\s*않|불분명|부적절|위반).{0,100}"
    r"(?:사용\s*범위|이용\s*범위|공유\s*범위|자료\s*사용|자료\s*이용)"
    r".{0,35}(?:제한|축소|거절|보류|중단|차단).{0,35}"
    r"(?:거절|제한|축소|보류|중단|차단)",
    re.IGNORECASE,
)
_OPEN_DATA_USE_CHOICE_RE = re.compile(
    r"(?:그대로\s*)?(?:사용|이용|허용)(?:하|할|했|·|ㆍ|/).{0,35}"
    r"(?:제한|거절|보류|중단)|"
    r"(?:제한|거절|보류|중단).{0,35}"
    r"(?:그대로\s*)?(?:사용|이용|허용)(?:하|할|했|·|ㆍ|/)",
    re.IGNORECASE,
)
_FOLLOW_UP_PREACCEPTED_PERSONAL_COST_RE = re.compile(
    r"(?:방금|앞서|발표에서).{0,55}(?:본인(?:이|의)?|지원자(?:가|의)?)"
    r".{0,35}(?:감수하겠다고|감내하겠다고|떠안겠다고|책임지겠다고|"
    r"직접\s*관철하겠다고)",
    re.IGNORECASE,
)
_CANDIDATE_PRIVILEGED_DIRECT_ACTION_RE = re.compile(
    r"(?:본인(?:이|은)?|지원자(?:가|는)?).{0,45}직접\s*"
    r"(?:승인|결재|허가|최종\s*확정|집행\s*승인)",
    re.IGNORECASE,
)
_COINCIDENT_CORRELATE_RE = re.compile(
    r"(?:민원|불만|문의|이탈|사고).{0,30}(?:같은|동일한?)\s*"
    r"(?:달|기간|시기|분기).{0,30}(?:증가|감소|발생)|"
    r"(?:같은|동일한?)\s*(?:달|기간|시기|분기).{0,35}"
    r"(?:민원|불만|문의|이탈|사고).{0,25}(?:증가|감소|발생)",
    re.IGNORECASE,
)
_FORCED_SINGLE_CAUSE_RE = re.compile(
    r"(?:원인|이유)(?:을|를)?\s*(?:하나|한\s*가지|단일).{0,20}"
    r"(?:판정|결정|특정|도출)|"
    r"(?:하나|한\s*가지|단일)의?\s*(?:원인|이유).{0,20}"
    r"(?:판정|결정|특정|도출)",
    re.IGNORECASE,
)
_CAUSAL_LINK_EVALUATION_RE = re.compile(
    r"(?:수치|집행|성과|변화).{0,45}(?:민원|불만|문의|이탈|사고)"
    r".{0,35}(?:연결|결부|연관).{0,30}(?:원인|인과)|"
    r"(?:민원|불만|문의|이탈|사고).{0,45}(?:수치|집행|성과|변화)"
    r".{0,35}(?:연결|결부|연관).{0,30}(?:원인|인과)|"
    r"(?:민원|불만|문의|이탈|사고).{0,35}(?:원인\s*근거|인과\s*근거)",
    re.IGNORECASE,
)
_DIRECT_CAUSAL_ASSUMPTION_RE = re.compile(
    r"(?:민원|불만|문의|이탈|사고).{0,45}"
    r"(?:때문|원인으로|원인이라고|인과관계가\s*있다고)",
    re.IGNORECASE,
)
_CAUSAL_UNCERTAINTY_RE = re.compile(
    r"(?:원인\s*)?가설|반증|상관(?:관계)?|인과\s*여부|대안\s*설명|"
    r"다른\s*설명|검증(?:할|해|하여|한)\s*(?:자료|방법|가설)",
    re.IGNORECASE,
)

# Uploaded notices, job descriptions, and candidate profiles are untrusted
# data.  If a model follows an instruction embedded in that data, the command
# can surface in a question, follow-up, or evaluation point.  Detect only
# explicit command-shaped artifacts here: a mere discussion of API-key
# security, conflicting work directions, external systems, or JSON APIs must
# remain valid interview content.
_DIRECT_COMMAND_END = r"(?=$|[.!?。！？])"
_INSTRUCTION_INJECTION_ARTIFACT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "instruction_override",
        re.compile(
            r"(?:모든\s*)?(?:이전(?:의)?|앞선|위(?:의)?|상위)\s*"
            r"(?:시스템\s*)?(?:지시|명령|프롬프트)(?:를|을)?\s*"
            r"(?:전부\s*|모두\s*)?(?:"
            r"무시(?:하라|해라|하세요|하십시오|하시오|해\s*주세요|해줘)"
            + _DIRECT_COMMAND_END
            + r"|잊(?:어라|으세요|으십시오|어\s*주세요|어줘)"
            + _DIRECT_COMMAND_END
            + r"|따르지\s*마(?:라|세요|십시오)"
            + _DIRECT_COMMAND_END
            + r"|무시하고\s*(?:아래|다음|새로운)\s*(?:지시|명령|요청|프롬프트))",
            re.IGNORECASE,
        ),
    ),
    (
        "system_prompt_disclosure",
        re.compile(
            r"(?:원문\s*)?시스템\s*프롬프트(?:를|을)?\s*"
            r"(?:그대로\s*|전부\s*|모두\s*)?(?:"
            r"(?:공개|출력)(?:하라|해라|하세요|하십시오|하시오|해\s*주세요|해줘)"
            + _DIRECT_COMMAND_END
            + r"|보여\s*(?:줘|주세요|주십시오|라)"
            + _DIRECT_COMMAND_END
            + r"|알려\s*(?:줘|주세요|주십시오|라)"
            + _DIRECT_COMMAND_END
            + r")",
            re.IGNORECASE,
        ),
    ),
    (
        "secret_disclosure",
        re.compile(
            r"(?:API\s*키|api[_\s-]?key|비밀(?:키|정보|값)|secret(?:s)?|"
            r"접근\s*토큰|인증\s*(?:토큰|정보)|환경\s*변수)(?:를|을|값을)?\s*"
            r"(?:그대로\s*|전부\s*|모두\s*)?(?:"
            r"(?:공개|출력|복사|전송|노출)(?:하라|해라|하세요|하십시오|하시오|해\s*주세요|해줘)"
            + _DIRECT_COMMAND_END
            + r"|보여\s*(?:줘|주세요|주십시오|라)"
            + _DIRECT_COMMAND_END
            + r"|알려\s*(?:줘|주세요|주십시오|라)"
            + _DIRECT_COMMAND_END
            + r")",
            re.IGNORECASE,
        ),
    ),
    (
        "tool_execution",
        re.compile(
            r"(?:MCP\s*)?(?:도구|툴|함수|플러그인|셸|쉘|터미널)(?:를|을)?\s*"
            r"(?:호출|실행)(?:하라|해라|하세요|하십시오|하시오|해\s*주세요|해줘)"
            + _DIRECT_COMMAND_END,
            re.IGNORECASE,
        ),
    ),
    (
        "external_communication",
        re.compile(
            r"(?:외부\s*(?:서버|사이트|URL|주소|시스템|서비스|채널|이메일|메신저)|"
            r"웹훅|인터넷)(?:로|으로|에|을|를)?(?:\s*[0-9A-Za-z가-힣_.:/-]+){0,4}\s*"
            r"(?:접속|전송|업로드|통신|발송)(?:하라|해라|하세요|하십시오|하시오|해\s*주세요|해줘)"
            + _DIRECT_COMMAND_END,
            re.IGNORECASE,
        ),
    ),
    (
        "forced_json_only",
        re.compile(
            r"(?:반드시\s*)?JSON(?:\s*형식)?(?:으로)?\s*만\s*"
            r"(?:출력|응답|답변)(?:하라|해라|하세요|하십시오|하시오|해\s*주세요|해줘)?"
            + _DIRECT_COMMAND_END,
            re.IGNORECASE,
        ),
    ),
    (
        "english_instruction_injection",
        re.compile(
            r"(?:^|[.!?:]\s+)(?:please\s+)?(?:"
            r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?"
            r"|(?:reveal|print|show|return|expose|send)\s+(?:the\s+)?"
            r"(?:system\s+prompt|api[-_\s]?key|secrets?|access\s+token)"
            r"|(?:run|execute|invoke)\s+(?:the\s+|this\s+)?(?:tool|function|shell|terminal)"
            r"|(?:return|respond|output)\s+(?:only|solely)\s+(?:in\s+)?json"
            r")\s*[.!?]?\s*$",
            re.IGNORECASE,
        ),
    ),
)

_ANSWER_SLOT_PATTERN = (
    r"(?:항목|자료|원자료|수치|지표|문구|순서|대안|방안|대응\s*방향|조정안|계획|안|"
    r"조치|판단|결정|선택|기준|과정|부분|내용|오류|재검산|표현|질문|기록|"
    r"산출물|목표(?:값|치)?|환경\s*변화|배분\s*기준|검토\s*과정|첫\s*조치)"
)

_ADAPTIVE_FOLLOW_UP_PATTERNS = (
    # The candidate owns a concrete judgment/reference from the answer and a
    # newly supplied fact would force a revision.  Requiring both halves keeps
    # vague decorations such as "방금 답변을 다시 설명" non-adaptive.
    re.compile(
        r"(?:방금|앞서).{0,45}(?:적용|판정|처리|보류|위임|중요|수용|유보)"
        r".{0,25}(?:근거|기준|판단|차이|요청|주장|사항).{0,80}"
        r"(?:적용되지|부족|불명확|다르|바뀌|확인되|추가되|빠지|반대|충돌)"
        r".{0,35}(?:면|다면)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:방금|앞서).{0,35}(?:적용한|판단한|보류한|위임한|수용한|남겨\s*둔|"
        r"중요하다고\s*판단한).{0,35}(?:근거|기준|요청|주장|사항|차이)"
        r".{0,60}(?:이유|자료|조건|범위|경계|감수|책임|바꾸|수정|유지|전환|확인)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:방금|앞서).{0,60}(?:판단|결정).{0,50}(?:근거|기준)"
        r".{0,30}(?:부족|불명확|다르|바뀌|반대).{0,20}(?:면|다면)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:방금|앞서)\s*['\"“”‘’][^'\"“”‘’]{2,80}['\"“”‘’]"
        r"\s*(?:고|라고)\s*(?:말씀|답변|설명)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:방금|앞서).{0,50}(?:하겠다고|한다고|겠다고)\s*"
        r"(?:말씀)?(?:하신|한).{0,20}" + _ANSWER_SLOT_PATTERN,
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:방금|앞서).{1,45}(?:다고|라고)\s*하신\s*" + _ANSWER_SLOT_PATTERN,
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![0-9A-Za-z가-힣])(?:말씀|설명|답변|언급|제시|제안|선택|결정|정|지목|발표)"
        r"(?:하신|하셨|한|하셨는데)(?!\s*결과(?:를|가|는|의|\s)).{0,35}"
        + _ANSWER_SLOT_PATTERN,
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:방금|앞서)\s*발표에서.{0,50}"
        r"(?:중요|우선|변화|목표|지표|수치|계획|대안|방안|안)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:왜|어째서)\s*그렇게\s*(?:판단|선택|결정|조치|제안)",
        re.IGNORECASE,
    ),
    re.compile(
        r"그\s*(?:판단|행동|조치|수치|근거|대안|선택|결정|갈등|오류|방법|"
        r"조정안|계획|지표)",
        re.IGNORECASE,
    ),
    re.compile(
        r"답변(?:에|에서|중).{0,40}(?:빠졌|없|언급하지|불분명|확인되지|말하지)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![0-9A-Za-z가-힣])(?:선택한|선택하신|제시한|제시하신|정한|정하신|설명한|설명하신|"
        r"언급한|언급하신|발표한|발표하신).{0,25}"
        r"(?:조치|판단|기준|대안|방안|안|순서|제안|자료|업무|처리|지표|목표|계획)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:그|방금|앞서)\s*(?:확인|검토|협의|합의|분석|계산|비교)\s*결과"
        r".{0,70}(?:라면|이라면|나왔다면|확인됐다면|드러났다면|밝혀졌다면)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:방금|앞서)\s*말씀하신\s*(?:착수|진행)\s*판단"
        r".{0,70}(?:놓친|간과한)\s*(?:위험|조건|요구사항|비용|일정)"
        r".{0,25}(?:발견|확인|드러나|밝혀지).{0,10}(?:면|다면)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:목표치|지표|대안|방안|항목|금액|일정|우선순위|조정안|계획)"
        r"(?:을|를|이|가)?\s*(?:올리|낮추|정하|선택하|제시하|조정하|바꾸|유지하|줄이|늘리)"
        r"(?:신|셨|았다|었다).{0,80}"
        r"(?:담당\s*부서|부서|기관|연구진|경영진|상대(?:\s*기관)?|이해관계자)"
        r".{0,45}(?:반대|거부|이의|수용할\s*수\s*없)",
        re.IGNORECASE,
    ),
    re.compile(
        r"그렇게.{0,35}(?:정리해|작성해|수정해|합의해|처리해)\s*"
        r"(?:남기신|만드신)?\s*(?:기록|문서|결과|산출물)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:조정|결정|처리|협의|수정|배치|선택|제안|합의)"
        r"(?:이|가|을|를|한|하신)?\s*(?:완료\s*)?(?:뒤|후).{0,80}"
        r"(?:실제|효과|이행|채택|확인|검증|수정|책임|수치|성과|변화|달라|"
        r"기록|오류|잔액|결과값|후속)",
        re.IGNORECASE,
    ),
    re.compile(
        r"모든\s*처리가\s*끝난\s*뒤.{0,60}(?:수치|기록|잔액|결과값|산출물)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:최종\s*합의|결정\s*결과|조정\s*결과|처리\s*결과).{0,60}"
        r"(?:실제|효과|이행|확인|수치|기록|변화|달라)",
        re.IGNORECASE,
    ),
    # A concrete answer slot may be challenged by newly supplied contrary
    # evidence.  Keep both halves mandatory so cosmetic phrases such as
    # "발표에서 무엇을 말했습니까" do not become adaptive by themselves.
    re.compile(
        r"(?:"
        r"(?:방금|앞서)\s*(?:말씀|설명|제시|선택|결정)(?:하신|한).{0,30}"
        r"(?:우선\s*)?(?:조정\s*대상|선택|결정|판단|대안|방안|안|항목|기준)"
        r"|"
        r"(?:방금|앞서)?\s*(?:발표|답변|설명)(?:에서|중).{0,45}"
        r"(?:근거로\s*(?:든|사용한|제시한|언급한)|(?:선택|결정|진단|판단)한)"
        r".{0,30}(?:수치|자료|지표|근거|원인|대안|방안|안)"
        r")"
        r".{0,70}(?:"
        r"(?:반대|반박|상충|다른).{0,30}(?:자료|근거|수치|결과|사실)"
        r".{0,25}(?:제시|확인|드러나|밝혀지)"
        r"|(?:입증|설명|뒷받침)하지\s*못"
        r"|(?:놓친|간과한)\s*(?:위험|조건|요구사항|비용|일정)"
        r".{0,25}(?:발견|확인|드러나|밝혀지)"
        r"|(?:위험|일정|필수\s*(?:지출|비용)|요구사항|조건|근거|자료|항목).{0,25}"
        r"(?:빠뜨|누락).{0,25}(?:사실|자료|근거|결과).{0,25}"
        r"(?:확인|드러나|밝혀지)"
        r").{0,10}(?:면|다면)",
        re.IGNORECASE,
    ),
    # Likewise, a probe is adaptive when it names the candidate's concrete
    # agreement/decision output and asks for an artifact proving execution.
    # A generic reference to "the result" is intentionally insufficient.
    re.compile(
        r"(?:방금|앞서)\s*(?:말씀|설명|제시|발표)(?:하신|한)\s*"
        r"(?:합의|결정|조정|처리|제안|협의)\s*(?:결과|내용|안)"
        r".{0,70}(?:실제\s*)?(?:일정|요구사항|계약|문서|업무|계획|산출물)"
        r".{0,35}(?:반영|이행|적용|변경|수정|실행|채택|쓰이|활용)"
        r".{0,70}(?:회신|메일|공문|기록|문서|일정표|계약서|요구서|산출물|수치|근거)",
        re.IGNORECASE,
    ),
    # A concrete slot selected in the answer may be challenged without the
    # word ``말씀하신``.  Keep both the selected slot and counter-evidence
    # mandatory so cosmetic references such as "방금 말씀하신 결과" remain
    # non-adaptive.
    re.compile(
        r"(?:방금|앞서).{0,45}"
        r"(?:고른|선택한|정한|분류한|도출한|제시한|한다고\s*한)"
        r".{0,35}(?:근거|자료|가설|실험|합의안|건|대상|주장|상태|수치|항목)"
        r".{0,100}(?:충돌|다르|틀렸|반증|반론|검증되지|확인되지|누락|발견|애매|불일치)",
        re.IGNORECASE,
    ),
    # A discussion answer can be probed through the exact opposing claim the
    # candidate accepted and the one they rejected.  This is materially more
    # specific than a bare reference to "the result".
    re.compile(
        r"(?:방금|앞서)\s*(?:수용한|받아들인).{0,35}주장"
        r".{0,45}(?:수용하지\s*않은|배제한|거절한).{0,25}주장",
        re.IGNORECASE,
    ),
    # A discussion probe may refer to the concrete ground that the candidate
    # just agreed to accept, then remove that ground.  Requiring both the
    # accepted stance and the missing/counterfactual ground keeps a bare
    # "방금 말씀하신 결과" outside this exception.
    re.compile(
        r"(?:방금|앞서).{0,35}(?:상대(?:\s*입장)?(?:에서|의)?\s*)?"
        r"(?:수용|받아들이)(?:하겠다고|기로).{0,25}(?:근거|주장)"
        r"(?:이|가|은|는)?.{0,30}(?:빠(?:지|진|져|졌)|없|누락|반증|확인되지)",
        re.IGNORECASE,
    ),
    # Likewise, a newly remaining risk is answer-adaptive when the probe
    # names the exact agreement boundary selected in the preceding answer.
    re.compile(
        r"(?:방금|앞서).{0,25}정한\s*합의\s*(?:범위|안)"
        r".{0,70}(?:핵심\s*)?(?:위험|쟁점|요구사항)(?:이|가|은|는)?"
        r".{0,25}(?:남|해소되지|반영되지)",
        re.IGNORECASE,
    ),
    # A presentation probe is answer-linked when it identifies evidence used
    # for the candidate's priority and supplies a concrete challenge to it.
    re.compile(
        r"(?:발표|답변)(?:에서|중).{0,55}"
        r"(?:근거로\s*(?:사용한|든|제시한)|우선순위의\s*근거로\s*사용한)"
        r".{0,30}(?:수치|자료|지표|근거)"
        r".{0,70}(?:반론|반박|일시적\s*변동|왜곡|대표성\s*부족)"
        r".{0,30}(?:제기|제시|확인|나오)",
        re.IGNORECASE,
    ),
    # An answer-owned basis is genuinely probed when later evidence can make
    # that exact basis invalid.  Both the answer trace and a concrete
    # falsifier are mandatory; merely asking the candidate to repeat a number
    # or a result therefore remains non-adaptive.
    re.compile(
        r"(?:"
        r"(?:방금|앞서).{0,55}(?:적용하겠다고\s*한|제시한|선택한|정한|작성한)"
        r".{0,30}(?:근거|수치|기준|판정값|가설|대장|조정안|계획|안)"
        r"|(?:발표|답변)(?:에서|중).{0,55}(?:근거로\s*든|사용한|선택한|정한)"
        r".{0,30}(?:수치|자료|지표|근거|기준|판정값|가설|대안|조정안)"
        r")"
        r".{0,140}(?:개정|반증|반론|불일치|충돌|상충|일회성|집계\s*시점|"
        r"영향을\s*받|왜곡|충족되지|미달|확인되지|사실과\s*다르|요구받)"
        r".{0,35}(?:면|다면|는데도)",
        re.IGNORECASE,
    ),
    # A presentation probe may ask the candidate to own the cost created by
    # the exact figure/criterion they chose.  Requiring an answer-owned slot,
    # a causal reference to that slot, and an explicit cost/accountability
    # consequence prevents a generic "왜 그랬습니까" probe from passing.
    re.compile(
        r"(?:발표|답변)(?:에서|중).{0,65}(?:사용한|선택한|정한|제시한)"
        r".{0,35}(?:수치|지표|근거|기준|배정)"
        r".{0,90}그\s*(?:수치|지표|근거|기준|선택)(?:이|가|을|를)?\s*때문에"
        r".{0,90}(?:반발|부담|불이익|비용|손실)"
        r".{0,45}(?:감수|책임)",
        re.IGNORECASE,
    ),
    # Discussion probes can branch on the boundary between the opposing
    # position accepted in the answer and the one rejected.  The requested
    # boundary/evidence is required in addition to the two stance slots.
    re.compile(
        r"(?:방금|앞서).{0,20}(?:수용한|받아들인).{0,45}"
        r"(?:입장|주장|요구|부분).{0,45}"
        r"(?:수용하지\s*않은|받아들이지\s*않은|거절한|배제한).{0,30}"
        r"(?:입장|주장|요구|부분)"
        r".{0,80}(?:경계|근거|자료|책임|예외)",
        re.IGNORECASE,
    ),
    # A produced work artifact is answer-owned when the probe names that
    # artifact and asks how a concrete mutation remains traceable.  This is
    # deliberately narrower than accepting "앞서 작성한 결과" on its own.
    re.compile(
        r"(?:방금|앞서).{0,35}(?:작성한|제시한|만든|도출한)"
        r".{0,25}(?:대장|표|안|계획|기록|문서)"
        r".{0,100}(?:정정|변경|수정|보완).{0,45}"
        r"(?:이력|추적|연결|대조|검증)",
        re.IGNORECASE,
    ),
)

# Some natural probes do not repeat the exact answer-slot grammar above.  They
# still branch from the answer when three relationships are present: a deictic
# reference to the just-given answer, an answer-owned choice, and a new test of
# that choice.  Keep these relations separate so a bag of words such as
# "앞서 답변한 근거·경계·책임을 다시 설명" cannot pass on vocabulary alone.
_ANSWER_OWNED_DEICTIC_RE = re.compile(
    r"(?:방금|앞서|발표에서|답변에서).{0,100}(?:"
    r"(?:적용|선택|판정|수용|보류|수정|위임|협의|제시|작성|결정|정정|허용|거절|인정)"
    r"(?:하신|한|하기로\s*한|했다고\s*한|하겠다고\s*한)"
    r"|근거로\s*(?:든|사용한)|(?:1\s*순위|우선순위)(?:로)?\s*둔|남겨\s*둔|"
    r"(?:가능|중요|필요)하다고\s*본|정한|"
    r"(?:말씀|말|언급|설명)한\s*(?:결과|변화|효과|산출물)|"
    r"(?:제출\s*가능\s*여부|처리\s*가능\s*여부|판정).{0,30}"
    r"(?:판단|결정)(?:할\s*때|하면서).{0,35}(?:사용|확인|고른|택한)|"
    r"(?:보류|이송|정정|위임|보고)(?:하거나|하고|한).{0,20}"
    r"(?:건|문서|요청|항목)|"
    r"(?:잠정값|미확정값|확정값)(?:으로)?\s*(?:표시|분류|기재)한|"
    r"(?:인정|처리|다루)(?:하거나|하지\s*않|할\s*수\s*있다고|지\s*않았다고)?.{0,15}"
    r"(?:판단|답했|답변))",
    re.IGNORECASE,
)
_ANSWER_CHOICE_VALIDITY_RE = re.compile(
    r"(?:문서|규정|지침|기준|근거|사실|자료|조건|목적).{0,90}"
    r"(?:유효|효력|적용\s*(?:대상|시점|범위)|확인되지|불명확|없(?:다|다면)|"
    r"달라|반증).{0,90}"
    r"(?:확인|검증|판별|대조|바꾸|수정|유지|철회|전환)",
    re.IGNORECASE,
)
_ANSWER_CHOICE_BOUNDARY_RE = re.compile(
    r"(?:추가|더\s*넓|반대|새로|달라진).{0,55}"
    r"(?:요구|제시|확인|드러나|바뀌).{0,110}"
    r"(?:범위|경계|예외|철회|제한|교환|양보|거절|바꾸|수정|전환)",
    re.IGNORECASE,
)
_ANSWER_CHOICE_ACCOUNTABILITY_RE = re.compile(
    r"(?:원칙|기준|판단|결정|방식|선택).{0,80}"
    r"(?:때문|따라|초래|발생).{0,65}"
    r"(?:지연|반발|피해|불이익|부담|비용|손실).{0,85}(?:감수|책임)",
    re.IGNORECASE,
)
_ANSWER_CHOICE_PRIORITY_RE = re.compile(
    r"(?:1\s*순위|우선순위|먼저|처리\s*주체).{0,95}"
    r"(?:직접\s*처리|위임|보고|방식|선택).{0,55}(?:이유|근거).{0,65}"
    r"(?:지연|피해|위험|누락|영향|결과)",
    re.IGNORECASE,
)
_ANSWER_CHOICE_EXPERIMENT_RE = re.compile(
    r"(?:가설|검증\s*방법|실험|시험).{0,105}(?:"
    r"(?:반증|검증|대조).{0,25}(?:하려면|한다면|할\s*경우).{0,90}"
    r"(?:자료|결과|조건|기준|키|기간|순서)"
    r"|(?:시험|실험|검증).{0,50}"
    r"(?:한다면|하려면|나오면|발견되면|확인되면|충족되지\s*않으면)"
    r".{0,100}(?:관찰|판정|중단).{0,80}(?:결과|조건|기준|기간|순서)"
    r")",
    re.IGNORECASE,
)
_ANSWER_CHOICE_AUDIT_TRAIL_RE = re.compile(
    r"(?:기록|대장|문서|값).{0,85}"
    r"(?:덮어쓰지|정정|수정|바뀌|변경되).{0,65}"
    r"(?:변경\s*전후|정정\s*전후|수정\s*전후|변경\s*이력|승인자|승인\s*흔적)"
    r".{0,70}(?:남기|추적|연결|보존|기록)",
    re.IGNORECASE,
)
_ANSWER_CHOICE_CONSEQUENCE_ACTION_RE = re.compile(
    r"(?:때문|따라|초래|발생).{0,75}"
    r"(?:지연|반발|피해|불이익|부담|비용|손실|영향).{0,90}"
    r"(?:본인|직접).{0,45}(?:행동|조치|대응|감당|책임).{0,85}"
    r"(?:결과|효과|변화).{0,35}(?:확인|검증|입증|측정)",
    re.IGNORECASE,
)
_ANSWER_CHOICE_HYPOTHESIS_FALSIFIER_RE = re.compile(
    r"(?:가설|원인\s*(?:판정|추정)).{0,90}"
    r"(?:뒤집|반증(?:하|할|되|시킬)|기각(?:하|할|되)|틀렸|맞지\s*않|"
    r"사실과\s*다르).{0,55}"
    r"(?:자료|증거|결과|사실|관찰값)",
    re.IGNORECASE,
)
_ANSWER_CHOICE_MINIMUM_TEST_RE = re.compile(
    r"(?:가설|원인).{0,75}(?:확인|검증).{0,20}(?:하려면|하기\s*위해)"
    r".{0,65}(?:최소|작은|제한된).{0,25}(?:검증|시험|실험)"
    r".{0,100}(?:관찰|결과).{0,70}(?:중단|전환|바꾸|다른\s*가설)",
    re.IGNORECASE,
)
_ANSWER_TEST_OUTCOME_DECISION_RE = re.compile(
    r"(?:최소\s*)?(?:검증|시험|실험).{0,80}"
    r"(?:관찰|측정)?\s*결과.{0,30}(?:나오|확인되|드러나|밝혀지).{0,10}"
    r"(?:면|다면).{0,80}(?:중단|계속|전환).{0,45}"
    r"(?:결론|판정|가설|조사).{0,100}(?:이유|근거|구별|판별|설명)",
    re.IGNORECASE,
)
_ANSWER_EXCEPTION_RISK_ARTIFACT_RE = re.compile(
    r"(?:예외|경계|범위).{0,85}(?:위험|왜곡|오류|영향).{0,75}"
    r"(?:표|기록|문서|대장|보고서).{0,45}(?:드러내|표시|구분|반영|남기)",
    re.IGNORECASE,
)
_ANSWER_EVIDENCE_RIVAL_TEST_RE = re.compile(
    r"(?:근거|수치|자료|지표).{0,80}"
    r"(?:일시적|우연|계절|시기\s*차이|다른\s*원인|반대\s*설명|왜곡)"
    r".{0,55}(?:가능성|주장|해석|변동).{0,80}"
    r"(?:기간|원자료|자료|지표|기록).{0,45}(?:추가|대조|비교|확인|검증)",
    re.IGNORECASE,
)
_ANSWER_CHANGED_CONDITION_CONCLUSION_RE = re.compile(
    r"(?:목적|시점|조건|대상|범위|근거|자료|사실|출처|측정\s*기간|"
    r"집계\s*기간|집계\s*범위|단위).{0,85}"
    r"(?:다르|달라지|바뀌|변경되|확인되지|불명확|없다면|빠지).{0,45}"
    r"(?:면|다면).{0,75}(?:결론|판정|판단|결정|범위).{0,35}"
    r"(?:바꾸|바뀌|바뀝|달라|수정|조정|유지|철회|보류|전환)",
    re.IGNORECASE,
)
_ANSWER_MISSING_EXCEPTION_SUPPORT_ROUTE_RE = re.compile(
    r"(?:예외|특례|인정|허용).{0,85}"
    r"(?:사실|증빙|근거|조건|기록)(?:이|가|은|는)?.{0,35}"
    r"(?:빠지|빠졌|누락|없|확인되지).{0,70}"
    r"(?:보완|이송|상급자\s*보고|승인권자).{0,75}"
    r"(?:기준|경계|나누|구분|결정)",
    re.IGNORECASE,
)
_ANSWER_MISSING_CONDITION_CONCLUSION_RE = re.compile(
    r"(?:예외|허용|인정|부정|판정|결정).{0,55}(?:답변|판단|결론).{0,45}"
    r"(?:빠진|누락된|확인되지\s*않은).{0,25}(?:조건|근거|사실|증빙)"
    r".{0,35}(?:면|다면).{0,65}(?:결론|판정|기록|문서).{0,35}"
    r"(?:바꾸|수정|보완|달라)",
    re.IGNORECASE,
)
_ANSWER_RESULT_USE_BRANCH_RE = re.compile(
    r"(?:결과|산출물|보고서|기록)(?:가|이|은|는)?.{0,45}"
    r"(?:활용|채택|반영|사용)(?:됐|되었|되|했).{0,15}(?:면|다면).{0,65}"
    r"(?:확인|입증|기록|근거).{0,70}"
    r"(?:활용|채택|반영|사용)(?:되지\s*않|되지\s*못|안\s*됐|되지\s*않았)"
    r".{0,15}(?:면|다면).{0,75}(?:다시\s*)?(?:점검|검토|수정|보완)",
    re.IGNORECASE,
)
_ANSWER_RULE_EXCEPTION_SCOPE_RE = re.compile(
    r"(?:규칙|기준|원칙).{0,80}(?:서로\s*다른|다른|추가|복수).{0,45}"
    r"(?:프로그램|사업|대상|항목|문서|자료).{0,35}(?:참여|적용|포함|발생)"
    r".{0,35}(?:경우|때).{0,65}(?:자료|기록|근거).{0,30}(?:확인|대조)"
    r".{0,55}(?:범위|예외|포함|제외).{0,25}(?:정하|조정|구분)",
    re.IGNORECASE,
)
_ANSWER_RESIDUAL_MISMATCH_RE = re.compile(
    r"(?:판정표|검토표|분석표|대장|기록|문서|보고서|안)(?:대로|를|을|에)?"
    r".{0,55}(?:수정|적용|반영)(?:했|한|하고).{0,20}(?:는데도|후에도|뒤에도)"
    r".{0,80}(?:다르|불일치|맞지\s*않).{0,50}(?:오류|원인|가능성)"
    r".{0,30}(?:확인|점검|대조)",
    re.IGNORECASE,
)
_ANSWER_EVIDENCE_CONFLICT_RESOLUTION_RE = re.compile(
    r"(?:사용|선택|확인|고른).{0,30}(?:자료|근거|문서).{0,60}"
    r"(?:다른|별도).{0,25}(?:자료|문서|근거).{0,30}(?:충돌|상충|다르)"
    r".{0,50}(?:기준|근거).{0,25}(?:확정|판정|결정|조정)",
    re.IGNORECASE,
)
_ANSWER_RESERVED_ITEM_IMPACT_RE = re.compile(
    r"(?:유보|보류|남겨\s*둔).{0,30}(?:항목|쟁점|사항|범위).{0,55}"
    r"(?:일정|범위|결과|이행).{0,30}(?:영향|지연|차질).{0,45}"
    r"(?:범위|예외|경계).{0,35}(?:조정|수정|축소|확대)",
    re.IGNORECASE,
)
_ANSWER_NEW_AUTHORITY_PURPOSE_RE = re.compile(
    r"(?:보류|이송|위임|보고).{0,40}(?:건|문서|요청|항목).{0,55}"
    r"(?:목적|권한|근거).{0,45}(?:자료|사실|기록)?(?:가|이)?\s*"
    r"(?:추가|확인|보완).{0,25}(?:되|됐|된다면|되면).{0,65}"
    r"(?:제공|처리|사용|열람).{0,25}(?:범위|주체|담당).{0,35}"
    r"(?:다시\s*)?(?:정하|조정|바꾸|결정)",
    re.IGNORECASE,
)
_ANSWER_CHOICE_ADVERSE_EFFECT_RE = re.compile(
    r"(?:선택|적용|정한|제시).{0,40}(?:방식|원칙|기준|안|결정).{0,70}"
    r"(?:불리|부담|영향|손실|위험|오류).{0,45}"
    r"(?:확인|검증|관찰|점검|예상과\s*다르).{0,70}"
    r"(?:조정|수정|바꾸|책임|담당|기준|자료)",
    re.IGNORECASE,
)
_ANSWER_CHOICE_IMPACT_CHECK_RE = re.compile(
    r"(?:선택|적용|정한|제시).{0,40}(?:방식|원칙|기준|안|결정).{0,80}"
    r"(?:비교|연속성|입력|현장|일정|비용|대상).{0,45}"
    r"(?:불리|부담|영향|손실|위험|오류).{0,45}"
    r"(?:확인|검증|관찰|점검|측정)",
    re.IGNORECASE,
)
_ANSWER_PROVISIONAL_EVIDENCE_BRANCH_RE = re.compile(
    r"(?:잠정값|미확정값|확정\s*전\s*값|확인\s*중인\s*값).{0,45}"
    r"(?:증빙|근거|자료).{0,35}(?:확보되지|도착하지|누락|없).{0,20}"
    r"(?:면|다면).{0,65}(?:보고|반영|결재|승인).{0,30}"
    r"(?:범위|요청|경계).{0,30}(?:바꾸|조정|구분|정하)",
    re.IGNORECASE,
)
_ANSWER_ORIGINAL_RECORD_AUDIT_RE = re.compile(
    r"(?:정정|보류|수정|변경).{0,35}(?:문서|기록|건|항목).{0,45}"
    r"(?:원기록|기존\s*기록|변경\s*전\s*기록).{0,30}(?:보존|유지|남기)"
    r".{0,65}(?:변경자|수정자|변경\s*시점|변경\s*사유|승인자)"
    r".{0,45}(?:항목|이력|기록).{0,25}(?:연결|표시|남기)",
    re.IGNORECASE,
)
_ANSWER_ACCEPTANCE_MAINTENANCE_RE = re.compile(
    r"(?:수용|양보|인정|허용).{0,60}(?:요구|입장|예외|범위|경계).{0,90}"
    r"(?:자료|사실|조건|근거).{0,45}(?:확인|성립|충족).{0,25}"
    r"(?:때|경우|면|다면).{0,55}(?:유지|철회|수용|거절|조정|바꾸)",
    re.IGNORECASE,
)
_ANSWER_RENEGOTIATED_BOUNDARY_RE = re.compile(
    r"(?:경계|범위|조건|예외).{0,90}(?:다시|추가|재차).{0,45}"
    r"(?:조정|양보|수용|변경).{0,75}(?:요구|제안).{0,80}"
    r"(?:교환|수용|거절|유지|철회).{0,45}(?:조건|기준|범위|경계)",
    re.IGNORECASE,
)
_ANSWER_COST_REALIZATION_RE = re.compile(
    r"(?:감수|예상|선택).{0,55}(?:비용|지연|반발|부담|불이익|손실)"
    r".{0,65}(?:실제|현실).{0,30}(?:발생|확인|나타나).{0,15}(?:면|다면)"
    r".{0,75}(?:유지|철회|수정|전환|바꾸).{0,45}(?:경계|기준|조건|근거)",
    re.IGNORECASE,
)
_ANSWER_COUNTEREVIDENCE_REVISION_RE = re.compile(
    r"(?:기준|경계|판단|결정|가설|근거|선택|설명).{0,90}"
    r"(?:맞지\s*않|반증|반대|상충|충돌|뒤집|사실과\s*다르).{0,45}"
    r"(?:자료|사실|결과|증거).{0,35}(?:나오|나온|확인되|발견되|제시되)"
    r".{0,12}(?:면|다면).{0,75}(?:어떤\s*)?"
    r"(?:부분|항목|범위|판단|결정|결론|분석|기준|경계).{0,25}"
    r"(?:다시\s*(?:판단|검토)|바꾸|수정|재검토)",
    re.IGNORECASE,
)
_ANSWER_ALTERNATIVE_DECISION_BRANCH_RE = re.compile(
    r"(?:처리|제공|허용|수용|승인|진행).{0,35}"
    r"(?:할\s*수\s*있(?:다)?|한다|했다)(?:고|다고|라고)\s*"
    r"(?:답|말).{0,15}(?:면|다면).{0,75}"
    r"(?:범위|항목|대상|조치).{0,40}(?:줄이|제한|정하|바꾸|수정).{0,80}"
    r"(?:보류|거절|제한|중단).{0,35}(?:한다고|하기로).{0,20}"
    r"(?:답|말).{0,15}(?:면|다면).{0,75}"
    r"(?:정보|자료|근거|조건|권한|목적).{0,45}(?:보완|확인|추가|충족).{0,55}"
    r"(?:판단|결정|결론).{0,25}(?:바꾸|수정|전환)",
    re.IGNORECASE,
)
_ANSWER_OPTION_BRANCH_RE = re.compile(
    r"(?:방금|앞서|답변에서).{0,90}(?:처리|제공|허용|수용|승인|진행)"
    r".{0,50}(?:답|말).{0,20}(?:면|다면)",
    re.IGNORECASE,
)
_ANSWER_ALTERNATE_BRANCH_RE = re.compile(
    r"(?:보류|거절|제한|중단).{0,40}(?:답|말).{0,20}(?:면|다면)",
    re.IGNORECASE,
)
_ANSWER_BRANCH_BOUNDARY_RE = re.compile(
    r"(?:범위|항목|대상).{0,45}(?:줄이|제한|정하|바꾸|수정)",
    re.IGNORECASE,
)
_ANSWER_BRANCH_REVISION_RE = re.compile(
    r"(?:정보|자료|근거|조건|권한|목적).{0,50}(?:보완|확인|추가|충족)"
    r".{0,60}(?:판단|결정|결론).{0,30}(?:바꾸|수정|전환)",
    re.IGNORECASE,
)
_RELATIONAL_ANSWER_BRANCH_PATTERNS = (
    _ANSWER_CHOICE_VALIDITY_RE,
    _ANSWER_CHOICE_BOUNDARY_RE,
    _ANSWER_CHOICE_ACCOUNTABILITY_RE,
    _ANSWER_CHOICE_PRIORITY_RE,
    _ANSWER_CHOICE_EXPERIMENT_RE,
    _ANSWER_CHOICE_AUDIT_TRAIL_RE,
    _ANSWER_CHOICE_CONSEQUENCE_ACTION_RE,
    _ANSWER_CHOICE_HYPOTHESIS_FALSIFIER_RE,
    _ANSWER_CHOICE_MINIMUM_TEST_RE,
    _ANSWER_TEST_OUTCOME_DECISION_RE,
    _ANSWER_EXCEPTION_RISK_ARTIFACT_RE,
    _ANSWER_EVIDENCE_RIVAL_TEST_RE,
    _ANSWER_CHANGED_CONDITION_CONCLUSION_RE,
    _ANSWER_MISSING_EXCEPTION_SUPPORT_ROUTE_RE,
    _ANSWER_MISSING_CONDITION_CONCLUSION_RE,
    _ANSWER_RESULT_USE_BRANCH_RE,
    _ANSWER_RULE_EXCEPTION_SCOPE_RE,
    _ANSWER_RESIDUAL_MISMATCH_RE,
    _ANSWER_EVIDENCE_CONFLICT_RESOLUTION_RE,
    _ANSWER_RESERVED_ITEM_IMPACT_RE,
    _ANSWER_NEW_AUTHORITY_PURPOSE_RE,
    _ANSWER_CHOICE_ADVERSE_EFFECT_RE,
    _ANSWER_CHOICE_IMPACT_CHECK_RE,
    _ANSWER_PROVISIONAL_EVIDENCE_BRANCH_RE,
    _ANSWER_ORIGINAL_RECORD_AUDIT_RE,
    _ANSWER_ACCEPTANCE_MAINTENANCE_RE,
    _ANSWER_RENEGOTIATED_BOUNDARY_RE,
    _ANSWER_COST_REALIZATION_RE,
    _ANSWER_COUNTEREVIDENCE_REVISION_RE,
    _ANSWER_ALTERNATIVE_DECISION_BRANCH_RE,
)
_INLINE_ANSWER_CONTENT_BRANCH_RE = re.compile(
    r"답변(?:에서|에).{0,100}(?:다루|언급|포함|제시|설명)"
    r"(?:지\s*않았|지\s*못했|지\s*않|지\s*못하).{0,15}(?:면|다면)"
    r".{0,80}(?:보완|추가|수정|확인|바꾸).{0,100}"
    r"(?:다뤘|언급했|포함했|제시했|설명했|빠지지\s*않았).{0,15}(?:면|다면)"
    r".{0,80}(?:기록|흔적|근거|증거|이유|확인)",
    re.IGNORECASE,
)
_GENERIC_ANSWER_RESTATEMENT_RE = re.compile(
    r"(?:다시|그대로|차례로|한\s*번\s*더).{0,18}"
    r"(?:말씀|말해|설명|읽어|정의|요약)|"
    r"(?:말씀|설명|읽기|정의|요약).{0,18}(?:반복|다시|그대로)",
    re.IGNORECASE,
)


def _has_answer_alternative_decision_branch(text: str) -> bool:
    """Return whether two reported answer choices lead to different next actions."""

    return bool(
        _ANSWER_OPTION_BRANCH_RE.search(text)
        and _ANSWER_ALTERNATE_BRANCH_RE.search(text)
        and _ANSWER_BRANCH_BOUNDARY_RE.search(text)
        and _ANSWER_BRANCH_REVISION_RE.search(text)
    )

_FOLLOW_UP_CONDITION_BRANCH_RE = re.compile(
    r"(?:경우|때|라면|이면|하면|했다면|않으면|없으면|있으면|시에)",
    re.IGNORECASE,
)
_ADAPTIVE_FOLLOW_UP_CONDITION_PATTERNS = (
    # The branch is driven by something present, absent, or unclear in the
    # candidate's answer rather than by an interviewer's opaque discretion.
    re.compile(
        r"(?:답변|말씀|설명|발표)(?:\s*내용)?(?:에|에서|중|이|가|으로).{0,60}"
        r"(?:없|있|빠지|누락|언급|제시|포함|선택|결정|불분명|모호|"
        r"구체적이지|명확하지|충분하지|일치하지|다르|상충)",
        re.IGNORECASE,
    ),
    # A condition may also branch directly on a choice or decision that the
    # candidate has just made in the answer.
    re.compile(
        r"(?:지원자|후보자|응답자)(?:가|이)\s*.{0,35}"
        r"(?:선택|결정|제안|제시|언급|설명|답변|말씀)"
        r"(?:하신|한|했|했다|하지\s*않은|하지\s*않았)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:선택|결정|제안|제시|언급|설명|답변|말씀)하신.{0,40}",
        re.IGNORECASE,
    ),
    # Outcome-dependent branches are adaptive even when the condition does not
    # repeat the word '답변' (for example, a missing metric or a failed result).
    re.compile(
        r"(?:결과|성과|수치|지표|근거|대안|방안|조치)(?:가|이|은|는)"
        r".{0,35}(?:없|있|누락|불분명|모호|미흡|부족|미달|초과|실패|"
        r"상충|다르|언급되지|제시되지|확인되지)",
        re.IGNORECASE,
    ),
)

_QUANTIFIED_FACT_RE = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:%|퍼센트|분|시간|일|주|개월|건|명|개|회|원|만원|억)?|"
    r"(?:한|두|세|네|열)\s*(?:명|건|개|회|시간|일|개월|사업|기관|부서|문서)|"
    r"(?:오늘|내일|당일|월말|분기말|마감\s*직전|하루|이틀|사흘|정오|"
    r"오전|오후|다음\s*(?:연도|분기)|첫\s*\d+개월|두\s*달\s*연속)",
    re.IGNORECASE,
)
_INCIDENT_RE = re.compile(
    r"오류|불일치|누락|고장|중단|지연|민원|사고|초과|부족|변경|취소|반려|"
    r"이탈|감소|증가|줄었|연락(?:이\s*)?되지|거부|잘못|이상값|차이|다릅|미제출|"
    r"(?:기준|금액|일정|내용)(?:이|가|은|는|과|와)?\s*(?:서로\s*)?다르|"
    r"제출하지\s*않|확인되지\s*않|빠져\s*있|빠졌|동결|유지하기\s*어려운",
    re.IGNORECASE,
)
_ARTIFACT_RE = re.compile(
    r"원자료|집계표|보고서|계약서|도면|발주서|견적서|메일|공문|규정|지침|증빙|"
    r"시스템|데이터|자료|예산|사업비|수치|재고|설비|고객\s*요청|민원|일정표|승인|"
    r"실적표|수요조사|요구서|요약본|제안서|사업계획서|과업지시서|협약(?:안|서)?",
    re.IGNORECASE,
)
_ACTOR_ACTION_RE = re.compile(
    r"(?:고객|민원인|팀장|상급자|관리자|동료|담당자|협력업체|산업체|대학\s*연구자|발주처|현장책임자|연구책임자|"
    r"원자료\s*담당자|승인권자|다른\s*부서|현지\s*기관)(?:은|는|이|가).{1,45}"
    r"(?:요구|요청|지시|주장|반대|거부|연락되지|답하지|제출하|처리하|바꾸|취소)",
    re.IGNORECASE,
)
_OPPOSING_STAKEHOLDER_DILEMMA_RE = re.compile(
    r"(?:사업부서|연구진|계약부서|담당\s*부서|경영진|수행기관|상대\s*기관|"
    r"협력기관|관계기관|유관기관|연구원\s*내부\s*부서|내부\s*부서|"
    r"기획부서|재무부서|현업부서|현장(?:\s*의견)?|주관기관|검증기관|점검\s*담당자|발주처|승인권자|국제교류\s*부서|전산화\s*부서)"
    r"(?:은|는|이|가).{1,100}(?:요구|주장|제안|하자고|원하|필요|유지하라고|지키라고)"
    r".{0,35}(?:하고|하지만|반면|으나|며),?\s*"
    r"(?:사업부서|연구진|계약부서|담당\s*부서|경영진|수행기관|상대\s*기관|"
    r"협력기관|관계기관|유관기관|연구원\s*내부\s*부서|내부\s*부서|"
    r"기획부서|재무부서|현업부서|현장(?:\s*의견)?|주관기관|검증기관|점검\s*담당자|발주처|승인권자|국제교류\s*부서|전산화\s*부서)"
    r"(?:은|는|이|가).{1,100}(?:어렵|불가|반대|거부|수\s*없)",
    re.IGNORECASE,
)
_VERIFICATION_VS_SCHEDULE_POSITION_RE = re.compile(
    r"(?:"
    r"(?:검증|확인).{0,55}(?:끝|완료).{0,40}(?:입장|주장)"
    r".{0,90}(?:일정|마감|착수).{0,55}(?:먼저|준수|사후).{0,40}(?:입장|주장)"
    r"|"
    r"(?:일정|마감|착수).{0,55}(?:먼저|준수|사후).{0,40}(?:입장|주장)"
    r".{0,90}(?:검증|확인).{0,55}(?:끝|완료).{0,40}(?:입장|주장)"
    r")",
    re.IGNORECASE,
)
_PRIORITY_VS_FULL_EVIDENCE_POSITION_RE = re.compile(
    r"(?:주관기관|관계기관|수행기관|현업부서|사업부서)(?:은|는|이|가)"
    r".{1,65}(?:핵심\s*결과|요약본|일부\s*자료|선제출).{0,25}(?:먼저|우선)"
    r".{0,45}(?:입장|주장)"
    r".{0,100}(?:검증기관|점검\s*담당자|내부\s*부서|연구진)(?:은|는|이|가)"
    r".{1,85}(?:원자료|근거자료|산출\s*과정|검증|확인).{0,45}(?:입장|주장)",
    re.IGNORECASE,
)
_QUANTITATIVE_VS_CONTEXT_POSITION_RE = re.compile(
    r"(?:기획부서|평가부서|경영진)(?:은|는|이|가).{1,80}"
    r"(?:정량\s*증빙|수치\s*기준).{0,50}(?:엄격|일관|적용).{0,35}"
    r"(?:주장|요구).{0,120}(?:현업부서|현장|사업부서)(?:은|는|이|가)"
    r".{1,90}(?:참여자\s*특성|장기\s*효과|현장\s*사정|운영\s*여건)"
    r".{0,45}(?:반영|고려)",
    re.IGNORECASE,
)
_CAUSAL_CLAIM_RE = re.compile(
    r"(?:[가-힣A-Za-z][가-힣A-Za-z\s]{0,25})(?:은|는|이|가)\s*"
    r"(?P<cause>[가-힣A-Za-z0-9·\s]{2,45}?)(?:을|를)?\s*"
    r"원인으로\s*(?:보고|판단|지목)",
    re.IGNORECASE,
)
_COMPETING_EVALUATION_STANCES_RE = re.compile(
    r"(?:동일한?|공통의?|모든\s*사업에)\s*(?:수치|정량\s*증빙)?\s*기준?.{0,45}(?:엄격|일관|적용).{0,35}"
    r"(?:주장|요구).{0,120}(?:지역별|현장|운영)\s*(?:여건|맥락)|"
    r"(?:지역별|현장|운영)\s*(?:여건|맥락).{0,90}(?:반영|고려).{0,45}"
    r"(?:동일한?|공통의?|모든\s*사업에)\s*(?:수치|정량\s*증빙)?\s*기준?",
    re.IGNORECASE,
)
_STRICT_COMMON_RULE_POSITION_RE = re.compile(
    r"(?:"
    r"(?:모든|동일한?|공통의?|일관된?|현재).{0,55}(?:정량|수치|증빙|기준)"
    r"|(?:정량|수치|증빙).{0,45}(?:동일한?|공통의?|일관된?)\s*기준"
    r").{0,70}(?:적용|평가|판정|종결|유지|보류)",
    re.IGNORECASE,
)
_CONTEXT_EXCEPTION_POSITION_RE = re.compile(
    r"(?:현장|지역|장기|질적|운영).{0,55}"
    r"(?:맥락|여건|효과|성과|사정|특성).{0,70}"
    r"(?:반영|고려|예외|인정|유지)|"
    r"(?:현장|지역|장기|질적|운영)\s*(?:맥락|여건|효과|성과|사정|특성)?"
    r".{0,55}(?:따라|근거로).{0,35}(?:예외|인정|유지)",
    re.IGNORECASE,
)
_COMMON_QUANTITATIVE_RULE_STANCE_RE = re.compile(
    r"(?:사업\s*간|대상\s*간)?\s*(?:비교\s*가능성|일관성).{0,55}"
    r"(?:정량|수치).{0,30}(?:기준|목표).{0,45}(?:동일|공통|일관).{0,30}"
    r"(?:적용|평가|판정)|"
    r"(?:정량|수치).{0,30}(?:기준|목표).{0,35}(?:동일|공통|일관).{0,30}"
    r"(?:적용|평가|판정).{0,55}(?:비교\s*가능성|일관성)",
    re.IGNORECASE,
)
_QUALITATIVE_CONTEXT_STANCE_RE = re.compile(
    r"(?:사업|현장|지역|참여자).{0,35}(?:특성|여건|맥락).{0,55}"
    r"(?:정성|현장|장기).{0,30}(?:자료|성과|효과).{0,45}"
    r"(?:비중|반영|고려).{0,30}(?:높|늘|조정|요구|주장)|"
    r"(?:정성|현장|장기).{0,30}(?:자료|성과|효과).{0,45}"
    r"(?:비중|반영|고려).{0,30}(?:높|늘|조정).{0,45}"
    r"(?:사업|현장|지역|참여자).{0,35}(?:특성|여건|맥락)",
    re.IGNORECASE,
)
_MEASUREMENT_CONTINUITY_TRADEOFF_RE = re.compile(
    r"(?:기존|현재).{0,25}(?:산식|집계\s*방식|측정\s*방식).{0,35}"
    r"(?:유지|그대로).{0,40}(?:비교|연속성).{0,30}(?:쉽|유지|보존)"
    r".{0,55}(?:지만|반면|으나).{0,55}(?:누락|중복|오류).{0,35}"
    r"(?:계속|반복).{0,80}(?:산식|집계\s*방식|측정\s*방식).{0,35}"
    r"(?:바꾸|변경|수정).{0,45}(?:비교|연속성).{0,30}(?:약해|깨지|낮아)",
    re.IGNORECASE,
)
_EXPLICIT_POSITION_CONTRAST_RE = re.compile(
    r"(?:한쪽|한\s*편).{0,180}(?:다른\s*쪽|다른\s*편)|"
    r"(?:입장|주장|요구).{0,120}(?:반면|하지만|그러나|맞서|대립|충돌)"
    r".{0,120}(?:입장|주장|요구)",
    re.IGNORECASE,
)
_PARTIAL_DELIVERY_POSITION_RE = re.compile(
    r"(?:핵심|일부|요약|우선).{0,35}(?:결과|자료|내용|항목).{0,35}"
    r"(?:만|부터).{0,25}(?:먼저|우선).{0,35}(?:제출|수령|접수|받)",
    re.IGNORECASE,
)
_VERIFIED_DELIVERY_POSITION_RE = re.compile(
    r"(?:전체|모든|전부|원자료와\s*산출\s*과정).{0,55}"
    r"(?:검증|확인|대조).{0,35}(?:끝|완료).{0,45}"
    r"(?:미루|제출|접수|받|시작)",
    re.IGNORECASE,
)
_CURRENT_MATERIAL_FIRST_POSITION_RE = re.compile(
    r"(?:현재|기존|가용|확인\s*전).{0,35}(?:자료|수치|내용).{0,35}"
    r"(?:우선|먼저).{0,30}(?:접수|제출|반영|사용).{0,55}"
    r"(?:일정|마감).{0,25}(?:유지|준수|지키)|"
    r"(?:일정|마감).{0,25}(?:유지|준수|지키).{0,55}"
    r"(?:현재|기존|가용|확인\s*전).{0,35}(?:자료|수치|내용).{0,35}"
    r"(?:우선|먼저).{0,30}(?:접수|제출|반영|사용)",
    re.IGNORECASE,
)
_VERIFY_BEFORE_OFFICIAL_USE_POSITION_RE = re.compile(
    r"(?:검증되지\s*않|확인되지\s*않|미검증).{0,45}(?:자료|수치|값|내용)"
    r".{0,55}(?:공식\s*기록|보고|제출|사용).{0,40}"
    r"(?:굳|위험|오류|왜곡).{0,70}(?:확인|검증).{0,30}(?:후|뒤)|"
    r"(?:검증되지\s*않|확인되지\s*않|미검증).{0,45}(?:자료|수치|값|내용)"
    r".{0,55}(?:확인|검증).{0,30}(?:후|뒤).{0,30}(?:제출|접수|사용)",
    re.IGNORECASE,
)
_QUANTITATIVE_PRIORITY_POSITION_RE = re.compile(
    r"(?:정량|수치|실적|지표).{0,45}(?:우선|그대로|현재).{0,45}"
    r"(?:적용|평가|판정|종결|확정)|"
    r"(?:우선|현재).{0,35}(?:정량|수치|실적|지표).{0,35}"
    r"(?:적용|평가|판정|종결|확정)",
    re.IGNORECASE,
)
_CONTEXTUAL_REJECTION_POSITION_RE = re.compile(
    r"(?:지역|현장|운영|장기|질적).{0,55}(?:여건|맥락|효과|성과|특성)"
    r".{0,70}(?:반영|고려).{0,45}(?:않|없).{0,35}"
    r"(?:수용할\s*수\s*없|거부|반대|왜곡|부당)|"
    r"(?:지역|현장|운영|장기|질적).{0,55}(?:여건|맥락|효과|성과|특성)"
    r".{0,70}(?:반영|고려|예외|조정).{0,45}(?:요구|주장|입장)",
    re.IGNORECASE,
)
_FIXED_TOTAL_RESOURCE_RE = re.compile(
    r"(?:총|전체)?\s*(?:자원|예산|인력|재원).{0,35}"
    r"(?:동결|고정|제한|부족|늘릴\s*수\s*없|한정)",
    re.IGNORECASE,
)
_MULTIPLE_RESOURCE_DEMAND_RE = re.compile(
    r"(?:두|세|여러|모든|각)\s*(?:사업|부서|과제|기관).{0,55}"
    r"(?:요구|수요|증액|늘|증가)|"
    r"(?:요구|수요).{0,25}(?:모두|동시에).{0,25}(?:늘|증가|초과)",
    re.IGNORECASE,
)
_ALLOCATION_TRADEOFF_RE = re.compile(
    r"(?:줄이|삭감|감축|축소|배분|조정).{0,85}"
    r"(?:반발|지연|중단|미달|손실|불이익|의무).{0,45}(?:예상|발생|위험|우려)?",
    re.IGNORECASE,
)
_RELATIVE_RESOURCE_CLAIM_RE = re.compile(
    r"(?:사업|부서|과제|기관)(?:은|는|이|가).{0,95}?"
    r"(?:확대|증액|증원|현\s*수준\s*유지|유지|최소\s*지원|우선\s*지원)"
    r".{0,30}?(?:요청|요구)",
    re.IGNORECASE,
)
_RELATIVE_ALLOCATION_DECISION_RE = re.compile(
    r"(?:어느|어떤).{0,25}(?:사업|부서|과제|기관).{0,35}"
    r"(?:상대적으로\s*)?우선|"
    r"(?:상대적\s*)?우선순위.{0,35}(?:결정|선택|배분|조정)|"
    r"(?:상대적으로\s*)?(?:배분|지원).{0,35}(?:우선|순위)",
    re.IGNORECASE,
)
_RECORDED_VALUE_RE = re.compile(
    r"(?P<value>\d+(?:[.,]\d+)?|[공영일이삼사오육칠팔구십백천만억한두세네열스무]+)\s*"
    r"(?P<unit>%|퍼센트|명|건|원|일|개월|시간)?\s*(?:으로)?\s*"
    r"(?:적혀|기재|기록)",
    re.IGNORECASE,
)
_MULTI_SOURCE_VALUE_CONFLICT_RE = re.compile(
    r"(?:같은\s*)?(?:지표|수치|값|참여자\s*수|인원|건수|금액|집행액|성과값)"
    r"(?:이|가|은|는)?.{0,140}(?:마다|별로)\s*"
    r"(?:반복(?:적으로|해서|적)?)?\s*"
    r"(?:다르게\s*(?:나타|표시|집계)|다르(?:고|며))",
    re.IGNORECASE,
)
_DILEMMA_RE = re.compile(
    r"하지만|그러나|반면|그런데|동시에|충돌|상충|불일치|서로\s*다|둘\s*중|"
    r"(?:총)?사업비.{0,30}(?:같|고정)|"
    r"연락(?:이\s*)?되지|거부|제한|부족|기한|마감|예산|인력|위험|우선\s*제출|"
    r"함께\s*지키|모두\s*유지하기\s*어려운|"
    r"(?:기준|금액|일정|내용).{0,50}(?:기준|금액|일정|내용)(?:이|가|은|는|과|와)?\s*다르|"
    r"모두.{0,35}(?:반영|수용|유지).{0,15}(?:수\s*없|어려운)|고정되어.{0,40}(?:수\s*없|어려운)|"
    r"(?:기한|일정).{0,50}(?:신뢰|정확)|(?:신뢰|정확).{0,50}(?:기한|일정)|"
    r"(?:수요|요구).{0,50}(?:동결|부족|어려운)",
    re.IGNORECASE,
)

# A scenario can contain a concrete operational dilemma without saying
# "하지만" as a standalone conjunction.  These predicates require linked
# facts rather than treating a single anomaly, deadline, or stakeholder noun
# as sufficient.
_RESOURCE_OUTCOME_DIVERGENCE_RE = re.compile(
    r"(?:"
    r"(?:집행(?:액|률)?|투입(?:액|비|량)?|예산|비용|인력|처리량).{0,40}"
    r"(?:늘|증가|상승|높|초과|웃도|근접).{0,45}(?:지만|반면|으나|는데|동시에)"
    r".{0,70}(?:성과|산출(?:량|물)?|효과|달성률|품질).{0,40}"
    r"(?:급감|감소|하락|낮|줄|정체|미달)"
    r"|(?:성과|산출(?:량|물)?|효과|달성률|품질).{0,40}"
    r"(?:급감|감소|하락|낮|줄|정체|미달).{0,45}(?:지만|반면|으나|는데|동시에)"
    r".{0,70}(?:집행(?:액|률)?|투입(?:액|비|량)?|예산|비용|인력|처리량).{0,40}"
    r"(?:늘|증가|상승|높|초과|웃도|근접)"
    r")",
    re.IGNORECASE,
)
_DATA_QUALITY_DEFECT_RE = re.compile(
    r"(?:실적|성과|집계|값|데이터|자료|항목|측정).{0,50}"
    r"(?:누락|빠지|중복|불일치|오류|왜곡)|"
    r"(?:누락|빠지|중복|불일치|오류|왜곡).{0,50}"
    r"(?:실적|성과|집계|값|데이터|자료|항목|측정)",
    re.IGNORECASE,
)
_TIMELINESS_PRESSURE_RE = re.compile(
    r"(?:보고|제출|공개|마감|일정).{0,45}"
    r"(?:지연.{0,15}(?:허용하지|불가|어렵)|늦추지|임박|오늘|즉시|서둘|준수|지키)|"
    r"(?:지연.{0,15}(?:허용하지|불가|어렵)|늦추지|임박|오늘|즉시|서둘|지키)"
    r".{0,45}(?:보고|제출|공개|마감|일정)",
    re.IGNORECASE,
)
_STAKEHOLDER_PRESSURE_RE = re.compile(
    r"(?:경영진|부서|상급자|관계자|요청자|기관|현업).{0,85}"
    r"(?:유리|요구|요청|압박|반발|허용하지|강행|재촉|주장)",
    re.IGNORECASE,
)
_WORK_QUEUE_MULTIPLICITY_RE = re.compile(
    r"(?:두|세|여러|복수|\d+)\s*(?:건|개|종|가지|문서)|"
    r"동시에\s*(?:도착|접수|처리)|(?:문서|업무|요청).{0,25}(?:동시에|각각)",
    re.IGNORECASE,
)
_WORK_QUEUE_DEADLINE_RE = re.compile(
    r"오늘|내일|당일|오전|정오|오후|즉시|곧|마감|기한|까지",
    re.IGNORECASE,
)
_AUTHORITY_CONSTRAINT_RE = re.compile(
    r"(?:결재|승인|정정|처리).{0,25}권한.{0,35}(?:없|제한|상급자|다른)|"
    r"(?:결재자|승인권자|권한자|담당자|상급자).{0,45}"
    r"(?:부재|외부|복귀하지|연락되지)",
    re.IGNORECASE,
)
_CAPACITY_CONSTRAINT_RE = re.compile(
    r"(?:한|두|\d+)\s*(?:명|건|개).{0,35}(?:만|밖에|제한|투입)|"
    r"(?:한|두|\d+)\s*(?:건|개)(?:만)?\s*(?:맡|처리)",
    re.IGNORECASE,
)

_DEMAND_SENTENCE_RE = re.compile(
    r"[?？]|(?:말씀|설명|발표|제시|답변)(?:해|하여)\s*(?:주십시오|주세요)|"
    r"토론(?:해|하여)\s*(?:주십시오|주세요)|"
    r"(?:하|하시|되시)(?:겠습니까|시겠습니까)",
    re.IGNORECASE,
)
_DIRECT_DEMAND_CUE_RE = re.compile(
    r"(?:담당자|지원자|본인)(?:이|은|는)?\s*(?:직접\s*)?"
    r"(?=(?:무엇|어떤|어느|어떻게))|"
    r"(?:담당자|지원자|본인)(?:라면|이라면)|"
    r"무엇(?:을|를|으로|부터|인지)?|"
    r"어떤\s+[^\s,?.]{1,20}(?:을|를|으로|에|에게|와|과|인지)|"
    r"어느\s+[^\s,?.]{1,20}(?:을|를|으로|에|에서|인지)|"
    r"어떻게|왜|누구(?:에게|와|를|가)?|언제|얼마(?:나|를|가)?|"
    r"몇\s*(?:개|건|명|회)?",
    re.IGNORECASE,
)
_DEMAND_WH_RE = re.compile(
    r"무엇(?:을|를|으로|부터|인지)?|어떤|어느|어떻게|왜|"
    r"누구(?:에게|와|를|가)?|언제|얼마(?:나|를|가)?|몇\s*(?:개|건|명|회)?",
    re.IGNORECASE,
)
_DEMAND_SLOT_ACTION_RE = re.compile(
    r"확인|검토|대조|분석|비교|검증|파악|판단|결정|선택|승인|거절|"
    r"조정|배분|설계|대응|처리|설득|협의|조율|통보|회신|보고|요청|"
    r"기록|남기|작성|정리|점검|보완|수정|측정|계산|제시|설명|발표",
    re.IGNORECASE,
)
_DEMAND_NOMINAL_SLOT_RE = re.compile(
    r"확인\s*방법|점검\s*방안|실행\s*일정|최종\s*조정안|목표\s*수치|"
    r"목표값|우선순위|조정안|산식|이유|원자료|자료|근거|기준|수치|지표|"
    r"문구|항목|기록|산출물|일정|대안|방안|조치|위험|결과",
    re.IGNORECASE,
)
_DEMAND_FAMILY_PATTERNS: dict[str, re.Pattern[str]] = {
    "자료검토": re.compile(r"확인|검토|대조|분석|비교|검증|파악|살펴", re.IGNORECASE),
    "의사결정": re.compile(
        r"판단|결정|선택|우선순위|승인|거절|조정|배분|설계|대응|처리",
        re.IGNORECASE,
    ),
    "이해관계자": re.compile(
        r"설득|협의|조율|통보|회신|보고(?!서)|요청|소통|합의", re.IGNORECASE
    ),
    "기록": re.compile(r"기록|남기|작성|문서|산출물|정리", re.IGNORECASE),
    "후속": re.compile(
        r"후속|점검|모니터|보완|재검토|재조정|재배분|수정", re.IGNORECASE
    ),
    "계량": re.compile(
        r"수치|지표|산식|측정|계산|목표(?:값|치)|금액|정량|달성률|집행률",
        re.IGNORECASE,
    ),
}
_INDEPENDENT_DEMAND_OUTPUT_PATTERNS: dict[str, re.Pattern[str]] = {
    "fact_review": re.compile(r"확인할\s*(?:사실|자료)|검토할\s*(?:사실|자료)"),
    "decision_rule": re.compile(
        r"(?:공통|단일|하나의)\s*(?:판정|평가|적용)?\s*(?:규칙|기준|원칙)"
    ),
    "hold_or_revision": re.compile(r"(?:적용|결정|판정).{0,35}(?:보류|수정|변경)"),
    "joint_artifact": re.compile(
        r"(?:공동|합의)\s*(?:평가)?(?:원칙|기준)?안|(?:공동|합의)\s*(?:기록|결과서)"
    ),
    "accountability": re.compile(
        r"(?:결정|판단|선택).{0,70}(?:결과|영향).{0,45}(?:책임|감당)"
    ),
    "escalation": re.compile(
        r"(?:미합의|합의.{0,12}(?:실패|이르지\s*못)|남은\s*쟁점).{0,90}"
        r"(?:결정권자|승인권자|상급자).{0,35}(?:넘기|이송|보고)"
    ),
}

_METADATA_LABEL_FIELDS = (
    ("competency", "competency"),
    ("compeUnitName", "competency"),
    ("competency_name", "competency"),
    ("ncs_detail", "ncs_detail"),
    ("ncsSubdCdnm", "ncs_detail"),
    ("matchedDetailName", "ncs_detail"),
    ("question_focus", "question_focus"),
    ("question_focus_surface", "question_focus_surface"),
    ("factor", "factor"),
    ("factorName", "factor"),
    ("factor_name", "factor"),
    ("ksa_factor", "factor"),
    ("ksaFactor", "factor"),
    ("official_label", "official_label"),
    ("officialLabel", "official_label"),
    ("official_factor", "official_label"),
    ("officialFactor", "official_label"),
    ("official_ksa_label", "official_label"),
    ("officialKsaLabel", "official_label"),
    ("required_factorName", "official_label"),
    ("ksa_refs", "official_label"),
)
_GENERIC_METADATA_LABEL_KEYS = frozenset(
    {
        "업무",
        "직무",
        "해당업무",
        "해당직무",
        "핵심업무",
        "핵심역량",
        "핵심수행기준",
        "능력",
        "기술",
        "지식",
        "태도",
        "담당자",
        "과제",
    }
)
_NATURAL_APPLY_OBJECT_RE = re.compile(
    r"(?:법|법규|법령|규정|지침|매뉴얼|기준|원칙|정책|계약|도면|공식|계산식|"
    r"시스템|프로그램|소프트웨어|도구|장비|기법|방법론)$",
    re.IGNORECASE,
)
_NATURAL_TOOL_NAME_RE = re.compile(
    r"(?:Microsoft\s*)?(?:Excel|Word|PowerPoint)|Power\s*BI|"
    r"엑셀|워드|파워포인트|한글|한컴오피스|ERP|SAP|Jira|Notion|"
    r"Python|SQL|R|SAS|SPSS|Tableau",
    re.IGNORECASE,
)
_PUBLIC_TASK_OBJECT_RE = re.compile(r"(?:절차|기준)$", re.IGNORECASE)
_TAXONOMY_LABEL_SUFFIX_RE = re.compile(
    r"(?:능력|기술|지식|태도|자세|의지|개념|이해)$",
    re.IGNORECASE,
)


def _clean_text(value: Any, *, limit: int = 1000) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _normalize_method(value: Any) -> str:
    method = _clean_text(value, limit=80)
    key = re.sub(r"\s+", "", method).casefold()
    return _METHOD_ALIASES.get(method.casefold()) or _METHOD_ALIASES.get(key) or method


def _pattern_evidence(patterns: Sequence[re.Pattern[str]], text: str) -> list[str]:
    evidence: list[str] = []
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            found = _clean_text(match.group(0), limit=180)
            if found and found not in evidence:
                evidence.append(found)
    return evidence


def _instruction_injection_exposures(
    question: str,
    follow_up_texts: Sequence[str],
    evaluation_point_texts: Sequence[str],
) -> list[dict[str, str]]:
    """Locate explicit prompt-injection commands in candidate-visible copy."""

    segments = [
        ("question", question),
        *[(f"follow_ups[{index}]", text) for index, text in enumerate(follow_up_texts)],
        *[
            (f"evaluation_points[{index}]", text)
            for index, text in enumerate(evaluation_point_texts)
        ],
    ]
    exposures: list[dict[str, str]] = []
    seen: set[tuple[str, int, int, str]] = set()
    for location, text in segments:
        for pattern_name, pattern in _INSTRUCTION_INJECTION_ARTIFACT_PATTERNS:
            for match in pattern.finditer(text):
                key = (location, match.start(), match.end(), pattern_name)
                if key in seen:
                    continue
                seen.add(key)
                exposures.append(
                    {
                        "location": location,
                        "pattern": pattern_name,
                        "phrase": _clean_text(match.group(0), limit=220),
                    }
                )
    return exposures


def _follow_up_rows(value: Any) -> list[tuple[str, str]]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        values: Sequence[Any] = [value]
    elif isinstance(value, Sequence):
        values = value
    else:
        return []

    rows: list[tuple[str, str]] = []
    for raw in values:
        if isinstance(raw, Mapping):
            text = _clean_text(raw.get("question") or raw.get("text"), limit=300)
            condition = _clean_text(
                raw.get("ask_if") or raw.get("condition") or raw.get("when"),
                limit=200,
            )
        else:
            text = _clean_text(raw, limit=300)
            condition = ""
        if text:
            rows.append((text, condition))
    return rows


def _is_adaptive_follow_up(text: str, condition: str = "") -> bool:
    if _GENERIC_ANSWER_RESTATEMENT_RE.search(text):
        return False
    if (
        condition
        and _FOLLOW_UP_CONDITION_BRANCH_RE.search(condition)
        and any(
            pattern.search(condition)
            for pattern in _ADAPTIVE_FOLLOW_UP_CONDITION_PATTERNS
        )
    ):
        return True
    if any(pattern.search(text) for pattern in _ADAPTIVE_FOLLOW_UP_PATTERNS):
        return True
    if _INLINE_ANSWER_CONTENT_BRANCH_RE.search(
        text
    ) or _has_answer_alternative_decision_branch(text):
        return True
    return bool(
        _ANSWER_OWNED_DEICTIC_RE.search(text)
        and any(pattern.search(text) for pattern in _RELATIONAL_ANSWER_BRANCH_PATTERNS)
    )


def _source_from_item(item: Mapping[str, Any]) -> str:
    direct = item.get("question_source") or item.get("generation_source")
    if direct:
        return _clean_text(direct, limit=100)
    provenance = item.get("provenance")
    if isinstance(provenance, Mapping):
        return _clean_text(
            provenance.get("question_source") or provenance.get("source"),
            limit=100,
        )
    return ""


def _is_deterministic_source(source: str) -> bool:
    normalized = re.sub(r"[\s\-]+", "_", source).casefold()
    return bool(
        normalized
        and (
            normalized in DETERMINISTIC_QUESTION_SOURCES
            or "template" in normalized
            or "deterministic" in normalized
            or normalized.startswith("rule_fallback")
        )
    )


def _metadata_labels(item: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return distinct official labels and the metadata fields that supplied them."""

    labels_by_key: dict[str, dict[str, Any]] = {}
    for source_field, canonical_field in _METADATA_LABEL_FIELDS:
        raw_value = item.get(source_field)
        if isinstance(raw_value, Sequence) and not isinstance(raw_value, (str, bytes)):
            raw_labels: Sequence[Any] = raw_value
        else:
            raw_labels = [raw_value]
        for raw_label in raw_labels:
            if isinstance(raw_label, Mapping):
                continue
            label = _clean_text(raw_label, limit=180).strip(" \t\r\n'\"“”‘’[]{}")
            key = re.sub(r"[^0-9A-Za-z가-힣]+", "", label).casefold()
            if len(key) < 2 or key in _GENERIC_METADATA_LABEL_KEYS:
                continue
            if key not in labels_by_key:
                labels_by_key[key] = {
                    "label": label,
                    "metadata_fields": [canonical_field],
                }
            elif canonical_field not in labels_by_key[key]["metadata_fields"]:
                labels_by_key[key]["metadata_fields"].append(canonical_field)
    return list(labels_by_key.values())


def _flexible_label_pattern(label: str) -> str:
    # Metadata and model output do not always use the same whitespace or
    # middle-dot convention (for example, ``계획대비`` vs ``계획 대비``).
    # Match the exact alphanumeric label after that harmless normalization;
    # the boundary and the grammar-specific suffixes below still prevent
    # partial-token matches from becoming leaks.
    characters = re.findall(r"[0-9A-Za-z가-힣]", label)
    body = r"[^0-9A-Za-z가-힣]*".join(re.escape(char) for char in characters)
    return (
        r"(?<![0-9A-Za-z가-힣])"
        r"(?:['\"“”‘’]\s*)?"
        f"{body}"
        r"(?:\s*['\"“”‘’])?"
    )


def _is_natural_apply_object(label: str) -> bool:
    compact = re.sub(r"\s+", " ", label).strip()
    return bool(
        _NATURAL_APPLY_OBJECT_RE.search(compact)
        or _NATURAL_TOOL_NAME_RE.fullmatch(compact)
    )


def _is_natural_explanation_object(label: str) -> bool:
    compact = re.sub(r"\s+", " ", label).strip()
    return bool(
        _is_natural_apply_object(compact) or _PUBLIC_TASK_OBJECT_RE.search(compact)
    )


def _label_like_patterns(label: str) -> tuple[tuple[str, re.Pattern[str]], ...]:
    label_pattern = _flexible_label_pattern(label)
    patterns: list[tuple[str, re.Pattern[str]]] = [
        (
            "named_work_context",
            re.compile(
                label_pattern
                + r"\s*(?:관련\s*)?(?:업무|직무)\s*(?:에서|중|상|를|을|의|로서)",
                re.IGNORECASE,
            ),
        ),
        (
            "named_assignee_role",
            re.compile(
                label_pattern + r"\s*(?:업무\s*)?담당자(?:로서|라면|의\s*입장에서)",
                re.IGNORECASE,
            ),
        ),
        (
            "named_task_context",
            re.compile(
                label_pattern + r"\s*(?:관련\s*)?(?:과제|과업)(?:로|에서|를|를\s*수행)",
                re.IGNORECASE,
            ),
        ),
    ]
    # A raw taxonomy label can also be disguised as a seemingly open
    # definition question.  It is still the internal label, not a translation
    # into an incident, decision, action, or work product.  This catches
    # genitive/topic/object forms such as "<label>의 의미와 확인 기준" while
    # leaving ordinary names of statutes, regulations, tools, and generated
    # public task objects alone.
    if not _is_natural_explanation_object(label):
        patterns.append(
            (
                "named_label_explanation",
                re.compile(
                    label_pattern + r"\s*(?:"
                    r"(?:의|이라는|에\s*대한)\s*(?:의미|뜻|정의|개념|"
                    r"핵심\s*요소|구성\s*요소|요건|필요성|중요성|"
                    r"확인\s*기준|평가\s*기준|관찰\s*기준|판단\s*기준)"
                    r"|(?:은|는|이|가)\s*(?:무엇|어떤\s*(?:의미|뜻|기준)|"
                    r"어떻게\s*(?:이해|정의|확인|평가))"
                    r"|(?:을|를)\s*(?:설명|정의|확인|평가)"
                    r"|에\s*(?:대해|관해)\s*(?:설명|정의|확인|평가)"
                    r")",
                    re.IGNORECASE,
                ),
            )
        )
    # Applying a regulation, tool, or formula is ordinary work language.  In
    # contrast, applying an exact KSA/competency label ("... 능력을 적용") is
    # taxonomy leakage.  Keeping this lexical exception narrow prevents a
    # useful sentence such as "개인정보 보호 규정을 적용했다" from failing.
    if not _is_natural_apply_object(label):
        patterns.append(
            (
                "named_ksa_application",
                re.compile(
                    label_pattern + r"\s*(?:이라는\s*)?(?:을|를|이|가|과|와)?\s*"
                    r"(?:(?:직접|실제로)\s*)?(?:적용|활용|발휘)"
                    r"(?:해|하여|한|할|하는|했|하고|한다|했습니다|하겠)",
                    re.IGNORECASE,
                ),
            )
        )
    # Evaluation points are often short noun phrases, so no explanatory verb
    # follows the copied label.  Taxonomy-shaped labels are unsafe even in
    # that bare form.  This narrow suffix gate deliberately excludes ordinary
    # competency/task names, statutes, tools, and public objects ending in
    # ``절차`` or ``기준``.
    if _TAXONOMY_LABEL_SUFFIX_RE.search(re.sub(r"\s+", " ", label).strip()):
        patterns.append(
            (
                "exact_taxonomy_label",
                re.compile(
                    label_pattern
                    + r"(?=$|[^0-9A-Za-z가-힣]|(?:은|는|이|가|을|를|의|에|과|와|도|만|로|으로|에서))",
                    re.IGNORECASE,
                ),
            )
        )
    return tuple(patterns)


def _label_like_metadata_exposures(
    item: Mapping[str, Any],
    question: str,
    follow_up_texts: Sequence[str],
    evaluation_point_texts: Sequence[str],
) -> list[dict[str, Any]]:
    segments = [
        ("question", question),
        *[(f"follow_ups[{index}]", text) for index, text in enumerate(follow_up_texts)],
        *[
            (f"evaluation_points[{index}]", text)
            for index, text in enumerate(evaluation_point_texts)
        ],
    ]
    exposures: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, str]] = set()
    occupied_spans: dict[tuple[str, str], list[tuple[int, int]]] = {}
    for label_row in _metadata_labels(item):
        label = str(label_row["label"])
        label_key = re.sub(r"[^0-9A-Za-z가-힣]+", "", label).casefold()
        for pattern_name, pattern in _label_like_patterns(label):
            for location, text in segments:
                for match in pattern.finditer(text):
                    location_key = (label_key, location)
                    if any(
                        match.start() < end and match.end() > start
                        for start, end in occupied_spans.get(location_key, [])
                    ):
                        continue
                    exposure_key = (label_key, location, match.start(), pattern_name)
                    if exposure_key in seen:
                        continue
                    seen.add(exposure_key)
                    occupied_spans.setdefault(location_key, []).append(match.span())
                    exposures.append(
                        {
                            "metadata_fields": list(label_row["metadata_fields"]),
                            "label": label,
                            "location": location,
                            "pattern": pattern_name,
                            "phrase": _clean_text(match.group(0), limit=220),
                        }
                    )
    return exposures


def _scenario_stimulus(item: Mapping[str, Any], question: str) -> str:
    parts = [question]
    for key in (
        "scenario",
        "scenario_text",
        "task_prompt",
        "stimulus",
        "case_text",
        "materials_summary",
    ):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value)
    return _clean_text(" ".join(parts), limit=2400)


def _response_demand_text(question: str) -> str:
    """Return only the candidate-facing request portion of a long prompt."""

    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?？])\s+", question)
        if sentence.strip()
    ]
    first_demand_index = next(
        (
            index
            for index, sentence in enumerate(sentences)
            if _DEMAND_SENTENCE_RE.search(sentence)
        ),
        None,
    )
    if first_demand_index is None:
        return ""

    first_sentence = sentences[first_demand_index]
    cue = _DIRECT_DEMAND_CUE_RE.search(first_sentence)
    if cue:
        first_sentence = first_sentence[cue.start() :]
    return _clean_text(
        " ".join([first_sentence, *sentences[first_demand_index + 1 :]]),
        limit=1600,
    )


def _demand_overload_metrics(question: str) -> dict[str, int | bool]:
    demand_text = _response_demand_text(question)
    slot_keys: set[str] = set()
    for match in _DEMAND_WH_RE.finditer(demand_text):
        cue = re.sub(r"[^가-힣A-Za-z]+", "", match.group(0)).casefold()
        tail = demand_text[match.end() : match.end() + 50]
        action = _DEMAND_SLOT_ACTION_RE.search(tail)
        if action:
            slot_keys.add(f"{cue}:{action.group(0).casefold()}")
            continue
        noun = re.search(r"\s*([가-힣A-Za-z0-9·]{2,20})", tail)
        slot_keys.add(f"{cue}:{noun.group(1).casefold() if noun else ''}")

    nominal_slot_keys = {
        re.sub(r"\s+", "", match.group(0)).casefold()
        for match in _DEMAND_NOMINAL_SLOT_RE.finditer(demand_text)
    }
    families = {
        family
        for family, pattern in _DEMAND_FAMILY_PATTERNS.items()
        if pattern.search(demand_text)
    }
    independent_outputs = {
        output
        for output, pattern in _INDEPENDENT_DEMAND_OUTPUT_PATTERNS.items()
        if pattern.search(demand_text)
    }
    # Some natural Korean prompts enumerate requested deliverables immediately
    # before "발표해 주십시오" without an overt 무엇/어떻게.  The larger of
    # the explicit WH slots and unique requested nouns captures both forms
    # without double-counting "어떤 자료를 확인" as two demands.
    demand_slot_count = max(len(slot_keys), len(nominal_slot_keys))
    demand_family_count = len(families)
    response_demand_length = len(demand_text)
    independent_output_count = len(independent_outputs)
    dense_serial_warning = bool(
        len(question) >= 320
        and response_demand_length >= 150
        and demand_family_count >= 2
        and independent_output_count >= 4
    )
    return {
        "demand_slot_count": demand_slot_count,
        "demand_family_count": demand_family_count,
        "response_demand_length": response_demand_length,
        "independent_output_count": independent_output_count,
        # Shadow-only high-precision signal.  Repeated verbs within one family
        # (for example 확인/검토/대조) collapse before this threshold is applied.
        "overload_warning": bool(
            (demand_slot_count >= 5 and demand_family_count >= 4)
            or dense_serial_warning
        ),
    }


def _has_competing_causal_claims(text: str) -> bool:
    causes = {
        re.sub(
            r"(?:을|를)$", "", re.sub(r"[^0-9A-Za-z가-힣]+", "", match.group("cause"))
        )
        for match in _CAUSAL_CLAIM_RE.finditer(text)
    }
    causes.discard("")
    return len(causes) >= 2


def _has_conflicting_recorded_values(text: str) -> bool:
    values = {
        (
            re.sub(r"[^0-9A-Za-z가-힣.%]+", "", match.group("value")).casefold(),
            re.sub(r"\s+", "", match.group("unit") or "").casefold(),
        )
        for match in _RECORDED_VALUE_RE.finditer(text)
    }
    return len(values) >= 2


def _has_data_quality_pressure(text: str) -> bool:
    """Return whether a data defect is coupled to time and stakeholder pressure."""

    return bool(
        _DATA_QUALITY_DEFECT_RE.search(text)
        and _TIMELINESS_PRESSURE_RE.search(text)
        and _STAKEHOLDER_PRESSURE_RE.search(text)
    )


def _has_constrained_work_queue(text: str) -> bool:
    """Return whether multiple urgent items exceed the available authority/capacity."""

    deadline_mentions = len(_WORK_QUEUE_DEADLINE_RE.findall(text))
    return bool(
        _WORK_QUEUE_MULTIPLICITY_RE.search(text)
        and deadline_mentions >= 2
        and _AUTHORITY_CONSTRAINT_RE.search(text)
        and _CAPACITY_CONSTRAINT_RE.search(text)
    )


def _has_competing_policy_positions(text: str) -> bool:
    """Return whether two policy positions conflict under an operational pressure."""

    return bool(
        (
            (
                _STRICT_COMMON_RULE_POSITION_RE.search(text)
                and _CONTEXT_EXCEPTION_POSITION_RE.search(text)
            )
            or (
                _COMMON_QUANTITATIVE_RULE_STANCE_RE.search(text)
                and _QUALITATIVE_CONTEXT_STANCE_RE.search(text)
            )
        )
        and _EXPLICIT_POSITION_CONTRAST_RE.search(text)
        and (
            _TIMELINESS_PRESSURE_RE.search(text)
            or _STAKEHOLDER_PRESSURE_RE.search(text)
        )
    )


def _has_competing_delivery_positions(text: str) -> bool:
    """Return whether early partial delivery conflicts with verify-before-delivery."""

    return bool(
        (
            _PARTIAL_DELIVERY_POSITION_RE.search(text)
            and _VERIFIED_DELIVERY_POSITION_RE.search(text)
        )
        or (
            _CURRENT_MATERIAL_FIRST_POSITION_RE.search(text)
            and _VERIFY_BEFORE_OFFICIAL_USE_POSITION_RE.search(text)
        )
    ) and bool(_TIMELINESS_PRESSURE_RE.search(text))


def _has_quantitative_context_positions(text: str) -> bool:
    """Return whether schedule-led numeric evaluation opposes contextual review."""

    return bool(
        len(re.findall(r"입장", text)) >= 2
        and _QUANTITATIVE_PRIORITY_POSITION_RE.search(text)
        and _CONTEXTUAL_REJECTION_POSITION_RE.search(text)
        and (
            _TIMELINESS_PRESSURE_RE.search(text)
            or _STAKEHOLDER_PRESSURE_RE.search(text)
        )
    )


def _has_constrained_resource_allocation(text: str) -> bool:
    """Return whether fixed resources force a consequential choice among demands."""

    relative_claim_count = len(_RELATIVE_RESOURCE_CLAIM_RE.findall(text))
    relative_allocation = bool(
        relative_claim_count >= 2 and _RELATIVE_ALLOCATION_DECISION_RE.search(text)
    )
    return bool(
        _FIXED_TOTAL_RESOURCE_RE.search(text)
        and (
            (
                _MULTIPLE_RESOURCE_DEMAND_RE.search(text)
                and _ALLOCATION_TRADEOFF_RE.search(text)
            )
            or relative_allocation
        )
    )


def _scenario_signals(text: str) -> dict[str, Any]:
    artifacts = list(
        dict.fromkeys(match.group(0) for match in _ARTIFACT_RE.finditer(text))
    )
    quantified = bool(_QUANTIFIED_FACT_RE.search(text))
    multi_source_conflict = bool(_MULTI_SOURCE_VALUE_CONFLICT_RE.search(text))
    incident = bool(_INCIDENT_RE.search(text) or multi_source_conflict)
    resource_outcome_divergence = bool(_RESOURCE_OUTCOME_DIVERGENCE_RE.search(text))
    data_quality_pressure = _has_data_quality_pressure(text)
    constrained_work_queue = _has_constrained_work_queue(text)
    competing_policy_positions = _has_competing_policy_positions(text)
    competing_delivery_positions = _has_competing_delivery_positions(text)
    quantitative_context_positions = _has_quantitative_context_positions(text)
    constrained_resource_allocation = _has_constrained_resource_allocation(text)
    measurement_continuity_tradeoff = bool(
        _MEASUREMENT_CONTINUITY_TRADEOFF_RE.search(text)
    )
    relational_dilemma = bool(
        resource_outcome_divergence
        or data_quality_pressure
        or constrained_work_queue
        or competing_policy_positions
        or competing_delivery_positions
        or quantitative_context_positions
        or constrained_resource_allocation
        or measurement_continuity_tradeoff
    )
    opposing_stakeholders = bool(
        _OPPOSING_STAKEHOLDER_DILEMMA_RE.search(text)
        or _VERIFICATION_VS_SCHEDULE_POSITION_RE.search(text)
        or _PRIORITY_VS_FULL_EVIDENCE_POSITION_RE.search(text)
        or _QUANTITATIVE_VS_CONTEXT_POSITION_RE.search(text)
        or _has_competing_causal_claims(text)
        or _COMPETING_EVALUATION_STANCES_RE.search(text)
        or competing_policy_positions
        or competing_delivery_positions
        or quantitative_context_positions
    )
    actor_action = bool(_ACTOR_ACTION_RE.search(text) or opposing_stakeholders)
    dilemma = bool(
        _DILEMMA_RE.search(text)
        or opposing_stakeholders
        or _has_conflicting_recorded_values(text)
        or multi_source_conflict
        or relational_dilemma
    )
    concrete_fact = bool(
        (quantified and (incident or artifacts))
        or actor_action
        or (len(artifacts) >= 2 and incident)
        or relational_dilemma
    )
    return {
        "quantified_fact": quantified,
        "incident": incident,
        "actor_action": actor_action,
        "artifact_count": len(artifacts),
        "dilemma": dilemma,
        "concrete_fact": concrete_fact,
    }


def _issue(
    code: str, field: str, message: str, evidence: Sequence[str]
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": "error",
        "field": field,
        "message": message,
        "evidence": list(
            dict.fromkeys(_clean_text(value, limit=220) for value in evidence if value)
        )[:5],
    }


def _has_personal_cost_acceptance(text: str) -> bool:
    return bool(
        _PERSONAL_COST_OBJECT_RE.search(text)
        and _MANDATED_COST_ACCEPTANCE_RE.search(text)
    )


def _leading_or_assumptive_exposures(
    question: str,
    follow_ups: Sequence[str],
    evaluation_points: Sequence[str],
    *,
    method: str,
) -> list[str]:
    """Find answer-direction signals that only become invalid in combination.

    Interview questions may legitimately ask about a difficult choice, a cost,
    direct work, or accountability.  They become leading when the prompt makes
    the candidate personally accept all of those before giving an answer.  A
    behavioral prompt also needs an alternative route when it presupposes that
    exact costly event.  This intentionally avoids case identifiers and exact
    generated-question strings so paraphrases receive the same decision.
    """

    exposures: list[str] = []
    fallback_available = any(
        _EXPERIENCE_FALLBACK_RE.search(text)
        for text in (question, *follow_ups)
        if text
    )
    personal_cost = _has_personal_cost_acceptance(question)
    direct_action = bool(_CANDIDATE_DIRECT_ACTION_RE.search(question))
    personal_liability = bool(_PERSONAL_OUTCOME_LIABILITY_RE.search(question))
    forced_decision = bool(_CANDIDATE_FORCED_DECISION_RE.search(question))
    direct_enforcement = bool(_DIRECT_ENFORCEMENT_RE.search(question))
    explicit_liability_precondition = bool(
        _EXPLICIT_PERSONAL_LIABILITY_PRECONDITION_RE.search(question)
    )

    forced_costly_personal_answer = bool(
        explicit_liability_precondition
        or (
            personal_cost
            and (
                (direct_action and personal_liability)
                or (forced_decision and direct_enforcement)
            )
        )
    )
    # A conditional or hypothetical fallback makes a tightly framed experience
    # question answerable by candidates who did not encounter the exact event.
    # It does not cure a one-sided ethics question that has already declared
    # restriction/refusal to be the required past action.
    if forced_costly_personal_answer and not (
        method == "경험면접" and fallback_available
    ):
        exposures.append(
            "forced_personal_cost_action_liability | question: " + question
        )

    if (
        method == "경험면접"
        and personal_cost
        and _FORCED_COSTLY_DISCOVERY_EXPERIENCE_RE.search(question)
        and not fallback_available
        and not forced_costly_personal_answer
    ):
        exposures.append("presumed_costly_discovery | question: " + question)

    one_sided_data_control = bool(
        method == "경험면접"
        and _ONE_SIDED_DATA_CONTROL_EXPERIENCE_RE.search(question)
        and not _OPEN_DATA_USE_CHOICE_RE.search(question)
    )
    if one_sided_data_control:
        exposures.append("one_sided_ethical_experience | question: " + question)

    if (
        _AUTHORITY_CONSTRAINT_RE.search(question)
        and _CANDIDATE_PRIVILEGED_DIRECT_ACTION_RE.search(question)
    ):
        exposures.append("forced_action_beyond_authority | question: " + question)

    evaluation_text = " ".join(evaluation_points)
    causal_direction = bool(
        _COINCIDENT_CORRELATE_RE.search(question)
        and _FORCED_SINGLE_CAUSE_RE.search(question)
        and not _CAUSAL_UNCERTAINTY_RE.search(question + " " + evaluation_text)
    )
    if causal_direction:
        exposures.append("cooccurrence_prescribed_as_cause | question/evaluation_points")

    for index, text in enumerate(follow_ups):
        if _FOLLOW_UP_PREACCEPTED_PERSONAL_COST_RE.search(text):
            exposures.append(f"preaccepted_personal_cost | follow_ups[{index}]: {text}")

    for index, text in enumerate(evaluation_points):
        if (
            _has_personal_cost_acceptance(text)
            and _DIRECT_ENFORCEMENT_RE.search(text)
            and re.search(r"(?:결과\s*)?책임", text, re.IGNORECASE)
        ):
            exposures.append(
                f"personal_sacrifice_scoring_key | evaluation_points[{index}]: {text}"
            )

    return list(dict.fromkeys(exposures))


def evaluate_question_realism(
    item: Mapping[str, Any] | str,
    *,
    method: str | None = None,
    follow_ups: Sequence[Any] | str | None = None,
    question_source: str | None = None,
) -> dict[str, Any]:
    """Evaluate whether one question is ready to be used by a real panel.

    ``item`` may be the existing interview-question dictionary or just the
    question string.  Keyword arguments override values from the dictionary,
    which makes the function convenient both at generation time and in an
    offline corpus audit.  The input is never mutated.
    """

    if isinstance(item, Mapping):
        source_item: Mapping[str, Any] = item
        question = _clean_text(
            item.get("question") or item.get("question_text"), limit=3000
        )
        resolved_method = _normalize_method(
            method if method is not None else item.get("type") or item.get("method")
        )
        raw_follow_ups: Any = (
            follow_ups if follow_ups is not None else item.get("follow_ups")
        )
        if raw_follow_ups is None and item.get("follow_up"):
            raw_follow_ups = [item.get("follow_up")]
        source = _clean_text(
            question_source if question_source is not None else _source_from_item(item),
            limit=100,
        )
    else:
        source_item = {}
        question = _clean_text(item, limit=3000)
        resolved_method = _normalize_method(method)
        raw_follow_ups = follow_ups
        source = _clean_text(question_source, limit=100)

    follow_up_rows = _follow_up_rows(raw_follow_ups)
    evaluation_point_rows = _follow_up_rows(source_item.get("evaluation_points"))
    issues: list[dict[str, Any]] = []

    follow_up_texts = [text for text, _condition in follow_up_rows]
    evaluation_point_texts = [text for text, _condition in evaluation_point_rows]
    visible_text = "\n".join([question, *follow_up_texts])
    instruction_injection_exposures = _instruction_injection_exposures(
        question,
        follow_up_texts,
        evaluation_point_texts,
    )
    label_exposures = _label_like_metadata_exposures(
        source_item,
        question,
        follow_up_texts,
        evaluation_point_texts,
    )
    demand_metrics = _demand_overload_metrics(question)

    scaffold_evidence = _pattern_evidence(_GENERIC_SCAFFOLD_PATTERNS, visible_text)
    if resolved_method == "토론면접" and re.match(
        r"^\s*\[토론과제\]", question, re.IGNORECASE
    ):
        discussion_signals = _scenario_signals(
            _scenario_stimulus(source_item, question)
        )
        if (
            discussion_signals["concrete_fact"]
            and discussion_signals["dilemma"]
            and (
                discussion_signals["artifact_count"] >= 1
                or _has_competing_policy_positions(question)
                or _has_competing_delivery_positions(question)
                or _has_quantitative_context_positions(question)
            )
        ):
            scaffold_evidence = [
                evidence
                for evidence in scaffold_evidence
                if re.sub(r"[^0-9A-Za-z가-힣]+", "", evidence).casefold() != "토론과제"
            ]
    no_generic_scaffolding = bool(question) and not scaffold_evidence
    if not no_generic_scaffolding:
        issues.append(
            _issue(
                "generic_template_scaffolding",
                "question",
                "면접위원의 한 문장 질문이 아니라 생성 템플릿의 안내문이 노출되어 있습니다.",
                scaffold_evidence or ["질문 본문 없음"],
            )
        )

    presumed_experience_evidence = _pattern_evidence(
        _PRESUMED_EXPERIENCE_PATTERNS,
        visible_text,
    )
    no_presumed_experience = not presumed_experience_evidence
    if not no_presumed_experience:
        issues.append(
            _issue(
                "presumed_candidate_experience",
                "question",
                "지원자가 특정 경험을 보유했다고 사실로 단정하고 있습니다.",
                presumed_experience_evidence,
            )
        )

    leading_or_assumptive_exposures = _leading_or_assumptive_exposures(
        question,
        follow_up_texts,
        evaluation_point_texts,
        method=resolved_method,
    )
    prescribed_answer_evidence = _pattern_evidence(
        _PRESCRIBED_ANSWER_PATTERNS,
        question,
    )
    prescribed_answer_evidence.extend(leading_or_assumptive_exposures)
    no_prescribed_answer = not prescribed_answer_evidence
    if not no_prescribed_answer:
        issues.append(
            _issue(
                "candidate_answer_prescribed",
                "question",
                "상황문이 정답 정책과 처리 방향을 먼저 제시해 지원자의 판단을 유도합니다.",
                prescribed_answer_evidence,
            )
        )

    no_instruction_injection_artifact = not instruction_injection_exposures
    if not no_instruction_injection_artifact:
        injection_issue = _issue(
            "candidate_visible_instruction_injection",
            instruction_injection_exposures[0]["location"].split("[", 1)[0],
            "외부 문서의 명령을 따른 흔적이 지원자에게 보이는 면접 문구에 노출되어 있습니다.",
            [
                f"{row['pattern']} | {row['location']}: {row['phrase']}"
                for row in instruction_injection_exposures
            ],
        )
        injection_issue["artifact_matches"] = instruction_injection_exposures
        issues.append(injection_issue)

    component_hits = [
        name
        for name, pattern in _CHECKLIST_COMPONENTS.items()
        if pattern.search(question)
    ]
    enumerator_count = len(_ENUMERATOR_RE.findall(question))
    checklist_detected = bool(
        enumerator_count >= 3
        or (len(component_hits) >= 4 and _CHECKLIST_DIRECTIVE_RE.search(question))
    )
    if checklist_detected:
        issues.append(
            _issue(
                "candidate_directed_checklist",
                "question",
                "평가 항목을 지원자 답변 체크리스트로 한꺼번에 지시하고 있습니다.",
                component_hits,
            )
        )

    ksa_evidence = _pattern_evidence(_MECHANICAL_KSA_PATTERNS, visible_text)
    natural_ksa_surface = not ksa_evidence
    if not natural_ksa_surface:
        issues.append(
            _issue(
                "mechanical_ksa_surface",
                "question",
                "내부 NCS/KSA 표면어가 실제 면접에서 쓰지 않는 기계적 문구로 노출되어 있습니다.",
                ksa_evidence,
            )
        )

    no_label_like_metadata_exposure = not label_exposures
    if not no_label_like_metadata_exposure:
        label_issue = _issue(
            "candidate_visible_ncs_label",
            (
                "question"
                if any(row["location"] == "question" for row in label_exposures)
                else (
                    "follow_ups"
                    if any(
                        row["location"].startswith("follow_ups[")
                        for row in label_exposures
                    )
                    else "evaluation_points"
                )
            ),
            "NCS 능력단위·세분류·KSA 명칭을 실제 업무 맥락처럼 지원자 질문에 삽입했습니다.",
            [
                f"{','.join(row['metadata_fields'])}={row['label']} | "
                f"{row['location']}: {row['phrase']}"
                for row in label_exposures
            ],
        )
        label_issue["metadata_matches"] = label_exposures
        issues.append(label_issue)

    non_adaptive_follow_ups = [
        text
        for text, condition in follow_up_rows
        if not _is_adaptive_follow_up(text, condition)
    ]
    follow_up_keys = [re.sub(r"\s+", "", text).casefold() for text, _ in follow_up_rows]
    duplicate_follow_ups = [
        text
        for (text, _), key in zip(follow_up_rows, follow_up_keys)
        if follow_up_keys.count(key) > 1
    ]
    adaptive_follow_up_count = len(follow_up_rows) - len(non_adaptive_follow_ups)
    # A structured interview benefits from one standardized verification probe
    # that every candidate receives.  Require at least two thirds to branch
    # from the candidate's answer instead of rewarding the cosmetic repetition
    # of "말씀하신" in every line.
    required_adaptive_follow_up_count = (
        (2 * len(follow_up_rows) + 2) // 3 if follow_up_rows else 0
    )
    answer_adaptive_follow_ups = bool(
        adaptive_follow_up_count >= required_adaptive_follow_up_count
        and not duplicate_follow_ups
    )
    if not answer_adaptive_follow_ups:
        issues.append(
            _issue(
                "non_adaptive_follow_ups",
                "follow_ups",
                "꼬리질문의 3분의 2 이상은 지원자의 직전 답변을 받아 깊어져야 합니다.",
                [*non_adaptive_follow_ups, *duplicate_follow_ups],
            )
        )

    scenario_applicable = resolved_method in _TASK_METHODS
    scenario = _scenario_stimulus(source_item, question)
    scenario_signals = (
        _scenario_signals(scenario)
        if scenario_applicable
        else {
            "quantified_fact": False,
            "incident": False,
            "actor_action": False,
            "artifact_count": 0,
            "dilemma": False,
            "concrete_fact": False,
        }
    )
    concrete_scenario = bool(
        not scenario_applicable
        or (scenario_signals["concrete_fact"] and scenario_signals["dilemma"])
    )
    if not concrete_scenario:
        present_signals = [
            name
            for name in ("quantified_fact", "incident", "actor_action", "dilemma")
            if scenario_signals[name]
        ]
        present_signals.append(f"artifact_count={scenario_signals['artifact_count']}")
        issues.append(
            _issue(
                "missing_concrete_event_or_dilemma",
                "question",
                "상황·과제형 질문에 판단을 갈라놓을 구체 사건과 제약 조건이 없습니다.",
                [f"method={resolved_method}", *present_signals],
            )
        )

    deterministic_provenance = _is_deterministic_source(source)
    if deterministic_provenance:
        issues.append(
            _issue(
                "raw_deterministic_provenance",
                "question_source",
                "템플릿·규칙 기반 원문은 현장성 검토 없이 ready 상태가 될 수 없습니다.",
                [source],
            )
        )

    checks = {
        "no_generic_template_scaffolding": no_generic_scaffolding,
        "no_candidate_checklist": not checklist_detected,
        "natural_ksa_surface": natural_ksa_surface,
        "answer_adaptive_follow_ups": answer_adaptive_follow_ups,
        "concrete_scenario": concrete_scenario,
        "not_raw_deterministic_provenance": not deterministic_provenance,
        "no_label_like_metadata_exposure": no_label_like_metadata_exposure,
        "no_presumed_experience": no_presumed_experience,
        "no_prescribed_answer": no_prescribed_answer,
        "no_instruction_injection_artifact": no_instruction_injection_artifact,
    }
    score = max(
        0,
        100
        - sum(weight for check, weight in CHECK_WEIGHTS.items() if not checks[check]),
    )
    issue_codes = [issue["code"] for issue in issues]

    return {
        "policy_version": REALISM_POLICY_VERSION,
        "passed": all(checks.values()),
        "score": score,
        "checks": checks,
        "issues": issues,
        "issue_codes": issue_codes,
        "applicable_checks": [
            check
            for check in checks
            if check != "concrete_scenario" or scenario_applicable
        ],
        "metrics": {
            "question_chars": len(question),
            "follow_up_count": len(follow_up_rows),
            "adaptive_follow_up_count": adaptive_follow_up_count,
            "required_adaptive_follow_up_count": required_adaptive_follow_up_count,
            "non_adaptive_follow_up_count": len(non_adaptive_follow_ups),
            "duplicate_follow_up_count": len(duplicate_follow_ups),
            "checklist_component_count": len(component_hits),
            "label_like_metadata_exposure_count": len(label_exposures),
            "instruction_injection_artifact_count": len(
                instruction_injection_exposures
            ),
            "leading_or_assumptive_exposure_count": len(
                leading_or_assumptive_exposures
            ),
            "scenario_signals": scenario_signals,
            **demand_metrics,
        },
    }


def evaluate_question_set_realism(items: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate field-realism results for a generated question set."""

    results = [evaluate_question_realism(item) for item in items]
    issue_counts = Counter(code for result in results for code in result["issue_codes"])
    checks = {
        check: bool(results) and all(result["checks"][check] for result in results)
        for check in CHECK_WEIGHTS
    }
    return {
        "policy_version": REALISM_POLICY_VERSION,
        "passed": bool(results) and all(result["passed"] for result in results),
        "score": round(sum(result["score"] for result in results) / len(results), 1)
        if results
        else 0.0,
        "checks": checks,
        "issue_counts": dict(sorted(issue_counts.items())),
        "items": results,
    }


# ``assess`` reads naturally at an integration call site while keeping the
# more explicit ``evaluate`` name for corpus tooling.
assess_question_realism = evaluate_question_realism


__all__ = [
    "CHECK_WEIGHTS",
    "DETERMINISTIC_QUESTION_SOURCES",
    "REALISM_POLICY_VERSION",
    "assess_question_realism",
    "evaluate_question_realism",
    "evaluate_question_set_realism",
]
