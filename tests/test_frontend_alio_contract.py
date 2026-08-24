from __future__ import annotations

from pathlib import Path


INDEX_HTML = Path(__file__).resolve().parents[1] / "app" / "static" / "index.html"


def test_alio_source_panel_can_import_bounded_files_and_keeps_human_review() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert 'id="alioSourceBlock"' in html
    assert 'id="alioUrl"' in html
    assert 'id="btnInspectAlio"' in html
    assert 'id="alioPostingSelect"' in html
    assert 'id="alioAttachmentList"' in html
    assert "fetch('/api/alio/attachments'" in html
    assert "fetch('/api/alio/attachment'" in html
    assert "human_review_required" in html
    assert "자동 가져오기" in html
    assert "세분류는 자동 확정하지 않으며 사람이 추출 근거를 확인해야 합니다." in html
    assert "await parseJdForReview(file)" in html
    assert "await parseNoticeForReview(file)" in html
    assert "function alioAttachmentFilename(contentDisposition, fallback)" in html
    assert "response.headers.get('content-disposition')" in html
    assert "new File([blob], item.name" not in html
