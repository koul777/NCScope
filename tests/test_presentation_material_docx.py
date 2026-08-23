from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_generated_presentation_packet_downloads_as_docx() -> None:
    packet = {
        "generated": True,
        "title": "\uBC1C\uD45C\uACFC\uC81C \uC790\uB8CC",
        "ncs_detail": "\uC804\uAE30\uC124\uBE44\uC6B4\uC601",
        "competency": "\uC804\uAE30\uC124\uBE44 \uC810\uAC80",
        "focus": "\uC810\uAC80 \uB2A5\uB825",
        "case_materials": [{"source": "NCS", "field": "\uADFC\uAC70", "value": "\uC790\uB8CC"}],
        "case_facts": ["D+1"],
        "slide_outline": [{"slide": 1, "title": "\uD604\uD669", "instruction": "\uC815\uB9AC"}],
        "use_rules": ["\uC0AC\uB78C \uAC80\uD1A0 \uD544\uC694"],
    }
    with TestClient(app) as client:
        response = client.post("/api/presentation-material/docx", json={"presentation_material": packet})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert response.content[:2] == b"PK"
    assert response.headers["content-disposition"] == 'attachment; filename="presentation-material.docx"'
