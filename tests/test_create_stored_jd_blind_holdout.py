from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "create_stored_jd_blind_holdout.py"
SPEC = importlib.util.spec_from_file_location("create_stored_jd_blind_holdout", SCRIPT)
assert SPEC and SPEC.loader
selector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(selector)


def test_select_records_is_seeded_unique_stratified_and_excluded(tmp_path: Path) -> None:
    for index, suffix in enumerate((".hwp", ".hwp", ".pdf", ".pdf", ".zip")):
        (tmp_path / f"source-{index}{suffix}").write_bytes(f"record-{index}".encode())
    excluded = selector.file_sha256(tmp_path / "source-0.hwp")
    quotas = {".hwp": 1, ".pdf": 2, ".zip": 1}

    first = selector.select_records(
        tmp_path,
        seed="fixed",
        quotas=quotas,
        excluded_hashes={excluded},
        excluded_filenames=set(),
    )
    second = selector.select_records(
        tmp_path,
        seed="fixed",
        quotas=quotas,
        excluded_hashes={excluded},
        excluded_filenames=set(),
    )

    assert first == second
    assert len(first) == 4
    assert len({record["sha256"] for record in first}) == 4
    assert excluded not in {record["sha256"] for record in first}
    assert [record["suffix"] for record in first].count(".pdf") == 2
