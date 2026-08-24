from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "audit_stored_jd_coordinate_contract.py"
)
SPEC = importlib.util.spec_from_file_location(
    "audit_stored_jd_coordinate_contract",
    SCRIPT_PATH,
)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def _write_doc(path: Path, text: bytes) -> Path:
    path.write_bytes(text)
    return path


def _valid_structured() -> dict:
    return {
        "fields": {
            "ability_units": ["문서작성", "표밖능력"],
            "positioned_items": [
                {
                    "section": "ability_units",
                    "text": "문서작성",
                    "raw_cell_text": "01.문서 작성",
                    "page": 0,
                    "table_index": 0,
                    "source": "kordoc",
                    "layout": "row_label_value",
                    "label_cell": {
                        "row": 2,
                        "column": 0,
                        "row_span": 1,
                        "column_span": 1,
                    },
                    "value_cell": {
                        "row": 2,
                        "column": 1,
                        "row_span": 1,
                        "column_span": 2,
                    },
                    "row_context_cells": [
                        {
                            "text": "능력단위",
                            "column": 0,
                            "row_span": 1,
                            "column_span": 1,
                        },
                        {
                            "text": "01.문서 작성",
                            "column": 1,
                            "row_span": 1,
                            "column_span": 2,
                        },
                    ],
                },
                {
                    "section": "ncs_detail",
                    "text": "문서작성",
                    "raw_cell_text": "문서작성",
                    "page": 0,
                    "table_index": 0,
                    "label_cell": {
                        "row": 1,
                        "column": 0,
                        "row_span": 1,
                        "column_span": 1,
                    },
                    "value_cell": {
                        "row": 1,
                        "column": 1,
                        "row_span": 1,
                        "column_span": 1,
                    },
                    "row_context_cells": [
                        {
                            "text": "세분류",
                            "column": 0,
                            "row_span": 1,
                            "column_span": 1,
                        },
                        {
                            "text": "문서작성",
                            "column": 1,
                            "row_span": 1,
                            "column_span": 1,
                        },
                    ],
                },
            ],
        }
    }


def test_audit_counts_valid_positioned_items_and_separates_final_ability_denominator(
    tmp_path: Path,
) -> None:
    doc = _write_doc(tmp_path / "private-employer.pdf", b"same bytes")

    def parse_file(path: Path, max_bytes: int):
        assert path == doc
        assert max_bytes == 1024
        return {"markdown": "private markdown"}, _valid_structured()

    result = audit.audit_corpus(
        [doc],
        expected_files=1,
        expected_unique_contents=1,
        max_file_bytes=1024,
        parse_file=parse_file,
    )

    summary = result["summary"]
    assert summary["passed"] is True
    assert summary["coordinate_contract_item_count"] == 2
    assert summary["coordinate_contract_valid_item_count"] == 2
    assert summary["coordinate_contract_pct"] == 100.0
    assert summary["coordinate_contract_definition"] == (
        "logical_coordinate_shape_and_raw_value_cell_text_alignment_"
        "not_native_page_fidelity"
    )
    assert summary["kordoc_block_derived_coordinate_item_count"] == 1
    assert summary["kordoc_block_derived_coordinate_pct"] == 50.0
    assert summary["direct_table_coordinate_item_count"] == 0
    assert summary["direct_table_coordinate_pct"] == 0.0
    assert summary["recovered_logical_coordinate_item_count"] == 1
    assert summary["final_ability_unit_count"] == 2
    assert summary["final_ability_with_table_position_count"] == 1
    assert summary["final_ability_without_table_position_count"] == 1
    assert summary["final_ability_has_table_position_pct"] == 50.0
    assert summary["final_ability_table_position_association"] == (
        "document_unique_normalized_exact_name_non_recall_diagnostic"
    )
    assert summary["final_unique_ability_name_with_position_pct"] == 50.0
    assert summary["positioned_ability_source_counts"] == {"kordoc": 1}
    assert summary["positioned_ability_layout_counts"] == {"row_label_value": 1}


def test_audit_flags_missing_coordinate_fields_without_using_final_ability_denominator(
    tmp_path: Path,
) -> None:
    doc = _write_doc(tmp_path / "private-doc.hwp", b"doc")

    def parse_file(_path: Path, _max_bytes: int):
        return {
            "markdown": "private markdown"
        }, {
            "fields": {
                "ability_units": ["문서작성"],
                "positioned_items": [
                    {
                        "section": "ability_units",
                        "text": "문서작성",
                        "raw_cell_text": "문서작성",
                        "page": None,
                        "table_index": 0,
                        "label_cell": {
                            "row": 2,
                            "column": 0,
                            "row_span": 1,
                            "column_span": 1,
                        },
                        "value_cell": {
                            "row": 2,
                            "column": 1,
                            "row_span": 0,
                            "column_span": 1,
                        },
                        "row_context_cells": [
                            {
                                "text": "문서작성",
                                "column": 1,
                                "row_span": 1,
                                "column_span": 1,
                            }
                        ],
                    }
                ],
            }
        }

    result = audit.audit_corpus(
        [doc],
        expected_files=1,
        expected_unique_contents=1,
        max_file_bytes=1024,
        parse_file=parse_file,
    )

    summary = result["summary"]
    assert summary["passed"] is False
    assert summary["coordinate_contract_item_count"] == 1
    assert summary["coordinate_contract_valid_item_count"] == 0
    assert summary["coordinate_contract_pct"] == 0.0
    assert summary["final_ability_unit_count"] == 1
    assert summary["final_ability_has_table_position_pct"] == 100.0
    assert summary["reason_counts"] == {
        "invalid_value_cell_span": 1,
        "missing_page": 1,
    }
    assert result["failures"] == [
        {
            "seq": 1,
            "suffix": ".hwp",
            "status": "coordinate_contract_failure",
            "reasons": ["invalid_value_cell_span", "missing_page"],
        }
    ]


def test_audit_rejects_value_cell_that_points_to_code_instead_of_item_text(
    tmp_path: Path,
) -> None:
    doc = _write_doc(tmp_path / "private-code-cell.pdf", b"coordinate")
    structured = _valid_structured()
    ability = structured["fields"]["positioned_items"][0]
    ability["value_cell"]["column"] = 1
    ability["row_context_cells"] = [
        {
            "text": "0202030201_22v3",
            "column": 1,
            "row_span": 1,
            "column_span": 1,
        },
        {
            "text": "01.문서 작성",
            "column": 2,
            "row_span": 1,
            "column_span": 1,
        },
    ]

    def parse_file(_path: Path, _max_bytes: int):
        return {"markdown": ""}, structured

    result = audit.audit_corpus(
        [doc],
        expected_files=1,
        expected_unique_contents=1,
        max_file_bytes=1024,
        parse_file=parse_file,
    )

    assert result["summary"]["passed"] is False
    assert result["summary"]["reason_counts"] == {
        "value_cell_evidence_text_mismatch": 1
    }


def test_coordinate_alignment_allows_ordinal_around_raw_evidence_text() -> None:
    item = _valid_structured()["fields"]["positioned_items"][0]
    item["raw_cell_text"] = "문서 작성"

    assert audit._coordinate_reasons(item) == []


def test_coordinate_alignment_requires_explicit_raw_cell_text() -> None:
    item = _valid_structured()["fields"]["positioned_items"][0]
    item.pop("raw_cell_text")

    assert audit._coordinate_reasons(item) == ["missing_raw_cell_text"]


def test_audit_rejects_negative_page_and_table_indexes(tmp_path: Path) -> None:
    doc = _write_doc(tmp_path / "private-negative.pdf", b"negative")
    structured = _valid_structured()
    structured["fields"]["positioned_items"][0]["page"] = -1
    structured["fields"]["positioned_items"][0]["table_index"] = -2

    def parse_file(_path: Path, _max_bytes: int):
        return {"markdown": ""}, structured

    result = audit.audit_corpus(
        [doc],
        expected_files=1,
        expected_unique_contents=1,
        max_file_bytes=1024,
        parse_file=parse_file,
    )

    assert result["summary"]["passed"] is False
    assert result["summary"]["reason_counts"] == {
        "invalid_page": 1,
        "invalid_table_index": 1,
    }


def test_audit_fails_closed_when_corpus_has_no_positioned_evidence(
    tmp_path: Path,
) -> None:
    doc = _write_doc(tmp_path / "private-empty.pdf", b"empty")

    def parse_file(_path: Path, _max_bytes: int):
        return {"markdown": ""}, {"fields": {"ability_units": [], "positioned_items": []}}

    result = audit.audit_corpus(
        [doc],
        expected_files=1,
        expected_unique_contents=1,
        max_file_bytes=1024,
        parse_file=parse_file,
    )

    assert result["summary"]["passed"] is False
    assert result["summary"]["audit_failures"] == [
        "insufficient_positioned_evidence"
    ]


def test_audit_report_does_not_expose_original_filenames_or_text(tmp_path: Path) -> None:
    source_name = "private-employer-secret-jd.pdf"
    source_text = "민감한 원문"
    doc = _write_doc(tmp_path / source_name, source_text.encode("utf-8"))

    def parse_file(_path: Path, _max_bytes: int):
        return {"markdown": source_text}, _valid_structured()

    result = audit.audit_corpus(
        [doc],
        expected_files=1,
        expected_unique_contents=1,
        max_file_bytes=1024,
        parse_file=parse_file,
    )
    json_path, md_path = audit.write_reports(result, tmp_path / "reports")
    rendered = json_path.read_text(encoding="utf-8") + md_path.read_text(
        encoding="utf-8"
    )

    assert source_name not in rendered
    assert source_text not in rendered
    assert "private-employer" not in rendered
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert (
        "logical_coordinate_shape_and_raw_value_cell_text_alignment_"
        "not_native_page_fidelity"
    ) in rendered
    assert "Kordoc native-block-derived" not in rendered
    assert payload["cases"] == [
        {"seq": 1, "suffix": ".pdf", "status": "ok", "reasons": []}
    ]


def test_audit_reports_corpus_drift_without_paths(tmp_path: Path) -> None:
    first = _write_doc(tmp_path / "first.pdf", b"dup")
    second = _write_doc(tmp_path / "second.pdf", b"dup")

    def parse_file(_path: Path, _max_bytes: int):
        return {"markdown": ""}, {"fields": {"ability_units": [], "positioned_items": []}}

    result = audit.audit_corpus(
        [first, second],
        expected_files=3,
        expected_unique_contents=2,
        max_file_bytes=1024,
        parse_file=parse_file,
    )

    summary = result["summary"]
    assert summary["passed"] is False
    assert summary["corpus_failures"] == [
        "unexpected_corpus_size",
        "unexpected_unique_content_count",
    ]
    assert summary["files"] == 2
    assert summary["unique_contents"] == 1


def test_audit_redacts_exception_messages_that_may_contain_private_source_data(
    tmp_path: Path,
) -> None:
    private_name = "private-employer-secret-jd.pdf"
    private_text = "private source text"
    doc = _write_doc(tmp_path / private_name, b"document")

    def parse_file(_path: Path, _max_bytes: int):
        raise RuntimeError(f"{private_name}: {private_text}")

    result = audit.audit_corpus(
        [doc],
        expected_files=1,
        expected_unique_contents=1,
        max_file_bytes=1024,
        parse_file=parse_file,
    )
    rendered = json.dumps(result, ensure_ascii=False)

    assert private_name not in rendered
    assert private_text not in rendered
    assert result["failures"] == [
        {
            "seq": 1,
            "suffix": ".pdf",
            "status": "parse_error",
            "reasons": ["runtime_parse_error"],
        }
    ]
