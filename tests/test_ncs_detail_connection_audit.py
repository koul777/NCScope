import json
import subprocess
import sys
from pathlib import Path

from scripts.audit_ncs_detail_connections import audit_catalogs


ROOT = Path(__file__).parents[1]


def _write_catalogs(tmp_path, details, units, detail_count=None, unit_count=None):
    dp = tmp_path / "details.json"
    up = tmp_path / "units.json"
    dp.write_text(json.dumps({"schema_version": 1, "classification_count": len(details) if detail_count is None else detail_count, "details": details}), encoding="utf-8")
    up.write_text(json.dumps({"schema_version": 1, "unit_count": len(units) if unit_count is None else unit_count, "units": units}), encoding="utf-8")
    return dp, up


def test_real_catalog_is_aggregate_pass():
    report = audit_catalogs(ROOT / "app/data/ncs_detail_catalog.json", ROOT / "app/data/ncs_unit_catalog.json")
    assert report["status"] == "pass"
    assert report["counts"]["active_detail"] == 1094
    assert report["counts"]["unit_total"] == 13282
    assert report["collisions"]["multi_version_base"] == 1
    assert "name" not in json.dumps(report["identities"])


def test_connection_and_collision_issues_are_counted(tmp_path):
    details = [
        {"code": "01010101", "name": "Alpha", "usage_yn": "Y"},
        {"code": "01010102", "name": " Alpha ", "usage_yn": "Y"},
    ]
    units = [
        {"code": "0101010101_17v2", "base_code": "0101010101", "name": "One", "detail_code": "01010101", "detail_name": "Beta"},
        {"code": "0101010101_19v2", "base_code": "0101010101", "name": "Two", "detail_code": "99999999", "detail_name": "Missing"},
    ]
    dp, up = _write_catalogs(tmp_path, details, units)
    report = audit_catalogs(dp, up)
    assert report["status"] == "fail"
    assert report["connections"]["detail_unit_name_mismatch"] == 1
    assert report["connections"]["orphan_unit"] == 1
    assert report["collisions"]["normalized_detail_name"] == 1
    assert report["collisions"]["multi_version_base"] == 1
    assert report["identities"]["orphan_unit_detail_codes"] == ["99999999"]


def test_count_error_fails_closed(tmp_path):
    dp, up = _write_catalogs(tmp_path, [{"code": "01010101", "name": "A", "usage_yn": "Y"}], [], detail_count=2)
    report = audit_catalogs(dp, up)
    assert report["status"] == "fail"
    assert "count_mismatch_details" in report["errors"]
    proc = subprocess.run([sys.executable, str(ROOT / "scripts/audit_ncs_detail_connections.py"), "--detail", str(dp), "--unit", str(up)], capture_output=True, text=True)
    assert proc.returncode == 1
    assert "count_mismatch_details" in proc.stdout


def test_code_shape_empty_name_duplicate_and_base_name_findings_fail(tmp_path):
    details = [{"code": "01010101", "name": "A", "usage_yn": "Y"}]
    units = [
        {"code": "0101010201_x", "base_code": "0101010201", "name": "", "detail_code": "01010109", "detail_name": ""},
        {"code": "0101010201_x", "base_code": "0101010301", "name": "Two", "detail_code": "01010103", "detail_name": "Other"},
    ]
    dp, up = _write_catalogs(tmp_path, details, units)
    report = audit_catalogs(dp, up)
    assert report["status"] == "fail"
    assert "unit_base_code_mismatch" in report["errors"]
    assert "base_detail_code_mismatch" in report["errors"]
    assert "empty_unit_name" in report["errors"]
    assert "empty_unit_detail_name" in report["errors"]
    assert "duplicate_full_code" in report["errors"]


def test_inactive_name_collision_is_not_active_collision(tmp_path):
    details = [
        {"code": "01010101", "name": "AㆍB", "usage_yn": "Y"},
        {"code": "01010102", "name": "AB", "usage_yn": "N"},
    ]
    dp, up = _write_catalogs(tmp_path, details, [])
    report = audit_catalogs(dp, up)
    assert report["collisions"]["normalized_detail_name"] == 0


def test_runtime_separator_family_collision_fails_closed(tmp_path):
    details = [
        {"code": "01010101", "name": "A∙B", "usage_yn": "Y"},
        {"code": "01010102", "name": "AB", "usage_yn": "Y"},
    ]
    dp, up = _write_catalogs(tmp_path, details, [])

    report = audit_catalogs(dp, up)

    assert report["status"] == "fail"
    assert report["collisions"]["normalized_detail_name"] == 1
    assert "active_detail_name_collision" in report["errors"]
