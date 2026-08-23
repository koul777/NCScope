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


def test_parse_review_prefers_pdf_detail_column_over_legacy_small_matches(mocker):
    mocker.patch(
        "app.main._parse_upload_document",
        return_value={"markdown": "NCS 분류체계", "blocks": []},
    )
    mocker.patch(
        "app.main.structure_job_description",
        return_value={
            "document": {"markdown": "NCS 분류체계"},
            "fields": {"ncs_detail_candidates": []},
        },
    )
    mocker.patch(
        "app.main.extract_sclass_from_pdf_bytes",
        return_value={
            "matched": ["인사·조직", "일반사무"],
            "detail_candidates": ["인사", "일반사무 지원"],
            "detail_candidate_evidence": [
                {"label": "인사", "page": 1, "source": "pdf_table_detail", "raw": "인사"},
                {
                    "label": "일반사무 지원",
                    "page": 1,
                    "source": "pdf_table_detail",
                    "raw": "일반사무 지원",
                },
            ],
        },
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/api/jd/parse-review",
            files={"jd_file": ("alio-jd.pdf", b"%PDF-test", "application/pdf")},
        )

    assert response.status_code == 200
    fields = response.json()["fields"]
    assert fields["ncs_detail_candidates"] == ["인사", "일반사무 지원"]
    assert fields["ncs_detail_source"] == "pdf_table_detail"
    assert fields["ncs_detail_candidate_evidence"][0]["detail"] == "인사"


def test_parse_review_does_not_promote_small_category_when_detail_table_has_no_mapping(mocker):
    mocker.patch(
        "app.main._parse_upload_document",
        return_value={"markdown": "NCS 분류체계", "blocks": []},
    )
    mocker.patch(
        "app.main.structure_job_description",
        return_value={
            "document": {"markdown": "NCS 분류체계"},
            "fields": {"ncs_detail_candidates": []},
        },
    )
    structural = mocker.patch(
        "app.main.extract_sclass_from_pdf_bytes",
        return_value={
            "matched": ["법무"],
            "detail_candidates": [],
            "detail_table_found": True,
            "detail_candidate_evidence": [],
        },
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/api/jd/parse-review",
            files={"jd_file": ("lawyer-jd.pdf", b"%PDF-test", "application/pdf")},
        )

    assert response.status_code == 200
    fields = response.json()["fields"]
    assert fields["ncs_detail_candidates"] == []
    assert fields["ncs_detail_source"] == "pdf_table_detail_empty"
    structural.assert_called_once()
