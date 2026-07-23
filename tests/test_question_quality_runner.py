from __future__ import annotations

import csv
import importlib.util
from pathlib import Path
import sys


_RUNNER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_alio_question_quality.py"
_SPEC = importlib.util.spec_from_file_location("ncscope_evaluate_alio_question_quality", _RUNNER_PATH)
assert _SPEC and _SPEC.loader
runner = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = runner
_SPEC.loader.exec_module(runner)


def test_allocate_question_counts_distributes_total_across_details() -> None:
    assert runner.allocate_question_counts(["총무", "사무행정"], 6) == [("총무", 3), ("사무행정", 3)]
    assert runner.allocate_question_counts(["총무", "사무행정", "인사"], 2) == [("총무", 1), ("사무행정", 1)]


def test_iter_cached_attachments_prefers_recent_alio_indices(tmp_path: Path) -> None:
    for name in ["302936_1_old.pdf", "303039_1_new.zip", "303003_1_mid.pdf"]:
        (tmp_path / name).write_bytes(b"x")

    files = runner.iter_cached_attachments(tmp_path, 2)

    assert [path.name for path in files] == ["303039_1_new.zip", "303003_1_mid.pdf"]


def test_write_quality_reports_emits_summary_and_question_csv(tmp_path: Path) -> None:
    rows = [
        {
            "idx": "1",
            "attachment": "직무기술서.pdf",
            "status": "ok_model",
            "detail_count": 1,
            "exact_detail_count": 1,
            "generated_questions": 6,
            "ready_questions": 6,
            "model_candidate_questions": 6,
            "model_questions": 6,
            "model_ready_questions": 6,
            "model_replaced_by_template_questions": 0,
            "template_inserted_questions": 0,
            "template_fallback_questions": 0,
            "template_fallback_ready_questions": 0,
            "average_score": 1.0,
            "coverage_adjusted_score": 1.0,
            "coverage_passed": True,
            "strict_template_passed": True,
            "model_quality_passed": True,
            "passed": True,
        }
    ]
    question_rows = [
        {
            "idx": "1",
            "attachment": "직무기술서.pdf",
            "detail": "사무행정",
            "member": "직무기술서.pdf",
            "question_index": 1,
            "type": "경험면접",
            "competency": "문서작성",
            "ncsClCd": "0202030201_25v3",
            "question_source": "model",
            "model_question_raw": "model raw question",
            "model_question_preserved": True,
            "model_replacement_reasons": "",
            "question": "문서작성 경험을 말씀해 주세요.",
            "score": 1.0,
            "ready": True,
            "issues": "",
        }
    ]

    md_path, csv_path, item_csv_path = runner.write_quality_reports(rows, question_rows, tmp_path)

    md_text = md_path.read_text(encoding="utf-8")
    assert "Documents strict source-explicit coverage + template-ready: 1" in md_text
    assert "Documents passed model-origin quality gate: 1" in md_text
    assert "Documents passed model-origin quality + strict coverage: 1" in md_text
    assert "Model candidate questions received: 6" in md_text
    assert "Model-origin questions evaluated: 6" in md_text
    assert "| idx | status | model pass | full pass | template pass |" in md_text
    assert "직무기술서.pdf" in csv_path.read_text(encoding="utf-8-sig")
    item_csv = item_csv_path.read_text(encoding="utf-8-sig")
    assert "question" in item_csv
    assert "canonical_detail" in item_csv
    assert "question_source" in item_csv
    assert "model_question_raw" in item_csv
    assert "model_question_preserved" in item_csv
    assert "model_replacement_reasons" in item_csv
    assert "follow_ups" in item_csv
    assert "evaluation_points" in item_csv
    assert "ksa_evidence_count" in item_csv
    with item_csv_path.open(encoding="utf-8-sig", newline="") as f:
        headers = set(next(csv.DictReader(f)).keys())
    assert {
        "canonical_detail",
        "question_source",
        "model_question_raw",
        "model_question_preserved",
        "model_replacement_reasons",
        "follow_ups",
        "evaluation_points",
        "ksa_evidence_count",
    } <= headers
    main_csv = csv_path.read_text(encoding="utf-8-sig")
    assert "manual_review_suggestions" in main_csv
    assert "detail_source" in main_csv
    assert "model_candidate_questions" in main_csv
    assert "model_replaced_by_template_questions" in main_csv
    assert "Documents question-ready with contextual detail recovery" in md_text
    assert "## Method Quality" in md_text
    assert "## Question Source" in md_text
    assert "사무행정" in item_csv


def test_write_quality_reports_emits_model_replacement_reason_summary(tmp_path: Path) -> None:
    rows = [
        {
            "idx": "1",
            "attachment": "jd.txt",
            "status": "template_ready",
            "generated_questions": 1,
            "ready_questions": 1,
            "model_candidate_questions": 1,
            "model_replaced_by_template_questions": 1,
            "template_fallback_questions": 1,
            "average_score": 1.0,
            "coverage_adjusted_score": 1.0,
            "strict_template_passed": True,
            "model_quality_passed": False,
            "passed": False,
        }
    ]
    question_rows = [
        {
            "idx": "1",
            "attachment": "jd.txt",
            "detail": "A",
            "question_index": 1,
            "type": "경험면접",
            "question_source": "template_fallback",
            "model_question_raw": "How would you do this?",
            "model_question_preserved": False,
            "model_replacement_reasons": "main_question_method_shape",
            "question": "fallback",
            "ready": True,
            "issues": "",
        }
    ]

    md_path, _, item_csv_path = runner.write_quality_reports(rows, question_rows, tmp_path)

    md_text = md_path.read_text(encoding="utf-8")
    assert "## Model Fallback Reasons" in md_text
    assert "| main_question_method_shape | 1 |" in md_text
    item_csv = item_csv_path.read_text(encoding="utf-8-sig")
    assert "How would you do this?" in item_csv
    assert "main_question_method_shape" in item_csv


def test_quality_gate_failures_detects_model_rate_and_fallbacks() -> None:
    rows = [
        {
            "model_candidate_questions": 4,
            "model_ready_questions": 2,
            "model_replaced_by_template_questions": 1,
            "template_inserted_questions": 1,
        }
    ]

    failures = runner.quality_gate_failures(
        rows,
        min_model_ready_rate=0.75,
        fail_on_model_replacements=True,
        fail_on_template_insertions=True,
    )

    assert "model_ready_rate 0.50 below minimum 0.75" in failures[0]
    assert "model_replacements 1 > 0" in failures
    assert "template_insertions 1 > 0" in failures


def test_quality_gate_failures_passes_when_model_questions_are_ready() -> None:
    rows = [
        {
            "model_candidate_questions": "6",
            "model_ready_questions": "6",
            "model_replaced_by_template_questions": "0",
            "template_inserted_questions": "0",
        }
    ]

    failures = runner.quality_gate_failures(
        rows,
        min_model_ready_rate=1.0,
        fail_on_model_replacements=True,
        fail_on_template_insertions=True,
    )

    assert failures == []


def test_evaluate_cached_document_builds_ready_quality_report(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "303003_1_직무기술서.txt"
    path.write_text("세분류: 사무행정\n직무내용: 문서 작성 및 관리", encoding="utf-8")

    def fake_search_units(details, max_units):
        assert details == ["사무행정"]
        return [
            {
                "ncsClCd": "0202030201_25v3",
                "compeUnitName": "문서작성",
                "ncsSclasCdnm": "사무행정",
                "ncsSubdCdnm": "총무·인사",
                "compeUnitDef": "요구사항을 파악하여 문서를 작성하는 능력이다.",
            }
        ]

    def fake_get_ksa(units, max_factors_per_unit):
        return [
            {
                "ncsClCd": "0202030201_25v3",
                "compeUnitName": "문서작성",
                "factorName": "문서 요구사항 파악",
                "factorSource": "ncs-mcp",
            }
        ]

    monkeypatch.setattr(runner, "search_units_by_detail", fake_search_units)
    monkeypatch.setattr(runner, "suggest_units_by_text", lambda details, max_units: [])
    monkeypatch.setattr(runner, "get_ksa_by_units", fake_get_ksa)

    row, question_rows = runner.evaluate_cached_document(
        path=path,
        max_bytes=1024 * 1024,
        questions_per_doc=6,
        follow_up_count=3,
        max_details_per_doc=4,
        max_units_per_detail=8,
        ksa_units=2,
        ksa_factors_per_unit=4,
    )

    assert row["status"] == "template_ready"
    assert row["detail_source"] == "explicit"
    assert row["generated_questions"] == 6
    assert row["ready_questions"] == 6
    assert row["model_candidate_questions"] == 0
    assert row["model_questions"] == 0
    assert row["model_replaced_by_template_questions"] == 0
    assert row["template_inserted_questions"] == 6
    assert row["template_fallback_questions"] == 6
    assert row["strict_template_passed"] is True
    assert row["model_quality_passed"] is False
    assert row["passed"] is False
    assert row["coverage_adjusted_score"] == 1.0
    assert row["average_score"] == 1.0
    assert len(question_rows) == 6


def test_evaluate_cached_document_model_mode_counts_model_questions(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "303105_1_jd.txt"
    path.write_text("세분류: 사무행정\n직무내용: 문서 작성 및 요구사항 관리", encoding="utf-8")

    def fake_search_units(details, max_units):
        assert details == ["사무행정"]
        return [
            {
                "ncsClCd": "0202030201_25v3",
                "compeUnitName": "문서작성",
                "ncsSclasCdnm": "일반사무",
                "ncsSubdCdnm": "사무행정",
                "matchedDetailName": "사무행정",
                "compeUnitDef": "요구사항을 파악하여 문서를 작성하는 능력이다.",
            }
        ]

    def fake_get_ksa(units, max_factors_per_unit):
        return [
            {
                "ncsClCd": "0202030201_25v3",
                "compeUnitName": "문서작성",
                "factorName": "문서 요구사항 파악",
                "factorSource": "ncs-mcp",
            }
        ]

    def fake_build_strategy_with_openai(**kwargs):
        assert kwargs["api_key_override"] == "test-key"
        assert kwargs["target_count_override"] == 1
        return {
            "question_generation_policy": "test_model_generation",
            "interview_questions": [
                {
                    "type": "경험면접",
                    "competency": "문서작성",
                    "ncsClCd": "0202030201_25v3",
                    "question": (
                        "문서작성 업무에서 문서 요구사항을 파악해 문제를 해결한 경험을 말씀해 주세요. "
                        "당시 상황, 본인 행동, 결과를 포함해 설명해 주세요."
                    ),
                    "follow_ups": [
                        "당시 상황에서 본인이 맡은 역할은 무엇이었습니까?",
                        "문서 요구사항 파악을 위해 실제로 취한 행동은 무엇이었습니까?",
                        "결과를 어떤 기준으로 확인했고 무엇을 학습했습니까?",
                    ],
                    "evaluation_points": [
                        "구체적상황설명",
                        "본인역할과행동",
                        "성과와학습",
                        "직무관련성",
                    ],
                }
            ],
        }

    monkeypatch.setattr(runner, "search_units_by_detail", fake_search_units)
    monkeypatch.setattr(runner, "suggest_units_by_text", lambda details, max_units: [])
    monkeypatch.setattr(runner, "get_ksa_by_units", fake_get_ksa)
    monkeypatch.setattr(runner, "build_jd_strategy_with_openai", fake_build_strategy_with_openai)

    row, question_rows = runner.evaluate_cached_document(
        path=path,
        max_bytes=1024 * 1024,
        questions_per_doc=1,
        follow_up_count=3,
        max_details_per_doc=4,
        max_units_per_detail=8,
        ksa_units=2,
        ksa_factors_per_unit=4,
        benchmark_mode="model",
        openai_api_key="test-key",
    )

    assert row["status"] == "ok_model"
    assert row["resolved_benchmark_mode"] == "model"
    assert row["strategy_generation_policy"] == "test_model_generation"
    assert row["generated_questions"] == 1
    assert row["model_candidate_questions"] == 1
    assert row["model_questions"] == 1
    assert row["model_ready_questions"] == 1
    assert row["model_replaced_by_template_questions"] == 0
    assert row["template_inserted_questions"] == 0
    assert row["template_fallback_questions"] == 0
    assert row["model_quality_passed"] is True
    assert row["passed"] is True
    assert len(question_rows) == 1
    assert question_rows[0]["question_source"] == "model"


def test_evaluate_cached_document_model_mode_counts_replaced_model_question(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "303106_1_jd.txt"
    path.write_text("dummy", encoding="utf-8")

    monkeypatch.setattr(
        runner,
        "parse_benchmark_document",
        lambda data, filename, max_bytes: {"markdown": "dummy jd text"},
    )
    monkeypatch.setattr(
        runner,
        "structure_job_description",
        lambda parsed, filename: {
            "fields": {
                "ncs_detail_candidates": ["A"],
                "ncs_detail_source": "explicit",
                "duties": ["document requirements management"],
            }
        },
    )

    monkeypatch.setattr(
        runner,
        "search_units_by_detail",
        lambda details, max_units: [
            {
                "ncsClCd": "0202030201_25v3",
                "compeUnitName": "Doc Writing",
                "ncsSclasCdnm": "Office",
                "ncsSubdCdnm": "A",
                "matchedDetailName": details[0],
                "compeUnitDef": "Write documents from requirements.",
            }
        ],
    )
    monkeypatch.setattr(runner, "suggest_units_by_text", lambda details, max_units: [])
    monkeypatch.setattr(
        runner,
        "get_ksa_by_units",
        lambda units, max_factors_per_unit: [
            {
                "ncsClCd": "0202030201_25v3",
                "compeUnitName": "Doc Writing",
                "factorName": "A factor",
                "factorSource": "ncs-mcp",
            }
        ],
    )
    monkeypatch.setattr(
        runner,
        "build_jd_strategy_with_openai",
        lambda **kwargs: {
            "question_generation_policy": "test_weak_model_generation",
            "interview_questions": [
                {
                    "type": "경험면접",
                    "competency": "Doc Writing",
                    "ncsClCd": "0202030201_25v3",
                    "question": "How would you do this?",
                    "follow_ups": ["More detail?"],
                    "evaluation_points": ["fit"],
                }
            ],
        },
    )

    row, question_rows = runner.evaluate_cached_document(
        path=path,
        max_bytes=1024 * 1024,
        questions_per_doc=1,
        follow_up_count=3,
        max_details_per_doc=4,
        max_units_per_detail=8,
        ksa_units=2,
        ksa_factors_per_unit=4,
        benchmark_mode="model",
        openai_api_key="test-key",
    )

    assert row["status"] == "template_ready"
    assert row["generated_questions"] == 1
    assert row["model_candidate_questions"] == 1
    assert row["model_questions"] == 0
    assert row["model_replaced_by_template_questions"] == 1
    assert row["template_inserted_questions"] == 0
    assert row["template_fallback_questions"] == 1
    assert row["model_quality_passed"] is False
    assert row["passed"] is False
    assert row["strict_template_passed"] is True
    assert len(question_rows) == 1
    assert question_rows[0]["question_source"] == "template_fallback"
    assert question_rows[0]["model_question_raw"] == "How would you do this?"
    assert question_rows[0]["model_question_preserved"] is False
    assert "main_question_method_shape" in question_rows[0]["model_replacement_reasons"]
    assert "follow_up_quality" in question_rows[0]["model_replacement_reasons"]


def test_evaluate_cached_document_fails_when_extracted_detail_is_unmatched(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "303099_1_직무기술서.txt"
    path.write_text("세분류: 사무행정, 카지노 고객 지원\n직무내용: 문서 요구사항 파악 및 고객 응대", encoding="utf-8")

    def fake_search_units(details, max_units):
        term = details[0]
        if term == "사무행정":
            return [
                {
                    "ncsClCd": "0202030201_25v3",
                    "compeUnitName": "문서작성",
                    "ncsSclasCdnm": "일반사무",
                    "ncsSubdCdnm": "사무행정",
                    "matchedDetailName": "사무행정",
                    "compeUnitDef": "요구사항을 파악하여 문서를 작성하는 능력이다.",
                }
            ]
        return []

    def fake_get_ksa(units, max_factors_per_unit):
        return [
            {
                "ncsClCd": "0202030201_25v3",
                "compeUnitName": "문서작성",
                "factorName": "문서 요구사항 파악",
                "factorSource": "ncs-mcp",
            }
        ]

    monkeypatch.setattr(runner, "search_units_by_detail", fake_search_units)
    monkeypatch.setattr(runner, "suggest_units_by_text", lambda details, max_units: [])
    monkeypatch.setattr(runner, "get_ksa_by_units", fake_get_ksa)

    row, question_rows = runner.evaluate_cached_document(
        path=path,
        max_bytes=1024 * 1024,
        questions_per_doc=6,
        follow_up_count=3,
        max_details_per_doc=4,
        max_units_per_detail=8,
        ksa_units=2,
        ksa_factors_per_unit=4,
    )

    assert row["status"] == "template_ready_partial_detail_coverage"
    assert row["passed"] is False
    assert row["generated_questions"] == 6
    assert row["ready_questions"] == 6
    assert row["coverage_adjusted_score"] == 0.0
    assert row["strict_template_passed"] is False
    assert row["model_quality_passed"] is False
    assert row["unmatched_detail_count"] == 1
    assert row["unmatched_details"] == "카지노 고객 지원"
    assert len(question_rows) == 6


def test_evaluate_cached_document_separates_skipped_details_from_unmatched(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "303101_1_jd.txt"
    path.write_text("dummy", encoding="utf-8")

    monkeypatch.setattr(runner, "parse_benchmark_document", lambda data, filename, max_bytes: {"markdown": "dummy"})
    monkeypatch.setattr(
        runner,
        "structure_job_description",
        lambda parsed, filename: {"fields": {"ncs_detail_candidates": ["A", "B", "C"]}},
    )
    monkeypatch.setattr(
        runner,
        "search_units_by_detail",
        lambda details, max_units: [
            {
                "ncsClCd": "0202030201_25v3",
                "compeUnitName": "A unit",
                "ncsSclasCdnm": "A small",
                "ncsSubdCdnm": details[0],
                "matchedDetailName": details[0],
                "compeUnitDef": "A definition",
            }
        ],
    )
    monkeypatch.setattr(runner, "suggest_units_by_text", lambda details, max_units: [])
    monkeypatch.setattr(
        runner,
        "get_ksa_by_units",
        lambda units, max_factors_per_unit: [
            {
                "ncsClCd": "0202030201_25v3",
                "compeUnitName": "A unit",
                "factorName": "A factor",
                "factorSource": "ncs-mcp",
            }
        ],
    )

    row, question_rows = runner.evaluate_cached_document(
        path=path,
        max_bytes=1024 * 1024,
        questions_per_doc=6,
        follow_up_count=3,
        max_details_per_doc=1,
        max_units_per_detail=8,
        ksa_units=2,
        ksa_factors_per_unit=4,
    )

    assert row["status"] == "template_ready_partial_detail_coverage"
    assert row["passed"] is False
    assert row["unmatched_detail_count"] == 0
    assert row["skipped_detail_count"] == 2
    assert row["uncovered_detail_count"] == 2
    assert row["coverage_adjusted_score"] == 0.0
    assert row["unmatched_details"] == ""
    assert row["skipped_details"] == "B; C"
    assert len(question_rows) == 6


def test_evaluate_cached_document_does_not_promote_element_level_false_friend(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "303102_1_jd.txt"
    path.write_text("dummy", encoding="utf-8")

    monkeypatch.setattr(runner, "parse_benchmark_document", lambda data, filename, max_bytes: {"markdown": "dummy"})
    monkeypatch.setattr(
        runner,
        "structure_job_description",
        lambda parsed, filename: {"fields": {"ncs_detail_candidates": ["재원환자 관리"]}},
    )
    monkeypatch.setattr(runner, "search_units_by_detail", lambda details, max_units: [])
    monkeypatch.setattr(
        runner,
        "suggest_units_by_text",
        lambda details, max_units: [
            {
                "ncsClCd": "0601020110_16v2",
                "compeUnitName": "진료비관리",
                "ncsSclasCdnm": "병원행정",
                "ncsSubdCdnm": "병원행정",
                "matchedElementName": "재원환자 관리하기",
                "isExactUnitNameMatch": False,
            }
        ],
    )

    row, question_rows = runner.evaluate_cached_document(
        path=path,
        max_bytes=1024 * 1024,
        questions_per_doc=6,
        follow_up_count=3,
        max_details_per_doc=4,
        max_units_per_detail=8,
        ksa_units=2,
        ksa_factors_per_unit=4,
    )

    assert row["status"] == "no_exact_units"
    assert row["exact_detail_count"] == 0
    assert row["unit_name_detail_count"] == 0
    assert row["unmatched_detail_count"] == 1
    assert row["unmatched_details"] == "재원환자 관리"
    assert "false friend" in row["manual_review_suggestions"]
    assert "0601020110_16v2" in row["manual_review_suggestions"]
    assert question_rows == []


def test_manual_review_suggestions_cover_known_health_and_facility_gaps() -> None:
    suggestions = runner._manual_review_suggestions(
        [
            "영상의학",
            "임상병리",
            "간호조무",
            "간호수행",
            "간호행정관리",
            "유지관리",
            "건축감리",
        ]
    )

    assert suggestions.count("manual-review-only") >= 5
    assert "false friend" in suggestions
    assert "human clinical laboratory" in suggestions
    assert "current local NCS_MCP has no exact detail coverage" in suggestions


def test_manual_review_suggestions_cover_culture_policy_gap() -> None:
    suggestions = runner._manual_review_suggestions(["문화・관광정책"])

    assert "manual-review-only" in suggestions
    assert "unit-name coverage" in suggestions


def test_evaluate_cached_document_accepts_exact_unit_name_resolution(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "303100_1_직무기술서.txt"
    path.write_text("세분류: 카지노 고객 지원\n직무내용: 고객 물품 보관 및 안내", encoding="utf-8")

    monkeypatch.setattr(runner, "search_units_by_detail", lambda details, max_units: [])
    monkeypatch.setattr(
        runner,
        "suggest_units_by_text",
        lambda details, max_units: [
            {
                "ncsClCd": "1203040206_23v2",
                "compeUnitName": "카지노 고객 지원",
                "ncsSclasCdnm": "관광레저서비스",
                "ncsSubdCdnm": "카지노운영관리",
                "canonicalDetailName": "카지노운영관리",
                "matchedDetailName": "카지노 고객 지원",
                "isExactUnitNameMatch": True,
                "factorSource": "ncs-mcp-suggest",
            }
        ],
    )
    monkeypatch.setattr(
        runner,
        "get_ksa_by_units",
        lambda units, max_factors_per_unit: [
            {
                "ncsClCd": "1203040206_23v2",
                "compeUnitName": "카지노 고객 지원",
                "factorName": "고객 지원 업무 지침 확인",
                "factorSource": "ncs-mcp",
            }
        ],
    )

    row, question_rows = runner.evaluate_cached_document(
        path=path,
        max_bytes=1024 * 1024,
        questions_per_doc=6,
        follow_up_count=3,
        max_details_per_doc=4,
        max_units_per_detail=8,
        ksa_units=2,
        ksa_factors_per_unit=4,
    )

    assert row["status"] == "template_ready_unit_name_resolved"
    assert row["passed"] is False
    assert row["strict_template_passed"] is False
    assert row["coverage_adjusted_score"] == 0.0
    assert row["exact_detail_count"] == 0
    assert row["unit_name_detail_count"] == 1
    assert row["unit_name_details"] == "카지노 고객 지원"
    assert row["unmatched_detail_count"] == 0
    assert len(question_rows) == 6


def test_evaluate_cached_document_does_not_strict_pass_contextual_detail(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "303104_1_jd.txt"
    path.write_text("dummy", encoding="utf-8")

    monkeypatch.setattr(runner, "parse_benchmark_document", lambda data, filename, max_bytes: {"markdown": "dummy"})
    monkeypatch.setattr(
        runner,
        "structure_job_description",
        lambda parsed, filename: {
            "fields": {
                "ncs_detail_candidates": ["화력발전설비운영"],
                "ncs_detail_source": "contextual",
            }
        },
    )
    monkeypatch.setattr(
        runner,
        "search_units_by_detail",
        lambda details, max_units: [
            {
                "ncsClCd": "1901010201_25v3",
                "compeUnitName": "화력발전설비 운전",
                "ncsSclasCdnm": "발전설비운영",
                "ncsSubdCdnm": "화력발전설비운영",
                "matchedDetailName": "화력발전설비운영",
                "compeUnitDef": "화력발전설비를 기준에 따라 운전하는 능력이다.",
            }
        ],
    )
    monkeypatch.setattr(runner, "suggest_units_by_text", lambda details, max_units: [])
    monkeypatch.setattr(
        runner,
        "get_ksa_by_units",
        lambda units, max_factors_per_unit: [
            {
                "ncsClCd": "1901010201_25v3",
                "compeUnitName": "화력발전설비 운전",
                "factorName": "발전설비 운전 기준 확인",
                "factorSource": "ncs-mcp",
            }
        ],
    )

    row, question_rows = runner.evaluate_cached_document(
        path=path,
        max_bytes=1024 * 1024,
        questions_per_doc=6,
        follow_up_count=3,
        max_details_per_doc=4,
        max_units_per_detail=8,
        ksa_units=2,
        ksa_factors_per_unit=4,
    )

    assert row["status"] == "template_ready_contextual_detail"
    assert row["passed"] is False
    assert row["strict_template_passed"] is False
    assert row["coverage_adjusted_score"] == 0.0
    assert row["detail_source"] == "contextual"
    assert row["ready_questions"] == 6
    assert len(question_rows) == 6
