from __future__ import annotations

from pathlib import Path


INDEX_HTML = Path(__file__).resolve().parents[1] / "app" / "static" / "index.html"


def test_desktop_layout_keeps_input_and_result_scroll_regions_independent() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert "body { margin:0; min-height:100%; height:100%; overflow-x:hidden; overflow-y:hidden;" in html
    assert ".input-scroll, .result-scroll" in html
    assert "overflow-y:auto; overflow-x:hidden" in html
    assert html.count('class="input-scroll"') == 1
    assert html.count('class="result-scroll"') == 1
    assert html.count(".input-scroll, .result-scroll") == 2


def test_mobile_layout_releases_fixed_height_without_removing_content_access() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert "@media (max-width:980px)" in html
    mobile_rule = html.split("@media (max-width:980px)", 1)[1]
    assert ".input-scroll, .result-scroll { overflow:visible" in mobile_rule
