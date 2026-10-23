"""Pin parse-path speedups to the exact behaviour they replaced."""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
from pathlib import Path

import pytest

from app.services import kordoc_parser


def _reference_norm(value) -> str:
    """The pre-optimisation ``_norm`` implementation, kept verbatim."""

    text = unicodedata.normalize("NFKC", str(value or ""))
    text = "".join(char for char in text if unicodedata.category(char) != "Co")
    text = re.sub(r"[·ᆞ․‧•∙⋅・ㆍ]", "", text)
    return re.sub(r"[\s:：·•\-_/\\()\[\]{}]+", "", text).lower()


def test_norm_matches_reference_for_separators_and_private_use() -> None:
    samples = [
        None,
        0,
        12.5,
        "",
        "NCS 세분류 : 사무행정",
        "경영·회계·사무 / 02-01-02",
        "ＮＣＳ　세분류（사무）",
        "인사조직관리\U000f0000\U0010fffd",
        "\U000ffffe\U0010fffe noncharacters stay",
        "ᆞ․‧•∙⋅・ㆍ middle dots",
        "[대]{중}(소)_세\\분/류-",
        "İSTANBUL ẞ Ⅳ ① ﬁ",
        "각 조합형",
    ]
    for value in samples:
        assert kordoc_parser._norm(value) == _reference_norm(value), value
    for code_point in (0xE000, 0xF8FF, 0xF0000, 0xFFFFD, 0x100000, 0x10FFFD):
        text = f"가{chr(code_point)}나"
        assert unicodedata.category(chr(code_point)) == "Co"
        assert kordoc_parser._norm(text) == _reference_norm(text) == "가나"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("사무행정, 회계·감사", ["사무행정", "회계·감사"]),
        ("01. 총무 02. 인사 03. 사무행정", ["총무", "인사", "사무행정"]),
        ("QM/QC관리", ["QM/QC관리"]),
        ("한식·양식·조리", ["한식조리", "양식조리"]),
        ("0201010101 경영기획", ["경영기획"]),
        ("1. 사무행정 / 경영기획", ["사무행정", "경영기획"]),
        ("총무 등", ["총무"]),
        ("경영기획;사무행정|인사", ["경영기획", "사무행정", "인사"]),
        ("  ", []),
    ],
)
def test_expand_composite_detail_candidate_outputs_are_pinned(
    value: str,
    expected: list[str],
) -> None:
    assert kordoc_parser._expand_composite_detail_candidate(value) == expected


@pytest.mark.parametrize(
    ("value", "nfkc", "raw"),
    [
        (
            "![img](a.png)**굵게** [링크](http://x) <br/>다음 줄<b>태그</b>",
            "굵게 링크 \n다음 줄 태그",
            "굵게 링크 \n다음 줄 태그",
        ),
        ("  | 값 :  ", "값", "값"),
        ("a\t\tb\n\n\nc", "a b\nc", "a b\nc"),
        ("＜ＮＣＳ＞ ①", "1", "＜ＮＣＳ＞ ①"),
    ],
)
def test_clean_text_outputs_are_pinned(value: str, nfkc: str, raw: str) -> None:
    assert kordoc_parser._clean_text(value) == nfkc
    assert kordoc_parser._clean_text(value, normalize_nfkc=False) == raw


_FAKE_WORKER = r'''
import json
import sys
import time

MARK = "\x1eNCSCOPE_KORDOC_WORKER "
out = sys.stdout
out.write("library banner without marker\n")
out.write("\n" + MARK + "0 ready {}\n")
out.flush()
for line in sys.stdin:
    if not line.strip():
        continue
    request = json.loads(line)
    name = request.get("filename")
    if name == "slow.pdf":
        time.sleep(30)
    if name == "broken.pdf":
        out.write("\n" + MARK + str(request["id"]) + ' error {"detail": "x"}\n')
        out.flush()
        continue
    out.write("stray library output\n")
    body = json.dumps({"echo": request["dataBase64"], "ocr": request["ocr"], "pid": __import__("os").getpid()})
    out.write("\n" + MARK + str(request["id"]) + " ok " + body + "\n")
    out.flush()
'''


@pytest.fixture
def fake_worker_script(tmp_path: Path):
    script = tmp_path / "scripts" / "fake_kordoc_worker.py"
    script.parent.mkdir()
    script.write_text(_FAKE_WORKER, encoding="utf-8")
    kordoc_parser._close_kordoc_worker()
    kordoc_parser._KORDOC_WORKER_RETRY_AT = 0.0
    yield script
    kordoc_parser._close_kordoc_worker()
    kordoc_parser._KORDOC_WORKER_RETRY_AT = 0.0


def _payload(filename: str = "job.hwp") -> dict:
    return {"filename": filename, "dataBase64": "QUJD", "ocr": True}


def test_worker_reuses_one_process_and_ignores_unmarked_output(
    fake_worker_script: Path,
) -> None:
    first = kordoc_parser._parse_with_kordoc_worker(
        sys.executable, fake_worker_script, _payload(), timeout=20
    )
    second = kordoc_parser._parse_with_kordoc_worker(
        sys.executable, fake_worker_script, _payload(), timeout=20
    )

    assert first is not None and second is not None
    first_body = json.loads(first)
    second_body = json.loads(second)
    assert first_body["echo"] == "QUJD"
    assert first_body["ocr"] is True
    assert first_body["pid"] == second_body["pid"]


def test_worker_parse_error_falls_back_to_one_shot_runner(
    fake_worker_script: Path,
) -> None:
    result = kordoc_parser._parse_with_kordoc_worker(
        sys.executable, fake_worker_script, _payload("broken.pdf"), timeout=20
    )

    assert result is None
    assert kordoc_parser._KORDOC_WORKER is None


def test_worker_timeout_raises_parse_error_and_stops_worker(
    fake_worker_script: Path,
) -> None:
    kordoc_parser._parse_with_kordoc_worker(
        sys.executable, fake_worker_script, _payload(), timeout=20
    )
    worker = kordoc_parser._KORDOC_WORKER
    assert worker is not None

    with pytest.raises(kordoc_parser.KordocParseError, match="timed out after 1s"):
        kordoc_parser._parse_with_kordoc_worker(
            sys.executable, fake_worker_script, _payload("slow.pdf"), timeout=1
        )

    assert kordoc_parser._KORDOC_WORKER is None
    assert worker._process.poll() is not None


def test_busy_or_disabled_worker_uses_one_shot_runner(
    fake_worker_script: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert kordoc_parser._KORDOC_WORKER_LOCK.acquire(blocking=False)
    try:
        assert (
            kordoc_parser._parse_with_kordoc_worker(
                sys.executable, fake_worker_script, _payload(), timeout=20
            )
            is None
        )
    finally:
        kordoc_parser._KORDOC_WORKER_LOCK.release()

    monkeypatch.setenv("KORDOC_PERSISTENT_WORKER", "0")
    assert (
        kordoc_parser._parse_with_kordoc_worker(
            sys.executable, fake_worker_script, _payload(), timeout=20
        )
        is None
    )
    assert kordoc_parser._KORDOC_WORKER is None


def test_worker_that_never_starts_is_not_retried_immediately(
    tmp_path: Path,
    fake_worker_script: Path,
) -> None:
    dead_script = tmp_path / "scripts" / "dead_worker.py"
    dead_script.write_text("import sys\nsys.exit(3)\n", encoding="utf-8")

    assert (
        kordoc_parser._parse_with_kordoc_worker(
            sys.executable, dead_script, _payload(), timeout=20
        )
        is None
    )
    assert kordoc_parser._KORDOC_WORKER_RETRY_AT > 0
    assert (
        kordoc_parser._parse_with_kordoc_worker(
            sys.executable, fake_worker_script, _payload(), timeout=20
        )
        is None
    )


def _kordoc_stdout() -> bytes:
    return json.dumps(
        {
            "success": True,
            "parser": "kordoc",
            "parser_version": kordoc_parser._KORDOC_PARSER_VERSION,
            "parser_execution": {
                "schema_version": "ncscope_parser_execution_v1",
                "role": "selected",
                "parser": "kordoc",
                "mode": "local_node_subprocess",
                "parser_version": kordoc_parser._KORDOC_PARSER_VERSION,
                "node_version": "24.14.0",
                "build_identity": {"kind": "local_source_bundle"},
            },
            "markdown": "| NCS 세분류 | 사무행정 |",
            "blocks": [],
        },
        ensure_ascii=False,
    ).encode("utf-8")


def test_local_kordoc_result_is_the_same_from_worker_and_one_shot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    stdout = _kordoc_stdout()

    class Completed:
        returncode = 0
        stderr = b""

    def one_shot(command, **kwargs):
        calls.append("one_shot")
        assert json.loads(kwargs["input"]) == {
            "filename": "job.hwp",
            "dataBase64": "ZG9j",
            "ocr": True,
        }
        completed = Completed()
        completed.stdout = stdout
        return completed

    monkeypatch.setattr(kordoc_parser.subprocess, "run", one_shot)
    monkeypatch.setattr(kordoc_parser, "_parse_with_kordoc_worker", lambda *a, **k: None)
    from_one_shot = kordoc_parser._parse_with_local_kordoc(
        b"doc", filename="job.hwp", ocr=True, timeout=30
    )

    seen: dict = {}

    def worker(node, script, payload, *, timeout, deadline):
        seen.update(payload=payload, timeout=timeout)
        assert 0 < deadline - time.monotonic() <= 30
        return stdout

    monkeypatch.setattr(kordoc_parser, "_parse_with_kordoc_worker", worker)
    from_worker = kordoc_parser._parse_with_local_kordoc(
        b"doc", filename="job.hwp", ocr=True, timeout=30
    )

    assert calls == ["one_shot"]
    assert seen == {
        "payload": {"filename": "job.hwp", "dataBase64": "ZG9j", "ocr": True},
        "timeout": 30,
    }
    assert from_worker == from_one_shot
    assert from_worker["markdown"] == "| NCS 세분류 | 사무행정 |"


def test_worker_serialises_requests_without_blocking_other_threads(
    fake_worker_script: Path,
) -> None:
    results: list[bytes | None] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            results.append(
                kordoc_parser._parse_with_kordoc_worker(
                    sys.executable, fake_worker_script, _payload(), timeout=20
                )
            )
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert not errors
    assert len(results) == 4
    # Requests that found the worker busy return None and use their own
    # one-shot process; at least one request used the worker.
    assert any(result is not None for result in results)


class _Completed:
    returncode = 0
    stderr = b""

    def __init__(self, stdout: bytes) -> None:
        self.stdout = stdout


def test_late_worker_failure_leaves_only_the_remaining_budget_for_one_shot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, float] = {}

    def late_failing_worker(node, script, payload, *, timeout, deadline):
        observed["deadline"] = deadline
        observed["deadline_budget"] = deadline - time.monotonic()
        time.sleep(0.6)
        observed["worker_returned_at"] = time.monotonic()
        return None

    def one_shot(command, **kwargs):
        observed["one_shot_timeout"] = kwargs["timeout"]
        return _Completed(_kordoc_stdout())

    monkeypatch.setattr(kordoc_parser, "_parse_with_kordoc_worker", late_failing_worker)
    monkeypatch.setattr(kordoc_parser.subprocess, "run", one_shot)

    started = time.monotonic()
    result = kordoc_parser._parse_with_local_kordoc(
        b"doc", filename="job.hwp", ocr=False, timeout=2
    )

    assert result["markdown"] == "| NCS 세분류 | 사무행정 |"
    assert 1.9 < observed["deadline_budget"] <= 2.0
    # The retry gets what is left of the 2 s budget, not a fresh 2 s. Compare
    # with the measured worker return time: Windows sleep can end a few
    # milliseconds early, so a fixed 2.0 - 0.6 bound was flaky.
    assert 0 < observed["one_shot_timeout"] <= observed["deadline"] - observed["worker_returned_at"]
    assert observed["one_shot_timeout"] < 2.0 - 0.5
    assert time.monotonic() - started + observed["one_shot_timeout"] <= 2.05


def test_worker_failure_after_the_budget_raises_the_usual_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def exhausted_worker(node, script, payload, *, timeout, deadline):
        time.sleep(max(0.0, deadline - time.monotonic()) + 0.05)
        return None

    def one_shot(command, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("one-shot retry must not start after the deadline")

    monkeypatch.setattr(kordoc_parser, "_parse_with_kordoc_worker", exhausted_worker)
    monkeypatch.setattr(kordoc_parser.subprocess, "run", one_shot)

    with pytest.raises(
        kordoc_parser.KordocParseError,
        match=r"^Kordoc parsing timed out after 1s$",
    ):
        kordoc_parser._parse_with_local_kordoc(
            b"doc", filename="job.hwp", ocr=False, timeout=1
        )


def test_worker_parse_uses_the_shared_deadline_not_a_fresh_timeout(
    fake_worker_script: Path,
) -> None:
    started = time.monotonic()
    with pytest.raises(kordoc_parser.KordocParseError, match="timed out after 30s"):
        kordoc_parser._parse_with_kordoc_worker(
            sys.executable,
            fake_worker_script,
            _payload("slow.pdf"),
            timeout=30,
            deadline=time.monotonic() + 2.0,
        )

    assert time.monotonic() - started < 10
    assert kordoc_parser._KORDOC_WORKER is None


def test_worker_is_not_started_once_the_deadline_has_passed(
    fake_worker_script: Path,
) -> None:
    assert (
        kordoc_parser._parse_with_kordoc_worker(
            sys.executable,
            fake_worker_script,
            _payload(),
            timeout=30,
            deadline=time.monotonic() - 0.1,
        )
        is None
    )
    assert kordoc_parser._KORDOC_WORKER is None
    assert kordoc_parser._KORDOC_WORKER_RETRY_AT == 0.0


_FAKE_KORDOC_MODULE = """
export const VERSION = "4.24.1";
export async function parse(bytes) {
  const kind = Buffer.from(bytes).toString("utf8");
  const block = (fontName) => ({ type: "paragraph", text: kind, style: { fontSize: 10, fontName } });
  if (kind === "pdf") {
    return { success: true, fileType: "pdf", markdown: kind, blocks: [block("g_d7_f2"), block("g_d8_f1")] };
  }
  if (kind === "hwpx") {
    return { success: true, fileType: "hwpx", markdown: kind, blocks: [block("g_d3_f1")] };
  }
  return { success: true, markdown: kind, blocks: [block("g_d4_f1")] };
}
"""


def test_runner_restores_pdfjs_font_ids_only_for_pdf_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_worker_script: Path,
) -> None:
    """Run the shipped kordoc_parse.mjs with a fake ``kordoc`` module.

    A Node resolve hook swaps the ``kordoc`` import, so both the one-shot and
    the ``--serve`` paths of the real runner run without real documents.
    """

    node = shutil.which("node") or shutil.which("node.exe")
    assert node, "Node.js is required to run the Kordoc runner"
    script = (
        Path(kordoc_parser.__file__).resolve().parents[2] / "scripts" / "kordoc_parse.mjs"
    )
    fake_kordoc = tmp_path / "fake_kordoc.mjs"
    fake_kordoc.write_text(_FAKE_KORDOC_MODULE, encoding="utf-8")
    hooks = tmp_path / "fake_kordoc_hooks.mjs"
    hooks.write_text(
        "export async function resolve(specifier, context, next) {\n"
        "  if (specifier === 'kordoc') {\n"
        f"    return {{ url: {json.dumps(fake_kordoc.as_uri())}, shortCircuit: true }};\n"
        "  }\n"
        "  return next(specifier, context);\n"
        "}\n",
        encoding="utf-8",
    )
    register = tmp_path / "register_fake_kordoc.mjs"
    register.write_text(
        "import { register } from 'node:module';\n"
        f"register({json.dumps(hooks.as_uri())});\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("NODE_OPTIONS", f"--import={register.as_uri()}")

    def request(kind: str) -> dict:
        return {
            "filename": f"job.{kind}",
            "dataBase64": base64.b64encode(kind.encode("utf-8")).decode("ascii"),
            "ocr": False,
        }

    def one_shot(kind: str) -> bytes:
        completed = subprocess.run(
            [node, str(script)],
            input=json.dumps(request(kind)).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(script.parents[1]),
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
        return completed.stdout

    from_worker = {
        kind: kordoc_parser._parse_with_kordoc_worker(
            node, script, request(kind), timeout=60
        )
        for kind in ("pdf", "hwpx", "unknown")
    }

    # Non-PDF and unknown formats pass through unchanged, so a real font face
    # that looks like a pdf.js id is kept exactly as the one-shot runner has it.
    assert from_worker["hwpx"] == one_shot("hwpx")
    assert b'"fontName":"g_d3_f1"' in from_worker["hwpx"]
    assert from_worker["unknown"] == one_shot("unknown")
    assert b'"fontName":"g_d4_f1"' in from_worker["unknown"]
    # PDF: pdf.js document numbers restart at d0 as in a fresh process.
    pdf_blocks = json.loads(from_worker["pdf"])["blocks"]
    assert [block["style"]["fontName"] for block in pdf_blocks] == ["g_d0_f2", "g_d1_f1"]
    assert json.loads(one_shot("pdf"))["blocks"][0]["style"]["fontName"] == "g_d7_f2"


_SLOW_READER_WORKER = r'''
import json
import sys
import time

MARK = "\x1eNCSCOPE_KORDOC_WORKER "
sys.stdout.write("\n" + MARK + "0 ready {}\n")
sys.stdout.flush()
# Do not read the request for a while: the parent's pipe write blocks.
time.sleep(2.0)
for line in sys.stdin:
    if not line.strip():
        continue
    request = json.loads(line)
    sys.stdout.write("\n" + MARK + str(request["id"]) + ' ok {"late": true}\n')
    sys.stdout.flush()
'''


def test_request_upload_is_bounded_by_the_same_deadline(
    tmp_path: Path,
    fake_worker_script: Path,
) -> None:
    script = tmp_path / "scripts" / "slow_reader_worker.py"
    script.write_text(_SLOW_READER_WORKER, encoding="utf-8")
    payload = {
        "filename": "large.pdf",
        "dataBase64": base64.b64encode(b"x" * (1024 * 1024)).decode("ascii"),
        "ocr": False,
    }

    # Start the worker outside the measured call so interpreter start-up time
    # cannot decide the outcome; the request itself has a 0.1 s budget.
    worker = kordoc_parser._KordocWorker(sys.executable, script, start_timeout=20)
    kordoc_parser._KORDOC_WORKER = worker

    started = time.monotonic()
    with pytest.raises(
        kordoc_parser.KordocParseError,
        match=r"^Kordoc parsing timed out after 0\.1s$",
    ):
        kordoc_parser._parse_with_kordoc_worker(
            sys.executable, script, payload, timeout=0.1
        )
    elapsed = time.monotonic() - started

    # Never wait for the worker's 2 s read delay.
    assert elapsed < 1.0, elapsed
    assert kordoc_parser._KORDOC_WORKER is None
    assert worker._process.poll() is not None


def test_slow_request_upload_after_startup_times_out_and_stops_the_worker(
    tmp_path: Path,
    fake_worker_script: Path,
) -> None:
    script = tmp_path / "scripts" / "slow_reader_worker.py"
    script.write_text(_SLOW_READER_WORKER, encoding="utf-8")
    worker = kordoc_parser._KordocWorker(sys.executable, script, start_timeout=20)
    payload = {
        "filename": "large.pdf",
        "dataBase64": base64.b64encode(b"x" * (1024 * 1024)).decode("ascii"),
        "ocr": False,
    }
    try:
        started = time.monotonic()
        with pytest.raises(kordoc_parser._KordocWorkerTimeout):
            worker.parse(payload, timeout=0.1)
        assert time.monotonic() - started < 1.0
    finally:
        closed_at = time.monotonic()
        worker.close()
        assert time.monotonic() - closed_at < 5.0
    assert worker._process.poll() is not None
