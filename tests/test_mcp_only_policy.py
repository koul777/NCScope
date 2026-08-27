from __future__ import annotations

import asyncio
import json
import io
import lzma
import threading
import time
import zipfile
import zlib
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.services import jd_strategy, ncs_mcp_client, openai_http
from app.services.jd_strategy import fetch_ncs_ksa_by_units


JD_TEXT = "\uc138\ubd84\ub958: \uacbd\uc601\uae30\ud68d\n\ub2f4\ub2f9\uc5c5\ubb34: \uacbd\uc601\uacc4\ud68d \uc218\ub9bd"
REQUEST_OPENAI_KEY = "sk-test-request-scoped-key"
SERVER_OPENAI_KEY = "sk-test-server-env-must-be-ignored"


@pytest.fixture(autouse=True)
def _attested_parser_execution_for_endpoint_mocks(mocker):
    def bind(structured):
        structured.setdefault(
            "parser_executions",
            [
                {
                    "schema_version": "ncscope_parser_execution_v1",
                    "role": "selected",
                    "parser": "plain_text",
                    "mode": "builtin_plain_text",
                    "build_identity": {"kind": "python_runtime_bundle"},
                    "runtime_bundle_sha256": main._evaluation_runtime_attestation()[
                        "runtime_bundle_sha256"
                    ],
                }
            ],
        )

    mocker.patch("app.main._bind_parser_executions_to_runtime", side_effect=bind)


@pytest.fixture(autouse=True)
def _stub_independent_ai_quality_review(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        main,
        "review_interview_questions_with_ai",
        lambda **kwargs: {
            "status": "passed",
            "reviewed_count": len(kwargs.get("questions") or []),
            "scores": [],
            "reason_codes": [],
            "items": [],
            "model": "gpt-5.6-sol",
            "provider": "openai_api",
        },
    )


def _openai_model_strategy(
    unit: dict,
    ksa: dict,
    methods: tuple[str, ...] = ("경험면접", "상황면접", "발표면접"),
    follow_up_count: int = 3,
) -> dict:
    competency = str(unit["compeUnitName"])
    focus = str(ksa["factorName"])
    evidence_id = main.stable_ksa_evidence_id(ksa)
    market_focus = "시장" in focus or "환경" in focus
    experience_question = (
        "다음 연도 사업계획 확정 직전, 고객 수요조사와 전년도 실적이 서로 다른 "
        "방향을 가리킨 실제 경험을 말씀해 주세요. 어떤 시장 환경 자료를 근거로 "
        "전망을 선택했고, 본인이 수정한 수요 비교표가 최종 계획에 반영된 결과를 설명해 주세요."
        if market_focus
        else "두 부서가 같은 문서에 서로 다른 필수 조건을 요구해 초안 승인이 멈춘 "
        "실제 경험을 말씀해 주세요. 원문과 요청 기록을 어떻게 대조해 필수 조건과 "
        "조정 가능한 조건을 구분했고, 본인이 만든 요구사항 대조표가 반영된 결과를 설명해 주세요."
    )
    situation_question = (
        "신규 사업의 수요조사는 성장 가능성을 보이지만 경쟁기관 자료와 지역 인구 "
        "추세는 서로 충돌하고 계획 확정은 오늘입니다. 어느 시장 환경 자료를 먼저 검증해 "
        "전망 기준을 정할지 판단하고, 조건별 수요 분석표를 제시해 주세요."
        if market_focus
        else "공문 원문에는 제출 대상이 전체 부서로 적혀 있지만 담당 부서의 요구서에는 "
        "일부 부서만 기재되어 서로 충돌하고 오늘 결재해야 합니다. 어느 원문을 먼저 확인해 적용 "
        "범위를 정할지 판단하고, 수정된 요구사항 대조표를 제시해 주세요."
    )
    presentation_question = (
        "지역별 수요표에는 신청 증가가, 전년도 실적표에는 참여 감소가 나타나고 가용 "
        "예산도 줄었습니다. 이 세 자료의 차이를 진단해 계획에 반영할 전망 하나를 선택하고, "
        "근거와 조건을 담은 시장환경 자료 비교표를 발표해 주세요."
        if market_focus
        else "공문 원문, 부서별 요구서, 현재 사업계획서에서 제출 대상이 서로 충돌하고 필수 항목 하나도 누락됐습니다. "
        "차이를 진단해 이번 결재본에 반영할 기준 하나를 선택하고, 근거와 예외를 담은 "
        "요구사항 대조표를 발표해 주세요."
    )
    discussion_question = (
        "[토론과제] 기획부서는 최근 수요조사의 성장 신호를 계획에 즉시 반영하자고 하지만, "
        "현업부서는 전년도 실적 하락이 확인되기 전에는 즉시 반영할 수 없다고 합니다. 계획 "
        "확정 마감이 임박했고 예산도 부족합니다. 기획부서 입장과 현업부서 입장의 "
        "품질·일정·비용 영향을 비교해 "
        "시장환경 자료와 전망 적용 범위를 합의하되, 합의가 어려우면 남은 쟁점과 결정권자에게 "
        "상신할 공동 수요 기준안을 제시해 주세요."
        if market_focus
        else "[토론과제] 요청 부서는 긴급 일정 때문에 현재 초안을 먼저 결재하자고 하고, "
        "검토 부서는 공고 원문과 다른 필수 항목을 바로잡기 전에는 결재할 수 없다고 합니다. "
        "확인할 문서와 적용 범위를 합의해 공동 요구사항 대조안을 제시해 주세요."
    )
    drafts = {
        "경험면접": {
            "question": experience_question,
            "follow_ups": [
                "방금 선택한 근거와 반대되는 자료를 당시 빠뜨렸다면 무엇이며 어떻게 다시 확인하겠습니까?",
                "앞서 정한 적용 범위에 예외를 요구하는 부서가 생겼다면 어느 기준으로 수용 여부를 정하겠습니까?",
                "말씀한 산출물이 최종안에 반영된 결과를 어떤 승인 기록이나 전후 변화로 확인했습니까?",
                "같은 상황이 반복된다면 방금 설명한 검토 순서에서 무엇을 먼저 바꾸겠습니까?",
                "그 개선이 실제로 작동했는지는 어떤 기록으로 확인하시겠습니까?",
            ],
            "evaluation_points": [
                "상황과 담당 역할의 구체성",
                "자료 확인과 판단 기준의 타당성",
                "본인이 수행한 행동의 명확성",
                "결과 지표와 후속 개선의 연계성",
            ],
        },
        "상황면접": {
            "question": situation_question,
            "follow_ups": [
                "방금 먼저 보겠다고 한 자료의 작성 시점이 다르다면 신뢰도를 어떻게 다시 판단하겠습니까?",
                "앞서 선택한 기준 때문에 제외되는 대상이 생겼다면 어느 조건에서 예외를 인정하겠습니까?",
                "제시한 산출물에서 판단 근거가 빠졌다는 검토 의견이 오면 무엇을 보완하겠습니까?",
                "그 결정이 권한을 벗어난다는 사실을 알게 되면 누구에게 어떤 근거로 보고하겠습니까?",
                "실행 뒤 예상과 다른 결과가 나오면 어떤 조건에서 결정을 수정하시겠습니까?",
            ],
            "evaluation_points": [
                "핵심 사실과 자료 확인의 정확성",
                "대안별 위험과 영향 비교의 타당성",
                "행동 및 보고 순서의 실현 가능성",
                "사후 점검과 재발 방지 계획의 구체성",
            ],
        },
        "발표면접": {
            "question": presentation_question,
            "follow_ups": [
                "앞서 선택한 근거 자료의 집계 범위가 다르다면 분석을 어떻게 수정하겠습니까?",
                "앞서 선택한 대안의 전제가 틀렸다는 반증이 나오면 어느 조건에서 권고를 바꾸겠습니까?",
                "제시한 실행 산출물에서 누락된 이해관계자가 있다면 누구이며 어떻게 반영하겠습니까?",
                "그 권고의 성과를 확인할 지표와 보고 시점은 무엇입니까?",
                "질의응답에서 핵심 전제가 흔들리면 발표 결론을 어떻게 조정하시겠습니까?",
            ],
            "evaluation_points": [
                "자료 분석 근거의 정확성",
                "대안 비교와 발표 구조의 논리성",
                "실행 계획과 역할 배분의 구체성",
                "성과 지표와 질의응답의 일관성",
            ],
        },
        "토론면접": {
            "question": discussion_question,
            "follow_ups": [
                "방금 수용하겠다고 하신 상대 입장의 근거가 실제 자료에 없다면 어느 사실을 추가로 확인하겠습니까?",
                "그 확인 결과 앞서 정한 합의 범위에 한쪽의 핵심 위험이 남는 것으로 나왔다면 어떤 예외 조건을 두겠습니까?",
                "제시한 공동안에서 책임 주체가 빠져 있다면 누구의 역할을 어떻게 보완하겠습니까?",
                "합의안의 실행 결과를 어느 기록으로 점검하겠습니까?",
                "실행 중 새로운 쟁점이 생기면 어떤 기준으로 다시 합의하시겠습니까?",
            ],
            "evaluation_points": [
                "대안별 장단점 분석의 균형성",
                "주장과 근거 연결의 논리성",
                "상대 의견 수용과 조정 행동",
                "합의안과 실행 조건의 구체성",
            ],
        },
    }

    return {
        "interview_questions": [
            {
                "type": method,
                "method": method,
                "question_source": "openai_api",
                "question_evidence_id": evidence_id,
                "question_focus": focus,
                "question_focus_source": "official_ksa",
                "question_focus_type": str(ksa.get("ksaTypeName") or "지식"),
                "ncsClCd": str(unit["ncsClCd"]),
                "competency": competency,
                "ncs_detail": str(unit["ncsSubdCdnm"]),
                "ksa_refs": [focus],
                **drafts[method],
                "follow_ups": list(drafts[method]["follow_ups"][:follow_up_count]),
            }
            for method in methods
        ]
    }


def test_json_responses_declare_utf8_for_windows_clients() -> None:
    with TestClient(main.app) as client:
        response = client.get("/health")

    assert response.status_code in {200, 503}
    assert response.headers["content-type"].lower() == "application/json; charset=utf-8"


def _upload_files() -> dict:
    return {
        "jd_file": ("jd.txt", JD_TEXT.encode("utf-8"), "text/plain"),
        "notice_file": ("notice.txt", "\uba74\uc811\ud3c9\uac00: \ubb38\uc81c\ud574\uacb0\ub2a5\ub825".encode("utf-8"), "text/plain"),
    }


def _patch_mcp_upload_common(mocker) -> None:
    mocker.patch("app.main.extract_small_categories_from_jd", return_value=[])
    mocker.patch("app.main.extract_detail_categories_from_jd", return_value=[])
    mocker.patch("app.main.extract_subcategory_text", return_value="")
    mocker.patch(
        "app.main.resolve_sclass_candidates_bundle",
        return_value={
            "reverse_sclass_candidates": [],
            "direct_sclass_candidates_raw": [],
            "csv_sclass_candidates": [],
            "verified_sclass": [],
        },
    )
    mocker.patch("app.main.infer_keywords_from_subcategory_ai", return_value=[])
    mocker.patch("app.main.review_ocr_terms_with_openai", return_value=[])


def _zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return buffer.getvalue()


def _mark_zip_encrypted(data: bytes) -> bytes:
    blob = bytearray(data)
    local_sig = b"PK\x03\x04"
    central_sig = b"PK\x01\x02"
    start = 0
    while True:
        idx = blob.find(local_sig, start)
        if idx < 0:
            break
        flags = int.from_bytes(blob[idx + 6 : idx + 8], "little") | 0x1
        blob[idx + 6 : idx + 8] = flags.to_bytes(2, "little")
        start = idx + 4
    start = 0
    while True:
        idx = blob.find(central_sig, start)
        if idx < 0:
            break
        flags = int.from_bytes(blob[idx + 8 : idx + 10], "little") | 0x1
        blob[idx + 8 : idx + 10] = flags.to_bytes(2, "little")
        start = idx + 4
    return bytes(blob)


def test_parse_review_returns_detail_candidates(mocker):
    mocker.patch("app.main.parse_with_kordoc", return_value={"markdown": JD_TEXT})
    mocker.patch(
        "app.main.structure_job_description",
        return_value={
            "document": {"markdown": JD_TEXT},
            "fields": {"ncs_detail_candidates": ["\uacbd\uc601\uae30\ud68d"]},
        },
    )

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/parse-review",
            files={"jd_file": ("jd.pdf", b"%PDF-test", "application/pdf")},
        )

    assert resp.status_code == 200
    assert resp.json()["fields"]["ncs_detail_candidates"] == ["\uacbd\uc601\uae30\ud68d"]
    assert resp.json()["evaluation_runtime_attestation"] == (
        main._evaluation_runtime_attestation()
    )


def test_parse_review_accepts_direct_hwp_with_kordoc(mocker):
    parse = mocker.patch("app.main.parse_with_kordoc", return_value={"markdown": JD_TEXT})

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/parse-review",
            files={"jd_file": ("job-description.hwp", b"hwp-binary", "application/x-hwp")},
        )

    assert resp.status_code == 200
    assert resp.json()["fields"]["ncs_detail_candidates"] == ["\uacbd\uc601\uae30\ud68d"]
    parse.assert_called_once()


def test_parse_review_rejects_runtime_files_changed_after_startup(mocker):
    changed = main._evaluation_runtime_attestation()
    changed["source_artifact_sha256"]["official_detail_catalog"] = "0" * 64
    mocker.patch(
        "app.main._build_evaluation_runtime_attestation",
        return_value=changed,
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/api/jd/parse-review",
            files={"jd_file": ("jd.txt", JD_TEXT.encode("utf-8"), "text/plain")},
        )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == (
        "evaluation_runtime_changed_after_startup"
    )


def test_parse_review_recovers_hwp_table_terms_after_partial_kordoc_success(mocker):
    flat_text = """
    NCS 분류체계
    대분류
    중분류
    소분류
    세분류(직무)
    02. 경영·회계·사무
    02. 총무·인사
    03. 일반사무
    02. 사무행정
    주요사업
    """
    mocker.patch(
        "app.main.parse_with_kordoc",
        return_value={
            "markdown": "담당업무: 문서 접수와 기록물 관리",
            "metadata": {"filename": "job-description.hwp"},
        },
    )
    extract = mocker.patch("app.main.extract_hwp_text", return_value=flat_text)
    search = mocker.patch(
        "app.main.search_units_by_detail",
        return_value=[{"ncsSubdCdnm": "사무행정", "ncsClCd": "0202030201_25v3"}],
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/api/jd/parse-review",
            files={"jd_file": ("job-description.hwp", b"hwp-binary", "application/x-hwp")},
        )

    assert response.status_code == 200
    assert response.json()["fields"]["ncs_detail_candidates"] == ["사무행정"]
    assert response.json()["fields"]["ncs_detail_source"] == "hwp_text_mcp_exact"
    extract.assert_called_once()
    search.assert_called_once()
    assert "사무행정" in search.call_args.args[0]
    assert search.call_args.kwargs == {"max_units": 200}


def test_parse_review_recovers_official_pdf_detail_from_mixed_no_mapping_zip(mocker):
    no_mapping_text = """
    NCS 분류체계
    세분류(NCS 미개발) 공학적방벽특성평가
    해당직무는 현재 NCS 분류체계상 Mapping 가능한 직무(세분류)가 없어,
    별도 분석을 통해 내용 도출
    """
    explicit_text = """
    NCS 분류체계
    대분류
    중분류
    소분류
    세분류(직무)
    02. 경영·회계·사무
    02. 총무·인사
    03. 일반사무
    02. 사무행정
    주요사업
    """
    data = _zip_bytes(
        {
            "research_no_mapping.pdf": b"pdf-one",
            "administration_with_ncs.pdf": b"pdf-two",
        }
    )
    mocker.patch(
        "app.main.parse_with_kordoc",
        side_effect=main.KordocParseError("node unavailable"),
    )
    extract = mocker.patch(
        "app.main.extract_pdf_text",
        side_effect=[no_mapping_text, explicit_text],
    )
    structural = mocker.patch(
        "app.main.extract_sclass_from_pdf_bytes",
        side_effect=[
            {"detail_candidates": ["공학적방벽특성평가"], "detail_table_found": True},
            {"detail_candidates": ["사무행정"], "detail_table_found": True},
        ],
    )
    search = mocker.patch(
        "app.main.search_units_by_detail",
        return_value=[{"ncsSubdCdnm": "사무행정", "ncsClCd": "0202030201_25v3"}],
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/api/jd/parse-review",
            files={"jd_file": ("mixed-jobs.zip", data, "application/zip")},
        )

    assert response.status_code == 200
    fields = response.json()["fields"]
    assert fields["ncs_detail_candidates"] == ["사무행정"]
    assert fields["ncs_detail_source"] == "pdf_text_mcp_exact"
    assert fields["ncs_detail_absence_declared_no_mapping"] is False
    assert extract.call_count == 2
    assert structural.call_count == 2
    search.assert_called_once()
    assert "사무행정" in search.call_args.args[0]
    assert search.call_args.kwargs == {"max_units": 200}


def test_flattened_multi_document_recovery_scales_mcp_unit_budget(mocker):
    terms = [
        "경영기획",
        "경영평가",
        "총무",
        "사무행정",
        "문헌정보관리",
        "산업안전관리공통직무",
    ]
    parsed = {
        "markdown": "",
        "metadata": {"classification_terms": terms},
    }
    fields = {
        "ncs_detail_candidates": [],
        "ncs_detail_absence_reason": "no_ncs_mapping_declared",
        "ncs_detail_absence_declared_no_mapping": True,
    }
    search = mocker.patch(
        "app.main.search_units_by_detail",
        return_value=[{"ncsSubdCdnm": term, "ncsClCd": f"code-{index}"} for index, term in enumerate(terms)],
    )

    main._recover_hangul_fallback_detail_candidates(parsed, fields)

    assert fields["ncs_detail_candidates"] == terms
    assert fields["ncs_detail_source"] == "pdf_text_mcp_exact"
    assert fields["ncs_detail_absence_declared_no_mapping"] is False
    search.assert_called_once_with(terms, max_units=240)


def test_single_pdf_fallback_keeps_structural_candidates_without_broad_mcp_search(mocker):
    parsed = {
        "markdown": "NCS 분류체계와 직무 표",
        "metadata": {
            "fallback": "pdf-text",
            "classification_terms": [
                "프로젝트관리",
                "산학협력관리",
                "경영기획",
                "경영평가",
                "총무",
                "인사",
                "예산",
            ],
        },
    }
    fields = {
        "ncs_detail_candidates": ["프로젝트관리", "인사"],
        "ncs_detail_source": "pdf_structural_table",
    }
    search = mocker.patch("app.main.search_units_by_detail")

    main._recover_hangul_fallback_detail_candidates(parsed, fields)

    assert fields["ncs_detail_candidates"] == ["프로젝트관리", "인사"]
    assert fields["ncs_detail_source"] == "pdf_structural_table"
    search.assert_not_called()


def test_single_hwp_keeps_structural_candidates_without_flattened_broadening(mocker):
    parsed = {
        "markdown": "NCS 분류체계와 구조화된 표",
        "metadata": {
            "hangul_classification_terms": [
                "전기기기제작",
                "전기기기설계",
                "전기설비설계",
            ],
        },
    }
    fields = {
        "ncs_detail_candidates": ["전기기기설계", "전기설비설계"],
        "ncs_detail_source": "explicit",
    }
    search = mocker.patch("app.main.search_units_by_detail")

    main._recover_hangul_fallback_detail_candidates(parsed, fields)

    assert fields["ncs_detail_candidates"] == ["전기기기설계", "전기설비설계"]
    assert fields["ncs_detail_source"] == "explicit"
    search.assert_not_called()


def test_parse_review_keeps_kordoc_result_when_optional_hwp_reader_crashes(mocker):
    mocker.patch("app.main.parse_with_kordoc", return_value={"markdown": JD_TEXT})
    mocker.patch("app.main.extract_hwp_text", side_effect=RuntimeError("bad ole crc"))

    with TestClient(main.app) as client:
        response = client.post(
            "/api/jd/parse-review",
            files={"jd_file": ("job-description.hwp", b"hwp-binary", "application/x-hwp")},
        )

    assert response.status_code == 200
    assert response.json()["fields"]["ncs_detail_candidates"] == ["경영기획"]
    assert "bad ole crc" not in response.text


def test_parse_review_recovers_direct_hwp_without_node_runtime(mocker):
    flat_text = """
    NCS 분류체계
    대분류
    중분류
    소분류
    세분류(직무)
    02. 경영·회계·사무
    02. 총무·인사
    03. 일반사무
    02. 사무행정
    주요사업
    """
    mocker.patch("app.main.parse_with_kordoc", side_effect=main.KordocParseError("node unavailable"))
    extract = mocker.patch("app.main.extract_hwp_text", return_value=flat_text)
    search = mocker.patch(
        "app.main.search_units_by_detail",
        return_value=[{"ncsSubdCdnm": "사무행정", "ncsClCd": "0202030201_25v3"}],
    )

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/parse-review",
            files={"jd_file": ("job-description.hwp", b"hwp-binary", "application/x-hwp")},
        )

    assert resp.status_code == 200
    assert resp.json()["fields"]["ncs_detail_candidates"] == ["사무행정"]
    assert resp.json()["fields"]["ncs_detail_source"] == "hwp_text_mcp_exact"
    extract.assert_called_once()
    search.assert_called_once()


def test_parse_review_accepts_zip_with_supported_jd_text():
    data = _zip_bytes({"job_description.txt": JD_TEXT})

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/parse-review",
            files={"jd_file": ("jd.zip", data, "application/zip")},
        )

    body = resp.json()
    assert resp.status_code == 200
    assert body["fields"]["ncs_detail_candidates"] == ["\uacbd\uc601\uae30\ud68d"]
    assert "ZIP member: job_description.txt" in body["document"]["markdown"]


def test_zip_parser_accepts_all_documents_up_to_bounded_member_limit() -> None:
    data = _zip_bytes(
        {
            f"job_description_{index:02}.txt": f"세분류: 경영기획\n담당업무: {index}"
            for index in range(main._ARCHIVE_MEMBER_LIMIT)
        }
    )

    parsed = main._parse_upload_document(data, "jobs.zip", "jd_file")

    assert len(parsed["metadata"]["members"]) == main._ARCHIVE_MEMBER_LIMIT
    assert not any("member limit" in warning for warning in parsed["warnings"])


def test_zip_parser_rejects_partial_processing_above_member_limit() -> None:
    data = _zip_bytes(
        {
            f"job_description_{index:02}.txt": "세분류: 경영기획"
            for index in range(main._ARCHIVE_MEMBER_LIMIT + 1)
        }
    )

    with pytest.raises(main.HTTPException) as exc_info:
        main._parse_upload_document(data, "jobs.zip", "jd_file")

    assert exc_info.value.status_code == 422
    assert "split the archive" in str(exc_info.value.detail)


def test_zip_parser_rejects_excessive_central_directory_before_opening() -> None:
    data = _zip_bytes(
        {
            f"unsupported_{index:03}.bin": "x"
            for index in range(main._ARCHIVE_ENTRY_LIMIT + 1)
        }
    )

    with pytest.raises(main.HTTPException) as exc_info:
        main._parse_upload_document(data, "many-entries.zip", "jd_file")

    assert exc_info.value.status_code == 422
    assert "too many entries" in str(exc_info.value.detail)


def test_zip_parser_rejects_large_directory_when_eocd_count_is_forged() -> None:
    data = bytearray(
        _zip_bytes(
            {
                f"unsupported_{index:03}_{'x' * 1100}.bin": "x"
                for index in range(main._ARCHIVE_ENTRY_LIMIT)
            }
        )
    )
    eocd = data.rfind(b"PK\x05\x06")
    assert eocd >= 0
    data[eocd + 8 : eocd + 10] = (1).to_bytes(2, "little")
    data[eocd + 10 : eocd + 12] = (1).to_bytes(2, "little")
    assert main._zip_declared_entry_count(bytes(data)) == 1

    with pytest.raises(main.HTTPException) as exc_info:
        main._parse_upload_document(bytes(data), "forged-count.zip", "jd_file")

    assert exc_info.value.status_code == 422
    assert "directory is too large" in str(exc_info.value.detail)


def test_zip_parser_maps_unsupported_directory_version_to_422() -> None:
    data = bytearray(_zip_bytes({"job_description.txt": JD_TEXT}))
    central = data.find(b"PK\x01\x02")
    assert central >= 0
    data[central + 6 : central + 8] = (198).to_bytes(2, "little")

    with pytest.raises(main.HTTPException) as exc_info:
        main._parse_upload_document(bytes(data), "unsupported-version.zip", "jd_file")

    assert exc_info.value.status_code == 422
    assert "not a readable ZIP archive" in str(exc_info.value.detail)


def test_zip_parser_maps_corrupt_deflate_stream_to_422(mocker) -> None:
    data = _zip_bytes({"job_description.txt": JD_TEXT})
    mocker.patch(
        "app.main.zipfile.ZipFile.read",
        side_effect=zlib.error("invalid compressed stream"),
    )

    with pytest.raises(main.HTTPException) as exc_info:
        main._parse_upload_document(data, "corrupt-stream.zip", "jd_file")

    assert exc_info.value.status_code == 422
    assert "no parseable" in str(exc_info.value.detail)


def test_zip_parser_maps_corrupt_lzma_stream_to_422(mocker) -> None:
    data = _zip_bytes({"job_description.txt": JD_TEXT})
    mocker.patch(
        "app.main.zipfile.ZipFile.read",
        side_effect=lzma.LZMAError("invalid LZMA stream"),
    )

    with pytest.raises(main.HTTPException) as exc_info:
        main._parse_upload_document(data, "corrupt-lzma.zip", "jd_file")

    assert exc_info.value.status_code == 422
    assert "no parseable" in str(exc_info.value.detail)


def test_parse_review_maps_oversized_html_table_span_to_422() -> None:
    data = b'<table><tr><td colspan="50000">detail</td></tr></table>'

    with TestClient(main.app) as client:
        response = client.post(
            "/api/jd/parse-review",
            files={"jd_file": ("oversized-table.txt", data, "text/plain")},
        )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "document_structure_limit_exceeded",
        "retryable": False,
    }


def test_parse_review_accepts_zip_with_hwp_member(mocker):
    data = _zip_bytes({"job_description.hwp": "hwp binary"})
    parse = mocker.patch("app.main.parse_with_kordoc", return_value={"markdown": JD_TEXT})

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/parse-review",
            files={"jd_file": ("jd.zip", data, "application/zip")},
        )

    body = resp.json()
    assert resp.status_code == 200
    assert body["fields"]["ncs_detail_candidates"] == ["\uacbd\uc601\uae30\ud68d"]
    assert "ZIP member: job_description.hwp" in body["document"]["markdown"]
    parse.assert_called_once()


def test_parse_review_accepts_zip_with_supported_jd_image(mocker):
    data = _zip_bytes({"job_description.jpg": "fake image bytes"})
    parse = mocker.patch("app.main.parse_with_kordoc", return_value={"markdown": JD_TEXT})

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/parse-review",
            files={"jd_file": ("jd.zip", data, "application/zip")},
        )

    body = resp.json()
    assert resp.status_code == 200
    assert body["fields"]["ncs_detail_candidates"] == ["\uacbd\uc601\uae30\ud68d"]
    assert "ZIP member: job_description.jpg" in body["document"]["markdown"]
    parse.assert_called_once()


def test_parse_review_reports_image_conversion_requirement_when_ocr_fails(mocker):
    mocker.patch(
        "app.main.parse_with_kordoc",
        side_effect=main.KordocParseError("image OCR failed"),
    )

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/parse-review",
            files={"jd_file": ("job-description.jpg", b"fake-image", "image/jpeg")},
        )

    assert resp.status_code == 422
    assert "PDF로 변환" in resp.json()["detail"]


def test_parse_review_rejects_encrypted_zip_as_422():
    data = _mark_zip_encrypted(_zip_bytes({"job_description.txt": JD_TEXT}))

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/parse-review",
            files={"jd_file": ("jd.zip", data, "application/zip")},
        )

    assert resp.status_code == 422
    assert "no parseable" in resp.text or "encrypted" in resp.text


def _confirmed_review_payload(fields: dict, confirmed: object = True, jd_text: str = JD_TEXT) -> dict:
    structured = {"document": {"markdown": jd_text}, "fields": fields}
    session = main._create_review_session(jd_text.encode("utf-8"), structured, "jd.txt")
    return {
        **structured,
        "review_confirmed": confirmed,
        "review_session_id": session["id"],
        "review_session": session,
    }


def test_reviewed_ability_unit_names_keep_only_transport_normalization() -> None:
    assert main._reviewed_ability_unit_names(
        [
            "02.경영·회계·사무 > 02.총무·인사 > 03.일반사무 > 02.사무행정 > 01.문서 작성",
            "0202030202_22v3 문서 관리",
            "요구능력단위: QM/QC관리",
        ]
    ) == ["문서 작성", "문서 관리", "QM/QC관리"]


def test_detail_lookup_canonicalizes_only_verified_ordinal_unit_decoration() -> None:
    assert main._canonicalize_detail_lookup_terms(
        ["외식운영관리 (02.식자재관리)"]
    ) == ["외식운영관리"]
    assert main._canonicalize_detail_lookup_terms(
        ["외식운영관리 (식자재관리)"]
    ) == ["외식운영관리 (식자재관리)"]
    assert main._canonicalize_detail_lookup_terms(
        ["사무행정(기록물)"]
    ) == ["사무행정(기록물)"]


def test_lock_units_to_reviewed_ability_units_is_exact_and_ordered() -> None:
    units = [
        {"ncsClCd": "0202030202_22v3", "compeUnitName": "문서 관리"},
        {"ncsClCd": "0202030201_22v3", "compeUnitName": "문서 작성"},
        {"ncsClCd": "0202030203_22v3", "compeUnitName": "회의 운영"},
    ]

    locked, missing = main._lock_units_to_reviewed_ability_units(
        units,
        ["문서 작성", "문서관리", "자료 검색"],
    )

    assert [row["ncsClCd"] for row in locked] == [
        "0202030201_22v3",
        "0202030202_22v3",
    ]
    assert all(row["requiredAbilityUnitMatch"] == "exact" for row in locked)
    assert missing == ["자료 검색"]


def test_lock_units_to_reviewed_ability_units_rejects_cross_detail_name_collision() -> None:
    units = [
        {
            "ncsClCd": "1402011101_24v1",
            "compeUnitName": "품질관리",
            "ncsSubdCdnm": "기계품질관리",
        },
        {
            "ncsClCd": "1701010101_24v1",
            "compeUnitName": "품질관리",
            "ncsSubdCdnm": "섬유생산관리",
        },
    ]

    locked, missing = main._lock_units_to_reviewed_ability_units(
        units,
        ["품질관리"],
    )

    assert locked == []
    assert missing == ["품질관리"]


def test_lock_units_to_reviewed_ability_units_rejects_same_detail_base_code_collision() -> None:
    units = [
        {"ncsClCd": "0202030201_22v3", "compeUnitName": "공통 능력"},
        {"ncsClCd": "0202030202_22v3", "compeUnitName": "공통능력"},
    ]

    locked, missing = main._lock_units_to_reviewed_ability_units(
        units,
        ["공통 능력"],
    )

    assert locked == []
    assert missing == ["공통 능력"]


def test_lock_units_to_reviewed_ability_units_accepts_versions_of_same_base_code() -> None:
    units = [
        {"ncsClCd": "0202010110_19v2", "compeUnitName": "총무보안관리"},
        {"ncsClCd": "0202010110_25v3", "compeUnitName": "총무보안관리"},
    ]

    locked, missing = main._lock_units_to_reviewed_ability_units(
        units,
        ["총무보안관리"],
    )

    assert [row["ncsClCd"] for row in locked] == [
        "0202010110_19v2",
        "0202010110_25v3",
    ]
    assert missing == []


def test_every_current_catalog_cross_detail_name_collision_remains_unlocked() -> None:
    ncs_mcp_client._official_details_by_name_key.cache_clear()
    ncs_mcp_client._official_units_by_name_key.cache_clear()
    rows_by_lock_key: dict[str, list[dict]] = {}
    for catalog_rows in ncs_mcp_client._official_units_by_name_key().values():
        for row in catalog_rows:
            key = main._norm_detail_coverage_key(row.get("compeUnitName"))
            if key:
                rows_by_lock_key.setdefault(key, []).append(row)
    collisions = [
        list(rows)
        for rows in rows_by_lock_key.values()
        if len(
            {
                str(row.get("officialDetailCode") or "").strip()
                for row in rows
                if str(row.get("officialDetailCode") or "").strip()
            }
        )
        > 1
    ]

    assert len(collisions) >= 190
    for rows in collisions:
        reviewed_name = str(rows[0]["compeUnitName"])
        locked, missing = main._lock_units_to_reviewed_ability_units(
            rows,
            [reviewed_name],
        )
        assert locked == []
        assert missing == [reviewed_name]


def test_scope_unassigned_units_uses_unique_exact_detail_membership() -> None:
    scoped, unresolved = (
        main._scope_reviewed_ability_units_by_exact_detail_membership(
            {
                "PR": [
                    {"ncsClCd": "0201020103_24v3", "compeUnitName": "언론 홍보"},
                ],
                "사무행정": [
                    {"ncsClCd": "0202030201_22v3", "compeUnitName": "문서 작성"},
                ],
            },
            ["문서작성", "언론홍보", "기관 자체 업무"],
        )
    )

    assert scoped == {"사무행정": ["문서작성"], "PR": ["언론홍보"]}
    assert unresolved == ["기관 자체 업무"]


def test_scope_unassigned_units_rejects_cross_detail_name_collision() -> None:
    scoped, unresolved = (
        main._scope_reviewed_ability_units_by_exact_detail_membership(
            {
                "첫째": [{"ncsClCd": "0101010101_1v1", "compeUnitName": "공통 관리"}],
                "둘째": [{"ncsClCd": "0202020201_1v1", "compeUnitName": "공통관리"}],
            },
            ["공통 관리"],
        )
    )

    assert scoped == {}
    assert unresolved == ["공통 관리"]


def test_recover_required_unit_uses_exact_name_and_unit_code_detail_scope() -> None:
    detail_units = [
        {"ncsClCd": "0202030201_22v3", "compeUnitName": "문서 작성"},
    ]
    candidates = [
        {
            "ncsClCd": "0202030207_22v4",
            "compeUnitName": "사무행정 업무 관리",
            # The serving DB currently has a corrupt classification link for
            # this row. Recovery must trust the code scope, not this path.
            "ncsSubdCdnm": "자원봉사관리",
        },
        {
            "ncsClCd": "0701020509_22v1",
            "compeUnitName": "사무행정 업무 관리",
            "ncsSubdCdnm": "자원봉사관리",
        },
    ]

    recovered, missing = main._recover_code_scoped_reviewed_ability_units(
        detail_units,
        ["사무행정 업무관리"],
        candidates,
    )

    assert [row["ncsClCd"] for row in recovered] == ["0202030207_22v4"]
    assert recovered[0]["requiredAbilityUnitMatch"] == "exact_name_code_scope_recovery"
    assert missing == []


def test_recover_required_unit_rejects_wrong_code_scope_and_semantic_name() -> None:
    detail_units = [
        {"ncsClCd": "0202030201_22v3", "compeUnitName": "문서 작성"},
    ]
    candidates = [
        {"ncsClCd": "0701020509_22v1", "compeUnitName": "사무행정 업무 관리"},
        {"ncsClCd": "0202030207_22v4", "compeUnitName": "사무행정 지원"},
    ]

    recovered, missing = main._recover_code_scoped_reviewed_ability_units(
        detail_units,
        ["사무행정 업무 관리"],
        candidates,
    )

    assert recovered == []
    assert missing == ["사무행정 업무 관리"]


def test_recover_required_unit_uses_unique_ordinal_for_small_current_name_change() -> None:
    detail_units = [
        {"ncsClCd": "0203020104_20v4", "compeUnitName": "결산처리"},
        {"ncsClCd": "0203020105_20v4", "compeUnitName": "회계정보시스템 운용"},
    ]

    recovered, missing = main._recover_ordinal_scoped_reviewed_ability_units(
        detail_units,
        ["회계정보시스템"],
        {main._norm_detail_coverage_key("회계정보시스템"): ["05"]},
    )

    assert [row["ncsClCd"] for row in recovered] == ["0203020105_20v4"]
    assert recovered[0]["requiredAbilityUnitMatch"] == "ordinal_code_scope_current_name"
    assert missing == []


def test_recover_required_unit_rejects_ordinal_only_or_ambiguous_candidates() -> None:
    detail_units = [
        {"ncsClCd": "0203020105_20v4", "compeUnitName": "완전히 다른 이름"},
        {"ncsClCd": "0101010105_20v1", "compeUnitName": "회계정보시스템 운용"},
    ]
    key = main._norm_detail_coverage_key("회계정보시스템")

    recovered, missing = main._recover_ordinal_scoped_reviewed_ability_units(
        detail_units,
        ["회계정보시스템"],
        {key: ["05"]},
    )

    assert recovered == []
    assert missing == ["회계정보시스템"]


def test_review_session_stores_only_hash_metadata_and_supports_retry():
    review = _confirmed_review_payload(
        {"ncs_detail_candidates": ["경영기획"]},
        jd_text=JD_TEXT,
    )
    session_id = review["review_session_id"]

    stored = main._REVIEW_SESSION_BY_ID[session_id]
    assert "markdown" not in stored
    assert stored["markdown_size"] == len(JD_TEXT.encode("utf-8"))

    validated = main._validate_review_session(review, JD_TEXT.encode("utf-8"))
    assert validated["markdown"] == JD_TEXT
    retry = main._validate_review_session(review, JD_TEXT.encode("utf-8"))
    assert retry["markdown"] == JD_TEXT


def test_review_session_caps_untrusted_multipart_filename():
    structured = {"document": {"markdown": JD_TEXT}, "fields": {}}
    public = main._create_review_session(
        JD_TEXT.encode("utf-8"),
        structured,
        ("x" * 20_000) + ".txt",
    )

    stored = main._REVIEW_SESSION_BY_ID.pop(public["id"])
    assert len(stored["filename"]) <= 160
    assert stored["filename"].endswith(".txt")


def test_notice_parse_review_prefills_duty_and_evaluation_text():
    notice = (
        "## 담당업무\n"
        "- 경영계획 수립 및 사업성과 분석\n"
        "## 면접전형 평가항목\n"
        "- 문제해결능력\n"
        "- 의사소통능력\n"
    )

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/notice/parse-review",
            files={"notice_file": ("notice.txt", notice.encode("utf-8"), "text/plain")},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert "경영계획 수립" in body["fields"]["duty_text"]
    assert "문제해결능력" in body["fields"]["evaluation_text"]
    assert body["review_session_id"] == body["review_session"]["id"]
    assert body["review_session"]["document_sha256"]
    assert body["review_session"]["markdown_sha256"]


def test_mcp_only_requires_human_review_confirmation(monkeypatch, mocker):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    _patch_mcp_upload_common(mocker)
    review = _confirmed_review_payload({"ncs_detail_candidates": ["\uacbd\uc601\uae30\ud68d"]}, confirmed=False)

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/strategy/upload",
            files=_upload_files(),
            data={
                "jd_review_json": json.dumps(review, ensure_ascii=False),
                "question_plan_json": json.dumps(
                    {
                        "items": [
                            {
                                "detail": "\uacbd\uc601\uae30\ud68d",
                                "enabled": True,
                                "main_count": 1,
                                "follow_up_count": 3,
                            },
                            {
                                "detail": "\uac04\ud638\uc5c5\ubb34 \ubcf4\uc870",
                                "enabled": True,
                                "main_count": 1,
                                "follow_up_count": 3,
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                "openai_api_key": REQUEST_OPENAI_KEY,
            },
        )

    assert resp.status_code == 400
    assert "review_confirmed" in resp.text


def test_mcp_only_rejects_truthy_string_confirmation(monkeypatch, mocker):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    _patch_mcp_upload_common(mocker)
    review = _confirmed_review_payload({"ncs_detail_candidates": ["\uacbd\uc601\uae30\ud68d"]}, confirmed="false")

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/strategy/upload",
            files=_upload_files(),
            data={
                "jd_review_json": json.dumps(review, ensure_ascii=False),
                "question_plan_json": json.dumps(
                    {
                        "items": [
                            {
                                "detail": "\uacbd\uc601\uae30\ud68d",
                                "enabled": True,
                                "main_count": 1,
                                "follow_up_count": 3,
                            },
                            {
                                "detail": "\uac04\ud638\uc5c5\ubb34 \ubcf4\uc870",
                                "enabled": True,
                                "main_count": 1,
                                "follow_up_count": 3,
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                "openai_api_key": REQUEST_OPENAI_KEY,
            },
        )

    assert resp.status_code == 400
    assert "review_confirmed" in resp.text


def test_mcp_only_requires_server_review_session(monkeypatch, mocker):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    _patch_mcp_upload_common(mocker)
    review = {"review_confirmed": True, "fields": {"ncs_detail_candidates": ["\uacbd\uc601\uae30\ud68d"]}}

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/strategy/upload",
            files=_upload_files(),
            data={
                "jd_review_json": json.dumps(review, ensure_ascii=False),
                "openai_api_key": REQUEST_OPENAI_KEY,
            },
        )

    assert resp.status_code == 400
    assert "review_session_id" in resp.text


def test_mcp_only_requires_reviewed_detail_candidates(monkeypatch, mocker):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    _patch_mcp_upload_common(mocker)
    review = _confirmed_review_payload({"ncs_detail_candidates": []})

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/strategy/upload",
            files=_upload_files(),
            data={
                "jd_review_json": json.dumps(review, ensure_ascii=False),
                "openai_api_key": REQUEST_OPENAI_KEY,
            },
        )

    assert resp.status_code == 422
    assert "detail candidates" in resp.text


def test_mcp_only_does_not_autofill_reviewed_detail_candidates(monkeypatch, mocker):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    _patch_mcp_upload_common(mocker)
    mocker.patch("app.main.extract_detail_categories_from_jd", return_value=["\uacbd\uc601\uae30\ud68d"])
    search = mocker.patch("app.main.search_units_by_detail", return_value=[])
    review = _confirmed_review_payload({"ncs_detail_candidates": []})

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/strategy/upload",
            files=_upload_files(),
            data={
                "jd_review_json": json.dumps(review, ensure_ascii=False),
                "openai_api_key": REQUEST_OPENAI_KEY,
            },
        )

    assert resp.status_code == 422
    assert "detail candidates" in resp.text
    search.assert_not_called()


def test_mcp_only_returns_manual_suggestions_when_detail_has_no_exact_match(monkeypatch, mocker):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    _patch_mcp_upload_common(mocker)
    mocker.patch("app.main.search_units_by_detail", return_value=[])
    suggestion = {
        "ncsClCd": "0601010101_20v1",
        "compeUnitName": "\uc758\ub8cc\uc9c0\uc6d0 \ud6c4\ubcf4",
        "ncsSubdCdnm": "\uc758\ub8cc\uae30\uae30\uad00\ub9ac",
        "source": "ncs-mcp-suggest",
        "isExactDetailMatch": False,
    }
    suggest = mocker.patch("app.main.suggest_units_by_text", return_value=[suggestion])
    review = _confirmed_review_payload({"ncs_detail_candidates": ["\uc784\uc0c1\ubcd1\ub9ac"]})

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/strategy/upload",
            files=_upload_files(),
            data={
                "jd_review_json": json.dumps(review, ensure_ascii=False),
                "openai_api_key": REQUEST_OPENAI_KEY,
            },
        )

    body = resp.json()
    assert resp.status_code == 422
    suggest.assert_called_once_with(["\uc784\uc0c1\ubcd1\ub9ac"], max_units=12)
    assert body["detail"]["lookup_terms"] == ["\uc784\uc0c1\ubcd1\ub9ac"]
    assert body["detail"]["suggested_ncs_units"] == [suggestion]
    assert "exact competency units" in body["detail"]["message"]


def test_mcp_only_fails_closed_when_authoritative_detail_lookup_is_unavailable(
    monkeypatch,
    mocker,
):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    _patch_mcp_upload_common(mocker)
    sentinel = "upstream socket disclosed detail"
    mocker.patch(
        "app.main.search_units_by_detail",
        side_effect=main.NcsMcpError(sentinel),
    )
    hrdk_verified = mocker.patch("app.main.fetch_ncs_units_hrdk_by_verified_sclass")
    hrdk_names = mocker.patch("app.main.fetch_ncs_units_hrdk_by_sclass_names")
    hrdk_keywords = mocker.patch("app.main.fetch_ncs_units_hrdk_by_keywords")
    local_map = mocker.patch("app.main.map_ncs")
    review = _confirmed_review_payload({"ncs_detail_candidates": ["경영기획"]})

    with TestClient(main.app) as client:
        response = client.post(
            "/api/jd/strategy/upload",
            files=_upload_files(),
            data={
                "jd_review_json": json.dumps(review, ensure_ascii=False),
                "openai_api_key": REQUEST_OPENAI_KEY,
            },
        )

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "ncs_mcp_unavailable"
    assert detail["retryable"] is True
    assert detail["lookup_terms"] == ["경영기획"]
    assert sentinel not in response.text
    assert REQUEST_OPENAI_KEY not in response.text
    hrdk_verified.assert_not_called()
    hrdk_names.assert_not_called()
    hrdk_keywords.assert_not_called()
    local_map.assert_not_called()


def test_mcp_only_canonicalizes_split_official_detail_before_exact_lookup(monkeypatch, mocker):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    _patch_mcp_upload_common(mocker)
    mocker.patch(
        "app.main.lookup_ncs_codes_by_sclass",
        return_value=[
            {
                "sclass_name": "프로젝트관리",
                "ncs_code_no": "010101",
                "ncs_lclass_code": "01",
                "ncs_mclass_code": "01",
                "ncs_sclass_code": "01",
                "confidence": 1.0,
            }
        ],
    )
    search = mocker.patch("app.main.search_units_by_detail", return_value=[])
    mocker.patch("app.main.suggest_units_by_text", return_value=[])
    review = _confirmed_review_payload(
        {"ncs_detail_candidates": ["프로젝트 관리"]}
    )

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/strategy/upload",
            files=_upload_files(),
            data={
                "jd_review_json": json.dumps(review, ensure_ascii=False),
                "openai_api_key": REQUEST_OPENAI_KEY,
            },
        )

    body = resp.json()
    assert resp.status_code == 422
    assert search.call_args.args[0] == ["프로젝트관리"]
    assert body["detail"]["lookup_terms"] == ["프로젝트관리"]


def test_mcp_only_rejects_partial_detail_exact_coverage(monkeypatch, mocker):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    # Public requests are single-detail. Bypass only that input boundary here
    # to retain coverage of the downstream exact-coverage/suggestion policy.
    monkeypatch.setattr(main, "_enforce_question_plan_capacity", lambda _plan: None)
    _patch_mcp_upload_common(mocker)
    matched_unit = {
        "ncsClCd": "0201010103_22v2",
        "compeUnitName": "\uacbd\uc601\uacc4\ud68d \uc218\ub9bd",
        "ncsSubdCdnm": "\uacbd\uc601\uae30\ud68d",
        "matchedDetailName": "\uacbd\uc601\uae30\ud68d",
        "source": "ncs-mcp",
    }
    mocker.patch("app.main.search_units_by_detail", return_value=[matched_unit])
    suggestion = {
        "ncsClCd": "0601010801_23v3",
        "compeUnitName": "\uc9c4\ub8cc\uc9c0\uc6d0\ubcf4\uc870",
        "ncsSubdCdnm": "\uc694\uc591\uc9c0\uc6d0",
        "source": "ncs-mcp-suggest",
    }
    suggest = mocker.patch("app.main.suggest_units_by_text", return_value=[suggestion])
    review = _confirmed_review_payload(
        {
            "ncs_detail_candidates": [
                "\uacbd\uc601\uae30\ud68d",
                "\uac04\ud638\uc5c5\ubb34 \ubcf4\uc870",
            ]
        }
    )

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/strategy/upload",
            files=_upload_files(),
            data={
                "jd_review_json": json.dumps(review, ensure_ascii=False),
                "question_plan_json": json.dumps(
                    {
                        "items": [
                            {
                                "detail": "\uacbd\uc601\uae30\ud68d",
                                "enabled": True,
                                "main_count": 1,
                                "follow_up_count": 3,
                            },
                            {
                                "detail": "\uac04\ud638\uc5c5\ubb34 \ubcf4\uc870",
                                "enabled": True,
                                "main_count": 1,
                                "follow_up_count": 3,
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                "openai_api_key": REQUEST_OPENAI_KEY,
            },
        )

    body = resp.json()
    assert resp.status_code == 422
    suggest.assert_called_once_with(["\uac04\ud638\uc5c5\ubb34 \ubcf4\uc870"], max_units=12)
    assert body["detail"]["matched_detail_terms"] == ["\uacbd\uc601\uae30\ud68d"]
    assert body["detail"]["unmatched_detail_terms"] == ["\uac04\ud638\uc5c5\ubb34 \ubcf4\uc870"]
    assert body["detail"]["suggested_ncs_units"] == [suggestion]
    assert "partial exact coverage" in body["detail"]["message"]


def test_mcp_only_rejects_reviewed_ability_unit_outside_exact_detail_units(monkeypatch, mocker):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    _patch_mcp_upload_common(mocker)
    unit = {
        "ncsClCd": "0202030201_22v3",
        "compeUnitName": "문서 작성",
        "ncsSubdCdnm": "사무행정",
        "matchedDetailName": "사무행정",
        "source": "ncs-mcp",
    }
    mocker.patch("app.main.search_units_by_detail", return_value=[unit])
    rerank = mocker.patch("app.main.rerank_ncs_matches")
    fetch_ksa = mocker.patch("app.main.fetch_ncs_ksa_by_units")
    review = _confirmed_review_payload(
        {
            "ncs_detail_candidates": ["사무행정"],
            "ability_units": ["존재하지 않는 능력단위"],
        }
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/api/jd/strategy/upload",
            files=_upload_files(),
            data={
                "jd_review_json": json.dumps(review, ensure_ascii=False),
                "openai_api_key": REQUEST_OPENAI_KEY,
            },
        )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "ncs_required_ability_unit_mismatch"
    assert detail["matched_ability_units"] == []
    assert detail["unmatched_ability_units"] == ["존재하지 않는 능력단위"]
    rerank.assert_not_called()
    fetch_ksa.assert_not_called()


def test_mcp_only_rejects_reviewed_ability_name_shared_by_selected_details(
    monkeypatch,
    mocker,
):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    # Public requests normally enforce one active detail. Exercise the
    # downstream invariant directly so future multi-detail support cannot mix
    # KSA from two official codes that happen to share one unit label.
    monkeypatch.setattr(main, "_enforce_question_plan_capacity", lambda _plan: None)
    _patch_mcp_upload_common(mocker)
    units = [
        {
            "ncsClCd": "1402011101_24v1",
            "compeUnitName": "품질관리",
            "ncsSubdCdnm": "기계품질관리",
            "matchedDetailName": "기계품질관리",
            "source": "ncs-mcp",
        },
        {
            "ncsClCd": "1701010101_24v1",
            "compeUnitName": "품질관리",
            "ncsSubdCdnm": "섬유생산관리",
            "matchedDetailName": "섬유생산관리",
            "source": "ncs-mcp",
        },
    ]
    mocker.patch("app.main.search_units_by_detail", return_value=units)
    mocker.patch("app.main.exact_official_units_by_name", return_value=units)
    mocker.patch("app.main.suggest_units_by_text", return_value=units)
    rerank = mocker.patch("app.main.rerank_ncs_matches")
    fetch_ksa = mocker.patch("app.main.fetch_ncs_ksa_by_units")
    review = _confirmed_review_payload(
        {
            "ncs_detail_candidates": ["기계품질관리", "섬유생산관리"],
            "ability_units": ["품질관리"],
        }
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/api/jd/strategy/upload",
            files=_upload_files(),
            data={
                "jd_review_json": json.dumps(review, ensure_ascii=False),
                "question_plan_json": json.dumps(
                    {
                        "items": [
                            {
                                "detail": "기계품질관리",
                                "enabled": True,
                                "main_count": 1,
                                "follow_up_count": 3,
                            },
                            {
                                "detail": "섬유생산관리",
                                "enabled": True,
                                "main_count": 1,
                                "follow_up_count": 3,
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                "openai_api_key": REQUEST_OPENAI_KEY,
            },
        )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "ncs_required_ability_unit_mismatch"
    assert detail["matched_ability_units"] == []
    assert detail["unmatched_ability_units"] == ["품질관리"]
    rerank.assert_not_called()
    fetch_ksa.assert_not_called()


def test_mcp_only_rejects_reviewed_ability_name_with_ambiguous_base_codes(
    monkeypatch,
    mocker,
):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    _patch_mcp_upload_common(mocker)
    units = [
        {
            "ncsClCd": "0202030201_22v3",
            "compeUnitName": "공통 능력",
            "ncsSubdCdnm": "사무행정",
            "matchedDetailName": "사무행정",
            "source": "ncs-mcp",
        },
        {
            "ncsClCd": "0202030202_22v3",
            "compeUnitName": "공통능력",
            "ncsSubdCdnm": "사무행정",
            "matchedDetailName": "사무행정",
            "source": "ncs-mcp",
        },
    ]
    mocker.patch("app.main.search_units_by_detail", return_value=units)
    rerank = mocker.patch("app.main.rerank_ncs_matches")
    fetch_ksa = mocker.patch("app.main.fetch_ncs_ksa_by_units")
    review = _confirmed_review_payload(
        {
            "ncs_detail_candidates": ["사무행정"],
            "ability_units": ["공통 능력"],
        }
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/api/jd/strategy/upload",
            files=_upload_files(),
            data={
                "jd_review_json": json.dumps(review, ensure_ascii=False),
                "openai_api_key": REQUEST_OPENAI_KEY,
            },
        )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "ncs_required_ability_unit_mismatch"
    assert detail["matched_ability_units"] == []
    assert detail["unmatched_ability_units"] == ["공통 능력"]
    rerank.assert_not_called()
    fetch_ksa.assert_not_called()


def test_mcp_only_upload_accepts_parenthetical_secretary_detail_without_manual_block(monkeypatch, mocker):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _patch_mcp_upload_common(mocker)
    catalog_row = _catalog_unit_by_code("0202030101_22v2")
    unit = {
        "ncsClCd": catalog_row["ncsClCd"],
        "compeUnitName": catalog_row["compeUnitName"],
        "ncsSubdCdnm": catalog_row["canonicalDetailName"],
        "compeUnitDef": "경영진 지원과 일정 관리를 수행한다",
        "score": 1.0,
    }
    ksa = {
        "ncsClCd": unit["ncsClCd"],
        "compeUnitName": unit["compeUnitName"],
        "factorName": "일정 조정 기준 검토",
        "elementName": "일정 관리",
        "ksaTypeName": "지식",
        "ksaNo": "K-01",
        "factorSource": "ncs-mcp",
        "ksaStatus": "official",
    }
    mocker.patch("app.services.ncs_mcp_client._tool_names", return_value={"ncs_search"})
    calls = []

    def fake_call_tool(name, arguments):
        assert name == "ncs_search"
        calls.append(arguments["query"])
        if arguments["query"] != unit["ncsSubdCdnm"]:
            return {"results": []}
        return {"results": [_mcp_catalog_row(catalog_row)]}

    mocker.patch("app.services.ncs_mcp_client._call_tool", side_effect=fake_call_tool)
    rerank = mocker.patch("app.main.rerank_ncs_matches", return_value=([unit], "rule"))
    mocker.patch("app.main.fetch_ncs_ksa_by_units", return_value=[ksa])
    mocker.patch("app.main.rank_ksa_factors_by_query", return_value=[ksa])
    mocker.patch("app.main.build_ncs_context_pack", return_value={})
    build_strategy = mocker.patch(
        "app.main.build_jd_strategy_with_openai",
        return_value=_openai_model_strategy(unit, ksa, methods=("경험면접",)),
    )
    review = _confirmed_review_payload(
        {"ncs_detail_candidates": ["비서 (글로벌경영사무 지원)"]}
    )

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/strategy/upload",
            files=_upload_files(),
            data={
                "jd_review_json": json.dumps(review, ensure_ascii=False),
                "openai_api_key": REQUEST_OPENAI_KEY,
            },
        )

    body = resp.json()
    assert resp.status_code == 200
    assert calls == ["비서 (글로벌경영사무 지원)", unit["ncsSubdCdnm"]]
    rerank.assert_called_once()
    assert build_strategy.call_count >= 1
    assert body["ncs_matches"][0]["ncsClCd"] == unit["ncsClCd"]
    assert body["ncs_matches"][0]["ncsSubdCdnm"] == "비서"


def test_mcp_only_success_uses_official_ksa(monkeypatch, mocker):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _patch_mcp_upload_common(mocker)
    unit = {
        "ncsClCd": "0201010103_22v2",
        "compeUnitName": "\uacbd\uc601\uacc4\ud68d \uc218\ub9bd",
        "ncsSubdCdnm": "\uacbd\uc601\uae30\ud68d",
        "compeUnitDef": "\uacbd\uc601\ubaa9\ud45c\ub97c \uc218\ub9bd\ud55c\ub2e4",
        "officialDetailCode": "02010101",
        "officialDetailName": "\uacbd\uc601\uae30\ud68d",
        "detailResolutionKind": "direct",
        "detailResolutionRule": "direct",
        "officialUnitBaseCode": "0201010103",
        "officialUnitName": "\uacbd\uc601\uacc4\ud68d \uc218\ub9bd",
        "unitResolutionKind": "catalog_full_code_exact",
        "unitVersionCompatible": False,
        "catalogUnitCodes": ["0201010103_22v2"],
        "score": 1.0,
    }
    ksa = {
        "ncsClCd": unit["ncsClCd"],
        "compeUnitName": unit["compeUnitName"],
        "factorName": "\uc2dc\uc7a5\ud658\uacbd \ubd84\uc11d",
        "elementName": "\uacbd\uc601\ud658\uacbd \ubd84\uc11d",
        "ksaTypeName": "\uc9c0\uc2dd",
        "ksaNo": "K-01",
        "factorSource": "ncs-mcp",
        "ksaStatus": "official",
    }
    mocker.patch("app.main.search_units_by_detail", return_value=[unit])
    rerank = mocker.patch(
        "app.main.rerank_ncs_matches",
        side_effect=lambda **kwargs: (
            [
                {
                    key: value
                    for key, value in kwargs["ncs_items"][0].items()
                    if key
                    in {
                        "ncsClCd",
                        "compeUnitName",
                        "ncsSubdCdnm",
                        "compeUnitDef",
                        "score",
                    }
                }
            ],
            "rule",
        ),
    )
    mocker.patch("app.main.fetch_ncs_ksa_by_units", return_value=[ksa])
    rank_ksa = mocker.patch("app.main.rank_ksa_factors_by_query", return_value=[ksa])
    mocker.patch("app.main.build_ncs_context_pack", return_value={})
    build_strategy = mocker.patch(
        "app.main.build_jd_strategy_with_openai",
        return_value=_openai_model_strategy(unit, ksa, methods=("경험면접",)),
    )
    review = _confirmed_review_payload(
        {
            "ncs_detail_candidates": ["\uacbd\uc601\uae30\ud68d"],
            "ability_units": ["\uacbd\uc601\uacc4\ud68d \uc218\ub9bd"],
        }
    )
    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/strategy/upload",
            files=_upload_files(),
            data={
                "jd_review_json": json.dumps(review, ensure_ascii=False),
                "openai_api_key": REQUEST_OPENAI_KEY,
                "duty_text": "duty: stakeholder workshop planning",
                "qualification_text": "\uc9c0\uc6d0\uc790\uaca9: \uad00\ub828 \ubd84\uc57c \uc2e4\ubb34\uacbd\ub825 3\ub144 \uc774\uc0c1",
                "preference_text": "\uc6b0\ub300\uc0ac\ud56d: \uacf5\uacf5\uae30\uad00 \uc0ac\uc5c5\uad00\ub9ac \uacbd\ud5d8",
                "evaluation_text": "evaluation: issue framing",
            },
        )

    body = resp.json()
    assert resp.status_code == 200
    rerank.assert_called_once()
    # Human-confirmed exact NCS detail matches use deterministic ranking; the
    # request key is reserved for the actual interview-question generation.
    assert rerank.call_args.kwargs["openai_api_key"] == ""
    rank_ksa.assert_called_once()
    ksa_query_text = rank_ksa.call_args.kwargs["query_text"]
    assert "duty: stakeholder workshop planning" in ksa_query_text
    assert "\uc2e4\ubb34\uacbd\ub825" in ksa_query_text
    assert "\uc0ac\uc5c5\uad00\ub9ac" in ksa_query_text
    assert "evaluation: issue framing" in ksa_query_text
    assert "\uacbd\uc601\uae30\ud68d" in ksa_query_text
    build_strategy.assert_called_once()
    api_key_override = build_strategy.call_args.kwargs["api_key_override"]
    assert api_key_override == REQUEST_OPENAI_KEY
    assert main.settings.resolve_openai_key(api_key_override) == REQUEST_OPENAI_KEY
    assert body["openai_key_source"] == "request"
    assert REQUEST_OPENAI_KEY not in resp.text
    assert body["jd_review_confirmed"] is True
    assert "\uc2e4\ubb34\uacbd\ub825" in body["qualification_text_preview"]
    assert "\uc0ac\uc5c5\uad00\ub9ac" in body["preference_text_preview"]
    assert body["ncs_source"].startswith("ncs-mcp")
    assert body["required_ability_units_reviewed"] == ["\uacbd\uc601\uacc4\ud68d \uc218\ub9bd"]
    assert body["required_ability_unit_lock_applied"] is True
    assert body["ncs_matches"][0]["requiredAbilityUnitMatch"] == "exact"
    assert body["ncs_matches"][0]["officialDetailCode"] == "02010101"
    assert body["ncs_matches"][0]["officialUnitBaseCode"] == "0201010103"
    assert body["ncs_matches"][0]["unitResolutionKind"] == "catalog_full_code_exact"
    assert body["ncs_matches"][0]["catalogUnitCodes"] == ["0201010103_22v2"]
    assert body["ncs_ksa"][0]["factorSource"] == "ncs-mcp"
    assert body["ncs_ksa"][0]["ksaStatus"] == "official"
    question = body["strategy"]["interview_questions"][0]
    assert question["question_source"].startswith("openai_api")
    assert question["question_evidence_id"] == main.stable_ksa_evidence_id(ksa)
    assert question["ksa_refs"] == ["\uc2dc\uc7a5\ud658\uacbd \ubd84\uc11d"]
    assert question["ksa_evidence"][0]["factorSource"] == "ncs-mcp"
    assert question["ksa_evidence"][0]["ksaStatus"] == "official"


@pytest.mark.parametrize(
    ("detail_name", "reviewed_name", "unit", "positioned_items", "catalog_rows", "match_mode"),
    [
        (
            "회계·감사",
            "회계정보시스템",
            {
                "ncsClCd": "0203020105_20v4",
                "compeUnitName": "회계정보시스템 운용",
                "ncsSubdCdnm": "회계·감사",
                "score": 1.0,
            },
            [
                {
                    "section": "ability_units",
                    "text": "회계정보시스템",
                    "ability_unit_ordinal": "05",
                }
            ],
            [],
            "ordinal_code_scope_current_name",
        ),
        (
            "사무행정",
            "사무행정 업무 관리",
            {
                "ncsClCd": "0202030207_22v4",
                "compeUnitName": "사무행정 업무 관리",
                "ncsSubdCdnm": "자원봉사관리",
                "score": 1.0,
            },
            [],
            [
                {
                    "ncsClCd": "0202030207_22v4",
                    "compeUnitName": "사무행정 업무 관리",
                    "ncsSubdCdnm": "자원봉사관리",
                }
            ],
            "exact_name_code_scope_recovery",
        ),
    ],
)
def test_recovered_required_unit_provenance_survives_endpoint_rerank(
    monkeypatch,
    mocker,
    detail_name,
    reviewed_name,
    unit,
    positioned_items,
    catalog_rows,
    match_mode,
):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _patch_mcp_upload_common(mocker)
    seed_unit = {
        "ncsClCd": f"{unit['ncsClCd'][:8]}01_1v1",
        "compeUnitName": "범위 확인용 공식 능력단위",
        "ncsSubdCdnm": detail_name,
        "score": 1.0,
    }
    search_rows = [unit] if match_mode.startswith("ordinal_") else [seed_unit]
    ksa = {
        "ncsClCd": unit["ncsClCd"],
        "compeUnitName": unit["compeUnitName"],
        "factorName": "공식 지식 근거",
        "elementName": "공식 능력단위요소",
        "ksaTypeName": "지식",
        "ksaNo": "K-01",
        "factorSource": "ncs-mcp",
        "ksaStatus": "official",
    }
    mocker.patch("app.main.search_units_by_detail", return_value=search_rows)
    mocker.patch("app.main.exact_official_units_by_name", return_value=catalog_rows)
    reranked = {
        key: value
        for key, value in unit.items()
        if key not in {"requiredAbilityUnitName", "requiredAbilityUnitMatch"}
    }
    mocker.patch("app.main.rerank_ncs_matches", return_value=([reranked], "rule"))
    mocker.patch("app.main.fetch_ncs_ksa_by_units", return_value=[ksa])
    mocker.patch("app.main.rank_ksa_factors_by_query", return_value=[ksa])
    mocker.patch("app.main.build_ncs_context_pack", return_value={})
    mocker.patch(
        "app.main.build_jd_strategy_with_openai",
        return_value=_openai_model_strategy(unit, ksa, methods=("경험면접",)),
    )
    review = _confirmed_review_payload(
        {
            "ncs_detail_candidates": [detail_name],
            "ability_units": [reviewed_name],
            "positioned_items": positioned_items,
        }
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/api/jd/strategy/upload",
            files=_upload_files(),
            data={
                "jd_review_json": json.dumps(review, ensure_ascii=False),
                "openai_api_key": REQUEST_OPENAI_KEY,
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["required_ability_units_reviewed"] == [reviewed_name]
    assert body["required_ability_unit_lock_applied"] is True
    assert body["ncs_matches"][0]["requiredAbilityUnitName"] == reviewed_name
    assert body["ncs_matches"][0]["requiredAbilityUnitMatch"] == match_mode


def test_ncs_link_provenance_reattach_rejects_conflicting_same_code():
    code = "0201010103_22v2"
    matches = [{"ncsClCd": code, "compeUnitName": "ranked"}]
    sources = [
        {"ncsClCd": code, "officialDetailCode": "02010101"},
        {"ncsClCd": code, "officialDetailCode": "02010102"},
    ]

    output = main._reattach_ncs_link_provenance(matches, sources)

    assert output == matches


def test_ncs_link_provenance_copies_only_controlled_fields():
    code = "0201010103_22v2"
    output = main._reattach_ncs_link_provenance(
        [{"ncsClCd": code}],
        [
            {
                "ncsClCd": code,
                "officialDetailCode": "02010101",
                "unitCatalogVerified": True,
                "unitVersionCompatible": False,
                "catalogUnitCodes": [code, code, ""],
                "matchScore": 1,
                "untrustedSecret": "must-not-copy",
            }
        ],
    )

    assert output == [
        {
            "ncsClCd": code,
            "officialDetailCode": "02010101",
            "unitCatalogVerified": True,
            "unitVersionCompatible": False,
            "catalogUnitCodes": [code],
            "matchScore": 1.0,
        }
    ]


def test_verified_detail_unit_decoration_uses_canonical_detail_at_endpoint(
    monkeypatch,
    mocker,
):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _patch_mcp_upload_common(mocker)
    unit = {
        "ncsClCd": "0202030201_22v3",
        "compeUnitName": "문서 작성",
        "ncsSubdCdnm": "사무행정",
        "score": 1.0,
    }
    ksa = {
        "ncsClCd": unit["ncsClCd"],
        "compeUnitName": unit["compeUnitName"],
        "factorName": "문서 작성 지식",
        "elementName": "문서 작성 계획",
        "ksaTypeName": "지식",
        "ksaNo": "K-01",
        "factorSource": "ncs-mcp",
        "ksaStatus": "official",
    }
    search = mocker.patch("app.main.search_units_by_detail", return_value=[unit])
    mocker.patch("app.main.rerank_ncs_matches", return_value=([unit], "rule"))
    mocker.patch("app.main.fetch_ncs_ksa_by_units", return_value=[ksa])
    mocker.patch("app.main.rank_ksa_factors_by_query", return_value=[ksa])
    mocker.patch("app.main.build_ncs_context_pack", return_value={})
    mocker.patch(
        "app.main.build_jd_strategy_with_openai",
        return_value=_openai_model_strategy(unit, ksa, methods=("경험면접",)),
    )
    review = _confirmed_review_payload(
        {
            "ncs_detail_candidates": ["사무행정 (01.문서 작성)"],
            "ability_units": ["문서 작성"],
        }
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/api/jd/strategy/upload",
            files=_upload_files(),
            data={
                "jd_review_json": json.dumps(review, ensure_ascii=False),
                "openai_api_key": REQUEST_OPENAI_KEY,
            },
        )

    assert response.status_code == 200, response.text
    search.assert_called_once()
    assert search.call_args.args[0] == ["사무행정"]
    assert response.json()["ncs_matches"][0]["requiredAbilityUnitMatch"] == "exact"


def test_upload_requires_request_key_even_with_server_openai_env(monkeypatch, mocker):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    monkeypatch.setenv("OPENAI_API_KEY", SERVER_OPENAI_KEY)
    _patch_mcp_upload_common(mocker)
    build_strategy = mocker.patch("app.main.build_jd_strategy_with_openai")
    review = _confirmed_review_payload({"ncs_detail_candidates": ["\uacbd\uc601\uae30\ud68d"]})

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/strategy/upload",
            files=_upload_files(),
            data={
                "jd_review_json": json.dumps(review, ensure_ascii=False),
            },
        )

    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "openai_api_key_required"
    assert resp.json()["detail"]["provider"] == "openai_api"
    assert resp.json()["detail"]["retryable"] is False
    assert SERVER_OPENAI_KEY not in resp.text
    build_strategy.assert_not_called()


def test_generate_from_text_uses_request_scoped_openai_key(monkeypatch, mocker):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    unit = {
        "ncsClCd": "0201010103_22v2",
        "compeUnitName": "\uacbd\uc601\uacc4\ud68d \uc218\ub9bd",
        "compeUnitLevel": "5",
        "ncsSubdCdnm": "\uacbd\uc601\uae30\ud68d",
        "compeUnitDef": "\uacbd\uc601\ubaa9\ud45c\ub97c \uc218\ub9bd\ud55c\ub2e4",
        "officialDetailCode": "02010101",
        "officialDetailName": "\uacbd\uc601\uae30\ud68d",
        "officialUnitBaseCode": "0201010103",
        "officialUnitName": "\uacbd\uc601\uacc4\ud68d \uc218\ub9bd",
        "unitResolutionKind": "catalog_full_code_exact",
        "unitVersionCompatible": False,
        "catalogUnitCodes": ["0201010103_22v2"],
        "mcpUnitName": "\uacbd\uc601\uacc4\ud68d \uc218\ub9bd",
    }
    ksa = {
        "ncsClCd": unit["ncsClCd"],
        "compeUnitName": unit["compeUnitName"],
        "factorName": "\uc2dc\uc7a5\ud658\uacbd \ubd84\uc11d",
        "elementName": "\uacbd\uc601\ud658\uacbd \ubd84\uc11d",
        "ksaTypeName": "\uc9c0\uc2dd",
        "ksaNo": "K-01",
        "factorSource": "ncs-mcp",
        "ksaStatus": "official",
    }
    mocker.patch("app.main.fetch_ncs_ksa_by_units", return_value=[ksa])
    rank_ksa = mocker.patch("app.main.rank_ksa_factors_by_query", return_value=[ksa])
    mocker.patch("app.main.build_ncs_context_pack", return_value={})
    build_strategy = mocker.patch(
        "app.main.build_jd_strategy_with_openai",
        return_value=_openai_model_strategy(
            unit,
            ksa,
            methods=("\ubc1c\ud45c\uba74\uc811",),
            follow_up_count=4,
        ),
    )
    with TestClient(main.app) as client:
        resp = client.post(
            "/api/questions/generate-from-text",
            json={
                "openai_api_key": REQUEST_OPENAI_KEY,
                "notice_text": "\uacbd\uc601\uae30\ud68d \ub2f4\ub2f9\uc5c5\ubb34",
                "duty_text": "duty: board reporting and KPI dashboard",
                "evaluation_text": "\ubb38\uc81c\ud574\uacb0\ub2a5\ub825",
                "selected_ncs": [unit],
                "question_plan": {
                    "items": [
                        {"detail": "\uacbd\uc601\uae30\ud68d", "enabled": True, "main_count": 1, "follow_up_count": 4}
                    ]
                },
                "interview_methods": ["\ubc1c\ud45c\uba74\uc811"],
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["openai_key_source"] == "request"
    assert body["operational_notice"] == main.OPERATIONAL_REVIEW_NOTICE
    assert body["question_plan"]["total_main_count"] == 1
    assert body["question_plan"]["follow_up_count"] == 4
    assert body["interview_methods"] == ["\ubc1c\ud45c\uba74\uc811"]
    assert body["ncs_matches"][0]["officialUnitBaseCode"] == "0201010103"
    assert body["ncs_matches"][0]["unitResolutionKind"] == "catalog_full_code_exact"
    assert body["ncs_matches"][0]["catalogUnitCodes"] == ["0201010103_22v2"]
    rank_ksa.assert_called_once()
    ksa_query_text = rank_ksa.call_args.kwargs["query_text"]
    assert "duty: board reporting and KPI dashboard" in ksa_query_text
    assert "\ubb38\uc81c\ud574\uacb0\ub2a5\ub825" in ksa_query_text
    assert "\uacbd\uc601\uae30\ud68d \ub2f4\ub2f9\uc5c5\ubb34" in ksa_query_text
    build_strategy.assert_called_once()
    kwargs = build_strategy.call_args.kwargs
    assert kwargs["api_key_override"] == REQUEST_OPENAI_KEY
    assert main.settings.resolve_openai_key(kwargs["api_key_override"]) == REQUEST_OPENAI_KEY
    assert kwargs["target_count_override"] == 1
    assert kwargs["follow_up_count"] == 4
    assert kwargs["question_plan"]["selected_terms"] == ["\uacbd\uc601\uae30\ud68d"]
    assert kwargs["interview_methods"] == ["\ubc1c\ud45c\uba74\uc811"]
    assert REQUEST_OPENAI_KEY not in resp.text
    assert body["ncs_matches"][0]["unitCatalogVerified"] is True
    assert body["ncs_matches"][0]["unitVersionCompatible"] is False
    assert body["ncs_matches"][0]["officialDetailCode"] == "02010101"
    assert body["ncs_matches"][0]["officialUnitBaseCode"] == "0201010103"


def test_generate_from_text_rejects_tampered_selected_unit_before_ksa(
    monkeypatch,
    mocker,
):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    catalog_row = _catalog_unit_by_code("0201010103_22v2")
    fetch_ksa = mocker.patch("app.main.fetch_ncs_ksa_by_units")
    build_strategy = mocker.patch("app.main.build_jd_strategy_with_openai")

    with TestClient(main.app) as client:
        response = client.post(
            "/api/questions/generate-from-text",
            json={
                "openai_api_key": REQUEST_OPENAI_KEY,
                "notice_text": "공고",
                "selected_ncs": [
                    {
                        "ncsClCd": catalog_row["ncsClCd"],
                        "compeUnitName": "변조된 능력단위명",
                        "ncsSubdCdnm": catalog_row["canonicalDetailName"],
                    }
                ],
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "selected_ncs_official_identity_mismatch",
        "message": (
            "선택한 NCS 능력단위의 코드·명칭·세분류 연결을 "
            "공식 카탈로그에서 확인할 수 없습니다."
        ),
        "invalid_items": [
            {
                "index": 0,
                "ncsClCd": catalog_row["ncsClCd"],
                "reason": "catalog_mismatch",
            }
        ],
        "retryable": False,
    }
    fetch_ksa.assert_not_called()
    build_strategy.assert_not_called()


def test_generate_from_text_requires_request_key_even_with_server_openai_env(monkeypatch, mocker):
    monkeypatch.setenv("OPENAI_API_KEY", SERVER_OPENAI_KEY)
    build_strategy = mocker.patch("app.main.build_jd_strategy_with_openai")

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/questions/generate-from-text",
            json={},
        )

    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "openai_api_key_required"
    assert resp.json()["detail"]["provider"] == "openai_api"
    assert resp.json()["detail"]["retryable"] is False
    assert SERVER_OPENAI_KEY not in resp.text
    build_strategy.assert_not_called()


def test_generate_from_text_never_falls_back_to_server_openai_key(monkeypatch, mocker):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    monkeypatch.setenv("OPENAI_API_KEY", SERVER_OPENAI_KEY)
    unit = {
        "ncsClCd": "0201010103_22v2",
        "compeUnitName": "\uacbd\uc601\uacc4\ud68d \uc218\ub9bd",
        "compeUnitLevel": "5",
        "ncsSubdCdnm": "\uacbd\uc601\uae30\ud68d",
        "compeUnitDef": "\uacbd\uc601\ubaa9\ud45c\ub97c \uc218\ub9bd\ud55c\ub2e4",
    }
    ksa = {
        "ncsClCd": unit["ncsClCd"],
        "compeUnitName": unit["compeUnitName"],
        "factorName": "\uc2dc\uc7a5\ud658\uacbd \ubd84\uc11d",
        "elementName": "\uacbd\uc601\ud658\uacbd \ubd84\uc11d",
        "ksaTypeName": "\uc9c0\uc2dd",
        "ksaNo": "K-01",
        "factorSource": "ncs-mcp",
        "ksaStatus": "official",
    }
    mocker.patch("app.main.fetch_ncs_ksa_by_units", return_value=[ksa])
    mocker.patch("app.main.rank_ksa_factors_by_query", return_value=[ksa])
    mocker.patch("app.main.build_ncs_context_pack", return_value={})
    build_strategy = mocker.patch(
        "app.main.build_jd_strategy_with_openai",
        return_value=_openai_model_strategy(unit, ksa, methods=("경험면접",)),
    )

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/questions/generate-from-text",
            json={
                "openai_api_key": REQUEST_OPENAI_KEY,
                "notice_text": "\uacbd\uc601\uae30\ud68d \ub2f4\ub2f9\uc5c5\ubb34",
                "selected_ncs": [unit],
            },
        )

    body = resp.json()
    assert resp.status_code == 200
    assert body["openai_key_source"] == "request"
    kwargs = build_strategy.call_args.kwargs
    assert kwargs["api_key_override"] == REQUEST_OPENAI_KEY
    assert main.settings.resolve_openai_key(kwargs["api_key_override"]) == REQUEST_OPENAI_KEY
    assert REQUEST_OPENAI_KEY not in resp.text
    assert SERVER_OPENAI_KEY not in resp.text


def test_generation_status_declares_request_scoped_key_contract(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SERVER_OPENAI_KEY)
    monkeypatch.setattr(
        main,
        "ncs_mcp_status",
        lambda: {"configured": True, "reachable": True, "ksaAvailable": True},
    )

    with TestClient(main.app) as client:
        status_resp = client.get("/api/generation-provider/status")
        health_resp = client.get("/health")

    assert status_resp.status_code == 200
    status = status_resp.json()
    assert status["provider"] == "openai_api"
    assert status["auth_mode"] == "request_scoped_api_key"
    assert status["status"] == "key_required"
    assert status["available"] is True
    assert status["authenticated"] is False
    assert status["credential_configured"] is False
    assert status["credential_managed_by"] == "request"
    assert status["requires_request_api_key"] is True
    assert status["local_only"] is False
    assert SERVER_OPENAI_KEY not in status_resp.text
    assert health_resp.status_code == 200
    health = health_resp.json()
    assert health["keys"]["openai"] is False
    assert health["keys"]["openai_institution_managed"] is False
    assert health["keys"]["openai_request_scoped"] is True


def test_generate_from_text_restricts_stale_question_plan_to_selected_ncs(monkeypatch, mocker):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    unit = {
        "ncsClCd": "0202030201_25v3",
        "compeUnitName": "\ubb38\uc11c\uc791\uc131",
        "compeUnitLevel": "3",
        "ncsSubdCdnm": "\uc0ac\ubb34\ud589\uc815",
        "compeUnitDef": "\ubb38\uc11c \uc694\uad6c\uc0ac\ud56d\uc744 \ud30c\uc545\ud558\uc5ec \ubb38\uc11c\ub97c \uc791\uc131\ud55c\ub2e4",
    }
    ksa = {
        "ncsClCd": unit["ncsClCd"],
        "compeUnitName": unit["compeUnitName"],
        "factorName": "\ubb38\uc11c \uc694\uad6c\uc0ac\ud56d \ud30c\uc545",
        "elementName": "\ubb38\uc11c \uc791\uc131 \uc900\ube44",
        "ksaTypeName": "\uc9c0\uc2dd",
        "ksaNo": "K-01",
        "factorSource": "ncs-mcp",
        "ksaStatus": "official",
    }
    mocker.patch("app.main.fetch_ncs_ksa_by_units", return_value=[ksa])
    mocker.patch("app.main.build_ncs_context_pack", return_value={})
    build_strategy = mocker.patch(
        "app.main.build_jd_strategy_with_openai",
        return_value=_openai_model_strategy(
            unit,
            ksa,
            methods=("경험면접",),
            follow_up_count=5,
        ),
    )

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/questions/generate-from-text",
            json={
                "openai_api_key": REQUEST_OPENAI_KEY,
                "notice_text": "\uc0ac\ubb34\ud589\uc815 \ub2f4\ub2f9\uc5c5\ubb34",
                "selected_ncs": [unit],
                "question_plan": {
                    "items": [
                        {"detail": "\ub2e4\ub978\uc138\ubd84\ub958", "enabled": True, "main_count": 9, "follow_up_count": 5}
                    ]
                },
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["question_plan"]["selected_terms"] == ["\uc0ac\ubb34\ud589\uc815"]
    assert body["question_plan"]["total_main_count"] == 1
    assert body["question_plan"]["follow_up_count"] == 5
    kwargs = build_strategy.call_args.kwargs
    assert kwargs["api_key_override"] == REQUEST_OPENAI_KEY
    assert main.settings.resolve_openai_key(kwargs["api_key_override"]) == REQUEST_OPENAI_KEY
    assert kwargs["question_plan"]["selected_terms"] == ["\uc0ac\ubb34\ud589\uc815"]
    assert kwargs["target_count_override"] == 1
    assert REQUEST_OPENAI_KEY not in resp.text


def _catalog_units_for_detail(detail_name: str) -> list[dict]:
    """Return real immutable catalog identities for an official detail label."""

    details = ncs_mcp_client._official_details_by_name_key().get(
        ncs_mcp_client._norm(detail_name), ()
    )
    assert len(details) == 1, detail_name
    detail = details[0]
    rows = [
        row
        for row in ncs_mcp_client._official_unit_catalog_rows()
        if row["officialDetailCode"] == detail["code"]
        and ncs_mcp_client._norm(row["canonicalDetailName"])
        == ncs_mcp_client._norm(detail["name"])
    ]
    assert rows, detail_name
    return rows


def _mcp_catalog_row(catalog_row: dict, *, code: str | None = None) -> dict:
    """Build an MCP response row with the selected catalog identity."""

    return {
        "id": code or catalog_row["ncsClCd"],
        "text": catalog_row["compeUnitName"],
        "path": {
            "small": "catalog-small",
            "sub": catalog_row["canonicalDetailName"],
        },
    }


def _catalog_unit_by_code(code: str) -> dict:
    rows = ncs_mcp_client._official_units_by_full_code().get(code, ())
    assert len(rows) == 1, code
    return rows[0]


def _patch_catalog_version_identity(mocker, catalog_row: dict, *codes: str) -> None:
    """Declare test-only static versions for one real canonical identity."""

    rows = [
        {**catalog_row, "ncsClCd": code, "officialUnitBaseCode": code.split("_", 1)[0]}
        for code in codes
    ]
    mocker.patch(
        "app.services.ncs_mcp_client._official_units_by_full_code",
        return_value={row["ncsClCd"]: (row,) for row in rows},
    )
    mocker.patch(
        "app.services.ncs_mcp_client._official_units_by_base_code",
        return_value={rows[0]["officialUnitBaseCode"]: tuple(rows)},
    )


def test_mcp_search_matches_detail_not_small_category(mocker):
    target = _catalog_units_for_detail("\uacbd\uc601\uae30\ud68d")[0]
    mocker.patch("app.services.ncs_mcp_client._tool_names", return_value={"ncs_search"})
    mocker.patch(
        "app.services.ncs_mcp_client._call_tool",
        return_value={
            "results": [
                {
                    "id": "0201010201_17v1",
                    "text": "\uc18c\ubd84\ub958\ub9cc \uc77c\uce58",
                    "path": {"small": "\uacbd\uc601\uae30\ud68d", "sub": "\uacbd\uc601\ubd84\uc11d"},
                },
                {
                    **_mcp_catalog_row(target),
                },
            ]
        },
    )

    rows = ncs_mcp_client.search_units_by_detail(["\uacbd\uc601\uae30\ud68d"])

    assert [row["ncsClCd"] for row in rows] == [target["ncsClCd"]]


def test_mcp_search_splits_multiple_detail_labels_from_one_input(mocker):
    mocker.patch("app.services.ncs_mcp_client._tool_names", return_value={"ncs_search"})
    calls: list[str] = []

    def fake_call_tool(_name, arguments):
        query = arguments["query"]
        calls.append(query)
        catalog_row = (
            _catalog_unit_by_code("0202020101_23v3")
            if query == _catalog_unit_by_code("0202020101_23v3")["canonicalDetailName"]
            else _catalog_unit_by_code("0101010201_17v2")
        )
        return {
            "results": [
                {
                    **_mcp_catalog_row(catalog_row),
                }
            ]
        }

    mocker.patch("app.services.ncs_mcp_client._call_tool", side_effect=fake_call_tool)

    rows = ncs_mcp_client.search_units_by_detail(["인사, 프로젝트관리"], max_units=10)

    assert calls == ["인사", "프로젝트관리"]
    assert [row["matchedDetailName"] for row in rows] == ["인사", "프로젝트관리"]


def test_mcp_search_preserves_official_detail_with_internal_commas(mocker):
    detail_name = "조선비계(족장, 발판, scaffolding)"
    catalog_row = _catalog_units_for_detail(detail_name)[0]
    calls: list[str] = []

    def fake_call_tool(_name, arguments):
        calls.append(arguments["query"])
        return {"results": [_mcp_catalog_row(catalog_row)]}

    mocker.patch("app.services.ncs_mcp_client._call_tool", side_effect=fake_call_tool)

    rows = ncs_mcp_client.search_units_by_detail([detail_name], max_units=5)

    assert calls == [detail_name]
    assert [row["ncsClCd"] for row in rows] == [catalog_row["ncsClCd"]]
    assert rows[0]["officialDetailCode"] == "15080205"


def test_every_official_detail_label_is_an_atomic_split_term():
    official_rows = [
        row
        for rows in ncs_mcp_client._official_details_by_name_key().values()
        for row in rows
    ]

    assert len(official_rows) == 1094
    assert all(
        ncs_mcp_client._split_detail_terms([row["name"]]) == [row["name"]]
        for row in official_rows
    )


def test_mcp_search_global_limit_preserves_each_confirmed_detail(mocker):
    calls: list[str] = []
    personnel_rows = _catalog_units_for_detail("인사")[:3]
    project_rows = _catalog_units_for_detail("프로젝트관리")[:2]

    def fake_call_tool(_name, arguments):
        query = arguments["query"]
        calls.append(query)
        catalog_rows = (
            personnel_rows
            if query == personnel_rows[0]["canonicalDetailName"]
            else project_rows
        )
        return {
            "results": [_mcp_catalog_row(row) for row in catalog_rows]
        }

    mocker.patch("app.services.ncs_mcp_client._call_tool", side_effect=fake_call_tool)

    rows = ncs_mcp_client.search_units_by_detail(
        ["인사", "프로젝트관리"],
        max_units=2,
    )

    assert calls == ["인사", "프로젝트관리"]
    assert [row["matchedDetailName"] for row in rows] == ["인사", "프로젝트관리"]


def test_mcp_search_small_output_limit_keeps_deep_exact_detail_match(mocker):
    query = "\uacbd\uc601\uae30\ud68d"
    catalog_row = _catalog_units_for_detail(query)[0]
    broad_rows = [
        {
            "id": f"010000{index:04d}_26v1",
            "text": f"\uad11\ubc94\uc704 \ud6c4\ubcf4 {index}",
            "path": {"small": "\uae30\ud68d\uc0ac\ubb34", "sub": "\uacbd\uc601\ubd84\uc11d"},
        }
        for index in range(149)
    ]
    exact_row = _mcp_catalog_row(catalog_row)

    def fake_call_tool(_name, arguments):
        assert arguments["limit"] == 500
        return {"results": [*broad_rows, exact_row]}

    mocker.patch("app.services.ncs_mcp_client._call_tool", side_effect=fake_call_tool)

    rows = ncs_mcp_client.search_units_by_detail([query], max_units=8)

    assert [row["ncsClCd"] for row in rows] == [exact_row["id"]]


def test_mcp_search_reallocates_unused_sparse_group_capacity(mocker):
    sparse = "\uc778\uc0ac"
    dense = "\ud504\ub85c\uc81d\ud2b8\uad00\ub9ac"
    sparse_rows = _catalog_units_for_detail(sparse)[:1]
    dense_rows = _catalog_units_for_detail(dense)[:10]

    def fake_call_tool(_name, arguments):
        query = arguments["query"]
        catalog_rows = sparse_rows if query == sparse else dense_rows
        return {
            "results": [_mcp_catalog_row(row) for row in catalog_rows]
        }

    mocker.patch("app.services.ncs_mcp_client._call_tool", side_effect=fake_call_tool)

    rows = ncs_mcp_client.search_units_by_detail([sparse, dense], max_units=10)

    assert len(rows) == 10
    assert [row["matchedDetailName"] for row in rows].count(sparse) == 1
    assert [row["matchedDetailName"] for row in rows].count(dense) == 9


def test_mcp_search_preserves_official_acronym_slash_detail(mocker):
    mocker.patch("app.services.ncs_mcp_client._tool_names", return_value={"ncs_search"})
    calls: list[str] = []
    catalog_row = _catalog_unit_by_code("0204020101_14v1")

    def fake_call_tool(_name, arguments):
        query = arguments["query"]
        calls.append(query)
        return {
            "results": [_mcp_catalog_row(catalog_row)]
        }

    mocker.patch("app.services.ncs_mcp_client._call_tool", side_effect=fake_call_tool)

    rows = ncs_mcp_client.search_units_by_detail(["QM/QC관리"], max_units=5)

    assert calls == ["QM/QC관리"]
    assert [row["ncsClCd"] for row in rows] == ["0204020101_14v1"]
    assert rows[0]["matchedDetailName"] == "QM/QC관리"


def test_mcp_ksa_alias_fields_preserve_and_balance_knowledge_skill_attitude(mocker):
    mocker.patch("app.services.ncs_mcp_client._tool_names", return_value={"ncs_unit_detail"})
    mocker.patch(
        "app.services.ncs_mcp_client._call_tool",
        return_value={
            "data": {
                "unit": {
                    "unit_code": "U1",
                    "unit_name": "문서관리",
                    "classification": {"sub": "사무행정"},
                },
                "elements": [
                    {
                        "element_id": "E1",
                        "element_name": "문서 점검",
                        "ksa": [
                            {"factor_name": "책임 있는 보고 자세", "factorType": "A", "number": "A-1"},
                            {"ksa_text": "문서 분류 기준", "ksa_type": "knowledge", "ksaNo": "K-1"},
                            {"factorName": "오류 대조 점검", "ksaType": "스킬", "ksa_no": "S-1"},
                        ],
                    }
                ],
            }
        },
    )

    rows = ncs_mcp_client.get_ksa_by_units([{"ncsClCd": "U1"}], max_factors_per_unit=3)

    assert [row["ksaTypeName"] for row in rows] == ["지식", "기술", "태도"]
    assert [row["factorName"] for row in rows] == ["문서 분류 기준", "오류 대조 점검", "책임 있는 보고 자세"]
    assert [row["ksaNo"] for row in rows] == ["K-1", "S-1", "A-1"]
    assert all(row["isOfficialKsa"] is True for row in rows)


def test_mcp_ksa_limit_balances_types_without_hiding_later_elements(mocker):
    mocker.patch("app.services.ncs_mcp_client._tool_names", return_value={"ncs_unit_detail"})
    first_element_rows = [
        {"text": f"첫 요소 지식 {index}", "ksa_type": "knowledge", "ksa_no": f"K{index}"}
        for index in range(1, 4)
    ] + [
        {"text": f"첫 요소 기술 {index}", "ksa_type": "skill", "ksa_no": f"S{index}"}
        for index in range(1, 4)
    ] + [
        {"text": f"첫 요소 태도 {index}", "ksa_type": "attitude", "ksa_no": f"A{index}"}
        for index in range(1, 4)
    ]
    mocker.patch(
        "app.services.ncs_mcp_client._call_tool",
        return_value={
            "data": {
                "unit": {
                    "unit_code": "U1",
                    "unit_name": "문서관리",
                    "classification": {"sub": "사무행정"},
                },
                "elements": [
                    {"element_id": "E1", "element_name": "작성", "ksa": first_element_rows},
                    {
                        "element_id": "E2",
                        "element_name": "검토",
                        "ksa": [
                            {"text": "둘째 요소 지식", "ksa_type": "knowledge", "ksa_no": "K4"},
                            {"text": "둘째 요소 기술", "ksa_type": "skill", "ksa_no": "S4"},
                            {"text": "둘째 요소 태도", "ksa_type": "attitude", "ksa_no": "A4"},
                        ],
                    },
                ],
            }
        },
    )

    rows = ncs_mcp_client.get_ksa_by_units([{"ncsClCd": "U1"}], max_factors_per_unit=6)

    assert len(rows) == 6
    assert [row["ksaTypeName"] for row in rows] == ["지식", "기술", "태도", "지식", "기술", "태도"]
    assert {row["elementId"] for row in rows} == {"E1", "E2"}
    assert sum(row["elementId"] == "E2" for row in rows) == 3
    assert {row["factorName"] for row in rows if row["elementId"] == "E2"} == {
        "둘째 요소 지식",
        "둘째 요소 기술",
        "둘째 요소 태도",
    }


def test_mcp_ksa_concurrency_preserves_confirmed_unit_order(monkeypatch, mocker):
    monkeypatch.setenv("NCS_MCP_KSA_CONCURRENCY", "3")
    mocker.patch(
        "app.services.ncs_mcp_client._tool_names",
        return_value={"ncs_unit_detail"},
    )
    lock = threading.Lock()
    active = 0
    max_active = 0

    def fake_call_tool(_name, arguments):
        nonlocal active, max_active
        code = arguments["unit_code"]
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep({"U1": 0.03, "U2": 0.01, "U3": 0.02}[code])
        with lock:
            active -= 1
        return {
            "data": {
                "unit": {
                    "unit_code": code,
                    "unit_name": f"Unit {code}",
                    "classification": {"sub": "Facilities"},
                },
                "elements": [
                    {
                        "element_id": f"E-{code}",
                        "element_name": f"Element {code}",
                        "ksa": [
                            {
                                "text": f"Knowledge {code}",
                                "ksa_type": "knowledge",
                                "ksa_no": "K1",
                            }
                        ],
                    }
                ],
            }
        }

    mocker.patch(
        "app.services.ncs_mcp_client._call_tool",
        side_effect=fake_call_tool,
    )

    rows = ncs_mcp_client.get_ksa_by_units(
        [{"ncsClCd": code} for code in ("U1", "U2", "U3")],
        max_factors_per_unit=1,
    )

    assert max_active >= 2
    assert [row["ncsClCd"] for row in rows] == ["U1", "U2", "U3"]
    assert all(row["unitIdentityVerified"] is True for row in rows)


def test_mcp_ksa_rejects_missing_or_mismatched_response_unit_identity(mocker):
    mocker.patch("app.services.ncs_mcp_client._tool_names", return_value={"ncs_unit_detail"})
    call = mocker.patch(
        "app.services.ncs_mcp_client._call_tool",
        return_value={
            "data": {
                "unit": {"unit_code": "WRONG", "unit_name": "Wrong"},
                "elements": [],
            }
        },
    )

    with pytest.raises(ncs_mcp_client.NcsMcpError, match="identity mismatch"):
        ncs_mcp_client.get_ksa_by_units([{"ncsClCd": "U1"}])

    assert call.call_count == 1


def test_mcp_ksa_retries_one_transient_unit_failure(mocker):
    mocker.patch("app.services.ncs_mcp_client._tool_names", return_value={"ncs_unit_detail"})
    call = mocker.patch(
        "app.services.ncs_mcp_client._call_tool",
        side_effect=[
            ncs_mcp_client.NcsMcpError("temporary"),
            {
                "data": {
                    "unit": {"unit_code": "U1", "unit_name": "Unit 1"},
                    "elements": [
                        {
                            "element_id": "E1",
                            "element_name": "Element",
                            "ksa": [{"text": "Knowledge", "ksa_type": "knowledge"}],
                        }
                    ],
                }
            },
        ],
    )

    rows = ncs_mcp_client.get_ksa_by_units([{"ncsClCd": "U1"}], max_factors_per_unit=1)

    assert call.call_count == 2
    assert rows[0]["responseUnitCode"] == "U1"


def test_parallel_mcp_ksa_failure_does_not_submit_or_retry_full_backlog(
    monkeypatch, mocker
):
    monkeypatch.setenv("NCS_MCP_KSA_CONCURRENCY", "4")
    calls: list[str] = []
    calls_lock = threading.Lock()

    def fail_call(_name, arguments):
        with calls_lock:
            calls.append(str(arguments["unit_code"]))
        raise ncs_mcp_client.NcsMcpError("offline")

    mocker.patch("app.services.ncs_mcp_client._call_tool", side_effect=fail_call)

    with pytest.raises(ncs_mcp_client.NcsMcpError, match="offline|batch cancelled"):
        ncs_mcp_client.get_ksa_by_units(
            [{"ncsClCd": f"U{index}"} for index in range(8)],
            max_factors_per_unit=1,
        )

    assert len(calls) <= 5
    assert set(calls).issubset({"U0", "U1", "U2", "U3"})


def test_parallel_mcp_ksa_returns_primary_failure_without_waiting_for_active_peer(
    monkeypatch, mocker
):
    monkeypatch.setenv("NCS_MCP_KSA_CONCURRENCY", "2")
    slow_started = threading.Event()
    release_slow = threading.Event()
    slow_finished = threading.Event()

    def mixed_call(_name, arguments):
        code = str(arguments["unit_code"])
        if code == "U0":
            assert slow_started.wait(timeout=1.0)
            raise ncs_mcp_client.NcsMcpError("primary fast failure")
        slow_started.set()
        try:
            assert release_slow.wait(timeout=2.0)
        finally:
            slow_finished.set()
        return {}

    mocker.patch("app.services.ncs_mcp_client._call_tool", side_effect=mixed_call)
    started_at = time.monotonic()
    try:
        with pytest.raises(
            ncs_mcp_client.NcsMcpError,
            match="primary fast failure",
        ):
            ncs_mcp_client.get_ksa_by_units(
                [{"ncsClCd": "U0"}, {"ncsClCd": "U1"}],
                max_factors_per_unit=1,
            )
        assert time.monotonic() - started_at < 0.5
    finally:
        release_slow.set()
        assert slow_finished.wait(timeout=1.0)


def test_mcp_search_rejects_legacy_combined_detail_with_single_detail_code(mocker):
    mocker.patch("app.services.ncs_mcp_client._tool_names", return_value={"ncs_search"})
    mocker.patch(
        "app.services.ncs_mcp_client._call_tool",
        return_value={
            "results": [
                {
                    "id": "1301010401_17v1",
                    "text": "일식 복어조리",
                    "path": {"small": "음식조리", "sub": "일식·복어조리"},
                }
            ]
        },
    )

    rows = ncs_mcp_client.search_units_by_detail(["일식· 복어・조리"])

    # Current NCS has separate 일식조리(13010104) and 복어조리(13010105)
    # details. A stale combined path label must not authorize only the first
    # code, even when punctuation normalization makes the surfaces equal.
    assert rows == []


def _two_distinct_official_detail_rows() -> tuple[dict[str, str], dict[str, str]]:
    rows = [
        row
        for matches in ncs_mcp_client._official_details_by_name_key().values()
        for row in matches
    ]
    first = rows[0]
    second = next(row for row in rows[1:] if row["code"] != first["code"])
    return first, second


def test_mcp_search_rejects_active_code_owned_by_another_detail(mocker):
    expected_detail, other_detail = _two_distinct_official_detail_rows()
    mocker.patch("app.services.ncs_mcp_client._tool_names", return_value={"ncs_search"})
    mocker.patch(
        "app.services.ncs_mcp_client._call_tool",
        return_value={
            "results": [
                {
                    "id": f"{other_detail['code']}01_25v1",
                    "text": "synthetic unit",
                    "path": {"small": "synthetic", "sub": expected_detail["name"]},
                }
            ]
        },
    )

    rows = ncs_mcp_client.search_units_by_detail([expected_detail["name"]], max_units=5)

    assert rows == []


def test_mcp_search_rejects_non_official_path_sub_name(mocker):
    expected_detail, _other_detail = _two_distinct_official_detail_rows()
    source_name = "synthetic non-official detail"
    mocker.patch("app.services.ncs_mcp_client._tool_names", return_value={"ncs_search"})
    mocker.patch(
        "app.services.ncs_mcp_client._call_tool",
        return_value={
            "results": [
                {
                    "id": f"{expected_detail['code']}01_25v1",
                    "text": "synthetic unit",
                    "path": {"small": "synthetic", "sub": source_name},
                }
            ]
        },
    )

    rows = ncs_mcp_client.search_units_by_detail([source_name], max_units=5)

    assert rows == []


def test_mcp_search_attaches_catalog_verified_detail_identity(mocker):
    expected_detail, _other_detail = _two_distinct_official_detail_rows()
    catalog_row = _catalog_units_for_detail(expected_detail["name"])[0]
    mocker.patch("app.services.ncs_mcp_client._tool_names", return_value={"ncs_search"})
    mocker.patch(
        "app.services.ncs_mcp_client._call_tool",
        return_value={
            "results": [_mcp_catalog_row(catalog_row)]
        },
    )

    rows = ncs_mcp_client.search_units_by_detail([expected_detail["name"]], max_units=5)

    assert len(rows) == 1
    assert rows[0]["officialDetailCode"] == expected_detail["code"]
    assert rows[0]["officialDetailName"] == expected_detail["name"]
    assert rows[0]["detailResolutionKind"] == "direct"
    assert rows[0]["detailResolutionRule"] == "direct"


@pytest.mark.parametrize(
    "malformed_code",
    [
        "{detail_code}",
        "{detail_code}ab",
        "{detail_code}01_25v1_extra",
        "{detail_code}01_25-v1",
    ],
)
def test_mcp_search_rejects_malformed_ability_unit_codes(mocker, malformed_code):
    expected_detail, _other_detail = _two_distinct_official_detail_rows()
    code = malformed_code.format(detail_code=expected_detail["code"])
    mocker.patch("app.services.ncs_mcp_client._tool_names", return_value={"ncs_search"})
    mocker.patch(
        "app.services.ncs_mcp_client._call_tool",
        return_value={
            "results": [
                {
                    "id": code,
                    "text": "synthetic unit",
                    "path": {
                        "small": "synthetic",
                        "sub": expected_detail["name"],
                    },
                }
            ]
        },
    )

    rows = ncs_mcp_client.search_units_by_detail([expected_detail["name"]], max_units=5)

    assert rows == []


@pytest.mark.parametrize("code_suffix", ["01", "01_25v1"])
def test_mcp_search_accepts_well_formed_ability_unit_codes(mocker, code_suffix):
    expected_detail, _other_detail = _two_distinct_official_detail_rows()
    catalog_row = _catalog_units_for_detail(expected_detail["name"])[0]
    expected_code = f"{expected_detail['code']}{code_suffix}"
    _patch_catalog_version_identity(mocker, catalog_row, expected_code)
    mocker.patch("app.services.ncs_mcp_client._tool_names", return_value={"ncs_search"})
    mocker.patch(
        "app.services.ncs_mcp_client._call_tool",
        return_value={
            "results": [_mcp_catalog_row(catalog_row, code=expected_code)]
        },
    )

    rows = ncs_mcp_client.search_units_by_detail([expected_detail["name"]], max_units=5)

    assert [row["ncsClCd"] for row in rows] == [expected_code]


def test_mcp_search_retries_simple_split_table_labels_in_compact_form(mocker):
    mocker.patch("app.services.ncs_mcp_client._tool_names", return_value={"ncs_search"})
    calls = []
    catalog_by_query = {
        name: _catalog_units_for_detail(name)[0]
        for name in ("프로젝트관리", "산학협력관리", "시각디자인", "경영기획")
    }

    def fake_call_tool(name, arguments):
        assert name == "ncs_search"
        query = arguments["query"]
        calls.append(query)
        catalog_row = catalog_by_query.get(query)
        if not catalog_row:
            return {"results": []}
        return {
            "results": [_mcp_catalog_row(catalog_row)]
        }

    mocker.patch("app.services.ncs_mcp_client._call_tool", side_effect=fake_call_tool)

    cases = [
        ("프로젝트 관리", "프로젝트관리"),
        ("산학협력 관리", "산학협력관리"),
        ("시각 디자인", "시각디자인"),
        ("경영 기획", "경영기획"),
    ]
    for source_label, official_label in cases:
        rows = ncs_mcp_client.search_units_by_detail([source_label], max_units=5)
        assert [row["ncsClCd"] for row in rows] == [
            catalog_by_query[official_label]["ncsClCd"]
        ]
        assert rows[0]["matchedDetailName"] == source_label
        assert rows[0]["ncsSubdCdnm"] == official_label
        assert rows[0]["detailResolutionKind"] == "format_variant"
        assert rows[0]["detailResolutionRule"] == "whitespace_compact"

    assert calls == [
        value
        for source_label, official_label in cases
        for value in (source_label, official_label)
    ]


def test_mcp_search_retries_punctuation_and_ordinal_formatting_variants(mocker):
    mocker.patch("app.services.ncs_mcp_client._tool_names", return_value={"ncs_search"})
    calls = []
    catalog_by_query = {
        "회계감사": _catalog_units_for_detail("회계·감사")[0],
        "영상촬영": _catalog_units_for_detail("영상촬영")[0],
    }

    def fake_call_tool(name, arguments):
        assert name == "ncs_search"
        query = arguments["query"]
        calls.append(query)
        catalog_row = catalog_by_query.get(query)
        if not catalog_row:
            return {"results": []}
        return {
            "results": [_mcp_catalog_row(catalog_row)]
        }

    mocker.patch("app.services.ncs_mcp_client._call_tool", side_effect=fake_call_tool)

    punctuation = ncs_mcp_client.search_units_by_detail(["회계.감사"], max_units=5)
    ordinal = ncs_mcp_client.search_units_by_detail(["02 영상촬영"], max_units=5)

    assert [row["ncsClCd"] for row in punctuation] == [
        catalog_by_query["회계감사"]["ncsClCd"]
    ]
    assert punctuation[0]["matchedDetailName"] == "회계.감사"
    assert punctuation[0]["ncsSubdCdnm"] == "회계·감사"
    assert punctuation[0]["detailResolutionRule"] == "punctuation_variant"
    assert [row["ncsClCd"] for row in ordinal] == [
        catalog_by_query["영상촬영"]["ncsClCd"]
    ]
    assert ordinal[0]["matchedDetailName"] == "02 영상촬영"
    assert ordinal[0]["ncsSubdCdnm"] == "영상촬영"
    assert ordinal[0]["detailResolutionRule"] == "ordinal_prefix_stripped"
    assert calls == ["회계.감사", "회계감사", "02 영상촬영", "02영상촬영", "영상촬영"]


def test_mcp_search_accepts_unicode_dot_leader_as_exact_format_variant(mocker):
    mocker.patch("app.services.ncs_mcp_client._tool_names", return_value={"ncs_search"})
    calls = []

    def fake_call_tool(name, arguments):
        assert name == "ncs_search"
        query = arguments["query"]
        calls.append(query)
        if query != "회계감사":
            return {"results": []}
        return {
            "results": [
                {
                    "id": "0203020101_25v1",
                    "text": "전표관리",
                    "path": {
                        "major": "경영·회계·사무",
                        "middle": "재무·회계",
                        "small": "회계",
                        "sub": "회계·감사",
                    },
                }
            ]
        }

    mocker.patch("app.services.ncs_mcp_client._call_tool", side_effect=fake_call_tool)

    rows = ncs_mcp_client.search_units_by_detail(["회계․감사"], max_units=5)

    assert calls == ["회계․감사", "회계감사"]
    assert rows[0]["matchedDetailName"] == "회계․감사"
    assert rows[0]["ncsSubdCdnm"] == "회계·감사"


def test_mcp_search_resolves_safe_detail_alias(mocker):
    mocker.patch("app.services.ncs_mcp_client._tool_names", return_value={"ncs_search"})
    calls = []

    def fake_call_tool(name, arguments):
        calls.append(arguments["query"])
        if arguments["query"] == "건축공사감리":
            return {
                "results": [
                    {
                        "id": "1403010301_17v1",
                        "text": "공사착공관리",
                        "path": {"small": "건축설계·감리", "sub": "건축공사감리"},
                    }
                ]
            }
        return {"results": []}

    mocker.patch("app.services.ncs_mcp_client._call_tool", side_effect=fake_call_tool)

    rows = ncs_mcp_client.search_units_by_detail(["건축감리"], max_units=5)

    assert calls == ["건축감리", "건축공사감리"]
    assert [row["ncsClCd"] for row in rows] == ["1403010301_17v1"]
    assert rows[0]["matchedDetailName"] == "건축감리"
    assert rows[0]["resolvedDetailName"] == "건축공사감리"
    assert rows[0]["detailQueryName"] == "건축공사감리"
    assert rows[0]["detailResolutionRule"] == "safe_alias"
    assert rows[0]["source"] == "ncs-mcp-detail-alias"


def test_mcp_search_falls_back_to_safe_alias_after_unverified_direct_match(mocker):
    mocker.patch("app.services.ncs_mcp_client._tool_names", return_value={"ncs_search"})
    calls = []

    def fake_call_tool(name, arguments):
        calls.append(arguments["query"])
        if arguments["query"] == "건축공사감리":
            return {
                "results": [
                    {
                        "id": "1403010301_17v1",
                        "text": "공사착공관리",
                        "path": {
                            "small": "건축설계·감리",
                            "sub": "건축공사감리",
                        },
                    }
                ]
            }
        return {
            "results": [
                {
                    "id": "1403010301_17v1",
                    "text": "건축감리 수행",
                    "path": {"small": "건축설계·감리", "sub": "건축감리"},
                }
            ]
        }

    mocker.patch("app.services.ncs_mcp_client._call_tool", side_effect=fake_call_tool)

    rows = ncs_mcp_client.search_units_by_detail(["건축감리"], max_units=5)

    assert calls == ["건축감리", "건축공사감리"]
    assert [row["ncsClCd"] for row in rows] == ["1403010301_17v1"]
    assert rows[0]["matchedDetailName"] == "건축감리"
    assert rows[0]["resolvedDetailName"] == "건축공사감리"
    assert rows[0]["detailQueryName"] == "건축공사감리"
    assert rows[0]["detailResolutionKind"] == "safe_alias"
    assert rows[0]["source"] == "ncs-mcp-detail-alias"


def test_mcp_search_matches_parenthetical_secretary_detail_to_official_subdetail(mocker):
    mocker.patch("app.services.ncs_mcp_client._tool_names", return_value={"ncs_search"})
    query = "비서 (글로벌경영사무 지원)"
    calls = []
    catalog_row = _catalog_unit_by_code("0202030101_22v2")

    def fake_call_tool(name, arguments):
        assert name == "ncs_search"
        calls.append(arguments["query"])
        if arguments["query"] != "비서":
            return {"results": []}
        return {
            "results": [_mcp_catalog_row(catalog_row)]
        }

    mocker.patch("app.services.ncs_mcp_client._call_tool", side_effect=fake_call_tool)

    rows = ncs_mcp_client.search_units_by_detail([query], max_units=5)

    assert calls == [query, "비서"]
    assert [row["ncsClCd"] for row in rows] == ["0202030101_22v2"]
    assert rows[0]["ncsSubdCdnm"] == "비서"
    assert rows[0]["matchedDetailName"] == query
    assert rows[0]["resolvedDetailName"] == "비서"
    assert rows[0]["detailQueryName"] == "비서"
    assert rows[0]["source"] == "ncs-mcp-detail-alias"


def test_mcp_search_uses_wider_window_for_truncated_exact_detail(mocker):
    mocker.patch("app.services.ncs_mcp_client._tool_names", return_value={"ncs_search"})
    calls = []

    def fake_call_tool(name, arguments):
        calls.append(arguments)
        if arguments["limit"] <= 50:
            return {"results": []}
        broad_rows = [
            {
                "id": f"broad-{idx}",
                "text": "시설물안전관리",
                "path": {"small": "총무", "sub": "자산관리"},
            }
            for idx in range(60)
        ]
        exact_rows = [
            {
                "id": "1401030101_25v3",
                "text": "유지관리 계획수립",
                "path": {"small": "건설시공후관리", "sub": "유지관리"},
            }
        ]
        return {"results": broad_rows + exact_rows}

    mocker.patch("app.services.ncs_mcp_client._call_tool", side_effect=fake_call_tool)

    rows = ncs_mcp_client.search_units_by_detail(["유지관리"], max_units=8)

    assert calls[0]["limit"] > 50
    assert [row["ncsClCd"] for row in rows] == ["1401030101_25v3"]
    assert rows[0]["ncsSubdCdnm"] == "유지관리"
    assert rows[0]["source"] == "ncs-mcp"


def test_mcp_suggest_units_by_text_keeps_non_exact_candidates(mocker):
    mocker.patch("app.services.ncs_mcp_client._tool_names", return_value={"ncs_search"})
    mocker.patch(
        "app.services.ncs_mcp_client._call_tool",
        return_value={
            "results": [
                {
                    "id": "2402010401_17v1",
                    "text": "\uc784\uc0c1\ubcd1\ub9ac \uad00\ub828 \uc9c8\ubcd1\uc9c4\ub2e8",
                    "path": {"small": "\ucd95\uc0b0\uc790\uc6d0\uac1c\ubc1c", "sub": "\uc218\uc758\uc11c\ube44\uc2a4"},
                    "score": 0.42,
                }
            ]
        },
    )

    rows = ncs_mcp_client.suggest_units_by_text(["\uc784\uc0c1\ubcd1\ub9ac"], max_units=5)

    assert rows[0]["ncsClCd"] == "2402010401_17v1"
    assert rows[0]["source"] == "ncs-mcp-suggest"
    assert rows[0]["isExactDetailMatch"] is False
    assert rows[0]["isExactUnitNameMatch"] is False
    assert rows[0]["canonicalDetailName"] == "\uc218\uc758\uc11c\ube44\uc2a4"


def test_mcp_suggest_units_by_text_marks_exact_unit_name_match(mocker):
    mocker.patch("app.services.ncs_mcp_client._tool_names", return_value={"ncs_search"})
    mocker.patch(
        "app.services.ncs_mcp_client._call_tool",
        return_value={
            "results": [
                {
                    "id": "1203040201_17v1",
                    "text": "\uce74\uc9c0\ub178 \uace0\uac1d \uc9c0\uc6d0",
                    "path": {"small": "\uad00\uad11\ub808\uc800\uc11c\ube44\uc2a4", "sub": "\uce74\uc9c0\ub178\uc6b4\uc601\uad00\ub9ac"},
                    "score": 0.0,
                }
            ]
        },
    )

    rows = ncs_mcp_client.suggest_units_by_text(["\uce74\uc9c0\ub178 \uace0\uac1d \uc9c0\uc6d0"], max_units=5)

    assert rows[0]["ncsClCd"] == "1203040201_17v1"
    assert rows[0]["isExactDetailMatch"] is False
    assert rows[0]["isExactUnitNameMatch"] is True
    assert rows[0]["canonicalDetailName"] == "\uce74\uc9c0\ub178\uc6b4\uc601\uad00\ub9ac"


def test_ncs_unit_options_falls_back_to_manual_suggestions(monkeypatch, mocker):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    mocker.patch("app.main.search_units_by_detail", return_value=[])
    suggestion = {
        "ncsClCd": "0601010101_20v1",
        "compeUnitName": "\uc758\ub8cc\uc9c0\uc6d0 \ud6c4\ubcf4",
        "ncsSubdCdnm": "\uc758\ub8cc\uae30\uae30\uad00\ub9ac",
        "source": "ncs-mcp-suggest",
    }
    mocker.patch("app.main.suggest_units_by_text", return_value=[suggestion])

    with TestClient(main.app) as client:
        resp = client.get("/api/ncs/units/options?q=\uc784\uc0c1\ubcd1\ub9ac&limit=10")

    body = resp.json()
    assert resp.status_code == 200
    assert body["source"] == "ncs-mcp-suggest"
    assert body["items"] == [suggestion]
    assert "Exact detail-class match" in body["message"]


def test_ncs_unit_options_splits_multi_term_query(monkeypatch, mocker):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    search = mocker.patch(
        "app.main.search_units_by_detail",
        return_value=[{"ncsClCd": "0202020101_23v3"}],
    )

    with TestClient(main.app) as client:
        response = client.get(
            "/api/ncs/units/options",
            params={"q": "인사, 프로젝트관리", "limit": 20},
        )

    assert response.status_code == 200
    search.assert_called_once_with(["인사", "프로젝트관리"], max_units=20)


def _install_synthetic_unit_catalog(mocker, rows):
    """Keep MCP-link verification tests independent of shipped catalog rows."""

    detail = {"code": "12345678", "name": "detail-alpha"}
    full_code_index = {}
    base_code_index = {}
    for row in rows:
        full_code_index.setdefault(row["ncsClCd"], []).append(row)
        base_code_index.setdefault(row["officialUnitBaseCode"], []).append(row)
    mocker.patch(
        "app.services.ncs_mcp_client._official_details_by_name_key",
        return_value={ncs_mcp_client._norm(detail["name"]): (detail,)},
    )
    mocker.patch(
        "app.services.ncs_mcp_client._official_units_by_full_code",
        return_value={key: tuple(value) for key, value in full_code_index.items()},
    )
    mocker.patch(
        "app.services.ncs_mcp_client._official_units_by_base_code",
        return_value={key: tuple(value) for key, value in base_code_index.items()},
    )
    return detail


def _synthetic_catalog_unit(code="1234567801_25v1", name="unit-alpha"):
    return {
        "ncsClCd": code,
        "officialUnitBaseCode": code.split("_", 1)[0],
        "compeUnitName": name,
        "officialDetailCode": "12345678",
        "canonicalDetailName": "detail-alpha",
    }


def _search_result(code, name="unit-alpha", detail="detail-alpha"):
    return {
        "id": code,
        "text": name,
        "path": {"small": "small-alpha", "sub": detail},
    }


def test_mcp_search_requires_full_catalog_code_and_canonical_unit_identity(mocker):
    catalog = _synthetic_catalog_unit()
    detail = _install_synthetic_unit_catalog(mocker, [catalog])
    mocker.patch(
        "app.services.ncs_mcp_client._call_tool",
        return_value={"results": [_search_result(catalog["ncsClCd"])]},
    )

    rows = ncs_mcp_client.search_units_by_detail([detail["name"]], max_units=1)

    assert len(rows) == 1
    assert rows[0]["officialUnitBaseCode"] == "1234567801"
    assert rows[0]["officialUnitName"] == "unit-alpha"
    assert rows[0]["mcpUnitName"] == "unit-alpha"
    assert rows[0]["unitResolutionKind"] == "catalog_full_code_exact"
    assert rows[0]["unitVersionCompatible"] is False
    assert rows[0]["catalogUnitCodes"] == [catalog["ncsClCd"]]


def test_mcp_search_rejects_exact_catalog_code_with_name_mismatch_without_fallback(mocker):
    catalog = _synthetic_catalog_unit()
    detail = _install_synthetic_unit_catalog(mocker, [catalog])
    mocker.patch(
        "app.services.ncs_mcp_client._call_tool",
        return_value={
            "results": [
                _search_result(catalog["ncsClCd"], name="renamed unit"),
            ]
        },
    )

    assert ncs_mcp_client.search_units_by_detail([detail["name"]]) == []


def test_mcp_search_rejects_duplicated_full_catalog_code(mocker):
    catalog = _synthetic_catalog_unit()
    duplicate = {**catalog, "compeUnitName": "conflicting unit"}
    detail = _install_synthetic_unit_catalog(mocker, [catalog, duplicate])
    mocker.patch(
        "app.services.ncs_mcp_client._call_tool",
        return_value={"results": [_search_result(catalog["ncsClCd"])]},
    )

    assert ncs_mcp_client.search_units_by_detail([detail["name"]]) == []


def test_mcp_search_rejects_duplicate_exact_catalog_code_identity(mocker):
    catalog_rows = [
        _synthetic_catalog_unit("1234567801_25v1", name="unit-alpha"),
        _synthetic_catalog_unit("1234567801_25v1", name="unit-alpha"),
    ]
    detail = _install_synthetic_unit_catalog(mocker, catalog_rows)
    mocker.patch(
        "app.services.ncs_mcp_client._call_tool",
        return_value={"results": [_search_result("1234567801_25v1")]},
    )

    assert ncs_mcp_client.search_units_by_detail([detail["name"]]) == []


def test_mcp_search_allows_only_catalog_proven_base_version_compatibility(mocker):
    catalog_rows = [
        _synthetic_catalog_unit("1234567801_24v1"),
        _synthetic_catalog_unit("1234567801_25v1"),
    ]
    detail = _install_synthetic_unit_catalog(mocker, catalog_rows)
    mcp_code = "1234567801_26v9"
    mocker.patch(
        "app.services.ncs_mcp_client._call_tool",
        return_value={"results": [_search_result(mcp_code)]},
    )

    rows = ncs_mcp_client.search_units_by_detail([detail["name"]])

    assert [row["ncsClCd"] for row in rows] == [mcp_code]
    assert rows[0]["unitResolutionKind"] == "catalog_base_version_compatible"
    assert rows[0]["catalogUnitCodes"] == [
        "1234567801_24v1",
        "1234567801_25v1",
    ]
    assert rows[0]["unitVersionCompatible"] is True


def test_mcp_search_rejects_base_version_fallback_when_same_base_has_multiple_names(mocker):
    catalog_rows = [
        _synthetic_catalog_unit("1234567801_24v1", name="unit-alpha"),
        _synthetic_catalog_unit("1234567801_25v1", name="unit-beta"),
    ]
    detail = _install_synthetic_unit_catalog(mocker, catalog_rows)
    mocker.patch(
        "app.services.ncs_mcp_client._call_tool",
        return_value={"results": [_search_result("1234567801_26v1", name="unit-alpha")]},
    )

    assert ncs_mcp_client.search_units_by_detail([detail["name"]]) == []


def test_mcp_search_invalid_row_does_not_consume_identity_or_limit(mocker):
    catalog = _synthetic_catalog_unit()
    detail = _install_synthetic_unit_catalog(mocker, [catalog])
    compatible_code = "1234567801_26v1"
    mocker.patch(
        "app.services.ncs_mcp_client._call_tool",
        return_value={
            "results": [
                _search_result(catalog["ncsClCd"], name="renamed unit"),
                _search_result(compatible_code),
            ]
        },
    )

    rows = ncs_mcp_client.search_units_by_detail([detail["name"]], max_units=1)

    assert [row["ncsClCd"] for row in rows] == [compatible_code]


def test_mcp_search_dedupes_versions_by_first_mcp_rank_without_suffix_inference(mocker):
    catalog_rows = [
        _synthetic_catalog_unit("1234567801_25v1"),
        _synthetic_catalog_unit("1234567801_24v1"),
    ]
    detail = _install_synthetic_unit_catalog(mocker, catalog_rows)
    first_mcp_code = "1234567801_26v1"
    mocker.patch(
        "app.services.ncs_mcp_client._call_tool",
        return_value={
            "results": [
                _search_result(first_mcp_code),
                _search_result("1234567801_25v1"),
            ]
        },
    )

    rows = ncs_mcp_client.search_units_by_detail([detail["name"]], max_units=5)

    assert [row["ncsClCd"] for row in rows] == [first_mcp_code]
    assert rows[0]["unitResolutionKind"] == "catalog_base_version_compatible"


def test_mcp_search_fails_loudly_when_bundled_detail_catalog_is_unavailable(mocker):
    mocker.patch(
        "app.services.ncs_mcp_client._official_details_by_name_key",
        return_value={},
    )
    mocker.patch(
        "app.services.ncs_mcp_client._official_unit_catalog_rows",
        return_value=(_synthetic_catalog_unit(),),
    )

    with pytest.raises(ncs_mcp_client.NcsMcpError, match="detail catalog"):
        ncs_mcp_client.search_units_by_detail(["detail-alpha"])


def test_ncs_units_options_reports_catalog_failure_instead_of_empty_result(
    mocker, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("NCS_MCP_URL", "http://ncs-mcp.test/mcp")
    mocker.patch(
        "app.services.ncs_mcp_client._official_details_by_name_key",
        return_value={
            ncs_mcp_client._norm("detail-alpha"): (
                {"code": "12345678", "name": "detail-alpha"},
            )
        },
    )
    mocker.patch(
        "app.services.ncs_mcp_client._official_unit_catalog_rows",
        return_value=(),
    )

    with TestClient(main.app) as client:
        response = client.get("/api/ncs/units/options", params={"q": "detail-alpha"})

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "ncs_mcp_search_failed"


def test_legacy_ncs_sclass_ksa_endpoint_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ENABLE_LEGACY_NCS_API", raising=False)

    with TestClient(main.app) as client:
        resp = client.get("/api/ncs/sclass/ksa?sclassName=\ucd1d\ubb34")

    assert resp.status_code == 410


def test_ksa_lookup_requires_ncs_mcp_url(monkeypatch):
    monkeypatch.delenv("NCS_MCP_URL", raising=False)

    with pytest.raises(ncs_mcp_client.NcsMcpError, match="NCS_MCP_URL"):
        fetch_ncs_ksa_by_units(
            [{"ncsClCd": "0201010103_22v2", "compeUnitName": "\uacbd\uc601\uacc4\ud68d \uc218\ub9bd"}],
            max_units=1,
            max_factors_per_unit=1,
        )


def test_read_upload_limited_never_requests_unbounded_body(monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")

    class FakeUpload:
        requested_sizes: list[int] = []

        async def read(self, size: int = -1) -> bytes:
            self.requested_sizes.append(size)
            return b"x" * size

    upload = FakeUpload()
    with pytest.raises(main.HTTPException) as exc_info:
        asyncio.run(main._read_upload_limited(upload, "jd_file"))

    assert exc_info.value.status_code == 413
    assert upload.requested_sizes == [1024 * 1024 + 1]


def test_request_body_limit_rejects_chunked_body_before_handler_completes():
    messages = [
        {"type": "http.request", "body": b"123", "more_body": True},
        {"type": "http.request", "body": b"456", "more_body": False},
    ]
    sent: list[dict] = []
    handler_completed = False

    async def receive():
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    async def inner(scope, receive_inner, send_inner):
        nonlocal handler_completed
        await receive_inner()
        await receive_inner()
        handler_completed = True

    middleware = main.RequestBodyLimitMiddleware(inner, limit_bytes=5)
    asyncio.run(
        middleware(
            {"type": "http", "headers": []},
            receive,
            send,
        )
    )

    assert handler_completed is False
    assert next(message for message in sent if message["type"] == "http.response.start")[
        "status"
    ] == 413


def test_mcp_status_does_not_expose_endpoint_or_exception_details(monkeypatch):
    sentinel = "http://127.0.0.1:9999/mcp?token=mcp-secret-sentinel"
    monkeypatch.setenv("NCS_MCP_URL", sentinel)
    monkeypatch.setattr(
        ncs_mcp_client,
        "_tool_names",
        lambda **_kwargs: (_ for _ in ()).throw(ncs_mcp_client.NcsMcpError(sentinel)),
    )

    status = ncs_mcp_client.ncs_mcp_status(force_refresh=True)

    assert status["lastError"] == "ncs_mcp_unreachable"
    assert "mcp-secret-sentinel" not in json.dumps(status)


def test_mcp_tool_cache_is_scoped_to_the_configured_endpoint(monkeypatch):
    current = {"endpoint": "http://mcp-a.example/mcp"}
    calls: list[str] = []

    monkeypatch.setattr(ncs_mcp_client, "_tools_cache", None)
    monkeypatch.setattr(ncs_mcp_client, "_endpoint", lambda: current["endpoint"])

    def fake_rpc(method, params=None):
        _ = params
        calls.append(current["endpoint"])
        tool_name = "tool-a" if "mcp-a" in current["endpoint"] else "tool-b"
        return {"tools": [{"name": tool_name}]}

    monkeypatch.setattr(ncs_mcp_client, "_rpc", fake_rpc)
    assert ncs_mcp_client._tool_names() == {"tool-a"}
    assert ncs_mcp_client._tool_names() == {"tool-a"}
    current["endpoint"] = "http://mcp-b.example/mcp"
    assert ncs_mcp_client._tool_names() == {"tool-b"}
    assert calls == ["http://mcp-a.example/mcp", "http://mcp-b.example/mcp"]


def test_mcp_request_session_initializes_once_and_reuses_transport(monkeypatch):
    monkeypatch.setenv("NCS_MCP_URL", "https://ncs.example/api/mcp")
    calls: list[dict] = []
    clients: list[object] = []

    class FakeResponse:
        def __init__(self, result, *, session_id=""):
            self.text = json.dumps({"jsonrpc": "2.0", "id": 1, "result": result})
            self.headers = {"mcp-session-id": session_id} if session_id else {}

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.closed = False
            clients.append(self)

        def post(self, endpoint, *, headers, json, timeout):
            calls.append(
                {
                    "endpoint": endpoint,
                    "headers": dict(headers),
                    "payload": dict(json),
                    "timeout": timeout,
                }
            )
            if json["method"] == "initialize":
                return FakeResponse(
                    {"protocolVersion": ncs_mcp_client.MCP_PROTOCOL_VERSION},
                    session_id="session-123",
                )
            return FakeResponse({"ok": True})

        def close(self):
            self.closed = True

    monkeypatch.setattr(ncs_mcp_client.httpx, "Client", FakeClient)

    with ncs_mcp_client.use_ncs_mcp_request_session():
        assert ncs_mcp_client._rpc("tools/list") == {"ok": True}
        assert ncs_mcp_client._rpc("tools/call", {"name": "ncs_search"}) == {"ok": True}

    assert len(clients) == 1
    assert clients[0].closed is True
    assert clients[0].kwargs == {
        "follow_redirects": False,
        "trust_env": False,
    }
    assert [call["payload"]["method"] for call in calls] == [
        "initialize",
        "tools/list",
        "tools/call",
    ]
    assert [call["payload"]["id"] for call in calls] == [1, 2, 3]
    assert "Mcp-Session-Id" not in calls[0]["headers"]
    assert calls[1]["headers"]["Mcp-Session-Id"] == "session-123"
    assert calls[2]["headers"]["Mcp-Session-Id"] == "session-123"


def test_mcp_request_session_reinitializes_after_transport_failure(monkeypatch):
    monkeypatch.setenv("NCS_MCP_URL", "https://ncs.example/api/mcp")
    calls: list[tuple[int, str]] = []
    clients: list[object] = []

    class FakeResponse:
        def __init__(self, result, *, session_id=""):
            self.text = json.dumps({"jsonrpc": "2.0", "id": 1, "result": result})
            self.headers = {"mcp-session-id": session_id} if session_id else {}

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, **_kwargs):
            self.index = len(clients)
            self.closed = False
            clients.append(self)

        def post(self, _endpoint, *, headers, json, timeout):
            _ = headers, timeout
            calls.append((self.index, json["method"]))
            if json["method"] == "initialize":
                return FakeResponse({}, session_id=f"session-{self.index}")
            if self.index == 0:
                raise ncs_mcp_client.httpx.ConnectError("dropped connection")
            return FakeResponse({"ok": True})

        def close(self):
            self.closed = True

    monkeypatch.setattr(ncs_mcp_client.httpx, "Client", FakeClient)

    with ncs_mcp_client.use_ncs_mcp_request_session():
        with pytest.raises(ncs_mcp_client.NcsMcpError, match="request failed"):
            ncs_mcp_client._rpc("tools/list")
        assert ncs_mcp_client._rpc("tools/list") == {"ok": True}

    assert calls == [
        (0, "initialize"),
        (0, "tools/list"),
        (1, "initialize"),
        (1, "tools/list"),
    ]
    assert len(clients) == 2
    assert all(client.closed for client in clients)


def test_parallel_ksa_calls_share_one_initialized_mcp_transport(monkeypatch):
    monkeypatch.setenv("NCS_MCP_URL", "https://ncs.example/api/mcp")
    monkeypatch.setenv("NCS_MCP_KSA_CONCURRENCY", "4")
    calls: list[dict] = []
    clients: list[object] = []
    calls_lock = ncs_mcp_client.threading.Lock()

    class FakeResponse:
        def __init__(self, result, *, session_id=""):
            self.text = json.dumps({"jsonrpc": "2.0", "id": 1, "result": result})
            self.headers = {"mcp-session-id": session_id} if session_id else {}

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, **_kwargs):
            self.closed = False
            clients.append(self)

        def post(self, _endpoint, *, headers, json, timeout):
            _ = timeout
            with calls_lock:
                calls.append({"headers": dict(headers), "payload": dict(json)})
            if json["method"] == "initialize":
                return FakeResponse({}, session_id="parallel-session")
            unit_code = json["params"]["arguments"]["unit_code"]
            return FakeResponse(
                {
                    "unit": {
                        "unit_code": unit_code,
                        "unit_name": unit_code,
                        "classification": {},
                    },
                    "elements": [
                        {
                            "element_id": f"element-{unit_code}",
                            "element_name": "element",
                            "ksa": [
                                {
                                    "text": f"factor-{unit_code}",
                                    "ksa_type": "knowledge",
                                }
                            ],
                        }
                    ],
                }
            )

        def close(self):
            self.closed = True

    monkeypatch.setattr(ncs_mcp_client.httpx, "Client", FakeClient)
    units = [
        {"ncsClCd": f"unit-{index}", "compeUnitName": f"Unit {index}"}
        for index in range(4)
    ]

    with ncs_mcp_client.use_ncs_mcp_request_session():
        rows = ncs_mcp_client.get_ksa_by_units(units, max_factors_per_unit=1)

    assert len(rows) == 4
    assert len(clients) == 1
    assert clients[0].closed is True
    assert [call["payload"]["method"] for call in calls].count("initialize") == 1
    detail_calls = [
        call for call in calls if call["payload"]["method"] == "tools/call"
    ]
    assert len(detail_calls) == 4
    assert all(
        call["headers"].get("Mcp-Session-Id") == "parallel-session"
        for call in detail_calls
    )
    request_ids = [call["payload"]["id"] for call in calls]
    assert sorted(request_ids) == list(range(1, 6))
    assert len(request_ids) == len(set(request_ids))


def test_mcp_status_bypasses_discovery_cache_to_detect_outage(monkeypatch):
    endpoint = "http://mcp.example/mcp"
    monkeypatch.setattr(ncs_mcp_client, "_endpoint", lambda: endpoint)
    monkeypatch.setattr(
        ncs_mcp_client,
        "_tools_cache",
        (ncs_mcp_client.time.monotonic(), endpoint, {"ncs_search", "ncs_unit_detail"}),
    )
    monkeypatch.setattr(
        ncs_mcp_client,
        "_rpc",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ncs_mcp_client.NcsMcpError("offline")),
    )

    status = ncs_mcp_client.ncs_mcp_status(force_refresh=True)

    assert status["configured"] is True
    assert status["reachable"] is False
    assert status["ksaAvailable"] is False


def test_mcp_status_reuses_short_probe_cache_and_scopes_it_to_endpoint(monkeypatch):
    current = {"endpoint": "http://mcp-a.example/mcp"}
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(ncs_mcp_client, "_status_cache", None)
    monkeypatch.setattr(ncs_mcp_client, "_endpoint", lambda: current["endpoint"])

    def fake_tool_names(*, force_refresh=False):
        calls.append((current["endpoint"], force_refresh))
        return {"ncs_search", "ncs_unit_detail"}

    monkeypatch.setattr(ncs_mcp_client, "_tool_names", fake_tool_names)

    first = ncs_mcp_client.ncs_mcp_status()
    second = ncs_mcp_client.ncs_mcp_status()
    current["endpoint"] = "http://mcp-b.example/mcp"
    third = ncs_mcp_client.ncs_mcp_status()

    assert first == second == third
    assert calls == [
        ("http://mcp-a.example/mcp", True),
        ("http://mcp-b.example/mcp", True),
    ]


def test_mcp_status_applies_a_bounded_cold_probe_budget(monkeypatch):
    monkeypatch.setattr(ncs_mcp_client, "_status_cache", None)
    monkeypatch.setattr(
        ncs_mcp_client, "_endpoint", lambda: "http://mcp.example/mcp"
    )
    seen_remaining: list[float | None] = []

    def fake_tool_names(*, force_refresh=False):
        assert force_refresh is True
        seen_remaining.append(ncs_mcp_client.remaining_request_budget_sec())
        return {"ncs_search", "ncs_unit_detail"}

    monkeypatch.setattr(ncs_mcp_client, "_tool_names", fake_tool_names)

    status = ncs_mcp_client.ncs_mcp_status(force_refresh=True)

    assert status["ksaAvailable"] is True
    assert len(seen_remaining) == 1
    assert seen_remaining[0] is not None
    assert 0 < seen_remaining[0] <= ncs_mcp_client._STATUS_PROBE_BUDGET_SEC
    assert ncs_mcp_client.remaining_request_budget_sec() is None


def test_mcp_status_returns_stale_value_without_waiting_for_active_probe(monkeypatch):
    endpoint = "http://mcp.example/mcp"
    stale = {
        "configured": True,
        "reachable": True,
        "tools": ["ncs_search", "ncs_unit_detail"],
        "ksaAvailable": True,
        "lastError": None,
    }
    monkeypatch.setattr(ncs_mcp_client, "_endpoint", lambda: endpoint)
    monkeypatch.setattr(
        ncs_mcp_client,
        "_status_cache",
        (ncs_mcp_client.time.monotonic() - 100, endpoint, stale),
    )
    started = threading.Event()
    release = threading.Event()

    def slow_tool_names(*, force_refresh=False):
        assert force_refresh is True
        started.set()
        release.wait(timeout=3)
        return {"ncs_search", "ncs_unit_detail"}

    monkeypatch.setattr(ncs_mcp_client, "_tool_names", slow_tool_names)

    with ThreadPoolExecutor(max_workers=2) as executor:
        probe = executor.submit(ncs_mcp_client.ncs_mcp_status)
        assert started.wait(timeout=1)
        before = time.monotonic()
        concurrent = ncs_mcp_client.ncs_mcp_status()
        elapsed = time.monotonic() - before
        release.set()
        refreshed = probe.result(timeout=2)

    assert elapsed < 0.1
    assert concurrent["reachable"] is True
    assert concurrent["stale"] is True
    assert concurrent["probeInProgress"] is True
    assert concurrent["cacheAgeSeconds"] >= 100
    assert refreshed["reachable"] is True


def test_concurrent_force_refresh_shares_inflight_probe_without_waiting(monkeypatch):
    endpoint = "http://mcp.example/mcp"
    monkeypatch.setattr(ncs_mcp_client, "_endpoint", lambda: endpoint)
    monkeypatch.setattr(ncs_mcp_client, "_status_cache", None)
    started = threading.Event()
    release = threading.Event()

    def slow_tool_names(*, force_refresh=False):
        assert force_refresh is True
        started.set()
        release.wait(timeout=3)
        return {"ncs_search", "ncs_unit_detail"}

    monkeypatch.setattr(ncs_mcp_client, "_tool_names", slow_tool_names)

    with ThreadPoolExecutor(max_workers=2) as executor:
        probe = executor.submit(ncs_mcp_client.ncs_mcp_status, force_refresh=True)
        assert started.wait(timeout=1)
        before = time.monotonic()
        concurrent = ncs_mcp_client.ncs_mcp_status(force_refresh=True)
        elapsed = time.monotonic() - before
        release.set()
        refreshed = probe.result(timeout=2)

    assert elapsed < 0.1
    assert concurrent["reachable"] is False
    assert concurrent["probeInProgress"] is True
    assert refreshed["reachable"] is True


def test_openai_http_error_does_not_expose_remote_body(monkeypatch):
    sentinel = "sk-secret-sentinel"
    monkeypatch.setattr(
        openai_http,
        "_run_curl_json",
        lambda **_kwargs: (401, f"reflected Authorization: Bearer {sentinel}"),
    )

    with pytest.raises(RuntimeError) as exc_info:
        openai_http._chat_with_curl(
            url="https://api.openai.com/v1/chat/completions",
            payload={},
            api_key=sentinel,
            timeout_sec=1,
        )

    error_message = str(exc_info.value)
    assert "openai_http_401" in error_message
    assert sentinel not in error_message


def test_strategy_model_error_does_not_expose_internal_exception(monkeypatch):
    sentinel = r"C:\secret DB_PASSWORD=sentinel"
    monkeypatch.setattr(
        jd_strategy,
        "_check_openai_connectivity",
        lambda **kwargs: (True, ""),
    )
    monkeypatch.setattr(
        jd_strategy,
        "post_chat_completions_with_retries",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError(sentinel)),
    )

    result = jd_strategy.build_strategy_with_openai(
        jd_text="직무기술서",
        notice_text="채용공고",
        strengths="",
        region="",
        ncs_matches=[
            {
                "ncsClCd": "0202030201_25v3",
                "compeUnitName": "문서작성",
            }
        ],
        ncs_ksa=[
            {
                "ncsClCd": "0202030201_25v3",
                "factorName": "문서 요구사항 파악",
            }
        ],
        api_key_override="sk-test",
        target_count_override=1,
    )

    serialized = json.dumps(result, ensure_ascii=False)
    assert result["error"] == "model_generation_failed: retry_request_failed"
    assert sentinel not in serialized


def test_parse_review_rejects_large_upload_before_kordoc(monkeypatch, mocker):
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    parse = mocker.patch("app.main.parse_with_kordoc")

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/parse-review",
            files={"jd_file": ("large.pdf", b"x" * (1024 * 1024 + 1), "application/pdf")},
        )

    assert resp.status_code == 413
    parse.assert_not_called()


def test_extract_sclass_rejects_large_upload_before_parser(monkeypatch, mocker):
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    parser = mocker.patch("app.main.extract_sclass_from_pdf_bytes")

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/extract-sclass",
            files={"jd_file": ("large.pdf", b"x" * (1024 * 1024 + 1), "application/pdf")},
        )

    assert resp.status_code == 413
    parser.assert_not_called()


def test_extract_sclass_does_not_expose_internal_exception(monkeypatch):
    sentinel = r"C:\internal\db.sqlite DB_PASSWORD=sentinel"
    monkeypatch.setattr(
        main,
        "extract_sclass_from_pdf_bytes",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(sentinel)),
    )

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/extract-sclass",
            files={"jd_file": ("jd.pdf", b"%PDF-test", "application/pdf")},
        )

    assert resp.status_code == 500
    assert resp.json()["detail"]["code"] == "jd_sclass_extract_failed"
    assert sentinel not in resp.text


def test_personalized_questions_reject_sensitive_query_text():
    with TestClient(main.app) as client:
        resp = client.post(
            "/api/questions/generate-personalized?ncs_code=02020302&job_posting=resume-text",
        )

    assert resp.status_code == 400
    assert "JSON body" in resp.text


def test_question_endpoints_reject_openai_key_query_param():
    with TestClient(main.app) as client:
        resp = client.post(
            "/api/questions/generate-by-ncs-code?openai_api_key=sk-query-key&ncs_code=02020302",
            json={"openai_api_key": "sk-body-key", "ncs_code": "02020302"},
        )

    assert resp.status_code == 400
    assert "openai_api_key" in resp.text
    assert "JSON body" in resp.text


def test_question_endpoints_reject_avoid_questions_query_param():
    with TestClient(main.app) as client:
        resp = client.post(
            "/api/questions/generate-by-ncs-code?avoid_questions_json=%5B%22prior-question%22%5D",
            json={"openai_api_key": REQUEST_OPENAI_KEY, "ncs_code": "02020302"},
        )

    assert resp.status_code == 400
    assert "avoid_questions_json" in resp.text
    assert "JSON body" in resp.text


def test_generate_by_ncs_code_requires_mcp_url(monkeypatch):
    monkeypatch.delenv("NCS_MCP_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/questions/generate-by-ncs-code",
            json={
                "openai_api_key": REQUEST_OPENAI_KEY,
                "ncs_code": "0202030201_25v3",
            },
        )

    assert resp.status_code == 503
    assert "NCS_MCP_URL" in resp.text


def test_generate_by_ncs_code_stops_without_official_ksa(monkeypatch):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        main,
        "generate_interview_questions_by_ncs_code",
        lambda **kwargs: {
            "generation_mode": "hybrid_ai_with_template_fallback",
            "ncs_ksa_available": False,
            "main_questions": [],
            "follow_up_questions": [],
        },
    )

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/questions/generate-by-ncs-code",
            json={
                "openai_api_key": REQUEST_OPENAI_KEY,
                "ncs_code": "0202030201_25v3",
            },
        )

    assert resp.status_code == 502
    assert resp.json()["detail"]["code"] == "openai_api_quality_rejected"
    assert resp.json()["detail"]["provider"] == "openai_api"
    assert resp.json()["detail"]["retryable"] is True


def test_generate_by_ncs_code_rejects_unverified_question_grounding(monkeypatch):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        main,
        "generate_interview_questions_by_ncs_code",
        lambda **kwargs: {
            "generation_mode": "ai_autonomous_ncs_code_only",
            "ncs_ksa_available": True,
            "main_questions": [
                {
                    "question": "가짜요소를 설명해 주세요.",
                    "type": "면접질문",
                    "ncsClCd": "0202030201_25v3",
                    "question_focus": "",
                    "question_focus_source": "unverified_model_output",
                    "ksa_refs": [],
                }
            ],
            "follow_up_questions": [],
        },
    )

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/questions/generate-by-ncs-code",
            json={
                "openai_api_key": REQUEST_OPENAI_KEY,
                "ncs_code": "0202030201_25v3",
            },
        )

    assert resp.status_code == 502
    assert resp.json()["detail"]["code"] == "openai_api_quality_rejected"


def test_generate_by_ncs_code_rejects_invalid_supported_method_shape(monkeypatch):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        main,
        "generate_interview_questions_by_ncs_code",
        lambda **kwargs: {
            "generation_mode": "ai_autonomous_ncs_code_only",
            "ncs_ksa_available": True,
            "main_questions": [
                {
                    "question": "공식요소를 간단히 말해 주세요.",
                    "type": "발표면접",
                    "ncsClCd": "0202030201_25v3",
                    "question_focus": "공식요소",
                    "question_focus_source": "official_ksa",
                    "ksa_refs": ["공식요소"],
                }
            ],
            "follow_up_questions": [],
        },
    )

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/questions/generate-by-ncs-code",
            json={
                "openai_api_key": REQUEST_OPENAI_KEY,
                "ncs_code": "0202030201_25v3",
            },
        )

    assert resp.status_code == 502
    assert resp.json()["detail"]["code"] == "openai_api_quality_rejected"


def test_generate_by_ncs_code_rejects_string_boolean(monkeypatch):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/questions/generate-by-ncs-code",
            json={
                "openai_api_key": REQUEST_OPENAI_KEY,
                "ncs_code": "0202030201_25v3",
                "include_followups": "false",
            },
        )

    assert resp.status_code == 422
    assert "include_followups must be a boolean" in resp.text


def test_strategy_upload_rejects_openai_key_query_param():
    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/strategy/upload?openai_api_key=sk-query-key",
            files={"jd_file": ("jd.txt", b"job description", "text/plain")},
            data={"openai_api_key": "sk-body-key"},
        )

    assert resp.status_code == 400
    assert "openai_api_key" in resp.text
    assert "form data" in resp.text
