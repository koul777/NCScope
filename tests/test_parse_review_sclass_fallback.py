from fastapi.testclient import TestClient

import app.main as main


def test_parse_review_canonicalizes_split_table_label_before_human_review(mocker):
    mocker.patch(
        "app.main._parse_upload_document",
        return_value={"markdown": "NCS 세분류명: 프로젝트 관리", "blocks": []},
    )
    mocker.patch(
        "app.main.structure_job_description",
        return_value={
            "document": {"markdown": "NCS 세분류명: 프로젝트 관리"},
            "fields": {"ncs_detail_candidates": ["프로젝트 관리"]},
        },
    )
    mocker.patch(
        "app.main.lookup_ncs_codes_by_sclass",
        return_value=[{"sclass_name": "프로젝트관리"}],
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/api/jd/parse-review",
            files={"jd_file": ("alio-jd.txt", b"job", "text/plain")},
        )

    assert response.status_code == 200
    assert response.json()["fields"]["ncs_detail_candidates"] == ["프로젝트관리"]


def test_parse_review_uses_real_kordoc_structure_for_alias_and_full_code(mocker):
    """Exercise the public review boundary without replacing the JD structurer."""

    mocker.patch(
        "app.main._parse_upload_document",
        return_value={
            "markdown": "NCS 세분류명: (02010101) 프로젝트관리\n담당업무: 사업 일정 및 이해관계자 관리",
            "blocks": [],
        },
    )
    mocker.patch(
        "app.main.lookup_ncs_codes_by_sclass",
        return_value=[{"sclass_name": "프로젝트관리"}],
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/api/jd/parse-review",
            files={"jd_file": ("alio-coded-jd.txt", b"job", "text/plain")},
        )

    assert response.status_code == 200
    fields = response.json()["fields"]
    assert fields["ncs_detail_candidates"] == ["프로젝트관리"]
    assert fields["ncs_detail_source"] == "explicit"
    assert fields["ncs_detail_candidate_evidence"][0]["source"] == "kordoc"
    assert "02010101" in fields["ncs_detail_candidate_evidence"][0]["snippet"]


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


def test_parse_review_reuses_structural_pdf_result_after_kordoc_failure(mocker):
    mocker.patch(
        "app.main.parse_with_kordoc",
        side_effect=main.KordocParseError("bridge unavailable"),
    )
    mocker.patch("app.main.extract_pdf_text", return_value="NCS classification")
    structural = mocker.patch(
        "app.main.extract_sclass_from_pdf_bytes",
        return_value={
            "matched": [],
            "detail_candidates": ["인사"],
            "detail_table_found": True,
            "detail_candidate_evidence": [
                {
                    "label": "인사",
                    "page": 1,
                    "source": "pdf_table_detail",
                    "raw": "인사",
                }
            ],
        },
    )
    mocker.patch(
        "app.main.structure_job_description",
        return_value={
            "document": {"markdown": "NCS classification"},
            "fields": {"ncs_detail_candidates": []},
        },
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/api/jd/parse-review",
            files={"jd_file": ("fallback.pdf", b"%PDF-test", "application/pdf")},
        )

    assert response.status_code == 200
    assert response.json()["fields"]["ncs_detail_candidates"] == ["인사"]
    assert main._PARSED_STRUCTURAL_SCLASS_CACHE_KEY not in response.text
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


def test_parse_review_skips_pdf_recovery_when_document_declares_no_ncs_mapping(mocker):
    mocker.patch(
        "app.main._parse_upload_document",
        return_value={"markdown": "NCS classification: not applicable", "blocks": []},
    )
    mocker.patch(
        "app.main.structure_job_description",
        return_value={
            "document": {"markdown": "NCS classification: not applicable"},
            "fields": {
                "ncs_detail_candidates": [],
                "ncs_detail_absence_state": "declared_no_mapping",
                "ncs_detail_absence_declared_no_mapping": True,
                "ncs_detail_absence_evidence": "not applicable",
            },
        },
    )
    structural = mocker.patch("app.main.extract_sclass_from_pdf_bytes")

    with TestClient(main.app) as client:
        response = client.post(
            "/api/jd/parse-review",
            files={"jd_file": ("declared-none.pdf", b"%PDF-test", "application/pdf")},
        )

    assert response.status_code == 200
    fields = response.json()["fields"]
    assert fields["ncs_detail_candidates"] == []
    assert fields["ncs_detail_absence_state"] == "declared_no_mapping"
    assert fields["ncs_detail_absence_declared_no_mapping"] is True
    structural.assert_not_called()


def test_parse_review_filters_marker_only_ability_artifacts_before_review_state(
    mocker,
):
    mocker.patch(
        "app.main._parse_upload_document",
        return_value={"markdown": "NCS ?몃텇瑜섎챸: PR", "blocks": []},
    )
    mocker.patch(
        "app.main.structure_job_description",
        return_value={
            "sections": {
                "ability_units": [
                    {"text": "¡", "section": "ability_units"},
                    {"text": "온라인 PR", "section": "ability_units"},
                ]
            },
            "document": {"markdown": "NCS ?몃텇瑜섎챸: PR"},
            "fields": {
                "ncs_detail_candidates": ["PR"],
                "ability_units": ["¡", "온라인 PR"],
                "ability_units_by_detail": {"PR": ["¡", "온라인 PR"]},
                "positioned_items": [
                    {"section": "ability_units", "text": "¡"},
                    {"section": "ability_units", "text": "온라인 PR"},
                ],
            },
        },
    )
    ability_states = mocker.patch(
        "app.main.classify_official_ability_unit_names",
        return_value=[
            {
                "sourceName": "온라인 PR",
                "mappingState": "official_exact_source_scoped",
                "catalogExact": True,
                "candidateDetailCodes": ["02010201"],
                "resolvedUnitCodes": ["0201020103_24v3"],
            }
        ],
    )
    mocker.patch("app.main.classify_official_detail_names", return_value=[])
    mocker.patch(
        "app.main.derive_detail_candidates_from_exact_ability_scopes",
        return_value=[],
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/api/jd/parse-review",
            files={"jd_file": ("artifact.txt", b"job", "text/plain")},
        )

    assert response.status_code == 200
    fields = response.json()["fields"]
    assert fields["ability_units"] == ["온라인 PR"]
    assert fields["ability_units_by_detail"] == {"PR": ["온라인 PR"]}
    assert [
        item["text"]
        for item in fields["positioned_items"]
        if item["section"] == "ability_units"
    ] == ["온라인 PR"]
    assert [
        item["text"]
        for item in response.json()["sections"]["ability_units"]
    ] == ["온라인 PR"]
    ability_states.assert_called_once_with(
        ["온라인 PR"],
        selected_detail_names=["PR"],
    )
