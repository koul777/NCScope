"""Attitude surfaces keep the official KSA verb instead of inventing one.

Regression for R9-C.  ``원활한 커뮤니케이션을 하고자 하는 적극적인 태도`` was
shown as ``…커뮤니케이션을 실행하는 적극적인 행동`` (a verb the KSA never
names), and stripping the intention ending ``하려는``/``하는`` before
``태도``/``자세`` left broken stems such as ``정확도 기 행동``,
``고객과 협 관련 행동`` and ``담수심 깊게 행동``.  The public surface now keeps
the official clause and only replaces the attitude head noun with ``행동``.
"""

from __future__ import annotations

import re

import pytest

from app.main import SUPPORTED_INTERVIEW_METHODS, _question_for_method, _question_task_frame
from app.services.question_surface import public_task_object

SUBJECT = "공적개발원조사업 총괄운영관리"
DETAIL = "공적개발원조"
DEFINITION = "공적개발원조사업을 총괄 운영한다."

# (official attitude factor, expected public surface, wording the old surface produced)
CASES = (
    (
        "원활한 커뮤니케이션을 하고자 하는 적극적인 태도",
        "원활한 커뮤니케이션을 하려는 적극적인 행동",
        "커뮤니케이션을 실행하는",
    ),
    (
        "세밀한 검토 및 적용으로 정확도를 기하려는 자세",
        "세밀한 검토 및 적용으로 정확도를 기하려는 행동",
        "정확도 기 행동",
    ),
    (
        "업무 변동 시 변동사항을 고객과 협의하려는 태도",
        "업무 변동 시 변동사항을 고객과 협의하려는 행동",
        "고객과 협 관련",
    ),
    (
        "도복이 우려되는 경우 담수심을 깊게 하려는 태도",
        "도복이 우려되는 경우 담수심을 깊게 하려는 행동",
        "담수심 깊게 행동",
    ),
    (
        "사고재발방지를 위한 대책과 조치를 강구하는 자세",
        "사고재발방지를 위한 대책과 조치를 강구하는 행동",
        "강구 관련 행동",
    ),
    (
        "고객이 강요당하는 느낌이 들지 않도록 하는 자세",
        "고객이 강요당하는 느낌이 들지 않도록 하는 행동",
        "않도록 관련 행동",
    ),
    (
        "대기 고객의 불편함을 줄이기 위해 배려하는 자세",
        "대기 고객의 불편함을 줄이기 위해 배려하는 행동",
        "배려 관련 행동",
    ),
    (
        "여러 분야의 요소들을 종합적으로 연계하려는 태도",
        "여러 분야의 요소들을 종합적으로 연계하려는 행동",
        "종합적으로 연계 행동",
    ),
    (
        "적립식 예금 세일즈를 적극적으로 완수하려는 자세",
        "적립식 예금 세일즈를 적극적으로 완수하려는 행동",
        "적극적으로 완수 행동",
    ),
    (
        "고객 · 소비자의 특성을 정확히 파악하려는 자세",
        "고객 · 소비자의 특성을 정확히 파악하려는 행동",
        "정확히 파악 행동",
    ),
    (
        "정확성을 유지하려는 태도",
        "정확성을 유지하려는 행동",
        "정확성 준수 행동",
    ),
    (
        "정확성을 우선하려는 태도",
        "정확성을 우선하려는 행동",
        "정확성 우선 행동",
    ),
    (
        "시료전처리 규정에 대하여 철저히 검토하려는 의지",
        "시료전처리 규정에 대하여 철저히 검토하려는 행동",
        "철저히 검토 행동",
    ),
)

# Official wording whose verb or particle the old surface replaced:
# ``유지`` became ``준수``, ``표준의 준수`` lost its object particle,
# ``자부심을 갖는`` dropped ``을`` and ``적용을 위한`` became ``적용 관련``.
CASES += (
    (
        "규격과 일치하는 생산을 위한 수치적 정확성 유지",
        "규격과 일치하는 생산을 위한 수치적 정확성을 유지하는 행동",
        "정확성 준수 행동",
    ),
    (
        "문제해결 및 대안 도출을 위한 적극적 자세 유지",
        "문제해결 및 대안 도출을 위한 적극적 자세를 유지하는 행동",
        "자세 준수 행동",
    ),
    (
        "설비운전매뉴얼, 작업지시서 및 생산 표준의 준수",
        "설비운전매뉴얼, 작업지시서 및 생산 표준을 준수하는 행동",
        "표준의 준수하는",
    ),
    (
        "업무에 대한 긍정적인 사고와 자부심을 갖는 태도",
        "업무에 대한 긍정적인 사고와 자부심을 갖는 행동",
        "자부심 갖는 행동",
    ),
    (
        "수요분석 결과에 대한 합리적인 적용을 위한 태도",
        "수요분석 결과에 대한 합리적인 적용을 위한 행동",
        "적용 관련 행동",
    ),
)

# ``해결하고자 한`` is the same present intention as ``하고자 하는``; the
# past form ``하려 했던`` clashed with hypothetical situation prompts.
# ``제시할 수 있는`` was fused into ``제시가능한``.  A label longer than
# the 62-character budget lost its verb and ended as ``제약사항을 행동``.
CASES += (
    (
        "고객의 요구를 해결하고자 한 태도",
        "고객의 요구를 해결하려는 행동",
        "하려 했던",
    ),
    (
        "업무 담당자로서 대안을 제시할 수 있는 태도",
        "업무 담당자로서 대안을 제시할 수 있는 행동",
        "제시가능한",
    ),
    (
        "협력대상국의 지역적, 정치적, 문화적 및 경제적 특성에 기반한 공적개발원조 사업의 "
        "독특한 특성과 제약사항을 이해하고자 하는 태도",
        "지역적, 정치적, 문화적 및 경제적 특성에 기반한 공적개발원조 사업의 독특한 특성과 "
        "제약사항을 이해하려는 행동",
        "제약사항을 행동",
    ),
)

# A spaced ``하는`` (``최우선으로 하는``, ``관찰을 하는``) is the same verbal
# modifier; it was deleted as a dangling ending and became ``… 관련 행동``.
CASES += (
    (
        "작업 시 안전을 최우선으로 하는 자세",
        "작업 시 안전을 최우선으로 하는 행동",
        "최우선으로 관련",
    ),
    (
        "주의 깊게 관찰을 하는 의지",
        "주의 깊게 관찰을 하는 행동",
        "관찰 관련 행동",
    ),
    (
        "검사·시험계획서를 명확히 숙지하여 시험일정에 차질이 없도록 사전 준비를 철저히 하는 태도",
        "검사·시험계획서를 명확히 숙지하여 시험일정에 차질이 없도록 사전 준비를 철저히 하는 행동",
        "철저히 관련",
    ),
)

# Two-syllable nouns ending in ``의`` (``협의``, ``주의``) are not a dangling
# genitive: stripping ``의`` left ``협 관련``/``주 관련``.  ``주의`` also
# gained an ``안전`` the KSA never names.  A verbal clause before ``노력``
# is kept like one before ``태도``.
CASES += (
    (
        "원활한 소통과 객관적 협의 태도",
        "원활한 소통과 객관적 협의 행동",
        "협 관련",
    ),
    (
        "제조 설비와 부속 기기의 일탈 사례 주의 자세",
        "제조 설비와 부속 기기의 일탈 사례 주의 행동",
        "주 관련",
    ),
    (
        "트렌드에 따른 실루엣, 디테일, 스타일 구성, 아이템 코디네이션의 경향을 주의 깊게 "
        "관찰하고 그 변화를 예측하려는 노력",
        "실루엣, 디테일, 스타일 구성, 아이템 코디네이션의 경향을 주의 깊게 관찰하고 그 변화를 "
        "예측하려는 행동",
        "변화를 행동",
    ),
)
TRAIT_NOUN_EXTRA = (
    (
        "교육효과를 높이고자 하는 열의",
        "교육효과를 높이고자 하는 열의 있는 행동",
    ),
)

# Wording that only exists because the surface rewrote the official clause.
INVENTED_OR_BROKEN = (
    "실행하는",
    " 관련 행동",
    "실행한 ",
    "준수 행동",
    "의 준수하는",
)


@pytest.mark.parametrize(("factor", "expected", "old_wording"), CASES)
def test_attitude_surface_keeps_official_clause(factor: str, expected: str, old_wording: str) -> None:
    surface, source = public_task_object(factor_name=factor, ksa_type="태도")

    assert source == "factor_repair"
    assert surface == expected
    assert old_wording not in surface
    for wording in INVENTED_OR_BROKEN:
        assert wording not in surface
    assert surface.endswith("행동")
    assert "행동 기준" not in surface


@pytest.mark.parametrize("method", SUPPORTED_INTERVIEW_METHODS)
@pytest.mark.parametrize(("factor", "expected", "old_wording"), CASES)
def test_attitude_surface_reads_in_every_interview_method(
    method: str, factor: str, expected: str, old_wording: str
) -> None:
    frame = _question_task_frame(
        focus=factor,
        focus_type="태도",
        subject=SUBJECT,
        detail=DETAIL,
        comp_def=DEFINITION,
        evidence_row={"factorName": factor, "ksaTypeName": "태도", "ncsClCd": "0101010104_17v2"},
    )
    assert frame["task_object"] == expected
    for index in (0, 3, 14):
        question = _question_for_method(
            method=method,
            subject=SUBJECT,
            focus=factor,
            detail=DETAIL,
            comp_def=DEFINITION,
            focus_type="태도",
            variation_index=index,
            task_frame=frame,
        )
        assert expected in question, question
        assert old_wording not in question, question
        for wording in INVENTED_OR_BROKEN:
            assert wording not in question, question
        # The official label itself is evidence, not candidate wording.
        assert factor not in question
        assert "행동 기준" not in question
        assert "행동이 드러난 행동" not in question
        assert f"{expected} 행동" not in question


# Attitude labels that end in a trait noun instead of ``태도``/``자세``.
# ``책임감 행동`` is a noun pile; ``책임감 있는 행동`` is what people say.
TRAIT_NOUN_CASES = (
    (
        "모델링의 명확화, 단순화, 규격화에 관한 책임감",
        "모델링의 명확화, 단순화, 규격화에 관한 책임감 있는 행동",
    ),
    (
        "수량, 납기, 품질 문제를 해결하고자하는 적극성",
        "수량, 납기, 품질 문제를 해결하려는 적극성 있는 행동",
    ),
    (
        "학습상황에 최적의 처방을 제시할 수 있는 유연성",
        "학습상황에 최적의 처방을 제시할 수 있는 유연성 있는 행동",
    ),
)


@pytest.mark.parametrize("method", SUPPORTED_INTERVIEW_METHODS)
@pytest.mark.parametrize(("factor", "expected"), (*TRAIT_NOUN_CASES, *TRAIT_NOUN_EXTRA))
def test_trait_noun_attitude_reads_as_behaviour(method: str, factor: str, expected: str) -> None:
    surface, source = public_task_object(factor_name=factor, ksa_type="태도")
    assert source == "factor_repair"
    assert surface == expected
    assert "가능한" not in surface

    frame = _question_task_frame(
        focus=factor,
        focus_type="태도",
        subject=SUBJECT,
        detail=DETAIL,
        comp_def=DEFINITION,
        evidence_row={"factorName": factor, "ksaTypeName": "태도", "ncsClCd": "0501020121_16v1"},
    )
    for index in (0, 3, 14):
        question = _question_for_method(
            method=method,
            subject=SUBJECT,
            focus=factor,
            detail=DETAIL,
            comp_def=DEFINITION,
            focus_type="태도",
            variation_index=index,
            task_frame=frame,
        )
        assert expected in question, question
        # The trait noun is never left as a bare noun pile or quoted label.
        assert f"{factor} 행동" not in question
        assert f"'{factor}'" not in question
        assert "가능한 유연성" not in question
        assert "있는 행동 행동" not in question
        assert "행동 기준" not in question


def test_nouns_that_do_not_take_iss_are_left_as_written() -> None:
    surface, _ = public_task_object(
        factor_name="최적의 시스템과 용량 산정에 대한 합리적인 사고", ksa_type="태도"
    )
    assert surface == "최적의 시스템과 용량 산정에 대한 합리적인 사고 행동"
    assert "사고 있는" not in surface


# Every attitude label the method-matrix benchmark samples (504 and 1008).
MATRIX_ATTITUDE_LABELS = (
    "원활한 커뮤니케이션을 하고자 하는 적극적인 태도",
    "여러 분야의 요소들을 종합적으로 연계하려는 태도",
    "모니터링 도구의 적절한 적용을 위한 합리적 태도",
    "경영평가 절차를 전체적인 관점에서 바라보는 태도",
    "적립식 예금 세일즈를 적극적으로 완수하려는 자세",
    "거치식 예금 세일즈를 적극적으로 완수하려는 자세",
    "조사 대상의 수준에 맞는 문항을 구성하려는 태도",
    "학습상황에 최적의 처방을 제시할 수 있는 유연성",
    "지식재산관리시스템 운영에 대한 숙련도 향상 의지",
    "모델링의 명확화, 단순화, 규격화에 관한 책임감",
    "치매와 동반된 불의의 사고에 대한 적극적인 대처",
    "비밀유지 및 개인정보 보호에 대한 책임있는 자세",
    "시설환경을 지속가능하게 개선하려는 적극적인 태도",
    "지역사회 문제해결을 위한 객관적이고 투명한 자세",
    "자료 분석 및 결과 해석에 대한 가치중립적 태도",
    "작품유통 채널별 특성에 따른 유통목표 달성 의지",
    "사고재발방지를 위한 대책과 조치를 강구하는 자세",
    "세밀한 검토 및 적용으로 정확도를 기하려는 자세",
    "고객이 강요당하는 느낌이 들지 않도록 하는 자세",
    "고객 · 소비자의 특성을 정확히 파악하려는 자세",
    "업무에 대한 긍정적인 사고와 자부심을 갖는 태도",
    "업무 변동 시 변동사항을 고객과 협의하려는 태도",
    "관리 시 사용 한 기기를 청결하게 관리하는 자세",
    "대기 고객의 불편함을 줄이기 위해 배려하는 자세",
    "조리과정 중 조리대 정리 정돈 및 위생관리 준수",
    "고객의 시간을 방해하지 않으려는 사려 깊은 태도",
    "수요를 합리적으로 예측하는 정밀하고 신중한 태도",
    "수요분석 결과에 대한 합리적인 적용을 위한 태도",
    "기계형식 인증과 관련된 업무에 관한 탐색적 의지",
    "논리적 근거가 명확한 결론을 도출하고자하는 노력",
    "원부자재에 대한 시험방법 및 합격기준 숙지 노력",
    "수요자가 요구하는 품질특성을 달성하기 위한 노력",
    "시험규격과 시험방법의 개정여부를 확인하려는 의지",
    "시료전처리 규정에 대하여 철저히 검토하려는 의지",
    "설비운전매뉴얼, 작업지시서 및 생산 표준의 준수",
    "규격과 일치하는 생산을 위한 수치적 정확성 유지",
    "최적의 시스템과 용량 산정에 대한 합리적인 사고",
    "국민의 생명과 안전을 확보한다는 인명존중의 사고",
    "문제해결 및 대안 도출을 위한 적극적 자세 유지",
    "자료 수집 및 정리를 체계적으로 수행하려는 노력",
    "수량, 납기, 품질 문제를 해결하고자하는 적극성",
    "전문 분야의 연구, 사교 모임에 참가하려는 태도",
    "서점 정기 방문하여 독자 욕구를 파악하려는 태도",
    "수질오염현장 조사 기록표를 성실히 작성하는 노력",
    "공정특성에 적합하게 물질수지도를 작성하려는 의지",
    "도복이 우려되는 경우 담수심을 깊게 하려는 태도",
    "친환경 재배 관련 다양한 기술을 습득하려는 의지",
)
LONG_ATTITUDE_LABELS = (
    "협력대상국의 지역적, 정치적, 문화적 및 경제적 특성에 기반한 공적개발원조 사업의 "
    "독특한 특성과 제약사항을 이해하고자 하는 태도",
    "변경하고 진화하는 환경에서 프로젝트를 효과적으로 지시하기 위해 적절한 수준의 지각, "
    "통찰 및 판단을 적용하는 태도",
    "여신거래상대방의 신용위험에 부합하는 적정 여신조건 제공으로 금융기관의 수익 및 "
    "자산건전성 제고에 기여하고자 하는 의지",
    "완성된 음악과 아트워크 뮤직비디오 등 후반작업을 효과적으로 매칭하기 위한 관련 자료를 "
    "적극적으로 연구 분석하는 자세",
)
_STEM_FRAGMENT = re.compile(r"(?:^|\s)[가-힣](?:\s+관련)?\s+행동$")
_VERB_NOUN_RELATED = re.compile(r"(?:강구|관리|배려|협의|협|기) 관련 행동$")
_DANGLING_BEFORE_BEHAVIOUR = re.compile(
    r"(?:을|를|이|가|및|와|과|의|에|으로|하여|하고|하며|위해)\s+행동$"
)


@pytest.mark.parametrize("factor", (*MATRIX_ATTITUDE_LABELS, *LONG_ATTITUDE_LABELS))
def test_attitude_surface_has_no_stem_fragment_or_fused_possibility(factor: str) -> None:
    surface, _ = public_task_object(factor_name=factor, ksa_type="태도")

    assert surface.endswith("행동"), surface
    assert len(surface) <= 80, surface
    assert not _STEM_FRAGMENT.search(surface), surface
    assert not _VERB_NOUN_RELATED.search(surface), surface
    assert not _DANGLING_BEFORE_BEHAVIOUR.search(surface), surface
    assert "실행하는" not in surface
    assert "하려 했던" not in surface
    if "가능" not in factor:
        # ``제시할 수 있는`` must not become ``제시가능한``.
        assert "가능" not in surface, surface


@pytest.mark.parametrize(
    ("factor", "closing"),
    zip(LONG_ATTITUDE_LABELS, ("이해하려는 행동", "적용하는 행동", "기여하려는 행동", "분석하는 행동")),
)
def test_long_attitude_label_keeps_its_closing_verb(factor: str, closing: str) -> None:
    # Labels over the 62-character budget are shortened from the front so
    # the surface still ends with the official verb, not ``…을 행동``.
    surface, _ = public_task_object(factor_name=factor, ksa_type="태도")
    assert surface.endswith(closing), surface
    assert surface.replace(" 행동", "") in factor.replace("하고자 하는", "하려는")


@pytest.mark.parametrize(
    ("factor", "expected"),
    (
        # A trailing annotation does not decide the particle: ``표준(…)`` is
        # still ``표준을``.
        (
            "공사 관련 규격과 표준(Code &; Standard)의 준수",
            "공사 관련 규격과 표준(Code &; Standard)을 준수하는 행동",
        ),
        (
            "설계 관련 법규(Regulation)의 준수",
            "설계 관련 법규(Regulation)를 준수하는 행동",
        ),
        # When the last word is not Korean the particle cannot be chosen
        # safely, so the official ``의`` is kept instead of guessing.
        ("관련법, Code의 준수", "관련법, Code의 준수 행동"),
        ("ISO(A) 유지", "ISO(A) 유지 행동"),
        ("KPI 유지", "KPI 유지 행동"),
    ),
)
def test_object_particle_follows_korean_word_or_keeps_official_particle(factor: str, expected: str) -> None:
    surface, _ = public_task_object(factor_name=factor, ksa_type="태도")
    assert surface == expected
    assert "의 준수하는" not in surface
    assert ")를" not in surface.replace("(Regulation)를", "")
