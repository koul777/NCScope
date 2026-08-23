from __future__ import annotations

from scripts.audit_ksa_question_surfaces import audit_rows, surface_issue_codes
from app.services.question_surface import has_dangling_surface, public_task_object
from app.services.question_surface import replace_official_ksa_surface


def _row(
    factor_name: str,
    ksa_type: str,
    *,
    element_id: str = "1",
    element_name: str = "프로젝트조직 정의하기",
) -> dict[str, str]:
    return {
        "unit_code": "0101010205_17v2",
        "unit_name": "프로젝트 인적자원관리",
        "element_id": element_id,
        "element_name": element_name,
        "ksa_type": ksa_type,
        "factor_name": factor_name,
        "competency_definition": "프로젝트 수행에 필요한 조직과 역할을 정의하고 관리하는 능력이다.",
    }


def test_surface_audit_keeps_same_element_measurements_distinct() -> None:
    report = audit_rows(
        [
            _row("메뉴 이해", "지식"),
            _row("고객 요구 이해", "지식"),
        ]
    )

    assert report["summary"]["row_count"] == 2
    assert report["issue_counts"]["same_element_surface_collision"] == 0
    assert report["surface_source_counts"] == {"factor_repair": 2}


def test_surface_audit_accepts_repaired_dangling_knowledge_factor() -> None:
    report = audit_rows([_row("승인된 변경에 대한 지식", "지식")])

    assert report["summary"]["strict_issue_total"] == 0
    assert report["examples"] == {}


def test_surface_audit_counts_unique_factor_collisions_not_repeated_rows() -> None:
    first = _row("위원회 운영 관련 법령", "지식")
    second = _row("위원회 운영 규정", "지식")
    report = audit_rows([first, second, first, second])

    assert report["summary"]["collision_count"] == 1
    assert report["summary"]["global_collision_group_count"] == 1
    assert report["summary"]["global_collision_variant_count"] == 1


def test_surface_issue_codes_rejects_non_task_clause_and_visible_alias() -> None:
    row = _row("리스크관리 및 대안을 제시할 수 있는 능력", "기술")

    issues = surface_issue_codes(
        row,
        surface="리스크관리 및 대안을 제시할 수 있는",
        surface_source="factor_repair",
    )

    assert "official_alias_visible" in issues
    assert "non_task_clause" in issues
    assert "weak_type_contract" in issues


def test_surface_issue_codes_accepts_observable_attitude_intention_clause() -> None:
    row = _row("경험을 활용하려는 노력", "태도")
    surface, source = public_task_object(
        factor_name=row["factor_name"],
        ksa_type=row["ksa_type"],
    )

    assert surface == "경험을 활용하려는 행동 기준"
    assert "non_task_clause" not in surface_issue_codes(
        row,
        surface=surface,
        surface_source=source,
    )


def test_attitude_support_intention_becomes_a_readable_support_behavior() -> None:
    surface, source = public_task_object(
        factor_name="입사예정자의 조직적응을 적극적으로 도와주고자 하는 태도",
        ksa_type="태도",
    )

    assert surface == "입사예정자의 조직적응 지원 행동 기준"
    assert source == "factor_repair"
    assert "도와주고자" not in surface
    assert "관련 행동 기준" not in surface
    assert not has_dangling_surface(surface)


def test_malformed_decision_attitude_falls_back_to_ncs_element() -> None:
    surface, source = public_task_object(
        factor_name="대안을 선택가능한 결정적 행동",
        ksa_type="태도",
        element_name="대안 선택하기",
        competency_name="전직지원",
    )

    assert surface == "대안 선택 수행 시 행동 기준"
    assert source == "element_name"
    assert "선택가능" not in surface
    assert "결정적 행동" not in surface


def test_strategic_hr_knowledge_surface_is_nominalized() -> None:
    surface, source = public_task_object(
        factor_name="전략적인 인적자원관리",
        ksa_type="지식",
        competency_name="인사기획",
    )

    assert surface == "전략적 인적자원관리 확인·판단 기준"
    assert source == "factor_repair"


def test_surface_issue_codes_rejects_action_plus_generic_performance_glue() -> None:
    issues = surface_issue_codes(
        _row("프로젝트 관리 영역들 간의 관계를 조율하는 능력", "기술"),
        surface="프로젝트 관리 영역들 간의 관계를 조율 수행·검증 절차",
        surface_source="factor_repair",
    )

    assert "unnatural_action_glue" in issues


def test_surface_issue_codes_accepts_established_research_and_analysis_tasks() -> None:
    research = surface_issue_codes(
        _row("요구조사 수행능력", "기술"),
        surface="요구조사 수행·검증 절차",
        surface_source="factor_repair",
    )
    analysis = surface_issue_codes(
        _row("수치해석 수행 능력", "기술"),
        surface="수치해석 수행·검증 절차",
        surface_source="factor_repair",
    )

    assert "unnatural_action_glue" not in research
    assert "unnatural_action_glue" not in analysis


def test_capability_knowledge_becomes_a_decision_object() -> None:
    surface, source = public_task_object(
        factor_name="설계도면과 현장여건을 검토할 수 있는 지식",
        ksa_type="지식",
        element_name="설계조건 확인하기",
    )

    assert surface == "설계도면과 현장여건 검토·판단 기준"
    assert source == "factor_repair"
    assert "할 수 있는" not in surface


def test_mislabeled_attitude_row_does_not_leave_a_dangling_knowledge_alias() -> None:
    surface, source = public_task_object(
        factor_name="관련 공종 설계 및 시공에 대한 지식",
        ksa_type="태도",
        element_name="감리계획서 작성하기",
    )

    assert surface == "관련 공종 설계 및 시공 관련 행동 기준"
    assert source == "factor_repair"
    assert "에 대한" not in surface


def test_skill_intent_suffix_becomes_an_observable_procedure() -> None:
    surface, source = public_task_object(
        factor_name="레크스포츠 종목에 따른 규칙을 적용하려는 능력",
        ksa_type="기술",
        element_name="규칙습득하기",
    )

    assert surface == "레크스포츠 종목에 따른 규칙 적용·검증 절차"
    assert source == "factor_repair"
    assert "하려는" not in surface


def test_skill_capability_clauses_become_natural_action_nouns() -> None:
    cases = {
        "내담자와 같이 경력 목표를 설정할 수 있는 능력": "내담자와 같이 경력 목표 설정·확인 절차",
        "작품실행에 적합한 예술가를 선택할 수 있는 능력": "작품실행에 적합한 예술가 선정·확인 절차",
        "분야별 설계중첩 및 누락을 조정할 수 있는 능력": "분야별 설계중첩 및 누락 조정·검증 절차",
        "최적 조건으로 자료를 수정·보완할 수 있는 능력": "최적 조건으로 자료 수정·보완·검증 절차",
        "새로운 영업기회를 찾아낼 수 있는 기회포착 능력": "새로운 영업기회 포착·검증 절차",
        "프로젝트 관리 영역들 간의 관계를 조율하는 능력": "프로젝트 관리 영역들 간의 관계 조율·검증 절차",
        "직접계측 조사를 기획하고 실행하는 능력": "직접계측 조사 기획·실행·검증 절차",
        "관리 대상 리스크를 선정할 수 있는 능력": "관리 대상 리스크 선정·확인 절차",
        "관련 장비를 다루는 능력": "관련 장비를 다루는 절차와 결과 검증 기준",
        "평가지표에 맞게 수행하는 기술": "평가지표에 맞게 실행·검증 절차",
        "자재소요량계획 활용 수행 능력": "자재소요량계획 활용·검증 절차",
        "보안 점검 및 설정 수행 능력": "보안 점검 및 설정·확인 절차",
    }

    for factor, expected in cases.items():
        surface, source = public_task_object(
            factor_name=factor,
            ksa_type="기술",
            element_name="업무 수행하기",
        )
        assert surface == expected
        assert source == "factor_repair"
        assert "할 수 있는" not in surface
        assert "설정 수행" not in surface
        assert "선택 수행" not in surface


def test_attitude_verb_intention_does_not_fuse_with_execution_wording() -> None:
    surface, source = public_task_object(
        factor_name="프로젝트 목표를 달성하고자 하는 책임 있는 태도",
        ksa_type="태도",
        element_name="프로젝트 목표 관리하기",
    )

    assert surface == "프로젝트 목표를 달성하려는 책임 있는 행동 기준"
    assert source == "factor_repair"
    assert "달성실행" not in surface
    assert "attitude_verb_fusion" not in surface_issue_codes(
        _row("프로젝트 목표를 달성하고자 하는 책임 있는 태도", "태도"),
        surface=surface,
        surface_source=source,
    )


def test_trailing_connective_is_removed_before_skill_task_suffix() -> None:
    surface, source = public_task_object(
        factor_name="현장 자료를 정리하고",
        ksa_type="기술",
        element_name="현장 자료 정리하기",
    )

    assert surface == "현장 자료 정리·검증 절차"
    assert source == "factor_repair"
    assert "정리하고 관련" not in surface


def test_candidate_surface_replacement_handles_particles_spacing_and_dot_variants() -> None:
    public = "승인된 변경 관련 확인·판단 기준"
    repaired, changed = replace_official_ksa_surface(
        "'승인된  변경에 대한 지식'을 어떤 범위에 적용합니까?",
        "승인된 변경에 대한 지식",
        public,
    )
    dotted, dotted_changed = replace_official_ksa_surface(
        "자료 수집ㆍ분석 기술과 관련된 산출물은 무엇입니까?",
        "자료 수집·분석 기술",
        "자료 수집·분석 절차",
    )
    untouched, untouched_changed = replace_official_ksa_surface(
        "관련 문서를 검토해 주세요.",
        "승인된 변경에 대한 지식",
        public,
    )

    assert changed is True
    assert repaired == f"{public}을 어떤 범위에 적용합니까?"
    assert dotted_changed is True
    assert dotted == "자료 수집·분석 절차와 관련된 산출물은 무엇입니까?"
    assert untouched_changed is False
    assert untouched == "관련 문서를 검토해 주세요."


def test_candidate_surface_replacement_does_not_replace_inside_another_word() -> None:
    repaired, changed = replace_official_ksa_surface(
        "위험관리 기준과 '관리'를 구분해 설명해 주세요.",
        "관리",
        "업무 관리 절차",
    )

    assert changed is True
    assert repaired == "위험관리 기준과 업무 관리 절차를 구분해 설명해 주세요."


def test_audit_summary_separates_strict_defects_from_semantic_collisions() -> None:
    report = audit_rows(
        [
            _row("위원회 운영 관련 법령", "지식"),
            _row("위원회 운영 규정", "지식"),
        ]
    )

    assert report["summary"]["strict_issue_total"] == 0
    assert report["summary"]["collision_count"] == 1
    assert report["issue_counts"]["same_element_surface_collision"] == 1
