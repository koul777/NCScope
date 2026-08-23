from fastapi.testclient import TestClient

import app.main as main


def test_parse_review_recovers_pdf_sclass_candidates_when_kordoc_is_empty(mocker):
    mocker.patch(
        "app.main._parse_upload_document",
        return_value={"markdown": "NCS 기반 직무기술서", "blocks": []},
    )
    mocker.patch(
        "app.main.structure_job_description",
        return_value={
            "document": {"markdown": "NCS 기반 직무기술서"},
            "fields": {"ncs_detail_candidates": []},
        },
    )
    structural = mocker.patch(
        "app.main.extract_sclass_from_pdf_bytes",
        return_value={"matched": ["경영기획", "총무"]},
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/api/jd/parse-review",
            files={"jd_file": ("alio-jd.pdf", b"%PDF-test", "application/pdf")},
        )

    assert response.status_code == 200
    fields = response.json()["fields"]
    assert fields["ncs_detail_candidates"] == ["경영기획", "총무"]
    assert fields["ncs_detail_source"] == "pdf_structural_fallback"
    assert fields["ncs_detail_candidate_evidence"][0]["source"] == "pdf_structural_fallback"
    structural.assert_called_once()


def test_parse_review_keeps_kordoc_candidates_as_primary_source(mocker):
    mocker.patch(
        "app.main._parse_upload_document",
        return_value={"markdown": "NCS 기반 직무기술서", "blocks": []},
    )
    mocker.patch(
        "app.main.structure_job_description",
        return_value={
            "document": {"markdown": "NCS 기반 직무기술서"},
            "fields": {"ncs_detail_candidates": ["경영기획"]},
        },
    )
    structural = mocker.patch(
        "app.main.extract_sclass_from_pdf_bytes",
        return_value={"matched": ["총무"]},
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/api/jd/parse-review",
            files={"jd_file": ("alio-jd.pdf", b"%PDF-test", "application/pdf")},
        )

    assert response.status_code == 200
    fields = response.json()["fields"]
    assert fields["ncs_detail_candidates"] == ["경영기획"]
    assert fields.get("ncs_detail_source") != "pdf_structural_fallback"
    structural.assert_not_called()
