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
    assert entries[0].collection_key == "interview-model"
    assert entries[0].lib_dstin_cd == "49"
    assert entries[0].menu_id == "MN02020303"


def test_discover_official_samples_applies_evaluation_sample_collection(monkeypatch) -> None:
    html = """
    <table>
      <tr>
        <td>157</td>
        <td class="subject">
          <a onclick="fn_view('20250115104842318')" title="(2024고도화)통신서비스_20-02-03">
            (2024고도화)통신서비스_20-02-03
          </a>
          <button onclick="gfn_file_downloadFile('01','20250115104839001','20250115104839699', {'downlDstinCd':'09'})">다운로드</button>
        </td>
      </tr>
    </table>
    """
    calls: list[tuple[str, dict[str, str] | None]] = []

    def fake_curl(url: str, post_data: dict[str, str] | None = None, timeout_sec: int = 60) -> bytes:
        calls.append((url, post_data))
        if post_data:
            return b'<span id="iframeBbsNtcSource">view</span>'
        return html.encode("utf-8")

    monkeypatch.setattr(samples, "_curl_bytes", fake_curl)

    collection = samples.SAMPLE_COLLECTIONS["evaluation-sample"]
    entries = samples.discover_official_samples(limit=1, list_url=collection.list_url, collection=collection)
    view_text = samples.fetch_sample_view_text(entries[0])

    assert entries[0].collection_key == "evaluation-sample"
    assert entries[0].collection_label == "전형별 평가샘플"
    assert entries[0].lib_dstin_cd == "30"
    assert entries[0].menu_id == "MN42020301"
    assert view_text == "view"
    assert calls[-1][1] == {
        "libDstinCd": "30",
        "menuId": "MN42020301",
        "libSeq": "20250115104842318",
    }


def test_discover_official_samples_derives_collection_from_evaluation_url(monkeypatch) -> None:
    html = """
    <table>
      <tr>
        <td>157</td>
        <td class="subject">
          <a onclick="fn_view('20250115104842318')" title="evaluation sample">
            evaluation sample
          </a>
          <button onclick="gfn_file_downloadFile('01','20250115104839001','20250115104839699', {'downlDstinCd':'09'})">download</button>
        </td>
      </tr>
    </table>
    """

    monkeypatch.setattr(samples, "_curl_bytes", lambda url: html.encode("utf-8"))

    entries = samples.discover_official_samples(
        limit=1,
        list_url="https://m.ncs.go.kr/blind/rh13/bbs_lib_list.do?libDstinCd=30&menuId=MN42030203",
    )

    assert len(entries) == 1
    assert entries[0].collection_key == "evaluation-sample"
    assert entries[0].lib_dstin_cd == "30"
    assert entries[0].menu_id == "MN42030203"


def test_discover_official_samples_paginates_when_limit_exceeds_first_page(monkeypatch) -> None:
    first_page = """
    <table>
      <tr>
        <td>292</td>
        <td class="subject">
          <a onclick="fn_view('1001')" title="sample 1">sample 1</a>
          <button onclick="gfn_file_downloadFile('01','mst1','detl1', {'downlDstinCd':'09'})">download</button>
        </td>
      </tr>
      <tr>
        <td>291</td>
        <td class="subject">
          <a onclick="fn_view('1002')" title="sample 2">sample 2</a>
          <button onclick="gfn_file_downloadFile('01','mst2','detl2', {'downlDstinCd':'09'})">download</button>
        </td>
      </tr>
    </table>
    """
    second_page = """
    <table>
      <tr>
        <td>290</td>
        <td class="subject">
          <a onclick="fn_view('1003')" title="sample 3">sample 3</a>
          <button onclick="gfn_file_downloadFile('01','mst3','detl3', {'downlDstinCd':'09'})">download</button>
        </td>
      </tr>
    </table>
    """
    calls: list[str] = []

    def fake_curl(url: str) -> bytes:
        calls.append(url)
        return second_page.encode("utf-8") if "pageIndex=1" in url else first_page.encode("utf-8")

    monkeypatch.setattr(samples, "_curl_bytes", fake_curl)

    entries = samples.discover_official_samples(limit=3, list_url=samples.INTERVIEW_LIST_URL)

    assert [entry.seq for entry in entries] == ["1001", "1002", "1003"]
    assert calls[0] == samples.INTERVIEW_LIST_URL
    assert any("pageIndex=1" in url for url in calls)


def test_discover_official_samples_normalizes_existing_page_index(monkeypatch) -> None:
    first_page = """
    <table>
      <tr>
        <td>292</td>
        <td class="subject">
          <a onclick="fn_view('1001')" title="sample 1">sample 1</a>
          <button onclick="gfn_file_downloadFile('01','mst1','detl1', {'downlDstinCd':'09'})">download</button>
        </td>
      </tr>
    </table>
    """
    second_page = """
    <table>
      <tr>
        <td>291</td>
        <td class="subject">
          <a onclick="fn_view('1002')" title="sample 2">sample 2</a>
          <button onclick="gfn_file_downloadFile('01','mst2','detl2', {'downlDstinCd':'09'})">download</button>
        </td>
      </tr>
    </table>
    """
    calls: list[str] = []

    def fake_curl(url: str) -> bytes:
        calls.append(url)
        return second_page.encode("utf-8") if "pageIndex=1" in url else first_page.encode("utf-8")

    monkeypatch.setattr(samples, "_curl_bytes", fake_curl)

    entries = samples.discover_official_samples(limit=2, list_url=f"{samples.INTERVIEW_LIST_URL}&pageIndex=1")

    assert [entry.seq for entry in entries] == ["1001", "1002"]
    assert "pageIndex" not in calls[0]
    assert "pageIndex=1" in calls[1]


def test_download_sample_archive_returns_cached_file_before_network(monkeypatch, tmp_path: Path) -> None:
    entry = samples.OfficialSampleEntry(
        seq="1001",
        title="sample/title",
        file_mstky="mst",
        filedetl_seq="detl",
        filename="sample/title",
    )
    cached = tmp_path / "1001_sample_title.zip"
    cached.write_bytes(b"PK\x03\x04cached")

    def fail_curl(*args, **kwargs) -> bytes:
        raise AssertionError("network should not be called for cached official sample")

    monkeypatch.setattr(samples, "_curl_bytes", fail_curl)

    assert samples.download_sample_archive(entry, tmp_path) == cached


def test_title_ncs_hints_normalizes_code_and_detail_label() -> None:
    assert samples._title_ncs_hints("(2024고도화)통신서비스_20-02-03") == ("20-02-03", "통신서비스")
    assert samples._title_ncs_hints("19-3-18. 자율주행개발 면접과제 및 평가양식") == ("19-03-18", "자율주행개발")


def test_method_and_artifact_type_are_derived_from_member_name() -> None:
    assert samples._method_from_name("경험면접 과제.hwp") == "경험면접"
    assert samples._method_from_name("상황면접 평가양식.hwp") == "상황면접"
    assert samples._method_from_name("발표면접 과제.hwp") == "발표면접"
    assert samples._method_from_name("토론면접 평가양식.hwp") == "토론면접"
    assert samples._method_from_name("창의적 문제해결력 면접과제.hwp") == "창의적 문제해결력면접"
    assert samples._methods_from_name_or_text(
        "통신서비스_20-02-03.hwp",
        "목 차 1) 경험면접 문항 2) 상황면접 문항 3) 발표면접 과제 4) 토론면접 과제 5) 창의적 문제해결력 면접과제",
    ) == ["경험면접", "상황면접", "발표면접", "토론면접", "창의적 문제해결력면접"]
    assert samples._artifact_type("경험면접 평가양식.hwp") == "evaluation_form"
    assert samples._artifact_type("경험면접 과제.hwp") == "task"
    assert samples._artifact_types(
        "통신서비스_20-02-03.hwp",
        "목차 2. 직무기술서 4. 채용공고 6. 면접질문지 7. 면접전형별 평가표",
    ) == ["job_description", "job_posting", "task", "evaluation_form"]


def test_infer_download_suffix_detects_zip_hwp_and_pdf() -> None:
    assert samples._infer_download_suffix(b"PK\x03\x04data") == ".zip"
    assert samples._infer_download_suffix(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 16) == ".hwp"
    assert samples._infer_download_suffix(b"%PDF-1.7\n") == ".pdf"


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
    assert rows[0]["has_candidate_instruction"] is True
    assert rows[0]["task_prompt_style"] == "experience_star_probe"
    assert rows[1]["method"] == "경험면접"
    assert rows[1]["artifact_type"] == "evaluation_form"
    assert rows[1]["has_evaluation_form"] is True
    assert rows[1]["has_scoring_criteria"] is True
    assert rows[1]["evaluation_elements"] == "문제해결; 의사소통 채점 기준을 기록한다."


def test_profile_sample_file_tracks_single_hwp_document_materials(monkeypatch, tmp_path: Path) -> None:
    hwp_path = tmp_path / "통신서비스_20-02-03.hwp"
    hwp_path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 16)

    def fake_parse(data: bytes, filename: str, ocr: bool = False) -> dict[str, str]:
        assert filename == "통신서비스_20-02-03.hwp"
        return {
            "markdown": (
                "2024년 능력중심 채용모델\n"
                "직무기술서\n채용공고\n면접질문지\n면접전형별 평가표\n"
                "1) 경험면접 문항\n2) 상황면접 문항\n3) 발표면접 과제\n"
                "4) 토론면접 과제\n5) 창의적 문제해결력 면접과제\n"
                "응시자 준비시간 10분 발표시간 5분 질의응답 5분 토론시간 20분\n"
                "면접위원 평정 100 90 80 70 60\n평가요소: 문제해결력, 의사소통"
            )
        }

    monkeypatch.setattr(samples, "parse_with_kordoc", fake_parse)

    rows, warnings = samples.profile_sample_file(hwp_path)
    summary = samples.summarize_sample("통신서비스", rows)

    assert warnings == []
    assert {row["method"] for row in rows} == {
        "경험면접",
        "상황면접",
        "발표면접",
        "토론면접",
        "창의적 문제해결력면접",
    }
    assert {row["method_source"] for row in rows} == {"document_text"}
    assert {row["artifact_type"] for row in rows} == {"job_description"}
    assert all("task" in row["artifact_types"] for row in rows)
    assert all("evaluation_form" in row["artifact_types"] for row in rows)
    assert summary["pairing_scope"] == "document"
    assert summary["has_task_and_eval_material"] is True
    assert summary["has_task_and_eval_pairs"] is True
    assert all(row["has_candidate_instruction"] for row in rows)
    assert all(row["has_interviewer_instruction"] for row in rows)
    assert all(row["has_time_limit"] for row in rows)
    assert all(row["has_rating_scale"] for row in rows)
    assert {row["prep_minutes"] for row in rows} == {"10"}
    assert {row["presentation_minutes"] for row in rows} == {"5"}
    assert {row["qa_minutes"] for row in rows} == {"5"}
    assert {row["discussion_minutes"] for row in rows} == {"20"}
    assert all("100" in row["rating_scale_labels"] for row in rows)
    assert all("문제해결력" in row["evaluation_elements"] for row in rows)


def test_profile_document_bytes_uses_method_sections_for_combined_documents() -> None:
    presentation = samples.METHOD_NAMES[2]
    discussion = samples.METHOD_NAMES[3]
    text = (
        "목차\n"
        f"1) {presentation} 과제\n"
        f"2) {discussion} 과제\n"
        f"{presentation} 과제\n"
        "응시자 준비시간 15분 발표 7분 질의응답 5분 "
        "평가요소: 논리 분석, 실행가능성\n"
        f"{discussion} 과제\n"
        "토론시간 30분 입장발표 후 최종 결론을 도출한다. "
        "평가항목: 의사소통, 조정능력\n"
        f"{presentation} 평가양식\n"
        "평가요소: 논리 분석, 실행가능성\n"
        f"{discussion} 평가양식\n"
        "평가항목: 의사소통, 조정능력\n"
    )

    rows, warnings = samples._profile_document_bytes("combined_sample.txt", text.encode("utf-8"))
    by_method = {row["method"]: row for row in rows}

    assert warnings == []
    assert by_method[presentation]["method_context_source"] == "document_section"
    assert by_method[discussion]["method_context_source"] == "document_section"
    assert by_method[presentation]["presentation_minutes"] == "7"
    assert by_method[presentation]["qa_minutes"] == "5"
    assert by_method[presentation]["discussion_minutes"] == ""
    assert by_method[discussion]["discussion_minutes"] == "30"
    assert by_method[discussion]["presentation_minutes"] == ""
    assert "논리 분석" in by_method[presentation]["evaluation_elements"]
    assert "조정능력" not in by_method[presentation]["evaluation_elements"]
    assert "조정능력" in by_method[discussion]["evaluation_elements"]


def test_method_sections_ignore_table_of_contents_markers() -> None:
    presentation = samples.METHOD_NAMES[2]
    discussion = samples.METHOD_NAMES[3]
    text = (
        "목차\n"
        f"1) {presentation} 과제\t10\n"
        f"2) {discussion} 과제\t20\n"
        "공통 안내\n"
        "응시자 준비시간 99분 토론시간 99분 평가요소: 공통누수\n"
        f"{presentation} 과제 1\n"
        "응시자 준비시간 15분 발표 7분 질의응답 5분 "
        "평가요소: 발표요소\n"
        f"{discussion} 과제 1\n"
        "토론시간 30분 입장발표 후 합의안을 도출한다. "
        "평가항목: 토론요소\n"
    )

    rows, warnings = samples._profile_document_bytes("combined_sample.txt", text.encode("utf-8"))
    by_method = {row["method"]: row for row in rows}

    assert warnings == []
    assert by_method[presentation]["method_context_source"] == "document_section"
    assert by_method[discussion]["method_context_source"] == "document_section"
    assert by_method[presentation]["prep_minutes"] == "15"
    assert by_method[discussion]["discussion_minutes"] == "30"
    assert "공통누수" not in by_method[presentation]["evaluation_elements"]
    assert "공통누수" not in by_method[discussion]["evaluation_elements"]


def test_method_sections_use_unsupported_interview_headings_as_boundaries() -> None:
    discussion = samples.METHOD_NAMES[3]
    presentation = samples.METHOD_NAMES[2]
    text = (
        f"{discussion} 과제 1\n"
        "토론시간 30분 입장발표 후 최종 합의안을 도출한다. 평가항목: 조정능력\n"
        "Business Case 면접과제\n"
        "토론시간 99분 발표시간 99분 평가요소: 미지원누수\n"
        f"{presentation} 과제 1\n"
        "응시자 준비시간 15분 발표 7분 질의응답 5분 평가요소: 발표요소\n"
    )

    contexts = samples._method_contexts_from_text(
        "combined_sample.txt",
        text,
        [discussion, presentation],
        "document_text",
    )
    discussion_text, discussion_source = contexts[discussion]

    assert discussion_source == "document_section"
    assert "Business Case" not in discussion_text
    assert "미지원누수" not in discussion_text
    assert "토론시간 30분" in discussion_text


def test_official_sample_structured_signals_extract_method_specific_elements() -> None:
    presentation = samples._official_sample_structured_signals(
        "발표면접",
        "응시자 준비시간 15분 발표 7분 질의응답 5분 면접위원 평정 탁월 우수 보통 미흡 평가요소: 논리 분석, 실현가능성",
    )
    discussion = samples._official_sample_structured_signals(
        "토론면접",
        "토론시간 30분 입장발표 후 최종 결론을 도출한다. 평가항목: 의사소통, 조정능력",
    )
    creative = samples._official_sample_structured_signals(
        "창의적 문제해결력면접",
        "미래예측을 바탕으로 해결안을 제시한다. 평가요소: 미래예측, 창의적 사고, 의사결정",
    )

    assert presentation["task_prompt_style"] == "presentation_materials_qna"
    assert presentation["prep_minutes"] == "15"
    assert presentation["presentation_minutes"] == "7"
    assert presentation["qa_minutes"] == "5"
    assert "탁월" in presentation["rating_scale_labels"]
    assert presentation["evaluation_elements"] == "논리 분석; 실현가능성"
    assert discussion["task_prompt_style"] == "discussion_opening_position"
    assert discussion["discussion_minutes"] == "30"
    assert discussion["evaluation_elements"] == "의사소통; 조정능력"
    assert creative["task_prompt_style"] == "creative_future_prediction_solution"
    assert creative["evaluation_elements"] == "미래예측; 창의적 사고; 의사결정"


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
            "collection": "채용모델 면접문항",
            "collection_id": "interview-model",
            "lib_dstin_cd": "49",
            "menu_id": "MN02020303",
            "seq": "20230426171219499",
            "ncs_code_hint": "19-03-03",
            "detail_label_hint": "?뺣낫?듭떊湲곌린媛쒕컻",
            "title": "정보통신기기개발 면접과제 및 평가양식",
            "view_text": "경험, 상황, 발표, 토론면접 과제 및 평가양식",
            "member_count": 2,
            "methods": "경험면접",
            "task_methods": "경험면접",
            "evaluation_methods": "경험면접",
            "method_count": 1,
            "artifact_types": "task; evaluation_form",
            "pairing_scope": "archive_members",
            "has_task_and_eval_material": True,
            "has_task_and_eval_pairs": True,
            "archive": "sample.zip",
            "download_path": "sample.zip",
            "download_suffix": ".zip",
            "container_type": "zip",
            "warnings": "",
        }
    ]
    member_rows = [
        {
            "collection": "채용모델 면접문항",
            "collection_id": "interview-model",
            "seq": "20230426171219499",
            "title": "정보통신기기개발 면접과제 및 평가양식",
            "member": "경험면접 과제.txt",
            "suffix": ".txt",
            "method": "경험면접",
            "method_source": "filename",
            "method_context_source": "full_document",
            "method_context_chars": 30,
            "artifact_type": "task",
            "artifact_types": "task",
            "chars": 30,
            "has_task_prompt": True,
            "has_evaluation_form": False,
            "has_candidate_instruction": True,
            "has_interviewer_instruction": False,
            "has_time_limit": True,
            "has_scoring_criteria": False,
            "has_rating_scale": False,
            "task_prompt_style": "experience_star_probe",
            "followup_section_labels": "",
            "prep_minutes": "5",
            "presentation_minutes": "",
            "qa_minutes": "",
            "discussion_minutes": "",
            "rating_scale_labels": "",
            "evaluation_elements": "",
            "terms": "평가요소",
        }
    ]

    md_path, csv_path, member_csv_path = samples.write_reports(sample_rows, member_rows, tmp_path)

    md_text = md_path.read_text(encoding="utf-8")
    assert "Samples profiled: 1" in md_text
    assert "Observed methods: 경험면접" in md_text
    assert "Supported methods not observed in sampled files:" in md_text
    assert "Samples with NCS code hints from title: 1" in md_text
    assert "Samples with detail label hints from title: 1" in md_text
    assert "Member method rows with candidate instructions: 1" in md_text
    assert "Member method rows with time-limit signals: 1" in md_text
    assert "Member method rows with structured timing values: 1" in md_text
    assert "Observed task prompt styles: experience_star_probe" in md_text
    assert "인바스켓면접" in md_text
    assert "직무지식면접" in md_text
    csv_text = csv_path.read_text(encoding="utf-8-sig")
    member_csv_text = member_csv_path.read_text(encoding="utf-8-sig")
    assert "collection_id" in csv_text
    assert "ncs_code_hint" in csv_text
    assert "detail_label_hint" in csv_text
    assert "19-03-03" in csv_text
    assert "download_suffix" in csv_text
    assert "정보통신기기개발" in csv_text
    assert "method_source" in member_csv_text
    assert "method_context_source" in member_csv_text
    assert "method_context_chars" in member_csv_text
    assert "has_candidate_instruction" in member_csv_text
    assert "has_time_limit" in member_csv_text
    assert "task_prompt_style" in member_csv_text
    assert "prep_minutes" in member_csv_text
    assert "rating_scale_labels" in member_csv_text
    assert "evaluation_elements" in member_csv_text
    assert "경험면접 과제.txt" in member_csv_text
