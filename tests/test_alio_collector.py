from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "collect_alio_documents.py"
SPEC = importlib.util.spec_from_file_location("ncscope_collect_alio_documents", SCRIPT_PATH)
collector = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = collector
SPEC.loader.exec_module(collector)


def test_date_windows_are_complete_non_overlapping_and_newest_first():
    windows = list(collector._date_windows(date(2025, 1, 1), date(2025, 2, 5), 10))

    assert windows[0] == (date(2025, 1, 27), date(2025, 2, 5))
    assert windows[-1][0] == date(2025, 1, 1)
    for previous, current in zip(windows, windows[1:]):
        assert current[1].toordinal() + 1 == previous[0].toordinal()


def test_profile_examples_require_one_explicit_official_sclass(tmp_path, monkeypatch):
    db_path = tmp_path / "corpus.sqlite3"
    with collector.sqlite3.connect(db_path) as connection:
        collector._schema(connection)
        connection.execute(
            "INSERT INTO postings(posting_id,url,discovered_at) VALUES('1','https://job.alio.go.kr/recruitview.do?idx=1','now')"
        )
        fields = {
            "ncs_detail_candidates": ["경영기획"],
            "ncs_detail_source": "explicit",
            "duties": ["경영계획 수립", "사업환경 분석"],
        }
        connection.execute(
            """
            INSERT INTO documents(posting_id,file_no,url,kind,filename,markdown,fields_json,status,updated_at)
            VALUES('1','11','https://www.alio.go.kr/download/download.json?fileNo=11','job_description','jd.txt','경영계획',?,'parsed','now')
            """,
            (collector._json(fields),),
        )
        connection.commit()
        examples = collector._profile_examples(connection)

    assert len(examples) == 1
    assert examples[0]["sclass_name"] == "경영기획"
    assert examples[0]["ncs_code_no"] == "020101"
