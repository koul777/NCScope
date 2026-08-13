from __future__ import annotations

import os
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from app.settings import settings


def _public_inst_key() -> str:
    key = settings.public_inst_key()
    if not key:
        raise RuntimeError("PUBLIC_INST_SERVICE_KEY or DATA_GO_KR_SERVICE_KEY is not set")
    return key


def _ncs_key() -> str:
    key = settings.ncs_key()
    if not key:
        raise RuntimeError("NCS_SERVICE_KEY or DATA_GO_KR_SERVICE_KEY is not set")
    return key


def fetch_public_inst(
    resource: str,
    page_no: int = 1,
    num_of_rows: int = 20,
    data_type: str = "json",
    extra_params: dict[str, Any] | None = None,
    timeout_sec: float = 20.0,
) -> dict[str, Any]:
    if resource not in {"list", "brnch"}:
        raise ValueError("resource must be one of: list, brnch")
    url = urljoin(settings.public_inst_base_url, resource)
    params = {
        "serviceKey": _public_inst_key(),
        "pageNo": page_no,
        "numOfRows": num_of_rows,
        "type": data_type.lower(),
    }
    if extra_params:
        params.update(extra_params)
    with httpx.Client(timeout=timeout_sec) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "")
        if "json" in ctype.lower() or data_type.lower() == "json":
            data = resp.json()
            rc = str(data.get("resultCode", ""))
            if rc and rc not in {"200", "00"}:
                raise RuntimeError(f"public_inst error resultCode={rc}, resultMsg={data.get('resultMsg')}")
            return {"format": "json", "data": data}
        return {"format": "xml", "data": resp.text}


def fetch_ncs(path: str, query: dict[str, Any], timeout_sec: float = 20.0) -> dict[str, Any]:
    raw_path = str(path or "").strip().replace("\\", "/")
    parsed_path = urlsplit(raw_path)
    if (
        not raw_path
        or parsed_path.scheme
        or parsed_path.netloc
        or parsed_path.query
        or parsed_path.fragment
        or any(ord(char) < 0x20 for char in raw_path)
    ):
        raise ValueError("path must be a relative NCS API path")

    normalized = raw_path.lstrip("/")
    base = urlsplit(str(settings.ncs_base_url or "").strip())
    if base.scheme not in {"http", "https"} or not base.netloc:
        raise RuntimeError("NCS_BASE_URL is invalid")
    url = urljoin(str(settings.ncs_base_url).rstrip("/") + "/", normalized)
    resolved = urlsplit(url)
    base_path = base.path.rstrip("/") + "/"
    if (
        resolved.scheme != base.scheme
        or resolved.netloc != base.netloc
        or not resolved.path.startswith(base_path)
    ):
        raise ValueError("path must stay within the configured NCS API base URL")
    params = dict(query)
    params.setdefault("serviceKey", _ncs_key())
    with httpx.Client(timeout=timeout_sec) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        return {
            "status_code": resp.status_code,
            "content_type": resp.headers.get("content-type", ""),
            "data": resp.text,
        }


def fetch_ncs_highschool_course(
    mcd_nm: str,
    targ_yy: str,
    cd_name: str | None = None,
    return_type: str = "xml",
    timeout_sec: float = 20.0,
) -> dict[str, Any]:
    """Call NCS high-school curriculum API (openapi14.do)."""
    mcd_nm = str(mcd_nm or "").strip()
    targ_yy = str(targ_yy or "").strip()
    if not mcd_nm:
        raise ValueError("mcd_nm is required")
    if targ_yy not in {"2015", "2018"}:
        raise ValueError("targ_yy must be one of: 2015, 2018")

    key = _ncs_key()
    key_variants = [key]
    # Some endpoints accept encoded key while others require decoded key.
    enc_key = httpx.QueryParams({"k": key}).get("k", "")
    if enc_key and enc_key != key:
        key_variants.append(enc_key)

    base_candidates: list[str] = []
    env_bases = os.getenv("NCS_BASE_URLS", "").strip()
    if env_bases:
        base_candidates.extend([x.strip() for x in env_bases.split(",") if x.strip()])
    base_candidates.extend(
        [
            settings.ncs_base_url,
            "https://www.ncs.go.kr/api/",
            "http://www.ncs.go.kr/api/",
        ]
    )
    # Deduplicate while preserving order.
    seen = set()
    uniq_bases: list[str] = []
    for b in base_candidates:
        bb = b.strip()
        if not bb or bb in seen:
            continue
        seen.add(bb)
        uniq_bases.append(bb)

    last_err: Exception | None = None
    for base in uniq_bases:
        url = urljoin(base.rstrip("/") + "/", "openapi14.do")
        for key_name in ("ServiceKey", "serviceKey"):
            for kval in key_variants:
                params: dict[str, Any] = {
                    key_name: kval,
                    "returnType": return_type.lower(),
                    "mcdNm": mcd_nm,
                    "targYy": targ_yy,
                }
                if cd_name and str(cd_name).strip():
                    params["cdName"] = str(cd_name).strip()
                try:
                    with httpx.Client(timeout=timeout_sec) as client:
                        resp = client.get(url, params=params)
                    if resp.status_code == 404:
                        continue
                    resp.raise_for_status()
                    return {
                        "status_code": resp.status_code,
                        "content_type": resp.headers.get("content-type", ""),
                        "data": resp.text,
                    }
                except Exception as e:
                    last_err = e
                    continue
    if last_err:
        raise last_err
    raise RuntimeError("failed to call NCS highschool API")


def fetch_recruitment(
    resource: str,
    page_no: int = 1,
    num_of_rows: int = 20,
    data_type: str = "json",
    timeout_sec: float = 20.0,
) -> dict[str, Any]:
    url = urljoin(settings.recruitment_base_url, resource.strip().lstrip("/"))
    params = {
        "serviceKey": _public_inst_key(),
        "pageNo": page_no,
        "numOfRows": num_of_rows,
        "type": data_type.lower(),
    }
    with httpx.Client(timeout=timeout_sec) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "")
        if "json" in ctype.lower() or data_type.lower() == "json":
            data = resp.json()
            rc = str(data.get("resultCode", ""))
            if rc and rc not in {"200", "00"}:
                raise RuntimeError(f"recruitment error resultCode={rc}, resultMsg={data.get('resultMsg')}")
            return {"format": "json", "data": data}
        return {"format": "xml", "data": resp.text}
