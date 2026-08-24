from __future__ import annotations

import json
from pathlib import Path

from app.main import app


ROOT = Path(__file__).resolve().parents[1]
RELEASE_VERSION = "1.4.7"
DISPLAY_VERSION = "v1.4.7"


def test_release_version_is_consistent_across_product_surfaces() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    package_lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    frontend = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
    mcp_client = (ROOT / "app" / "services" / "ncs_mcp_client.py").read_text(
        encoding="utf-8"
    )

    assert app.version == RELEASE_VERSION
    assert package["version"] == RELEASE_VERSION
    assert package_lock["version"] == RELEASE_VERSION
    assert package_lock["packages"][""]["version"] == RELEASE_VERSION
    assert f"# NCScope {DISPLAY_VERSION}\n" in readme
    assert readme.index("docs/media/ncscope-promo.gif") < readme.index(
        f"# NCScope {DISPLAY_VERSION}\n"
    )
    for relative_path in (
        "docs/media/ncscope-promo.gif",
        "docs/media/ncscope-promo.mp4",
        "docs/media/ncscope-explainer.mp4",
        "docs/media/ncscope-poster.jpg",
    ):
        media = ROOT / relative_path
        assert media.is_file()
        assert media.stat().st_size > 100_000
    assert f"<title>NCScope {DISPLAY_VERSION}</title>" in frontend
    assert f">{DISPLAY_VERSION}</span>" in frontend
    assert f'"clientInfo": {{"name": "ncscope", "version": "{DISPLAY_VERSION[1:]}"}}' in mcp_client
