from __future__ import annotations

import importlib.util
import io
from pathlib import Path
import sys
import zipfile


_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_ncs_official_interview_samples.py"
_SPEC = importlib.util.spec_from_file_location("ncscope_ncs_official_interview_samples", _SCRIPT_PATH)
assert _SPEC and _SPEC.loader
samples = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = samples
_SPEC.loader.exec_module(samples)


def _zip_path(tmp_path: Path, files: dict[str, str]) -> Path:
    path = tmp_path / "sample.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return path


def test_discover_official_samples_parses_view_and_download_keys(monkeypatch) -> None:
    html = """
    <table>
      <tr>
        <td>292</td>
        <td class="subject">
          <a onclick="fn_view('20230426171219499')" title="19-3-3. 정보통신기기개발 면접과제 및 평가양식(22년 고도화)">
            19-3-3. 정보통신기기개발 면접과제 및 평가양식(22년 고도화)
          </a>
          <button onclick="gfn_file_downloadFile('01','20230426171213386','20230426171213912', {'downlDstinCd':'09'})">다운로드</button>
        </td>
      </tr>
    </table>
    """

    monkeypatch.setattr(samples, "_curl_bytes", lambda url: html.encode("utf-8"))

    entries = samples.discover_official_samples(limit=1)

    assert len(entries) == 1
    assert entries[0].seq == "20230426171219499"
    assert entries[0].file_mstky == "20230426171213386"
    assert entries[0].filedetl_seq == "20230426171213912"
    assert entries[0].title == "19-3-3. 정보통신기기개발 면접과제 및 평가양식(22년 고도화)"


def test_method_and_artifact_type_are_derived_from_member_name() -> None:
    assert samples._method_from_name("경험면접 과제.hwp") == "경험면접"
    assert samples._method_from_name("상황면접 평가양식.hwp") == "상황면접"
    assert samples._method_from_name("발표면접 과제.hwp") == "발표면접"
    assert samples._method_from_name("토론면접 평가양식.hwp") == "토론면접"
    assert samples._artifact_type("경험면접 평가양식.hwp") == "evaluation_form"
    assert samples._artifact_type("경험면접 과제.hwp") == "task"


def test_profile_sample_archive_tracks_method_task_and_evaluation(tmp_path: Path) -> None:
    archive_path = _zip_path(
        tmp_path,
        {
            "경험면접 과제.txt": "면접과제\n응시자는 고객 요구사항을 파악하고 해결 방안을 설명하시오.",
            "경험면접 평가양식.txt": "평가양식\n평가요소: 문제해결, 의사소통\n채점 기준을 기록한다.",
        },
    )

    rows, warnings = samples.profile_sample_archive(archive_path)

    assert warnings == []
    assert len(rows) == 2
    assert rows[0]["method"] == "경험면접"
    assert rows[0]["artifact_type"] == "task"
    assert rows[0]["has_task_prompt"] is True
    assert rows[1]["method"] == "경험면접"
    assert rows[1]["artifact_type"] == "evaluation_form"
    assert rows[1]["has_evaluation_form"] is True


def test_extract_terms_cleans_html_table_fragments() -> None:
    text = """
    <table><tr><td>평가요소: 문제해결<br>의사소통</td><td></td></tr></table>
    평가양식 채점
    """

    terms = samples._extract_terms(text)

    assert terms[0] == "문제해결 의사소통 평가양식 채점"
    assert "<td>" not in " ".join(terms)
    assert "<br>" not in " ".join(terms)


def test_write_reports_emits_sample_and_member_csv(tmp_path: Path) -> None:
    sample_rows = [
        {
            "seq": "20230426171219499",
            "title": "정보통신기기개발 면접과제 및 평가양식",
            "view_text": "경험, 상황, 발표, 토론면접 과제 및 평가양식",
            "member_count": 2,
            "methods": "경험면접",
            "task_methods": "경험면접",
            "evaluation_methods": "경험면접",
            "method_count": 1,
            "has_task_and_eval_pairs": True,
            "archive": "sample.zip",
            "warnings": "",
        }
    ]
    member_rows = [
        {
            "seq": "20230426171219499",
            "title": "정보통신기기개발 면접과제 및 평가양식",
            "member": "경험면접 과제.txt",
            "suffix": ".txt",
            "method": "경험면접",
            "artifact_type": "task",
            "chars": 30,
            "has_task_prompt": True,
            "has_evaluation_form": False,
            "terms": "평가요소",
        }
    ]

    md_path, csv_path, member_csv_path = samples.write_reports(sample_rows, member_rows, tmp_path)

    assert "Samples profiled: 1" in md_path.read_text(encoding="utf-8")
    assert "정보통신기기개발" in csv_path.read_text(encoding="utf-8-sig")
    assert "경험면접 과제.txt" in member_csv_path.read_text(encoding="utf-8-sig")
