from __future__ import annotations

import csv
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "prepare_ncs_recruitment_goldset.py"
)
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "script_name",
    [
        "prepare_ncs_recruitment_goldset.py",
        "finalize_ncs_recruitment_goldset.py",
        "score_ncs_recruitment_goldset.py",
    ],
)
def test_gold_workflow_scripts_support_documented_direct_cli(script_name: str) -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script_name), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "usage:" in completed.stdout


def load_module():
    spec = importlib.util.spec_from_file_location(
        "prepare_ncs_recruitment_goldset_test", SCRIPT_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def benchmark_payload(case_ids: list[str]) -> dict:
    return {
        "summary": {
            "privacy": {
                "raw_filenames_written": False,
                "raw_document_text_written": False,
                "raw_labels_written": False,
            }
        },
        "cases": [
            {
                "case_id": case_id,
                "posting_id": f"posting-{index}",
                "status": "ok",
                "observed_detail_name_ids": [f"automatic-{index}"],
                "source_detail_name_ids": [f"source-{index}"],
            }
            for index, case_id in enumerate(case_ids, start=1)
        ],
    }


def source_row(case_id: str, path: Path, **extra: str) -> dict[str, str]:
    return {
        "case_id": case_id,
        "local_document_path": str(path),
        "original_filename": extra.get("original_filename", path.name),
        "posting_title": extra.get("posting_title", "private posting title"),
        "source_url": extra.get("source_url", "https://www.ncs.go.kr/blind/example"),
    }


def test_deterministic_split_is_content_based_and_validated() -> None:
    mod = load_module()
    digest = "0" * 64

    assert mod.deterministic_split(digest) == "gold_holdout"
    assert mod.deterministic_split(digest) == mod.deterministic_split(digest)
    with pytest.raises(mod.GoldsetPreparationError):
        mod.deterministic_split("not-a-sha256")
    with pytest.raises(mod.GoldsetPreparationError):
        mod.deterministic_split(digest, holdout_modulus=1)


def test_build_workflow_collapses_content_duplicates_and_starts_without_gold(
    tmp_path: Path,
) -> None:
    mod = load_module()
    duplicate_a = tmp_path / "first.pdf"
    duplicate_b = tmp_path / "same-content-copy.pdf"
    unique = tmp_path / "other.hwp"
    duplicate_a.write_bytes(b"same public JD bytes")
    duplicate_b.write_bytes(b"same public JD bytes")
    unique.write_bytes(b"different public JD bytes")

    payload = benchmark_payload(["case-a", "case-b", "case-c"])
    workflow = mod.build_workflow(
        payload,
        [
            source_row("case-b", duplicate_b),
            source_row("case-c", unique),
            source_row("case-a", duplicate_a),
        ],
    )

    assert workflow["summary"]["benchmark_case_count"] == 3
    assert workflow["summary"]["unique_document_count"] == 2
    assert workflow["summary"]["duplicate_case_count"] == 1
    assert workflow["summary"]["automatic_predictions_are_gold"] is False
    assert workflow["summary"]["is_gold"] is False
    assert all(record["is_gold"] is False for record in workflow["records"])
    assert all(
        record["usage_policy"] == "evaluation_only_no_training_or_rule_tuning"
        for record in workflow["records"]
    )

    duplicate_record = next(
        record for record in workflow["records"] if record["duplicate_case_count"] == 2
    )
    assert duplicate_record["case_ids"] == ["case-a", "case-b"]
    assert duplicate_record["split"] == mod.deterministic_split(
        duplicate_record["split_group_sha256"]
    )

    for rows in (workflow["reviewer_a"], workflow["reviewer_b"]):
        for row in rows:
            assert list(row) == mod.REVIEWER_FIELDS
            assert set(row).isdisjoint({"split", "local_document_path", "source_url"})
            assert row["mapping_state"] == ""
            assert row["detail_names_json"] == ""
            assert row["detail_codes_json"] == ""
            assert not any(
                token in key
                for key in row
                for token in ("observed", "predicted", "source_detail")
            )
    assert all(row["final_detail_codes_json"] == "" for row in workflow["adjudication"])
    assert "automatic-1" not in json.dumps(workflow["reviewer_a"])
    assert "source-1" not in json.dumps(workflow["reviewer_a"])
    mod.validate_workflow(workflow)


def test_build_workflow_keeps_same_posting_documents_in_one_split(
    tmp_path: Path,
) -> None:
    mod = load_module()
    first = tmp_path / "posting-part-a.pdf"
    second = tmp_path / "posting-part-b.pdf"
    third = tmp_path / "other-posting.pdf"
    first.write_bytes(b"first posting attachment")
    second.write_bytes(b"second posting attachment")
    third.write_bytes(b"other posting attachment")
    payload = benchmark_payload(["case-a", "case-b", "case-c"])
    payload["cases"][0]["posting_id"] = "shared-posting"
    payload["cases"][1]["posting_id"] = "shared-posting"

    workflow = mod.build_workflow(
        payload,
        [
            source_row("case-a", first),
            source_row("case-b", second),
            source_row("case-c", third),
        ],
    )

    by_case = {
        case_id: record
        for record in workflow["records"]
        for case_id in record["case_ids"]
    }
    assert by_case["case-a"]["split"] == by_case["case-b"]["split"]
    assert (
        by_case["case-a"]["split_group_sha256"]
        == by_case["case-b"]["split_group_sha256"]
    )
    assert workflow["summary"]["posting_id_cross_split_overlap_count"] == 0
    mod.validate_workflow(workflow)


def test_workflow_recomputes_and_rejects_supplied_component_digest(
    tmp_path: Path,
) -> None:
    mod = load_module()
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    payload = benchmark_payload(["case-a", "case-b"])
    payload["cases"][0]["posting_id"] = "shared-posting"
    payload["cases"][1]["posting_id"] = "shared-posting"
    workflow = mod.build_workflow(
        payload,
        [source_row("case-a", first), source_row("case-b", second)],
    )
    workflow["records"][0]["split_group_sha256"] = "a" * 64
    workflow["records"][0]["split"] = mod.deterministic_split("a" * 64)
    workflow["summary"]["records_sha256"] = mod.sha256_bytes(
        mod.canonical_json_bytes(workflow["records"])
    )

    with pytest.raises(mod.GoldsetPreparationError, match="deterministic split"):
        mod.validate_workflow(workflow)


def test_build_workflow_rejects_tuning_overlap_and_hash_mismatch(tmp_path: Path) -> None:
    mod = load_module()
    document = tmp_path / "jd.pdf"
    document.write_bytes(b"evaluation-only content")
    digest = mod.sha256_file(document)
    payload = benchmark_payload(["case-a"])

    with pytest.raises(mod.GoldsetPreparationError, match="leakage"):
        mod.build_workflow(
            payload,
            [source_row("case-a", document)],
            tuning_hashes={digest},
        )

    mismatched = source_row("case-a", document)
    mismatched["document_sha256"] = "f" * 64
    with pytest.raises(mod.GoldsetPreparationError, match="SHA-256 mismatch"):
        mod.build_workflow(payload, [mismatched])


def test_explicit_tuning_overlap_filter_removes_cases_with_a_local_audit(
    tmp_path: Path,
) -> None:
    mod = load_module()
    tuning_document = tmp_path / "tuning.pdf"
    evaluation_document = tmp_path / "evaluation.pdf"
    tuning_document.write_bytes(b"known tuning content")
    evaluation_document.write_bytes(b"new evaluation content")
    payload = benchmark_payload(["case-tuning", "case-evaluation"])

    filtered_payload, filtered_rows, audit = mod.exclude_tuning_overlap_candidates(
        payload,
        [
            source_row("case-tuning", tuning_document),
            source_row("case-evaluation", evaluation_document),
        ],
        tuning_hashes={mod.sha256_file(tuning_document)},
    )

    assert [row["case_id"] for row in filtered_payload["cases"]] == [
        "case-evaluation"
    ]
    assert [row["case_id"] for row in filtered_rows] == ["case-evaluation"]
    assert audit["excluded_case_ids"] == ["case-tuning"]
    assert audit["excluded_case_count"] == 1
    assert audit["remaining_unique_document_count"] == 1
    assert len(audit["audit_sha256"]) == 64
    workflow = mod.build_workflow(
        filtered_payload,
        filtered_rows,
        tuning_hashes={mod.sha256_file(tuning_document)},
    )
    assert workflow["summary"]["benchmark_case_count"] == 1


def test_tuning_filter_removes_entire_shared_posting_component(tmp_path: Path) -> None:
    mod = load_module()
    tuning_document = tmp_path / "tuning.pdf"
    sibling_document = tmp_path / "sibling.pdf"
    remaining_document = tmp_path / "remaining.pdf"
    tuning_document.write_bytes(b"known tuning")
    sibling_document.write_bytes(b"same posting sibling")
    remaining_document.write_bytes(b"independent evaluation")
    payload = benchmark_payload(["case-tuning", "case-sibling", "case-remaining"])
    payload["cases"][0]["posting_id"] = "shared-posting"
    payload["cases"][1]["posting_id"] = "shared-posting"

    filtered_payload, filtered_rows, audit = mod.exclude_tuning_overlap_candidates(
        payload,
        [
            source_row("case-tuning", tuning_document),
            source_row("case-sibling", sibling_document),
            source_row("case-remaining", remaining_document),
        ],
        tuning_hashes={mod.sha256_file(tuning_document)},
    )

    assert [row["case_id"] for row in filtered_payload["cases"]] == [
        "case-remaining"
    ]
    assert [row["case_id"] for row in filtered_rows] == ["case-remaining"]
    assert audit["excluded_case_ids"] == ["case-sibling", "case-tuning"]
    assert audit["excluded_posting_ids"] == ["shared-posting"]
    assert len(audit["excluded_split_group_sha256"]) == 1


def test_tuning_filter_links_out_of_window_tuning_posting_component(
    tmp_path: Path,
) -> None:
    mod = load_module()
    sibling_document = tmp_path / "sibling.pdf"
    remaining_document = tmp_path / "remaining.pdf"
    sibling_document.write_bytes(b"same posting sibling")
    remaining_document.write_bytes(b"independent evaluation")
    payload = benchmark_payload(["case-sibling", "case-remaining"])
    payload["cases"][0]["posting_id"] = "shared-posting"
    tuning_digest = "f" * 64

    filtered_payload, filtered_rows, audit = mod.exclude_tuning_overlap_candidates(
        payload,
        [
            source_row("case-sibling", sibling_document),
            source_row("case-remaining", remaining_document),
        ],
        tuning_hashes={tuning_digest},
        tuning_posting_ids_by_hash={tuning_digest: {"shared-posting"}},
    )

    assert [row["case_id"] for row in filtered_payload["cases"]] == [
        "case-remaining"
    ]
    assert [row["case_id"] for row in filtered_rows] == ["case-remaining"]
    assert audit["excluded_case_ids"] == ["case-sibling"]
    assert audit["out_of_window_tuning_document_count"] == 1
    assert audit["tuning_document_with_manifest_posting_id_count"] == 1
    assert audit["component_graph_document_count"] == 3


def test_tuning_filter_excludes_transitively_connected_posting_component(
    tmp_path: Path,
) -> None:
    mod = load_module()
    bridge_a = tmp_path / "bridge-a.pdf"
    bridge_a_copy = tmp_path / "bridge-a-copy.pdf"
    bridge_b = tmp_path / "bridge-b.pdf"
    remaining = tmp_path / "remaining.pdf"
    bridge_a.write_bytes(b"bridge attachment")
    bridge_a_copy.write_bytes(b"bridge attachment")
    bridge_b.write_bytes(b"second-hop attachment")
    remaining.write_bytes(b"independent attachment")
    payload = benchmark_payload(
        ["case-a-p1", "case-a-p2", "case-b-p2", "case-remaining"]
    )
    payload["cases"][0]["posting_id"] = "posting-1"
    payload["cases"][1]["posting_id"] = "posting-2"
    payload["cases"][2]["posting_id"] = "posting-2"
    tuning_digest = "e" * 64

    filtered_payload, _, audit = mod.exclude_tuning_overlap_candidates(
        payload,
        [
            source_row("case-a-p1", bridge_a),
            source_row("case-a-p2", bridge_a_copy),
            source_row("case-b-p2", bridge_b),
            source_row("case-remaining", remaining),
        ],
        tuning_hashes={tuning_digest},
        tuning_posting_ids_by_hash={tuning_digest: {"posting-1"}},
    )

    assert [row["case_id"] for row in filtered_payload["cases"]] == [
        "case-remaining"
    ]
    assert audit["excluded_case_ids"] == [
        "case-a-p1",
        "case-a-p2",
        "case-b-p2",
    ]
    assert audit["excluded_posting_ids"] == ["posting-1", "posting-2"]


def test_tuning_filter_rejects_unidentified_out_of_window_tuning_document(
    tmp_path: Path,
) -> None:
    mod = load_module()
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    first.write_bytes(b"first evaluation")
    second.write_bytes(b"second evaluation")

    with pytest.raises(
        mod.GoldsetPreparationError,
        match="outside the candidate corpus require posting_id",
    ):
        mod.exclude_tuning_overlap_candidates(
            benchmark_payload(["case-first", "case-second"]),
            [source_row("case-first", first), source_row("case-second", second)],
            tuning_hashes={"f" * 64},
        )


def test_load_tuning_identities_preserves_document_posting_links(
    tmp_path: Path,
) -> None:
    mod = load_module()
    first_digest = "a" * 64
    second_digest = "b" * 64
    manifest = tmp_path / "tuning.json"
    manifest.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "document_sha256": first_digest,
                        "posting_id": "posting-a",
                    },
                    {
                        "posting_ids": ["posting-b", "posting-c"],
                        "source": {"sha256": second_digest},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    hashes, posting_ids_by_hash = mod.load_tuning_identities([manifest])

    assert hashes == {first_digest, second_digest}
    assert posting_ids_by_hash == {
        first_digest: {"posting-a"},
        second_digest: {"posting-b", "posting-c"},
    }
    assert mod.load_tuning_hashes([manifest]) == hashes


@pytest.mark.parametrize(
    "posting_ids",
    [
        '["shared-posting"',
        '"shared-posting"',
        {"posting": "shared-posting"},
        [""],
        ["shared-posting", " shared-posting "],
    ],
)
def test_load_tuning_identities_rejects_malformed_plural_posting_ids(
    tmp_path: Path,
    posting_ids: object,
) -> None:
    mod = load_module()
    manifest = tmp_path / "tuning.json"
    manifest.write_text(
        json.dumps(
            {"document_sha256": "a" * 64, "posting_ids": posting_ids}
        ),
        encoding="utf-8",
    )

    with pytest.raises(mod.GoldsetPreparationError, match="posting_ids"):
        mod.load_tuning_identities([manifest])


def test_tuning_overlap_filter_rejects_all_candidates_removed(tmp_path: Path) -> None:
    mod = load_module()
    document = tmp_path / "tuning.pdf"
    document.write_bytes(b"only tuning content")

    with pytest.raises(mod.GoldsetPreparationError, match="removed every"):
        mod.exclude_tuning_overlap_candidates(
            benchmark_payload(["case-tuning"]),
            [source_row("case-tuning", document)],
            tuning_hashes={mod.sha256_file(document)},
        )


def test_build_workflow_requires_exact_source_index_coverage(tmp_path: Path) -> None:
    mod = load_module()
    document = tmp_path / "jd.pdf"
    document.write_bytes(b"one document")
    payload = benchmark_payload(["case-a", "case-b"])

    with pytest.raises(mod.GoldsetPreparationError, match="missing 1"):
        mod.build_workflow(payload, [source_row("case-a", document)])


def test_build_workflow_requires_posting_id_for_component_split(tmp_path: Path) -> None:
    mod = load_module()
    document = tmp_path / "jd.pdf"
    document.write_bytes(b"one document")
    payload = benchmark_payload(["case-a"])
    payload["cases"][0]["posting_id"] = ""

    with pytest.raises(mod.GoldsetPreparationError, match="nonblank posting_id"):
        mod.build_workflow(payload, [source_row("case-a", document)])


def test_workflow_integrity_detects_tampering(tmp_path: Path) -> None:
    mod = load_module()
    document = tmp_path / "jd.pdf"
    document.write_bytes(b"tamper test")
    workflow = mod.build_workflow(
        benchmark_payload(["case-a"]),
        [source_row("case-a", document)],
    )

    workflow["reviewer_a"][0]["document_sha256"] = "a" * 64
    with pytest.raises(mod.GoldsetPreparationError, match="does not match manifest"):
        mod.validate_workflow(workflow)


def test_write_workflow_emits_only_local_templates_with_artifact_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = load_module()
    document = tmp_path / "jd.pdf"
    document.write_bytes(b"write workflow")
    workflow = mod.build_workflow(
        benchmark_payload(["case-a"]),
        [source_row("case-a", document)],
    )
    fake_root = tmp_path / "repository"
    fake_root.mkdir()
    monkeypatch.setattr(mod, "ROOT", fake_root)
    output_dir = fake_root / "tmp" / "goldset"

    paths = mod.write_workflow(workflow, output_dir)

    assert set(paths) == {
        "manifest",
        "reviewer_a",
        "reviewer_b",
        "adjudication",
        "do_not_tune",
        "integrity",
    }
    assert all(path.is_file() for path in paths.values())
    integrity = json.loads(paths["integrity"].read_text(encoding="utf-8"))
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    assert integrity["local_only"] is True
    assert integrity["automatic_predictions_are_gold"] is False
    assert integrity["seed_integrity_version"] == mod.SEED_INTEGRITY_VERSION
    assert integrity["candidate_exclusion_audit"] == {
        "applied": False,
        "version": mod.CANDIDATE_EXCLUSION_AUDIT_VERSION,
        "audit_sha256": None,
    }
    assert manifest["summary"]["candidate_exclusion_audit"] == integrity[
        "candidate_exclusion_audit"
    ]
    assert set(integrity["artifact_sha256"]) == {
        "manifest",
        "reviewer_a",
        "reviewer_b",
        "adjudication",
        "do_not_tune",
    }
    with paths["reviewer_a"].open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == mod.REVIEWER_FIELDS
        reviewer_rows = list(reader)
    assert set(reviewer_rows[0]).isdisjoint(
        {"split", "local_document_path", "source_url"}
    )
    assert reviewer_rows[0]["review_status"] == "pending_independent_review"
    assert reviewer_rows[0]["detail_codes_json"] == ""

    with pytest.raises(mod.GoldsetPreparationError, match="tmp/"):
        mod.validate_local_output_dir(fake_root / "tracked-artifacts")


def test_write_workflow_seals_attested_candidate_exclusion_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = load_module()
    tuning_document = tmp_path / "tuning.pdf"
    evaluation_document = tmp_path / "evaluation.pdf"
    tuning_document.write_bytes(b"known tuning content")
    evaluation_document.write_bytes(b"evaluation content")
    tuning_digest = mod.sha256_file(tuning_document)
    payload = benchmark_payload(["case-tuning", "case-evaluation"])
    filtered_payload, filtered_rows, audit = mod.exclude_tuning_overlap_candidates(
        payload,
        [
            source_row("case-tuning", tuning_document),
            source_row("case-evaluation", evaluation_document),
        ],
        tuning_hashes={tuning_digest},
        source_input_sha256={
            "benchmark_json": "a" * 64,
            "source_index": "b" * 64,
            "tuning_manifests": ["c" * 64],
        },
    )
    workflow = mod.build_workflow(
        filtered_payload,
        filtered_rows,
        tuning_hashes={tuning_digest},
    )
    fake_root = tmp_path / "repository"
    output_dir = fake_root / "tmp" / "goldset"
    monkeypatch.setattr(mod, "ROOT", fake_root)

    paths = mod.write_workflow(workflow, output_dir, exclusion_audit=audit)

    assert "candidate_exclusions" in paths
    integrity = json.loads(paths["integrity"].read_text(encoding="utf-8"))
    assert integrity["artifact_count"] == 6
    assert integrity["artifact_sha256"]["candidate_exclusions"] == mod.sha256_file(
        paths["candidate_exclusions"]
    )
    sealed_audit = json.loads(
        paths["candidate_exclusions"].read_text(encoding="utf-8")
    )
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    assert audit["input_artifacts_attested"] is True
    assert len(audit["tuning_document_set_sha256"]) == 64
    assert len(audit["tuning_identity_ledger_sha256"]) == 64
    assert sealed_audit["remaining_records_sha256"] == workflow["summary"][
        "records_sha256"
    ]
    assert manifest["summary"]["candidate_exclusion_audit"] == integrity[
        "candidate_exclusion_audit"
    ]
    assert manifest["summary"]["candidate_exclusion_audit"]["audit_sha256"] == (
        sealed_audit["audit_sha256"]
    )

    tampered = dict(audit)
    tampered["excluded_case_count"] = 99
    with pytest.raises(mod.GoldsetPreparationError, match="audit digest mismatch"):
        mod.write_workflow(workflow, output_dir, exclusion_audit=tampered)


def test_main_seals_raw_inputs_and_rejects_empty_exclusion_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = load_module()
    fake_root = tmp_path / "repository"
    private_dir = fake_root / "private"
    private_dir.mkdir(parents=True)
    tuning_document = private_dir / "tuning.pdf"
    evaluation_document = private_dir / "evaluation.pdf"
    tuning_document.write_bytes(b"known tuning")
    evaluation_document.write_bytes(b"new evaluation")
    benchmark_path = private_dir / "benchmark.json"
    payload = benchmark_payload(["case-tuning", "case-evaluation"])
    benchmark_path.write_text(json.dumps(payload), encoding="utf-8")
    source_index_path = private_dir / "source.csv"
    with source_index_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["case_id", "local_document_path"],
        )
        writer.writeheader()
        writer.writerows(
            [
                {
                    "case_id": "case-tuning",
                    "local_document_path": str(tuning_document),
                },
                {
                    "case_id": "case-evaluation",
                    "local_document_path": str(evaluation_document),
                },
            ]
        )
    tuning_manifest_path = private_dir / "tuning.json"
    tuning_manifest_path.write_text(
        json.dumps(
            [
                {
                    "document_sha256": mod.sha256_file(tuning_document),
                    "posting_id": payload["cases"][0]["posting_id"],
                }
            ]
        ),
        encoding="utf-8",
    )
    output_dir = fake_root / "tmp" / "goldset"
    monkeypatch.setattr(mod, "ROOT", fake_root)

    assert mod.main(
        [
            str(benchmark_path),
            str(source_index_path),
            "--tuning-manifest",
            str(tuning_manifest_path),
            "--exclude-tuning-overlap",
            "--output-dir",
            str(output_dir),
        ]
    ) == 0

    audit = json.loads(
        (output_dir / "candidate_exclusions.local.json").read_text(
            encoding="utf-8"
        )
    )
    assert audit["input_artifact_sha256"] == {
        "benchmark_json": mod.sha256_file(benchmark_path),
        "source_index": mod.sha256_file(source_index_path),
        "tuning_manifests": [mod.sha256_file(tuning_manifest_path)],
    }
    assert audit["excluded_case_ids"] == ["case-tuning"]

    with pytest.raises(
        mod.GoldsetPreparationError,
        match="--tuning-manifest requires --exclude-tuning-overlap",
    ):
        mod.main(
            [
                str(benchmark_path),
                str(source_index_path),
                "--tuning-manifest",
                str(tuning_manifest_path),
                "--output-dir",
                str(fake_root / "tmp" / "missing-exclusion-flag"),
            ]
        )

    tuning_manifest_path.write_text(
        json.dumps(
            [
                {
                    "document_sha256": mod.sha256_file(tuning_document),
                    "posting_ids": '["shared-posting"',
                }
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(mod.GoldsetPreparationError, match="posting_ids"):
        mod.main(
            [
                str(benchmark_path),
                str(source_index_path),
                "--tuning-manifest",
                str(tuning_manifest_path),
                "--exclude-tuning-overlap",
                "--output-dir",
                str(fake_root / "tmp" / "malformed-posting-ids"),
            ]
        )

    with pytest.raises(
        mod.GoldsetPreparationError,
        match="requires a non-empty tuning manifest",
    ):
        mod.main(
            [
                str(benchmark_path),
                str(source_index_path),
                "--exclude-tuning-overlap",
                "--output-dir",
                str(fake_root / "tmp" / "missing-manifest"),
            ]
        )
