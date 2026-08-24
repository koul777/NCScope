from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import ipaddress
import json
import math
import mimetypes
import os
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, NamedTuple, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx


CONTRACT_VERSION = "ncscope-stored-jd-local-vercel-parity/v1"
DEFAULT_LOCAL_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_REMOTE_BASE_URL = "https://ncscope.vercel.app"
DEFAULT_NCS_MCP_URL = "https://ncscope-ncs-mcp.vercel.app/api/mcp"
DEFAULT_DIGEST_KEY_ENV = "NCSCOPE_PARITY_DIGEST_KEY"
DEFAULT_INPUT_DIR = "tmp/alio_jd_200_mcp"
DEFAULT_OUTPUT_DIR = "tmp/stored_jd_local_vercel_parity"
DEFAULT_DETAIL_CATALOG = "app/data/ncs_detail_catalog.json"
SUPPORTED_SUFFIXES = {".pdf", ".hwp", ".hwpx", ".docx", ".txt", ".zip"}

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_CONFIGURATION = 2
MAX_RATE_LIMIT_RETRIES = 3
MAX_RETRY_AFTER_SECONDS = 60.0

CSV_FIELDS = [
    "case_id",
    "occurrence",
    "suffix",
    "local_status",
    "remote_status",
    "parser_match",
    "structure_match",
    "ncs_match",
    "overall_match",
    "mismatch_types",
    "local_parser_digest",
    "remote_parser_digest",
    "local_structure_digest",
    "remote_structure_digest",
    "local_ncs_digest",
    "remote_ncs_digest",
    "local_detail_count",
    "remote_detail_count",
    "ncs_query_count",
]

NCS_ITEM_KEYS = (
    "ncsClCd",
    "compeUnitName",
    "compeUnitLevel",
    "compeUnitDef",
    "ncsLclasCdnm",
    "ncsMclasCdnm",
    "ncsSclasCdnm",
    "ncsSubdCdnm",
    "canonicalDetailName",
    "resolvedDetailName",
    "source",
    "matchScore",
    "isExactDetailMatch",
    "isExactUnitNameMatch",
)


class ConfigurationError(ValueError):
    pass


class ParityClient(Protocol):
    label: str

    def parse_document(self, data: bytes, upload_filename: str) -> "JsonResult": ...

    def ncs_units(self, public_detail_name: str, limit: int) -> "JsonResult": ...


class JsonResult(NamedTuple):
    status: str
    status_code: int
    payload: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok" and isinstance(self.payload, dict)


class EndpointContract(NamedTuple):
    parser_digest: str
    structure_digest: str
    parser_components: dict[str, str]
    structure_components: dict[str, str]
    detail_candidates: tuple[str, ...]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def normalize_text(value: str) -> str:
    return unicodedata.normalize("NFC", str(value)).replace("\r\n", "\n").replace("\r", "\n")


def canonical_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite numbers are not valid parity contract values")
        return value
    if isinstance(value, str):
        return normalize_text(value)
    if isinstance(value, (list, tuple)):
        return [canonical_value(item) for item in value]
    if isinstance(value, dict):
        return {
            normalize_text(str(key)): canonical_value(item)
            for key, item in sorted(value.items(), key=lambda pair: normalize_text(str(pair[0])))
        }
    return normalize_text(str(value))


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        canonical_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


class ContractDigester:
    def __init__(self, key: bytes) -> None:
        if len(key) < 32:
            raise ConfigurationError("parity digest key must be at least 32 bytes")
        self._key = bytes(key)

    def bytes(self, domain: str, value: bytes) -> str:
        domain_bytes = normalize_text(domain).encode("utf-8")
        return hmac.new(
            self._key,
            domain_bytes + b"\x00" + value,
            hashlib.sha256,
        ).hexdigest()

    def json(self, domain: str, value: Any) -> str:
        return self.bytes(domain, canonical_json_bytes(value))


def normalized_label_key(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[^0-9a-z\u3131-\u318e\uac00-\ud7a3]+", "", text)


def load_public_detail_index(path: Path) -> dict[str, tuple[str, ...]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("details") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ConfigurationError("NCS detail catalog must contain a details list")
    by_key: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = normalize_text(str(row.get("name") or "").strip())
        key = normalized_label_key(name)
        if name and key:
            by_key[key].add(name)
    if not by_key:
        raise ConfigurationError("NCS detail catalog contains no usable public names")
    return {key: tuple(sorted(names)) for key, names in sorted(by_key.items())}


def public_ncs_queries(
    values: Iterable[str],
    public_detail_index: dict[str, tuple[str, ...]],
) -> tuple[str, ...]:
    """Return catalog-owned names only; private source labels never become queries."""

    output: set[str] = set()
    for value in values:
        names = public_detail_index.get(normalized_label_key(value), ())
        if names:
            output.add(names[0])
    return tuple(sorted(output))


def safe_upload_filename(case_id: str, suffix: str) -> str:
    normalized_suffix = str(suffix or "").lower()
    if normalized_suffix not in SUPPORTED_SUFFIXES:
        raise ConfigurationError(f"unsupported document suffix: {normalized_suffix or '<empty>'}")
    if not re.fullmatch(r"[0-9a-f]{64}", str(case_id or "")):
        raise ConfigurationError("case_id must be a 64-character lowercase hex digest")
    return f"case-{case_id[:24]}{normalized_suffix}"


def normalize_base_url(value: str) -> str:
    split = urlsplit(str(value or "").strip())
    if split.scheme not in {"http", "https"} or not split.hostname:
        raise ConfigurationError("endpoint base URL must use http or https")
    if split.username or split.password or split.query or split.fragment:
        raise ConfigurationError("endpoint base URL must not contain credentials, query, or fragment")
    path = split.path.rstrip("/")
    return urlunsplit((split.scheme, split.netloc, path, "", ""))


def is_loopback_url(value: str) -> bool:
    host = urlsplit(value).hostname or ""
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_remote_upload(base_url: str, allow_remote_document_upload: bool) -> None:
    normalized = normalize_base_url(base_url)
    if is_loopback_url(normalized):
        return
    if urlsplit(normalized).scheme != "https":
        raise ConfigurationError("non-loopback document upload requires HTTPS")
    if not allow_remote_document_upload:
        raise ConfigurationError(
            "remote parsing uploads document bytes and creates review/audit state; "
            "pass --allow-remote-document-upload only after data-processing approval"
        )


class EndpointClient:
    def __init__(self, label: str, base_url: str, timeout_seconds: float) -> None:
        self.label = str(label)
        self.base_url = normalize_base_url(base_url)
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": f"ncscope-parity/{CONTRACT_VERSION.rsplit('/', 1)[-1]}"},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "EndpointClient":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    @staticmethod
    def _decode(response: httpx.Response) -> JsonResult:
        if response.status_code < 200 or response.status_code >= 300:
            return JsonResult(f"http_{response.status_code}", response.status_code)
        try:
            payload = response.json()
        except (ValueError, UnicodeError):
            return JsonResult("invalid_json", response.status_code)
        if not isinstance(payload, dict):
            return JsonResult("invalid_envelope", response.status_code)
        return JsonResult("ok", response.status_code, payload)

    def _request(self, method: str, path: str, **kwargs: Any) -> JsonResult:
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            try:
                response = self._client.request(method, path, **kwargs)
            except httpx.TimeoutException:
                return JsonResult("timeout", 0)
            except httpx.TransportError:
                return JsonResult("transport_error", 0)
            if response.is_redirect:
                return JsonResult("redirect_rejected", response.status_code)
            if response.status_code != 429 or attempt >= MAX_RATE_LIMIT_RETRIES:
                return self._decode(response)
            try:
                retry_after = float(response.headers.get("Retry-After", "1"))
            except (TypeError, ValueError):
                retry_after = 1.0
            time.sleep(max(0.0, min(MAX_RETRY_AFTER_SECONDS, retry_after)))
        return JsonResult("http_429", 429)

    def parse_document(self, data: bytes, upload_filename: str) -> JsonResult:
        content_type = mimetypes.guess_type(upload_filename)[0] or "application/octet-stream"
        return self._request(
            "POST",
            "/api/jd/parse-review",
            files={"jd_file": (upload_filename, data, content_type)},
        )

    def ncs_units(self, public_detail_name: str, limit: int) -> JsonResult:
        return self._request(
            "GET",
            "/api/ncs/units/options",
            params={"q": public_detail_name, "limit": int(limit)},
        )

    def preflight(self) -> dict[str, Any]:
        health = self._request("GET", "/health")
        openapi = self._request("GET", "/openapi.json")
        health_payload = health.payload or {}
        mcp = health_payload.get("ncs_mcp") if isinstance(health_payload.get("ncs_mcp"), dict) else {}
        paths = (openapi.payload or {}).get("paths")
        paths = paths if isinstance(paths, dict) else {}
        checks = {
            "health_http_ok": health.ok,
            "health_status_ok": health_payload.get("status") == "ok",
            "ncs_mcp_reachable": mcp.get("reachable") is True,
            "ncs_mcp_ksa_available": mcp.get("ksaAvailable") is True,
            "parse_review_contract_present": "/api/jd/parse-review" in paths,
            "ncs_units_contract_present": "/api/ncs/units/options" in paths,
        }
        return {
            "label": self.label,
            "base_url": self.base_url,
            "checks": checks,
            "passed": all(checks.values()),
            "health_status": health.status,
            "openapi_status": openapi.status,
            "api_version": str((openapi.payload or {}).get("info", {}).get("version") or "")
            if isinstance((openapi.payload or {}).get("info"), dict)
            else "",
        }


def _string_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(normalize_text(str(item).strip()) for item in value if str(item or "").strip())


def endpoint_contract(payload: dict[str, Any], digester: ContractDigester) -> EndpointContract:
    document = payload.get("document") if isinstance(payload.get("document"), dict) else {}
    sections = payload.get("sections") if isinstance(payload.get("sections"), dict) else {}
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    parser_components = {
        "provenance": digester.json(
            "parser/provenance",
            {
                "parser": payload.get("parser"),
                "parser_version": payload.get("parser_version"),
            },
        ),
        "markdown": digester.json("parser/markdown", document.get("markdown")),
        "metadata": digester.json("parser/metadata", document.get("metadata")),
        "outline": digester.json("parser/outline", document.get("outline")),
        "warnings": digester.json("parser/warnings", document.get("warnings")),
        "quality": digester.json(
            "parser/quality",
            {
                "qualitySummary": document.get("qualitySummary"),
                "pageQuality": document.get("pageQuality"),
            },
        ),
    }
    structure_components = {
        "sections": digester.json("structure/sections", sections),
        "fields": digester.json("structure/fields", fields),
        "review_required": digester.json(
            "structure/review-required", payload.get("review_required")
        ),
    }
    parser_contract_payload = {
        "contract_version": CONTRACT_VERSION,
        "components": parser_components,
    }
    structure_contract_payload = {
        "contract_version": CONTRACT_VERSION,
        "components": structure_components,
    }
    return EndpointContract(
        parser_digest=digester.json("parser/contract", parser_contract_payload),
        structure_digest=digester.json("structure/contract", structure_contract_payload),
        parser_components=parser_components,
        structure_components=structure_components,
        detail_candidates=_string_list(fields.get("ncs_detail_candidates")),
    )


def classify_endpoint_mismatches(
    local_result: JsonResult,
    remote_result: JsonResult,
    local_contract: EndpointContract | None,
    remote_contract: EndpointContract | None,
) -> list[str]:
    mismatches: list[str] = []
    if not local_result.ok:
        mismatches.append("local_parse_request_error")
    if not remote_result.ok:
        mismatches.append("remote_parse_request_error")
    if local_result.status != remote_result.status:
        mismatches.append("parse_status_mismatch")
    if not local_contract or not remote_contract:
        return mismatches
    if local_contract.parser_components["provenance"] != remote_contract.parser_components["provenance"]:
        mismatches.append("parser_provenance_mismatch")
    if any(
        local_contract.parser_components[key] != remote_contract.parser_components[key]
        for key in ("markdown", "metadata", "outline", "warnings", "quality")
    ):
        mismatches.append("parser_document_mismatch")
    if local_contract.structure_components["sections"] != remote_contract.structure_components["sections"]:
        mismatches.append("structured_sections_mismatch")
    if local_contract.structure_components["fields"] != remote_contract.structure_components["fields"]:
        mismatches.append("structured_fields_mismatch")
    if (
        local_contract.structure_components["review_required"]
        != remote_contract.structure_components["review_required"]
    ):
        mismatches.append("structured_review_gate_mismatch")
    return mismatches


def ncs_contract_payload(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("items") if isinstance(payload.get("items"), list) else []
    items: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        items.append({key: row.get(key) for key in NCS_ITEM_KEYS if key in row})
    items.sort(key=lambda item: canonical_json_bytes(item))
    return {
        "count": int(payload.get("count") or len(items)),
        "source": normalize_text(str(payload.get("source") or "")),
        "items": items,
    }


def _ncs_result_digest(
    result: JsonResult,
    digester: ContractDigester,
) -> str:
    if not result.ok:
        return digester.json("ncs/error", {"status": result.status, "status_code": result.status_code})
    return digester.json("ncs/result", ncs_contract_payload(result.payload or {}))


def _aggregate_ncs_digest(
    query_ids: Iterable[str],
    query_records: dict[str, dict[str, Any]],
    side: str,
    digester: ContractDigester,
) -> str:
    records = [
        {
            "query_id": query_id,
            "status": query_records[query_id][f"{side}_status"],
            "digest": query_records[query_id][f"{side}_digest"],
        }
        for query_id in sorted(set(query_ids))
    ]
    # The side is represented by the selected record values, not the HMAC
    # domain. Equal local/remote records must produce the same contract digest.
    return digester.json("ncs/aggregate", records)


def _case_id(
    data: bytes,
    occurrence: int,
    digester: ContractDigester,
) -> str:
    content_digest = hashlib.sha256(data).digest()
    return digester.bytes(
        "case/id",
        content_digest + int(occurrence).to_bytes(4, "big", signed=False),
    )


def collect_corpus_files(input_dir: Path) -> list[Path]:
    if not input_dir.is_dir():
        raise ConfigurationError("input directory does not exist")
    files = sorted(
        (path for path in input_dir.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES),
        key=lambda path: path.name,
    )
    if not files:
        raise ConfigurationError("input directory contains no supported documents")
    return files


def verify_corpus(
    files: list[Path],
    *,
    local_client: ParityClient,
    remote_client: ParityClient,
    digester: ContractDigester,
    public_detail_index: dict[str, tuple[str, ...]],
    expected_files: int,
    expected_unique_contents: int,
    max_file_bytes: int,
    ncs_limit: int,
) -> dict[str, Any]:
    content_counts: Counter[bytes] = Counter()
    content_seen: Counter[bytes] = Counter()
    file_payloads: list[tuple[Path, bytes, bytes]] = []
    for path in files:
        data = path.read_bytes()
        if len(data) > max_file_bytes:
            raise ConfigurationError("a corpus document exceeds --max-file-mb")
        raw_digest = hashlib.sha256(data).digest()
        content_counts[raw_digest] += 1
        file_payloads.append((path, data, raw_digest))

    cases: list[dict[str, Any]] = []
    private_case_state: list[dict[str, Any]] = []
    query_names_by_id: dict[str, str] = {}
    query_reference_counts: Counter[str] = Counter()
    for file_index, (path, data, raw_digest) in enumerate(file_payloads, start=1):
        content_seen[raw_digest] += 1
        occurrence = content_seen[raw_digest]
        case_id = _case_id(data, occurrence, digester)
        upload_filename = safe_upload_filename(case_id, path.suffix)
        local_result = local_client.parse_document(data, upload_filename)
        remote_result = remote_client.parse_document(data, upload_filename)
        local_contract = endpoint_contract(local_result.payload or {}, digester) if local_result.ok else None
        remote_contract = endpoint_contract(remote_result.payload or {}, digester) if remote_result.ok else None
        mismatches = classify_endpoint_mismatches(
            local_result,
            remote_result,
            local_contract,
            remote_contract,
        )
        local_details = local_contract.detail_candidates if local_contract else ()
        remote_details = remote_contract.detail_candidates if remote_contract else ()
        local_queries = public_ncs_queries(local_details, public_detail_index)
        remote_queries = public_ncs_queries(remote_details, public_detail_index)
        if set(local_queries) != set(remote_queries):
            mismatches.append("ncs_query_set_mismatch")
        union_queries = tuple(sorted(set(local_queries) | set(remote_queries)))
        query_ids: list[str] = []
        for query_name in union_queries:
            query_id = digester.json("ncs/query-id", query_name)
            query_ids.append(query_id)
            query_names_by_id.setdefault(query_id, query_name)
            query_reference_counts[query_id] += 1

        cases.append(
            {
                "case_id": case_id,
                "occurrence": occurrence,
                "suffix": path.suffix.lower(),
                "local_status": local_result.status,
                "remote_status": remote_result.status,
                "parser_match": bool(
                    local_contract
                    and remote_contract
                    and local_contract.parser_digest == remote_contract.parser_digest
                ),
                "structure_match": bool(
                    local_contract
                    and remote_contract
                    and local_contract.structure_digest == remote_contract.structure_digest
                ),
                "ncs_match": False,
                "overall_match": False,
                "mismatch_types": mismatches,
                "local_parser_digest": local_contract.parser_digest if local_contract else "",
                "remote_parser_digest": remote_contract.parser_digest if remote_contract else "",
                "local_structure_digest": local_contract.structure_digest if local_contract else "",
                "remote_structure_digest": remote_contract.structure_digest if remote_contract else "",
                "local_ncs_digest": "",
                "remote_ncs_digest": "",
                "local_detail_count": len(local_details),
                "remote_detail_count": len(remote_details),
                "ncs_query_count": len(query_ids),
            }
        )
        private_case_state.append({"query_ids": query_ids})
        print(
            f"parsed {file_index}/{len(file_payloads)} cases",
            file=sys.stderr,
            flush=True,
        )

    query_records: dict[str, dict[str, Any]] = {}
    sorted_query_ids = sorted(query_names_by_id)
    for query_index, query_id in enumerate(sorted_query_ids, start=1):
        public_name = query_names_by_id[query_id]
        local_result = local_client.ncs_units(public_name, ncs_limit)
        remote_result = remote_client.ncs_units(public_name, ncs_limit)
        local_digest = _ncs_result_digest(local_result, digester)
        remote_digest = _ncs_result_digest(remote_result, digester)
        query_records[query_id] = {
            "query_id": query_id,
            "reference_count": query_reference_counts[query_id],
            "local_status": local_result.status,
            "remote_status": remote_result.status,
            "local_digest": local_digest,
            "remote_digest": remote_digest,
            "match": local_result.ok and remote_result.ok and local_digest == remote_digest,
        }
        print(
            f"checked {query_index}/{len(sorted_query_ids)} public NCS queries",
            file=sys.stderr,
            flush=True,
        )

    for case, private_state in zip(cases, private_case_state):
        query_ids = private_state["query_ids"]
        local_ncs_digest = _aggregate_ncs_digest(query_ids, query_records, "local", digester)
        remote_ncs_digest = _aggregate_ncs_digest(query_ids, query_records, "remote", digester)
        ncs_ok = all(query_records[query_id]["match"] for query_id in query_ids)
        case["local_ncs_digest"] = local_ncs_digest
        case["remote_ncs_digest"] = remote_ncs_digest
        case["ncs_match"] = ncs_ok and local_ncs_digest == remote_ncs_digest
        if not case["ncs_match"]:
            case["mismatch_types"].append("ncs_result_mismatch")
        case["mismatch_types"] = sorted(set(case["mismatch_types"]))
        case["overall_match"] = (
            case["parser_match"]
            and case["structure_match"]
            and case["ncs_match"]
            and not case["mismatch_types"]
        )

    mismatch_counts = Counter(
        mismatch for case in cases for mismatch in case["mismatch_types"]
    )
    corpus_failures: list[str] = []
    unique_contents = len(content_counts)
    if len(files) != expected_files:
        corpus_failures.append("unexpected_corpus_size")
    if unique_contents != expected_unique_contents:
        corpus_failures.append("unexpected_unique_content_count")
    passed = not corpus_failures and all(case["overall_match"] for case in cases)
    summary = {
        "passed": passed,
        "expected_files": expected_files,
        "files": len(files),
        "expected_unique_contents": expected_unique_contents,
        "unique_contents": unique_contents,
        "duplicate_files": len(files) - unique_contents,
        "matched_cases": sum(bool(case["overall_match"]) for case in cases),
        "mismatched_cases": sum(not bool(case["overall_match"]) for case in cases),
        "ncs_public_query_count": len(query_records),
        "corpus_failures": corpus_failures,
        "mismatch_counts": dict(sorted(mismatch_counts.items())),
    }
    return {
        "contract_version": CONTRACT_VERSION,
        "generated_at": utc_now(),
        "privacy": {
            "digest": "HMAC-SHA-256",
            "key_stored_in_report": False,
            "original_filenames_stored": False,
            "document_text_stored": False,
            "extracted_labels_stored": False,
            "remote_upload_filename_pseudonymized": True,
            "ncs_queries_catalog_public_only": True,
        },
        "summary": summary,
        "ncs_queries": [query_records[key] for key in sorted(query_records)],
        "cases": cases,
    }


def write_reports(result: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"stored_jd_local_vercel_parity_{stamp}.json"
    csv_path = output_dir / f"stored_jd_local_vercel_parity_{stamp}.csv"
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for case in result.get("cases") or []:
            row = {field: case.get(field, "") for field in CSV_FIELDS}
            row["mismatch_types"] = ";".join(case.get("mismatch_types") or [])
            writer.writerow(row)
    return json_path, csv_path


def write_preflight_report(result: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"stored_jd_parity_preflight_{stamp}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def preflight_endpoints(
    targets: Iterable[tuple[str, str]],
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for label, base_url in targets:
        with EndpointClient(label, base_url, timeout_seconds) as client:
            output.append(client.preflight())
    return output


def _digest_key_from_env(name: str) -> bytes:
    value = os.getenv(name, "")
    if not value:
        raise ConfigurationError(f"set {name} to a private parity digest key (at least 32 bytes)")
    return value.encode("utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare 206 stored JDs through local and Vercel parse-review/NCS APIs "
            "using privacy-preserving deterministic HMAC contracts."
        )
    )
    parser.add_argument("--input-dir", default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--local-base-url", default=DEFAULT_LOCAL_BASE_URL)
    parser.add_argument("--remote-base-url", default=DEFAULT_REMOTE_BASE_URL)
    parser.add_argument("--ncs-mcp-url", default=DEFAULT_NCS_MCP_URL)
    parser.add_argument("--detail-catalog", default=DEFAULT_DETAIL_CATALOG)
    parser.add_argument("--digest-key-env", default=DEFAULT_DIGEST_KEY_ENV)
    parser.add_argument("--expected-files", type=int, default=206)
    parser.add_argument("--expected-unique-contents", type=int, default=198)
    parser.add_argument("--max-file-mb", type=int, default=4)
    parser.add_argument("--ncs-limit", type=int, default=200)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument(
        "--preflight-target",
        choices=("both", "local", "remote"),
        default="both",
        help="Limit --preflight-only to one endpoint; full verification always checks both.",
    )
    parser.add_argument(
        "--allow-remote-document-upload",
        action="store_true",
        help=(
            "Acknowledge that parse-review sends document bytes to the remote app "
            "and creates ephemeral review/audit state."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir = Path(args.output_dir)
    try:
        local_base_url = normalize_base_url(args.local_base_url)
        remote_base_url = normalize_base_url(args.remote_base_url)
        if args.preflight_only:
            targets = {
                "both": (("local", local_base_url), ("remote", remote_base_url)),
                "local": (("local", local_base_url),),
                "remote": (("remote", remote_base_url),),
            }[args.preflight_target]
            endpoints = preflight_endpoints(targets, args.timeout_seconds)
            report = {
                "contract_version": CONTRACT_VERSION,
                "generated_at": utc_now(),
                "mode": "read_only_preflight",
                "documents_uploaded": 0,
                "configured_ncs_mcp_url": normalize_base_url(args.ncs_mcp_url),
                "endpoints": endpoints,
                "passed": all(item["passed"] for item in endpoints),
            }
            report_path = write_preflight_report(report, output_dir)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            print(f"json={report_path}")
            return EXIT_OK if report["passed"] else EXIT_MISMATCH

        validate_remote_upload(remote_base_url, args.allow_remote_document_upload)
        digester = ContractDigester(_digest_key_from_env(args.digest_key_env))
        files = collect_corpus_files(Path(args.input_dir))
        public_detail_index = load_public_detail_index(Path(args.detail_catalog))
        with EndpointClient("local", local_base_url, args.timeout_seconds) as local_client, EndpointClient(
            "remote", remote_base_url, args.timeout_seconds
        ) as remote_client:
            endpoint_preflight = [local_client.preflight(), remote_client.preflight()]
            if not all(item["passed"] for item in endpoint_preflight):
                preflight_report = {
                    "contract_version": CONTRACT_VERSION,
                    "generated_at": utc_now(),
                    "mode": "full_verification_preflight",
                    "documents_uploaded": 0,
                    "endpoints": endpoint_preflight,
                    "passed": False,
                }
                report_path = write_preflight_report(preflight_report, output_dir)
                print(json.dumps(preflight_report, ensure_ascii=False, indent=2))
                print(f"json={report_path}")
                return EXIT_CONFIGURATION
            result = verify_corpus(
                files,
                local_client=local_client,
                remote_client=remote_client,
                digester=digester,
                public_detail_index=public_detail_index,
                expected_files=max(1, args.expected_files),
                expected_unique_contents=max(1, args.expected_unique_contents),
                max_file_bytes=max(1, args.max_file_mb) * 1024 * 1024,
                ncs_limit=max(1, min(1000, args.ncs_limit)),
            )
        result["endpoints"] = {
            "local": local_base_url,
            "remote": remote_base_url,
            "ncs_mcp": normalize_base_url(args.ncs_mcp_url),
        }
        json_path, csv_path = write_reports(result, output_dir)
        print(json.dumps(result["summary"], ensure_ascii=False, indent=2, sort_keys=True))
        print(f"json={json_path}")
        print(f"csv={csv_path}")
        return EXIT_OK if result["summary"]["passed"] else EXIT_MISMATCH
    except ConfigurationError as exc:
        print(f"configuration_error: {exc}", file=sys.stderr)
        return EXIT_CONFIGURATION


if __name__ == "__main__":
    raise SystemExit(main())
