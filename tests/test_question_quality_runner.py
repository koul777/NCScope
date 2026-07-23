from __future__ import annotations

import csv
import importlib.util
from pathlib import Path
import sys

import pytest


_RUNNER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_alio_question_quality.py"
_SPEC = importlib.util.spec_from_file_location("ncscope_evaluate_alio_question_quality", _RUNNER_PATH)
assert _SPEC and _SPEC.loader
runner = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = runner
_SPEC.loader.exec_module(runner)


def test_cli_dotenv_loader_sets_mcp_without_overriding_existing(tmp_path: Path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "NCS_MCP_URL=http://from-dotenv.example/mcp\n"
        "OPENAI_API_KEY=YOUR_KEY_HERE\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("NCS_MCP_URL", raising=False)
    runner._load_env_file(env_path)
    assert runner.os.getenv("NCS_MCP_URL") == "http://from-dotenv.example/mcp"
    assert runner.os.getenv("OPENAI_API_KEY", "") != "YOUR_KEY_HERE"

    monkeypatch.setenv("NCS_MCP_URL", "http://existing.example/mcp")
    env_path.write_text("NCS_MCP_URL=http://new.example/mcp\n", encoding="utf-8")
    runner._load_env_file(env_path)
    assert runner.os.getenv("NCS_MCP_URL") == "http://existing.example/mcp"


def test_mcp_preflight_reports_missing_url(monkeypatch) -> None:
    monkeypatch.delenv("NCS_MCP_URL", raising=False)

    assert "NCS_MCP_URL is required" in runner._mcp_preflight_error()

    monkeypatch.setenv("NCS_MCP_URL", "http://127.0.0.1:8778/mcp")
    assert runner._mcp_preflight_error() == ""


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
            "interview_methods": "경험면접, 창의적 문제해결력면접",
            "detail_count": 1,
            "exact_detail_count": 1,
            "generated_questions": 6,
            "ready_questions": 6,
            "model_candidate_questions": 6,
            "model_questions": 6,
            "model_full_questions": 6,
            "model_main_template_followup_questions": 0,
            "model_main_repaired_followup_questions": 0,
            "model_ready_questions": 6,
            "model_origin_ready_questions": 6,
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
    assert "Interview methods requested: 경험면접, 창의적 문제해결력면접 (1)" in md_text
    assert "Model candidate questions received: 6" in md_text
    assert "Model-origin questions evaluated: 6" in md_text
    assert "Model-origin ready questions: 6" in md_text
    assert "Fully model-preserved questions: 6" in md_text
    assert "Model-main with repaired follow-ups: 0" in md_text
    assert "Model-main with template follow-ups: 0" in md_text
    assert "Coverage blocker types: none" in md_text
    assert "| idx | status | model pass | full pass | template pass |" in md_text
    assert "| model cand | model-origin | full model | repaired fu | template fu |" in md_text
    assert "model kept" not in md_text
    assert "직무기술서.pdf" in csv_path.read_text(encoding="utf-8-sig")
    item_csv = item_csv_path.read_text(encoding="utf-8-sig")
    assert "question" in item_csv
    assert "canonical_detail" in item_csv
    assert "question_source" in item_csv
    assert "model_question_raw" in item_csv
    assert "model_followups_raw" in item_csv
    assert "model_evaluation_points_raw" in item_csv
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
        "model_followups_raw",
        "model_evaluation_points_raw",
        "model_question_preserved",
        "model_replacement_reasons",
        "follow_ups",
        "evaluation_points",
        "ksa_evidence_count",
    } <= headers
    main_csv = csv_path.read_text(encoding="utf-8-sig")
    assert "manual_review_suggestions" in main_csv
    assert "detail_source" in main_csv
    assert "checked_detail_count" in main_csv
    assert "max_details_per_doc" in main_csv
    assert "max_units_per_detail" in main_csv
    assert "interview_methods" in main_csv
    assert "model_candidate_questions" in main_csv
    assert "model_full_questions" in main_csv
    assert "model_main_template_followup_questions" in main_csv
    assert "model_main_repaired_followup_questions" in main_csv
    assert "model_origin_ready_questions" in main_csv
    assert "model_replaced_by_template_questions" in main_csv
    assert "coverage_blocker_type" in main_csv
    assert "coverage_blocker_details" in main_csv
    assert "resolved_parent_detail" in main_csv
    assert "review_action" in main_csv
    assert "coverage_blocker_reason" in main_csv
    assert "Documents question-ready with contextual detail recovery" in md_text
    assert "Checked detail labels" in md_text
    assert "Detail check limits" in md_text
    assert "## Method Quality" in md_text
    assert "## Question Source" in md_text
    assert "사무행정" in item_csv


def test_write_quality_reports_shows_strict_blockers_in_markdown(tmp_path: Path) -> None:
    rows = [
        {
            "idx": "303039",
            "attachment": "직무기술서.zip",
            "status": "template_ready_unit_name_resolved",
            "resolved_benchmark_mode": "template",
            "detail_source": "explicit",
            "detail_count": 2,
            "exact_detail_count": 1,
            "unit_name_detail_count": 1,
            "unmatched_detail_count": 0,
            "skipped_detail_count": 0,
            "generated_questions": 6,
            "ready_questions": 6,
            "coverage_blocker_type": "카지노 고객 지원: unit_name_only",
            "coverage_blocker_details": '[{"detail":"카지노 고객 지원","coverage_blocker_type":"unit_name_only"}]',
            "resolved_parent_detail": "카지노 고객 지원: 카지노운영관리",
            "review_action": "카지노 고객 지원: manual_review_unit_name",
            "coverage_adjusted_score": 0.0,
            "average_score": 1.0,
        }
    ]

    md_path, csv_path, _item_csv_path = runner.write_quality_reports(rows, [], tmp_path)

    md_text = md_path.read_text(encoding="utf-8")
    csv_text = csv_path.read_text(encoding="utf-8-sig")

    assert "strict blockers" in md_text
    assert "카지노 고객 지원: unit_name_only" in md_text
    assert "unresolved details" in md_text
    assert "coverage_blocker_details" in csv_text


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


def test_write_quality_reports_emits_quality_issue_summary_by_method(tmp_path: Path) -> None:
    rows = [
        {
            "idx": "1",
            "attachment": "jd.txt",
            "status": "needs_review",
            "generated_questions": 3,
            "ready_questions": 1,
            "model_candidate_questions": 0,
            "model_replaced_by_template_questions": 0,
            "template_fallback_questions": 3,
            "average_score": 0.33,
            "coverage_adjusted_score": 0.33,
            "strict_template_passed": False,
            "model_quality_passed": False,
            "passed": False,
        }
    ]
    question_rows = [
        {
            "idx": "1",
            "attachment": "jd.txt",
            "detail": "사무행정",
            "question_index": 1,
            "type": "상황면접",
            "question_source": "template_fallback",
            "question": "fallback",
            "ready": False,
            "issues": "follow_up_quality; ksa_grounded",
        },
        {
            "idx": "1",
            "attachment": "jd.txt",
            "detail": "사무행정",
            "question_index": 2,
            "type": "상황면접",
            "question_source": "template_fallback",
            "question": "fallback",
            "ready": False,
            "issues": "follow_up_quality",
        },
        {
            "idx": "1",
            "attachment": "jd.txt",
            "detail": "사무행정",
            "question_index": 3,
            "type": "발표면접",
            "question_source": "template_fallback",
            "question": "fallback",
            "ready": False,
            "issues": "official_sample_format",
        },
    ]

    md_path, _, _ = runner.write_quality_reports(rows, question_rows, tmp_path)

    md_text = md_path.read_text(encoding="utf-8")
    assert "## Quality Issues By Method" in md_text
    assert "| 상황면접 | follow_up_quality | 2 |" in md_text
    assert "| 상황면접 | ksa_grounded | 1 |" in md_text
    assert "| 발표면접 | official_sample_format | 1 |" in md_text


def test_quality_gate_failures_detects_model_rate_and_fallbacks() -> None:
    rows = [
        {
            "model_candidate_questions": 4,
            "model_origin_ready_questions": 2,
            "model_full_questions": 1,
            "model_main_repaired_followup_questions": 1,
            "model_replaced_by_template_questions": 1,
            "template_inserted_questions": 1,
        }
    ]

    failures = runner.quality_gate_failures(
        rows,
        min_model_ready_rate=0.75,
        min_full_model_rate=0.75,
        fail_on_model_replacements=True,
        fail_on_template_insertions=True,
        fail_on_repaired_followups=True,
    )

    assert "model_origin_ready_rate 0.50 below minimum 0.75" in failures[0]
    assert "full_model_rate 0.25 below minimum 0.75" in failures[1]
    assert "model_main_repaired_followups 1 > 0" in failures
    assert "model_replacements 1 > 0" in failures
    assert "template_insertions 1 > 0" in failures


def test_quality_gate_failures_passes_when_model_questions_are_ready() -> None:
    rows = [
        {
            "model_candidate_questions": "6",
            "model_origin_ready_questions": "6",
            "model_full_questions": "6",
            "model_main_repaired_followup_questions": "0",
            "model_replaced_by_template_questions": "0",
            "template_inserted_questions": "0",
        }
    ]

    failures = runner.quality_gate_failures(
        rows,
        min_model_ready_rate=1.0,
        min_full_model_rate=1.0,
        fail_on_model_replacements=True,
        fail_on_template_insertions=True,
        fail_on_repaired_followups=True,
    )

    assert failures == []


def test_quality_gate_failures_detects_low_evaluated_document_rate() -> None:
    rows = [
        {"status": "template_ready"},
        {"status": "needs_review"},
        {"status": "parsed_no_detail"},
        {"status": "no_exact_units"},
    ]

    failures = runner.quality_gate_failures(rows, min_evaluated_doc_rate=0.75)

    assert failures == [
        "evaluated_doc_rate 0.50 below minimum 0.75 (2/4 documents reached question evaluation)"
    ]


def test_main_returns_quality_gate_failure_for_full_model_and_repaired_followups(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    cache_dir = tmp_path / "cache"
    report_dir = tmp_path / "reports"
    cache_dir.mkdir()
    report_dir.mkdir()
    attachment = cache_dir / "303039_1_jd.txt"
    attachment.write_text("dummy", encoding="utf-8")
    md_path = report_dir / "quality.md"
    csv_path = report_dir / "quality.csv"
    item_csv_path = report_dir / "items.csv"

    monkeypatch.setenv("NCS_MCP_URL", "http://127.0.0.1:8778/mcp")
    monkeypatch.setattr(runner, "iter_cached_attachments", lambda cache, limit: [attachment])
    monkeypatch.setattr(
        runner,
        "evaluate_cached_document",
        lambda **kwargs: (
            {
                "model_candidate_questions": 2,
                "model_origin_ready_questions": 2,
                "model_full_questions": 1,
                "model_main_repaired_followup_questions": 1,
                "model_replaced_by_template_questions": 0,
                "template_inserted_questions": 0,
            },
            [],
        ),
    )
    monkeypatch.setattr(runner, "write_quality_reports", lambda rows, question_rows, out_dir: (md_path, csv_path, item_csv_path))
    monkeypatch.setattr(
        runner.sys,
        "argv",
        [
            "evaluate_alio_question_quality.py",
            "--cache-dir",
            str(cache_dir),
            "--report-dir",
            str(report_dir),
            "--no-load-dotenv",
            "--benchmark-mode",
            "model",
            "--openai-api-key",
            "sk-test",
            "--min-model-ready-rate",
            "1.0",
            "--min-full-model-rate",
            "1.0",
            "--fail-on-repaired-followups",
        ],
    )

    assert runner.main() == 2
    captured = capsys.readouterr()
    assert "quality_gate_failure=full_model_rate 0.50 below minimum 1.00" in captured.err
    assert "quality_gate_failure=model_main_repaired_followups 1 > 0" in captured.err
    assert "rows=1" in captured.out


def test_main_rejects_invalid_min_full_model_rate(monkeypatch, tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setenv("NCS_MCP_URL", "http://127.0.0.1:8778/mcp")
    monkeypatch.setattr(
        runner.sys,
        "argv",
        [
            "evaluate_alio_question_quality.py",
            "--cache-dir",
            str(cache_dir),
            "--no-load-dotenv",
            "--min-full-model-rate",
            "1.2",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        runner.main()

    assert str(exc.value) == "--min-full-model-rate must be between 0 and 1"


def test_main_rejects_invalid_min_evaluated_doc_rate(monkeypatch, tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setenv("NCS_MCP_URL", "http://127.0.0.1:8778/mcp")
    monkeypatch.setattr(
        runner.sys,
        "argv",
        [
            "evaluate_alio_question_quality.py",
            "--cache-dir",
            str(cache_dir),
            "--no-load-dotenv",
            "--min-evaluated-doc-rate",
            "-0.1",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        runner.main()

    assert str(exc.value) == "--min-evaluated-doc-rate must be between 0 and 1"


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
    assert row["model_full_questions"] == 0
    assert row["model_main_template_followup_questions"] == 0
    assert row["model_main_repaired_followup_questions"] == 0
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
    assert row["model_full_questions"] == 1
    assert row["model_main_template_followup_questions"] == 0
    assert row["model_main_repaired_followup_questions"] == 0
    assert row["model_ready_questions"] == 1
    assert row["model_origin_ready_questions"] == 1
    assert row["model_replaced_by_template_questions"] == 0
    assert row["template_inserted_questions"] == 0
    assert row["template_fallback_questions"] == 0
    assert row["model_quality_passed"] is True
    assert row["passed"] is True
    assert len(question_rows) == 1
    assert question_rows[0]["question_source"] == "model"


def test_evaluate_cached_document_model_mode_preserves_main_question_with_template_followups(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "303107_1_jd.txt"
    path.write_text("세분류: 사무행정\n직무내용: 문서 요구사항 파악 및 문서 작성", encoding="utf-8")

    model_question = (
        "문서작성 업무에서 문서 요구사항을 파악해 문제를 해결한 경험을 말씀해 주세요. "
        "당시 상황, 본인 행동, 결과를 포함해 설명해 주세요."
    )

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
            "question_generation_policy": "test_model_main_with_weak_followups",
            "interview_questions": [
                {
                    "type": "경험면접",
                    "competency": "문서작성",
                    "ncsClCd": "0202030201_25v3",
                    "question": model_question,
                    "follow_ups": ["추가로 설명해 주세요."],
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

    assert row["status"] == "template_ready"
    assert row["strategy_generation_policy"] == "test_model_main_with_weak_followups"
    assert row["generated_questions"] == 1
    assert row["model_candidate_questions"] == 1
    assert row["model_questions"] == 1
    assert row["model_full_questions"] == 0
    assert row["model_main_template_followup_questions"] == 1
    assert row["model_main_repaired_followup_questions"] == 0
    assert row["model_ready_questions"] == 0
    assert row["model_origin_ready_questions"] == 0
    assert row["model_replaced_by_template_questions"] == 0
    assert row["template_inserted_questions"] == 0
    assert row["template_fallback_questions"] == 0
    assert row["model_quality_passed"] is False
    assert row["passed"] is False
    assert len(question_rows) == 1
    assert question_rows[0]["question_source"] == "model_main_template_followups"
    assert question_rows[0]["question"] == model_question
    assert question_rows[0]["model_question_raw"] == model_question
    assert "추가로 설명" in question_rows[0]["model_followups_raw"]
    assert question_rows[0]["model_question_preserved"] is True
    assert "follow_up_quality" in question_rows[0]["model_replacement_reasons"]
    assert "문서 요구사항 파악" in str(question_rows[0]["follow_ups"])


def test_evaluate_cached_document_model_mode_counts_repaired_model_followups(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "303108_1_jd.txt"
    path.write_text("세분류: 사무행정\n직무내용: 문서 요구사항 파악 및 문서 작성", encoding="utf-8")

    model_question = (
        "문서작성 업무에서 문서 요구사항 파악 오류와 일정 지연이 동시에 발생한 상황입니다. "
        "어떤 판단 기준과 순서로 행동하고 위험을 통제하겠습니까?"
    )

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
            "question_generation_policy": "test_model_main_with_repairable_followups",
            "interview_questions": [
                {
                    "type": "상황면접",
                    "competency": "문서작성",
                    "ncsClCd": "0202030201_25v3",
                    "question": model_question,
                    "follow_ups": [
                        "우선 확인할 사실은 무엇입니까?",
                        "그 판단에 따른 행동의 이유는 무엇입니까?",
                        "후속점검은 어떻게 진행하겠습니까?",
                    ],
                    "evaluation_points": ["핵심 사실 확인", "판단 기준", "행동 순서와 첫 조치", "위험요인 인식"],
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
        interview_methods=["상황면접"],
    )

    assert row["status"] == "ok_model"
    assert row["strategy_generation_policy"] == "test_model_main_with_repairable_followups"
    assert row["generated_questions"] == 1
    assert row["ready_questions"] == 1
    assert row["model_candidate_questions"] == 1
    assert row["model_questions"] == 1
    assert row["model_full_questions"] == 0
    assert row["model_main_template_followup_questions"] == 0
    assert row["model_main_repaired_followup_questions"] == 1
    assert row["model_ready_questions"] == 0
    assert row["model_origin_ready_questions"] == 1
    assert row["model_quality_passed"] is True
    assert row["passed"] is True
    assert len(question_rows) == 1
    assert question_rows[0]["question_source"] == "model_main_repaired_followups"
    assert question_rows[0]["question"] == model_question
    assert "follow_up_focus_injected" in question_rows[0]["model_replacement_reasons"]
    assert "'문서 요구사항 파악'과 관련해" in question_rows[0]["follow_ups"]
    assert "그 판단에 따른 행동의 이유" in question_rows[0]["follow_ups"]


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
    assert row["model_full_questions"] == 0
    assert row["model_main_template_followup_questions"] == 0
    assert row["model_main_repaired_followup_questions"] == 0
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
    assert row["checked_detail_count"] == 1
    assert row["max_details_per_doc"] == 1
    assert row["max_units_per_detail"] == 8
    assert "skipped_by_max_details_per_doc" in row["coverage_blocker_type"]
    assert "increase_max_details_per_doc_or_manual_select" in row["review_action"]
    assert row["unmatched_detail_count"] == 0
    assert row["skipped_detail_count"] == 2
    assert row["uncovered_detail_count"] == 2
    assert row["coverage_adjusted_score"] == 0.0
    assert row["unmatched_details"] == ""
    assert row["skipped_details"] == "B; C"
    assert len(question_rows) == 6


def test_evaluate_cached_document_aggregates_mixed_coverage_blockers(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "303109_1_jd.txt"
    path.write_text("dummy", encoding="utf-8")

    monkeypatch.setattr(runner, "parse_benchmark_document", lambda data, filename, max_bytes: {"markdown": "dummy"})
    monkeypatch.setattr(
        runner,
        "structure_job_description",
        lambda parsed, filename: {
            "fields": {
                "ncs_detail_candidates": ["카지노 고객 지원", "문화・관광정책", "간호수행", "B"],
                "ncs_detail_source": "explicit",
            }
        },
    )
    monkeypatch.setattr(runner, "search_units_by_detail", lambda details, max_units: [])

    def fake_suggest_units_by_text(details, max_units):
        term = details[0]
        if term == "카지노 고객 지원":
            return [
                {
                    "ncsClCd": "1203040206_23v2",
                    "compeUnitName": "카지노 고객 지원",
                    "ncsSclasCdnm": "관광레저서비스",
                    "ncsSubdCdnm": "카지노운영관리",
                    "canonicalDetailName": "카지노운영관리",
                    "matchedDetailName": term,
                    "isExactUnitNameMatch": True,
                }
            ]
        return []

    monkeypatch.setattr(runner, "suggest_units_by_text", fake_suggest_units_by_text)
    monkeypatch.setattr(
        runner,
        "get_ksa_by_units",
        lambda units, max_factors_per_unit: [
            {
                "ncsClCd": "1203040206_23v2",
                "compeUnitName": "카지노 고객 지원",
                "factorName": "고객 물품 보관 기준 확인",
                "factorSource": "ncs-mcp",
            }
        ],
    )

    row, question_rows = runner.evaluate_cached_document(
        path=path,
        max_bytes=1024 * 1024,
        questions_per_doc=3,
        follow_up_count=3,
        max_details_per_doc=3,
        max_units_per_detail=8,
        ksa_units=2,
        ksa_factors_per_unit=4,
    )

    assert row["status"] == "template_ready_partial_detail_coverage"
    assert row["unit_name_detail_count"] == 1
    assert row["unmatched_detail_count"] == 2
    assert row["skipped_detail_count"] == 1
    assert "카지노 고객 지원: unit_name_only" in row["coverage_blocker_type"]
    assert "문화・관광정책: known_manual_review_catalog_gap" in row["coverage_blocker_type"]
    assert "간호수행: specialized_healthcare_label_unserved_by_mcp" in row["coverage_blocker_type"]
    assert "B: skipped_by_max_details_per_doc" in row["coverage_blocker_type"]
    assert "known_manual_review_catalog_gap" in row["coverage_blocker_details"]
    assert "카지노 고객 지원: 카지노운영관리" in row["resolved_parent_detail"]
    assert "카지노 고객 지원: manual_review_unit_name" in row["review_action"]
    assert "문화・관광정책: manual_review_known_catalog_gap" in row["review_action"]
    assert "간호수행: manual_review_healthcare_specialized_label" in row["review_action"]
    assert "B: increase_max_details_per_doc_or_manual_select" in row["review_action"]
    assert len(question_rows) == 3


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


def test_evaluate_cached_document_records_no_detail_absence_reason(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "303018_1_jd.txt"
    path.write_text("dummy", encoding="utf-8")

    monkeypatch.setattr(runner, "parse_benchmark_document", lambda data, filename, max_bytes: {"markdown": "dummy"})
    monkeypatch.setattr(
        runner,
        "structure_job_description",
        lambda parsed, filename: {
            "fields": {
                "ncs_detail_candidates": [],
                "ncs_detail_source": "",
                "ncs_detail_absence_reason": "no_ncs_mapping_declared",
            }
        },
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

    assert row["status"] == "parsed_no_detail"
    assert row["coverage_blocker_type"] == "parsed_no_detail"
    assert row["review_action"] == "manual_review_parse_or_source_mapping"
    assert row["coverage_blocker_reason"] == "no_ncs_mapping_declared"
    assert row["manual_review_suggestions"] == "parsed-no-detail: no_ncs_mapping_declared"
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


def test_exact_units_by_detail_keeps_canonical_detail_suggestion_for_manual_review(monkeypatch) -> None:
    monkeypatch.setattr(runner, "search_units_by_detail", lambda details, max_units: [])
    monkeypatch.setattr(
        runner,
        "suggest_units_by_text",
        lambda details, max_units: [
            {
                "ncsClCd": "1204020101_23v2",
                "compeUnitName": "문화관광정책 개발",
                "ncsSclasCdnm": "문화·예술",
                "ncsSubdCdnm": "문화·관광정책",
                "canonicalDetailName": "문화·관광정책",
                "matchedDetailName": details[0],
                "isExactDetailMatch": True,
                "isExactUnitNameMatch": False,
                "source": "ncs-mcp-suggest",
            }
        ],
    )

    exact_details, unit_name_details, units, unmatched = runner._exact_units_by_detail(
        ["문화・관광정책"],
        max_units_per_detail=8,
    )

    assert exact_details == []
    assert unit_name_details == []
    assert units == []
    assert unmatched == ["문화・관광정책"]

    _exact_details, _unit_name_details, _units, _unmatched, records = runner._detail_resolution_records(
        ["문화・관광정책"],
        max_units_per_detail=8,
    )

    assert records[0]["coverage_status"] == "unmatched"
    assert records[0]["coverage_blocker_type"] == "catalog_gap_verified_source_label"
    assert records[0]["resolved_parent_detail"] == "문화·관광정책"
    assert records[0]["review_action"] == "manual_review_canonical_detail"


def test_detail_resolution_uses_later_canonical_suggestion_parent(monkeypatch) -> None:
    monkeypatch.setattr(runner, "search_units_by_detail", lambda details, max_units: [])

    def fake_suggest_units_by_text(details, max_units):
        return [
            {
                "ncsClCd": "9999999999",
                "compeUnitName": "Unrelated Unit",
                "ncsSubdCdnm": "other-parent",
                "canonicalDetailName": "other-parent",
                "isExactDetailMatch": False,
                "isExactUnitNameMatch": False,
            },
            {
                "ncsClCd": "1204020101_23v2",
                "compeUnitName": "Culture Policy Unit",
                "ncsSubdCdnm": "culture-policy-parent",
                "canonicalDetailName": "culture-policy-parent",
                "matchedDetailName": details[0],
                "isExactDetailMatch": True,
                "isExactUnitNameMatch": False,
            },
        ]

    monkeypatch.setattr(runner, "suggest_units_by_text", fake_suggest_units_by_text)

    _exact_details, _unit_name_details, _units, _unmatched, records = runner._detail_resolution_records(
        ["culture-policy-parent"],
        max_units_per_detail=8,
    )

    assert records[0]["coverage_status"] == "unmatched"
    assert records[0]["coverage_blocker_type"] == "catalog_gap_verified_source_label"
    assert records[0]["resolved_parent_detail"] == "culture-policy-parent"


def test_exact_units_by_detail_rejects_clinical_false_friend_suggestions(monkeypatch) -> None:
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
                "canonicalDetailName": "병원행정",
                "matchedElementName": "재원환자 관리하기",
                "isExactDetailMatch": False,
                "isExactUnitNameMatch": False,
            }
        ],
    )

    exact_details, unit_name_details, units, unmatched = runner._exact_units_by_detail(
        ["재원환자 관리"],
        max_units_per_detail=8,
    )

    assert exact_details == []
    assert unit_name_details == []
    assert units == []
    assert unmatched == ["재원환자 관리"]


def test_exact_units_by_detail_keeps_healthcare_specialized_unit_name_suggestion_manual(
    monkeypatch,
) -> None:
    monkeypatch.setattr(runner, "search_units_by_detail", lambda details, max_units: [])
    monkeypatch.setattr(
        runner,
        "suggest_units_by_text",
        lambda details, max_units: [
            {
                "ncsClCd": "0601020101_23v1",
                "compeUnitName": "간호행정관리",
                "ncsSclasCdnm": "병원행정",
                "ncsSubdCdnm": "병원행정",
                "canonicalDetailName": "병원행정",
                "matchedDetailName": details[0],
                "isExactUnitNameMatch": True,
                "isExactDetailMatch": False,
            }
        ],
    )

    exact_details, unit_name_details, units, unmatched = runner._exact_units_by_detail(
        ["간호행정관리"],
        max_units_per_detail=8,
    )

    assert exact_details == []
    assert unit_name_details == []
    assert units == []
    assert unmatched == ["간호행정관리"]


def test_exact_units_by_detail_upgrades_healthcare_specialized_label_on_exact_search(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        runner,
        "search_units_by_detail",
        lambda details, max_units: [
            {
                "ncsClCd": "0602020001_26v1",
                "compeUnitName": "간호수행",
                "ncsSclasCdnm": "간호",
                "ncsSubdCdnm": details[0],
                "matchedDetailName": details[0],
            }
        ],
    )
    monkeypatch.setattr(runner, "suggest_units_by_text", lambda details, max_units: [])

    exact_details, unit_name_details, units, unmatched = runner._exact_units_by_detail(
        ["간호수행"],
        max_units_per_detail=8,
    )

    assert exact_details == ["간호수행"]
    assert unit_name_details == []
    assert len(units) == 1
    assert unmatched == []


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
    assert "unit_name_only" in row["coverage_blocker_type"]
    assert row["resolved_parent_detail"]
    assert "manual_review_unit_name" in row["review_action"]
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
