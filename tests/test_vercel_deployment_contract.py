from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_vercel_fastapi_entrypoint_and_duration_are_production_safe() -> None:
    config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    function = config["functions"]["api/index.py"]
    kordoc_function = config["functions"]["api/kordoc-parse.js"]

    assert function["maxDuration"] == 300
    assert "app/**" in function["includeFiles"]
    for excluded in ("tests/**", "docs/**", "reports/**", ".claude/**", ".github/**"):
        assert excluded in function["excludeFiles"]
    assert kordoc_function["maxDuration"] == 120
    assert config["routes"] == [
        {"src": "/api/kordoc-parse", "dest": "api/kordoc-parse.js"},
        {"src": "/(.*)", "dest": "api/index.py"},
    ]
    bridge_source = (ROOT / "api" / "kordoc-parse.js").read_text(encoding="utf-8")
    assert 'import("kordoc")' in bridge_source
    assert "parserModule?.VERSION" in bridge_source
    assert 'mode: "authenticated_serverless_bridge"' in bridge_source
    assert 'process.env.KORDOC_OFFLINE = "1"' in bridge_source
    assert "application/octet-stream" in bridge_source
    assert "KORDOC_BRIDGE_SECRET" in bridge_source
    assert 'hasStrongSharedSecret = /^[\\x20-\\x7E]{32,}$/' in bridge_source
    assert "normalizeSecret" in bridge_source
    assert "\\uFEFF" in bridge_source
    assert "ED25519_PUBLIC_KEY_RAW" in bridge_source
    assert "validateSignature" in bridge_source
    assert "validateSignatureHeaders" in bridge_source
    assert "x-ncscope-kordoc-body-sha256" in bridge_source
    assert bridge_source.index("const signatureHeaderReview") < bridge_source.index(
        "const bytes = await readBody(req)"
    )
    assert "kordoc_bridge_signed_request_rejected" in bridge_source
    assert "x-ncscope-bridge-rejection" in bridge_source
    assert "SIGNATURE_TTL_SECONDS = 120" in bridge_source
    assert "MAX_UPLOAD_BYTES = 4 * 1024 * 1024" in bridge_source
    assert "MAX_RESPONSE_BYTES = 4 * 1024 * 1024" in bridge_source
    assert (ROOT / "api" / "index.py").read_text(encoding="utf-8").strip().endswith(
        '__all__ = ["app"]'
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_kordoc_bridge_rejects_forged_signature_before_reading_body() -> None:
    bridge_uri = (ROOT / "api" / "kordoc-parse.js").resolve().as_uri()
    script = textwrap.dedent(
        f"""
        import {{ Readable }} from "node:stream";
        const {{ default: handler }} = await import({json.dumps(bridge_uri)});
        let bodyRead = false;
        const req = new Readable({{
          read() {{ bodyRead = true; this.push(Buffer.alloc(4 * 1024 * 1024)); this.push(null); }}
        }});
        req.method = "POST";
        req.headers = {{
          "content-type": "application/octet-stream",
          "x-ncscope-kordoc-timestamp": String(Math.floor(Date.now() / 1000)),
          "x-ncscope-kordoc-signature": "A".repeat(86),
          "x-ncscope-kordoc-body-sha256": "0".repeat(64),
          "x-ncscope-filename-b64": "",
          "x-ncscope-ocr": "0",
        }};
        const res = {{
          statusCode: 0,
          headers: {{}},
          setHeader(name, value) {{ this.headers[name] = value; }},
          end(body) {{ this.body = body; }},
        }};
        await handler(req, res);
        process.stdout.write(JSON.stringify({{ statusCode: res.statusCode, bodyRead }}));
        """
    )
    result = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert json.loads(result.stdout) == {"statusCode": 401, "bodyRead": False}


def test_kordoc_onnx_runtime_is_deduplicated_for_function_size() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    package_lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))

    assert package["overrides"]["onnxruntime-node"] == "1.24.3"
    installed_onnx_runtimes = {
        path: metadata["version"]
        for path, metadata in package_lock["packages"].items()
        if path.endswith("node_modules/onnxruntime-node")
    }
    assert installed_onnx_runtimes == {"node_modules/onnxruntime-node": "1.24.3"}
    assert "node_modules/@img/sharp-linux-x64" in package_lock["packages"]
    assert "node_modules/@img/sharp-libvips-linux-x64" in package_lock["packages"]


def test_python_upload_and_pdf_runtime_uses_audited_security_pins() -> None:
    requirements = {
        line.strip()
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert {
        "fastapi==0.141.1",
        "starlette==1.6.0",
        "pypdf==6.16.1",
        "python-multipart==0.0.32",
    }.issubset(requirements)


def test_vercel_runtime_uses_ephemeral_sqlite_and_small_upload_boundary() -> None:
    config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    environment = config["env"]

    assert environment["INTERVIEW_GENERATION_PROVIDER"] == "openai_api"
    assert environment["NCS_MCP_URL"] == "https://ncscope-ncs-mcp.vercel.app/api/mcp"
    assert environment["NCS_MCP_TIMEOUT_SEC"] == "5"
    assert environment["NCS_MCP_KSA_CONCURRENCY"] == "4"
    assert environment["KSA_RANK_MAX_UNITS"] == "5"
    assert environment["OPENAI_NET_CHECK_ENABLED"] == "false"
    assert environment["DATABASE_URL"] == "sqlite:////tmp/ncscope.db"
    assert environment["MAX_UPLOAD_MB"] == "4"
    assert environment["MAX_REQUEST_BODY_MB"] == "4.25"
    assert float(environment["MAX_REQUEST_BODY_MB"]) > float(environment["MAX_UPLOAD_MB"])
    assert environment["KORDOC_OFFLINE"] == "1"
    assert environment["KORDOC_BRIDGE_URL"] == "https://ncscope.vercel.app/api/kordoc-parse"
    assert environment["OPENAI_STRATEGY_CANDIDATE_MULTIPLIER"] == "1"
    assert environment["OPENAI_QUESTION_CANDIDATE_MULTIPLIER"] == "1"
    assert environment["OPENAI_QUESTION_VARIANT_ATTEMPTS"] == "1"
    assert environment["INSTITUTION_MODEL_REQUESTS_PER_BATCH"] == "1"
    assert environment["INSTITUTION_QUALITY_RETRY_ENABLED"] == "true"
    assert environment["INSTITUTION_GENERATION_BATCH_SIZE"] == "5"
    assert environment["INSTITUTION_GENERATION_BATCH_CONCURRENCY"] == "1"
    assert environment["GENERATION_REQUEST_BUDGET_SEC"] == "285"
    assert environment["DOCUMENT_PARSE_REQUEST_BUDGET_SEC"] == "285"
    assert environment["GENERATION_MAX_MAIN_QUESTIONS"] == "5"
    assert environment["OPENAI_RERANK_MODEL"] == "gpt-5.6-luna"
    assert environment["OPENAI_STRATEGY_MODEL"] == "gpt-5.6-terra"
    assert environment["OPENAI_STRATEGY_RETRY_MODEL"] == "gpt-5.6-sol"
    assert environment["OPENAI_QUESTION_MODEL"] == "gpt-5.6-terra"
    assert environment["OPENAI_QUALITY_REGENERATION_MODEL"] == "gpt-5.6-sol"
    assert environment["OPENAI_QUALITY_REVIEW_MODEL"] == "gpt-5.6-sol"
    assert environment["OPENAI_QUALITY_REVIEW_REASONING_EFFORT"] in {"high", "xhigh", "max"}
    assert environment["AI_QUALITY_REVIEW_TIMEOUT_SEC"] == "70"
    assert not any(key.startswith("OPENROUTER_") for key in environment)
    assert not any("api_key" in key.casefold() for key in environment)
    assert "KORDOC_BRIDGE_SECRET" not in environment


def test_vercel_upload_excludes_local_state_and_test_artifacts() -> None:
    ignored = {
        line.strip()
        for line in (ROOT / ".vercelignore").read_text(encoding="utf-8").splitlines()
        if line.strip()
    }

    assert {".env", "*.db", "tests", "reports", "tmp"}.issubset(ignored)
