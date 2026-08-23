from __future__ import annotations

from pathlib import Path


INDEX_HTML = Path(__file__).resolve().parents[1] / "app" / "static" / "index.html"


def test_alio_source_panel_is_bounded_metadata_only_and_keeps_human_file_selection() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert 'id="alioSourceBlock"' in html
    assert 'id="alioUrl"' in html
    assert 'id="btnInspectAlio"' in html
    assert 'id="alioPostingSelect"' in html
    assert 'id="alioAttachmentList"' in html
    assert "fetch('/api/alio/attachments'" in html
    assert "human_review_required" in html
    assert "파일은 사람이 선택해 기존 업로드 칸에 탑재합니다." in html
    assert "자동 전송하지 않습니다." in html
    assert "notice와 job_description 파일을 각각 내려받아" in html

