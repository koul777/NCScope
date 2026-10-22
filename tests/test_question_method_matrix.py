from __future__ import annotations

import json

from app.main import SUPPORTED_INTERVIEW_METHODS, _attach_ksa_evidence_to_strategy
from scripts.benchmark_question_method_matrix import (
    benchmark_rows,
    build_template_question,
    evidence_row,
)


def _row(factor_name: str, ksa_type: str, *, code: str) -> dict[str, str]:
    return {
        "major_code": code[:2],
        "major_name": "사업관리",
        "sub_name": "프로젝트관리",
        "unit_code": code,
        "unit_name": "프로젝트 인적자원관리",
        "unit_definition": "프로젝트 수행에 필요한 조직과 역할을 정의하고 팀을 관리하는 능력이다.",
        "element_name": "프로젝트조직 정의하기",
        "ksa_type": ksa_type,
        "factor_name": factor_name,
        "ksa_no": "1",
    }


def test_all_methods_and_ksa_types_pass_the_template_quality_matrix() -> None:
    report = benchmark_rows(
        [
            _row("승인된 변경에 대한 지식", "지식", code="0101010205_17v2"),
            _row("이해관계자들과 의사소통할 수 있는 능력", "기술", code="0101010205_17v2"),
            _row("원활한 의사소통을 위해 노력하려는 의지", "태도", code="0101010205_17v2"),
        ]
    )

    expected = len(SUPPORTED_INTERVIEW_METHODS) * 3
    assert report["summary"]["question_count"] == expected
    assert report["summary"]["ready_count"] == expected, json.dumps(
        report["failures"], ensure_ascii=False, indent=2
    )
    assert report["summary"]["idempotence_failure_count"] == 0
    assert report["summary"]["exact_surface_duplicate_count"] == 0
    assert report["summary"]["passed"] is True
    assert report["issue_counts"] == {}


def test_experience_templates_render_long_official_ksa_as_natural_panel_questions() -> None:
    rows = [
        _row(
            "외부전문가 검색 시스템(국가인재 DB등) 사용법",
            "지식",
            code="0201010206_22v2",
        ),
        _row(
            "아이디어 평가, 권리존속평가 등 권리 평가 기술",
            "기술",
            code="0501020109_14v1",
        ),
        _row(
            "조사 대상의 수준에 맞는 문항을 구성하려는 태도",
            "태도",
            code="0402020102_16v2",
        ),
    ]
    for row, unit_name in zip(
        rows,
        ("경영평가활동 수행", "지식재산 해외 법무 수행", "평생교육 요구분석"),
    ):
        row["unit_name"] = unit_name

    for variation_index, row in enumerate(rows, start=80):
        item = build_template_question(
            row,
            "경험면접",
            variation_index=variation_index,
        )
        evaluated = _attach_ksa_evidence_to_strategy(
            {
                "interview_questions": [item],
                "question_plan_used": {"total_main_count": 1},
            },
            [evidence_row(row)],
        )
        quality = evaluated["question_quality_report"]["items"][0]
        question = evaluated["interview_questions"][0]["question"]

        assert quality["ready"] is True, json.dumps(quality, ensure_ascii=False, indent=2)
        assert len(question) <= 360
        assert "조건과 유사한 사례를 우선해 주세요" not in question
        assert "사용법 확인·판단 기준을 실제 판단에 사용한 장면" not in question
        assert "권리 평가·검증 절차가 요구된 장면" not in question
        assert "문항 구성 행동 기준과 관련해" not in question


def test_creative_task_operationalizes_long_equipment_operation_skill() -> None:
    row = _row(
        "식육 추출 가공품 추출기 및 여과장치 운영 기술",
        "기술",
        code="2101010309_17v2",
    )
    row.update(
        {
            "major_name": "식품가공",
            "sub_name": "식육가공",
            "unit_name": "식육 추출 가공품 제조",
            "unit_definition": "추출기와 여과장치를 운영해 식육 추출 가공품을 제조한다.",
            "element_name": "추출 및 여과하기",
        }
    )

    report = benchmark_rows([row], methods=["창의적 문제해결력면접"])

    assert report["summary"]["passed"] is True, json.dumps(
        report["failures"], ensure_ascii=False, indent=2
    )
    assert report["issue_counts"] == {}
