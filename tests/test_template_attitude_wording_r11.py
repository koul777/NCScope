"""Template and attitude wording left over from the R9 panel review.

Official factor names and evidence ids stay as they are; these tests pin the
candidate-facing surface, the situation/discussion/in-basket/creative stems
and the scenario chosen for the job.
"""

from __future__ import annotations

import json

import pytest

from app.main import _domain_context_pack, _followups_for_method, _question_for_method
from app.services.question_surface import public_task_object

_AWKWARD_NOUN_ENDINGS = (
    "사고 행동",
    "숙지 행동",
    "달성 행동",
    "판단력 행동",
    "관찰력 행동",
    "대처 행동",
)


@pytest.mark.parametrize(
    ("factor", "expected"),
    [
        ("도면 숙지 의지", "도면을 숙지하려는 행동"),
        ("패션소품 정보 숙지 태도", "패션소품 정보를 숙지하는 행동"),
        ("규정 및 과정의 정확한 숙지", "규정 및 과정을 정확하게 숙지하는 행동"),
        ("원부자재에 대한 시험방법 및 합격기준 숙지 노력", "원부자재에 대한 시험방법 및 합격기준을 숙지하려는 행동"),
        ("성능 목표치 달성의지", "성능 목표치를 달성하려는 행동"),
        (
            "작품유통 채널별 특성에 따른 유통목표 달성 의지",
            "작품유통 채널별 특성에 따른 유통목표를 달성하려는 행동",
        ),
        ("거래 유형에 대한 정확한 판단력", "거래 유형을 정확하게 판단하는 행동"),
        ("시운전 결과에 따른 정확한 판단력", "시운전 결과에 따라 정확하게 판단하는 행동"),
        ("세심하고 주의 깊은 관찰력", "세심하고 주의 깊게 관찰하는 행동"),
        ("참여자에 대한 관찰력", "참여자를 관찰하는 행동"),
        ("시설관리시 상태파악을 위한 관찰력", "시설관리시 상태파악을 위해 관찰하는 행동"),
        ("위험요소 대처능력", "위험요소에 대처하는 행동"),
        ("비상 시 적극적인 대처 의지", "비상 시 적극적으로 대처하려는 행동"),
        (
            "치매와 동반된 불의의 사고에 대한 적극적인 대처",
            "치매와 동반된 불의의 사고에 적극적으로 대처하는 행동",
        ),
        ("합리적 계획수립을 위한 논리적 사고", "합리적 계획수립을 위해 논리적으로 사고하는 행동"),
        ("문화에 대한 긍정적인 사고", "문화에 대해 긍정적으로 사고하는 행동"),
        ("목표 지향적 사고", "목표 지향적으로 사고하는 행동"),
        ("점검 목적에 따른 분석적 사고", "점검 목적에 따라 분석적으로 사고하는 행동"),
        ("위기상황에 대한 신속하고 정확한 판단력", "위기상황을 신속하고 정확하게 판단하는 행동"),
        ("어린이놀이시설에 대한 주의깊은 관찰력", "어린이놀이시설을 주의깊게 관찰하는 행동"),
        ("공정별 운영 요령을 숙지", "공정별 운영 요령을 숙지하는 행동"),
    ],
)
def test_verbal_attitude_noun_reads_as_its_own_verb(factor: str, expected: str) -> None:
    surface, source = public_task_object(factor_name=factor, ksa_type="태도")

    assert surface == expected
    assert source == "factor_repair"
    assert not surface.endswith(_AWKWARD_NOUN_ENDINGS), surface
    assert " 있는 행동" not in surface
    # The verb comes from the official noun; no other act is added.
    for invented in ("실행", "준수", "발휘", "공유", "실천"):
        assert invented not in surface


@pytest.mark.parametrize(
    "factor",
    [
        # Coordinated nouns: only one of them would become a verb.
        "적극적 사고와 빠른 판단력",
        "원자재 성분 검사절차에 대한 이해 및 숙지의지",
        "결단력/판단력/관찰력",
        # A verb clause modifies the noun; turning it into a verb breaks it.
        "공사업무를 총괄하는 관리자적 사고",
        "조사된 내용을 객관적으로 이해하는 판단력",
        # A bare ``사고`` may mean an accident.
        "국민의 생명과 안전을 확보한다는 인명존중의 사고",
        # ``할 수 있는`` is a capability clause, not an adverb.
        "필요정보를 선별할 수 있는 판단력",
        "작은 변화도 포착할 수 있는 관찰력",
    ],
)
def test_verbal_attitude_noun_without_a_safe_reading_is_left_as_written(factor: str) -> None:
    surface, _ = public_task_object(factor_name=factor, ksa_type="태도")
    stripped = factor.replace("숙지의지", "숙지")

    assert surface == f"{stripped} 행동", surface
    assert "있는 행동" not in surface
    assert "할을" not in surface and "수 있게" not in surface


ATTITUDE_INTENT_FACTOR = "원활한 커뮤니케이션을 하고자 하는 적극적인 태도"


@pytest.mark.parametrize("method", ("상황면접", "토론면접"))
def test_attitude_stem_does_not_stack_practice_on_an_intention(method: str) -> None:
    question = _question_for_method(
        method,
        "고객 상담",
        ATTITUDE_INTENT_FACTOR,
        "사무행정",
        "고객 요청을 접수해 처리한다.",
        "태도",
    )

    assert "원활한 커뮤니케이션을 하려는 적극적인 행동" in question
    assert "실천" not in question
    assert "하려는 적극적인 행동을 실천" not in question
    if method == "상황면접":
        assert "하려는 적극적인 행동을 보이기 어려운 조건입니다" in question
    else:
        assert "하려는 적극적인 행동을 보여야 하는 가운데" in question


@pytest.mark.parametrize("method", ("상황면접", "창의적 문제해결력면접"))
def test_attitude_probe_does_not_stack_practice_on_an_intention(method: str) -> None:
    follow_ups = _followups_for_method(method, "고객 상담", ATTITUDE_INTENT_FACTOR, 3, focus_type="태도")
    merged = "\n".join(follow_ups)

    assert "원활한 커뮤니케이션을 하려는 적극적인 행동을" in merged
    assert "어떻게 보이겠으며" in merged
    assert "실천" not in merged


SKILL_FACTOR = "통계자료를 분석할 수 있는 능력"


def test_inbasket_skill_stem_uses_the_skill_to_sort_documents() -> None:
    surface, _ = public_task_object(factor_name=SKILL_FACTOR, ksa_type="기술")
    question = _question_for_method(
        "인바스켓면접",
        "기술통계분석",
        SKILL_FACTOR,
        "통계조사",
        "자료를 분석해 결과를 보고한다.",
        "기술",
    )

    assert f"문서마다 {surface}" in question
    assert "필요한지 판단해 처리 우선순위와 상급자 보고" in question
    # Sorting documents is not doing the skill at the same time.
    assert "실제로" not in question
    assert "하며 처리 우선순위" not in question
    assert "해 처리 우선순위" not in question.replace("판단해 처리 우선순위", "")
    assert "수행 단계·조치·산출물·품질 확인 방법" in question


def test_creative_skill_stem_names_the_problem_not_a_simultaneous_act() -> None:
    surface, _ = public_task_object(factor_name=SKILL_FACTOR, ksa_type="기술")
    question = _question_for_method(
        "창의적 문제해결력면접",
        "기술통계분석",
        SKILL_FACTOR,
        "통계조사",
        "자료를 분석해 결과를 보고한다.",
        "기술",
    )

    assert f"{surface} 없이는 해결하기 어려운 복합 문제가 발생했습니다" in question
    assert "실제로 하며" not in question
    assert "하며 해결해야 하는 복합 문제" not in question
    assert "실제로 수행할 단계·조치" in question


@pytest.mark.parametrize(
    ("detail", "subject", "focus", "definition", "foreign"),
    [
        (
            "병원행정",
            "병원구매관리",
            "종전 구입가격 및 시장가격 조사 능력",
            "병원구매관리란 병원운영과 진료에 필요한 물자를 구입하고 관리할 수 있는 능력이다.",
            ("환자", "건강정보", "보건교육"),
        ),
        (
            "총무",
            "업무지원",
            "법인 제증명 유효기간과 재고관리 능력",
            "업무지원이란 조직 구성원들이 업무상 발생되는 인장, 출장, 제 증명 관련 요청 사항을 "
            "적시에 지원하고 업무용 시설을 관리, 개선할 수 있는 능력이다.",
            ("설비 이상 징후", "안전 기준 미충족", "작업허가"),
        ),
        (
            "스포츠용품제작",
            "스포츠용품 생산관리",
            "데이터를 관리 능력",
            "스포츠용품 생산관리란 불량률 최소화 및 원가절감을 위해 적절한 인원배치를 기반으로 "
            "제품생산이 원활하게 이루어질 수 있도록 관리하는 능력이다.",
            ("예약 변경", "이용객", "프론트"),
        ),
    ],
)
def test_office_or_production_function_does_not_get_a_site_scenario(
    detail: str, subject: str, focus: str, definition: str, foreign: tuple[str, ...]
) -> None:
    context = _domain_context_pack(detail=detail, subject=subject, focus=focus, comp_def=definition)
    serialized = json.dumps(context, ensure_ascii=False)

    for phrase in foreign:
        assert phrase not in serialized, (phrase, context["situation"])


@pytest.mark.parametrize(
    ("detail", "subject", "definition", "expected"),
    [
        ("간호", "환자 간호", "환자의 상태를 관찰하고 간호를 제공한다.", "환자·근로자 문의"),
        ("스포츠시설운영관리", "스포츠시설 이용객 관리", "이용객 안전을 관리한다.", "이용객"),
        ("발전설비운영", "설비 점검", "설비를 점검하고 정비한다.", "설비 이상 징후"),
    ],
)
def test_site_scenario_stays_for_site_jobs(detail: str, subject: str, definition: str, expected: str) -> None:
    context = _domain_context_pack(detail=detail, subject=subject, focus="", comp_def=definition)

    assert expected in context["situation"], context["situation"]
