"""Safe Claude Code subscription adapter for interview-question generation.

The adapter intentionally consumes only the local user's saved Claude.ai
subscription login.  API keys, long-lived token environment variables and
third-party cloud-provider credentials are removed from every child process.
It is intended for a trusted, single-user workstation, not as an
arbitrary-prompt service exposed to untrusted remote users.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.services.codex_question_provider import (
    CodexInvalidOutputError,
    CodexQuestionProvider,
    SubprocessRunner,
    _OutputLimitExceeded,
    _diagnostic,
    _internal_reference_labels,
    _output_text,
)


_SCHEMA_PATH = Path(__file__).with_name("codex_question_schema.json").resolve()
_AUTH_FAILURE_MARKERS = (
    "not logged in",
    "login required",
    "please run /login",
    "please run claude auth login",
    "authentication required",
    "authentication failed",
    "authentication_error",
    "please log in",
    "please login",
    "oauth token has expired",
    "token expired",
    "unauthorized",
    "invalid api key",
    "invalid x-api-key",
    "invalid bearer token",
    "http 401",
    "status 401",
)
_LIMIT_FAILURE_MARKERS = (
    "usage limit",
    "weekly limit",
    "monthly limit",
    "5-hour limit",
    "5 hour limit",
    "hit your limit",
    "reached your limit",
    "rate limit",
    "rate_limit",
    "quota exceeded",
    "quota_exceeded",
    "too many requests",
    "http 429",
    "status 429",
    "credits exhausted",
    "credit balance is too low",
    "out of extra usage",
)
_SUBSCRIPTION_AUTH_METHOD = "claude.ai"
_FIRST_PARTY_PROVIDER = "firstparty"
_NON_SUBSCRIPTION_TYPES = {
    "",
    "none",
    "free",
    "api",
    "api_key",
    "console",
    "pay_as_you_go",
}
_SENSITIVE_ENVIRONMENT_NAMES = {
    # Anthropic credentials and alternate authentication routes.
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_CUSTOM_HEADERS",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CONFIG_DIR",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_SKIP_BEDROCK_AUTH",
    "CLAUDE_CODE_SKIP_VERTEX_AUTH",
    "CLAUDE_CODE_API_KEY_HELPER_TTL_MS",
    # Common direct API credentials that should not reach a model subprocess.
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
    "AZURE_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    # Prevent an accidentally configured cloud backend from supplying Claude.
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_BEARER_TOKEN_BEDROCK",
    "AWS_PROFILE",
    "AWS_DEFAULT_PROFILE",
    "AWS_CONFIG_FILE",
    "AWS_SHARED_CREDENTIALS_FILE",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "ANTHROPIC_BEDROCK_BASE_URL",
    "ANTHROPIC_VERTEX_PROJECT_ID",
    "CLOUD_ML_REGION",
    "GOOGLE_CLOUD_PROJECT",
    "ANTHROPIC_FOUNDRY_RESOURCE",
    "ANTHROPIC_FOUNDRY_BASE_URL",
    "AZURE_CLIENT_ID",
    "AZURE_TENANT_ID",
    "AZURE_CLIENT_SECRET",
    "AZURE_CONFIG_DIR",
}


class ClaudeCodeProviderError(RuntimeError):
    """Base error with a stable machine-readable code."""

    code = "claude_code_provider_error"
    retryable = False

    def __init__(self, message: str = "") -> None:
        super().__init__(message or self.code)


class ClaudeCodeUnavailableError(ClaudeCodeProviderError):
    code = "claude_code_unavailable"


class ClaudeCodeAuthenticationError(ClaudeCodeProviderError):
    code = "claude_code_authentication_required"


class ClaudeCodeUsageLimitError(ClaudeCodeProviderError):
    code = "claude_code_usage_limit_reached"
    retryable = True


class ClaudeCodeTimeoutError(ClaudeCodeProviderError):
    code = "claude_code_timeout"
    retryable = True


class ClaudeCodeInvalidOutputError(ClaudeCodeProviderError):
    code = "claude_code_invalid_output"


class ClaudeCodeExecutionError(ClaudeCodeProviderError):
    code = "claude_code_execution_failed"
    retryable = True


class ClaudeCodeInputTooLargeError(ClaudeCodeProviderError):
    code = "claude_code_input_too_large"


def _classify_cli_failure(
    stdout: str, stderr: str
) -> type[ClaudeCodeProviderError] | None:
    text = f"{stdout}\n{stderr}".casefold()
    if any(marker in text for marker in _LIMIT_FAILURE_MARKERS):
        return ClaudeCodeUsageLimitError
    if any(marker in text for marker in _AUTH_FAILURE_MARKERS):
        return ClaudeCodeAuthenticationError
    return None


def _subscription_type(value: Any) -> str:
    return re.sub(r"[\s-]+", "_", str(value or "").strip().casefold())


class ClaudeCodeProvider(CodexQuestionProvider):
    """Generate grounded questions through a local Claude Code subscription."""

    def __init__(
        self,
        *,
        runner: SubprocessRunner | None = None,
        executable_resolver: Callable[[str], str | None] = shutil.which,
        executable: str | None = None,
        timeout_sec: float = 180.0,
        auth_timeout_sec: float = 15.0,
        max_input_chars: int = 120_000,
        max_output_chars: int = 400_000,
        max_stderr_chars: int = 32_000,
    ) -> None:
        super().__init__(
            runner=runner,
            executable_resolver=executable_resolver,
            executable=executable,
            timeout_sec=timeout_sec,
            auth_timeout_sec=auth_timeout_sec,
            max_input_chars=max_input_chars,
            max_output_chars=max_output_chars,
            max_stderr_chars=max_stderr_chars,
        )

    @staticmethod
    def _prefer_native_windows_executable(executable: str) -> str:
        """Avoid a CMD/PowerShell shim when the bundled native binary exists."""

        path = Path(executable)
        if path.suffix.casefold() not in {".bat", ".cmd", ".ps1"}:
            return executable
        candidate = (
            path.parent
            / "node_modules"
            / "@anthropic-ai"
            / "claude-code"
            / "bin"
            / "claude.exe"
        )
        return str(candidate) if candidate.is_file() else executable

    def check_availability(self) -> str:
        executable = self._explicit_executable or self._executable_resolver("claude")
        if not executable:
            raise ClaudeCodeUnavailableError(
                "Claude Code CLI is not installed or is not on PATH"
            )
        return self._prefer_native_windows_executable(str(executable))

    ensure_available = check_availability

    def check_login(
        self, *, executable: str | None = None, cwd: str | None = None
    ) -> str:
        claude = executable or self.check_availability()
        try:
            completed = self._runner(
                [claude, "--safe-mode", "--no-chrome", "auth", "status"],
                input="",
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.auth_timeout_sec,
                cwd=cwd,
                env=self._safe_environment(),
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ClaudeCodeTimeoutError(
                "Timed out while checking Claude Code login status"
            ) from exc
        except (FileNotFoundError, PermissionError, OSError) as exc:
            raise ClaudeCodeUnavailableError(
                "Claude Code CLI could not be started"
            ) from exc
        except _OutputLimitExceeded as exc:
            raise ClaudeCodeInvalidOutputError(
                "Claude Code login status output was too large"
            ) from exc

        stdout = _output_text(completed, "stdout")
        stderr = _output_text(completed, "stderr")
        failure_type = _classify_cli_failure(stdout, stderr)
        if int(getattr(completed, "returncode", 1)) != 0:
            if failure_type is ClaudeCodeUsageLimitError:
                raise ClaudeCodeUsageLimitError(
                    "Claude Code account usage limit was reached"
                )
            detail = _diagnostic(stdout, stderr)
            raise ClaudeCodeAuthenticationError(
                "Claude Code is not signed in with a Claude subscription"
                f"{': ' + detail if detail else ''}"
            )

        try:
            status = json.loads(stdout.strip())
        except (json.JSONDecodeError, TypeError) as exc:
            raise ClaudeCodeInvalidOutputError(
                "Claude Code returned an invalid authentication status"
            ) from exc
        if not isinstance(status, dict):
            raise ClaudeCodeInvalidOutputError(
                "Claude Code returned an invalid authentication status"
            )

        auth_method = str(status.get("authMethod") or "").strip().casefold()
        api_provider = str(status.get("apiProvider") or "").strip().casefold()
        subscription_type = _subscription_type(status.get("subscriptionType"))
        if (
            status.get("loggedIn") is not True
            or auth_method != _SUBSCRIPTION_AUTH_METHOD
            or api_provider != _FIRST_PARTY_PROVIDER
            or subscription_type in _NON_SUBSCRIPTION_TYPES
        ):
            raise ClaudeCodeAuthenticationError(
                "Claude Code must be signed in through Claude.ai with a paid "
                "subscription; API, Console and cloud-provider auth are not allowed"
            )
        return "claude_subscription"

    ensure_subscription_login = check_login

    @staticmethod
    def _safe_environment() -> dict[str, str]:
        env = dict(os.environ)
        provider_prefixes = (
            "ANTHROPIC_",
            "CLAUDE_CODE_",
            "OPENAI_",
            "CODEX_",
            "AWS_",
            "AZURE_",
            "GOOGLE_",
            "GCP_",
            "VERTEX_",
            "CLOUD_ML_",
        )
        secret_suffixes = (
            "_API_KEY",
            "_AUTH_TOKEN",
            "_ACCESS_TOKEN",
            "_SECRET_KEY",
            "_PASSWORD",
            "_PROFILE",
            "_CREDENTIALS_FILE",
        )
        for name in list(env):
            normalized = name.upper()
            if (
                normalized == "CLAUDECODE"
                or normalized in _SENSITIVE_ENVIRONMENT_NAMES
                or normalized.startswith(provider_prefixes)
                or normalized.endswith(secret_suffixes)
            ):
                env.pop(name, None)
        return env

    @staticmethod
    def _runtime_schema_json(
        *, target_count: int, evidence_rows: list[dict[str, str]]
    ) -> str:
        schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
        properties = schema["properties"]["interview_questions"]["items"][
            "properties"
        ]
        evidence_ids = list(
            dict.fromkeys(
                str(row.get("evidence_id") or "").strip()
                for row in evidence_rows
                if str(row.get("evidence_id") or "").strip()
            )
        )
        ncs_codes = list(
            dict.fromkeys(
                str(row.get("ncsClCd") or "").strip()
                for row in evidence_rows
                if str(row.get("ncsClCd") or "").strip()
            )
        )
        if evidence_ids:
            properties["question_evidence_id"]["enum"] = evidence_ids
        if ncs_codes:
            properties["ncsClCd"]["enum"] = ncs_codes

        # Claude's raw JSON-schema interface rejects string-length constraints,
        # maxItems, and minItems values above one.  The application validator
        # below still enforces every original bound, including target_count
        # and exactly four evaluation points.  The shared schema description
        # keeps that exact-four contract visible to Claude after the numeric
        # array bounds have been removed for CLI compatibility.
        def make_compatible(value: Any) -> Any:
            if isinstance(value, dict):
                compatible: dict[str, Any] = {}
                for key, item in value.items():
                    if key in {"$schema", "title", "minLength", "maxLength", "maxItems"}:
                        continue
                    if key == "minItems" and item not in {0, 1}:
                        continue
                    compatible[key] = make_compatible(item)
                return compatible
            if isinstance(value, list):
                return [make_compatible(item) for item in value]
            return value

        compatible_schema = make_compatible(schema)
        question_description = compatible_schema["properties"][
            "interview_questions"
        ].get("description", "")
        compatible_schema["properties"]["interview_questions"]["description"] = (
            f"{question_description} Exactly {target_count} items are required."
        ).strip()
        return json.dumps(
            compatible_schema, ensure_ascii=False, separators=(",", ":")
        )

    def generate(
        self,
        jd_text: str,
        notice_text: str,
        strengths: str,
        region: str,
        ncs_matches: list[dict[str, Any]],
        ncs_ksa: list[dict[str, Any]] | None = None,
        ncs_context: dict[str, Any] | None = None,
        duty_text: str = "",
        evaluation_text: str = "",
        desired_job: str = "",
        api_key_override: str = "",
        target_count_override: int | None = None,
        follow_up_count: int = 3,
        question_plan: dict[str, Any] | None = None,
        interview_methods: list[str] | None = None,
        extra_context: str = "",
    ) -> dict[str, Any]:
        """Generate through saved Claude.ai auth; never forward an API key."""

        del api_key_override
        requested_follow_up_count = max(0, min(5, int(follow_up_count or 0)))
        target_count = max(1, min(40, int(target_count_override or 10)))
        source, evidence_rows = self._build_source_data(
            jd_text=jd_text,
            notice_text=notice_text,
            strengths=strengths,
            region=region,
            ncs_matches=ncs_matches,
            ncs_ksa=ncs_ksa,
            ncs_context=ncs_context,
            duty_text=duty_text,
            evaluation_text=evaluation_text,
            desired_job=desired_job,
            question_plan=question_plan,
            interview_methods=interview_methods,
            extra_context=extra_context,
            requested_follow_up_count=requested_follow_up_count,
        )
        prompt = self._build_prompt(source, target_count=target_count)
        if len(prompt) > self.max_input_chars:
            raise ClaudeCodeInputTooLargeError(
                f"Claude Code question input exceeds {self.max_input_chars} characters"
            )

        claude = self.check_availability()
        schema_json = self._runtime_schema_json(
            target_count=target_count, evidence_rows=evidence_rows
        )
        with tempfile.TemporaryDirectory(
            prefix="ncscope-claude-questions-"
        ) as temp_cwd:
            self.check_login(executable=claude, cwd=temp_cwd)
            command = [
                claude,
                "--print",
                "--output-format",
                "json",
                "--json-schema",
                schema_json,
                "--permission-mode",
                "dontAsk",
                "--tools",
                "",
                "--disable-slash-commands",
                "--no-session-persistence",
                "--safe-mode",
                "--strict-mcp-config",
                "--mcp-config",
                '{"mcpServers":{}}',
                "--no-chrome",
            ]
            try:
                completed = self._runner(
                    command,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout_sec,
                    cwd=temp_cwd,
                    env=self._safe_environment(),
                    check=False,
                    shell=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise ClaudeCodeTimeoutError(
                    "Claude Code question generation timed out"
                ) from exc
            except (FileNotFoundError, PermissionError, OSError) as exc:
                raise ClaudeCodeUnavailableError(
                    "Claude Code CLI could not be started"
                ) from exc
            except _OutputLimitExceeded as exc:
                raise ClaudeCodeInvalidOutputError(
                    "Claude Code output exceeded the configured limit"
                ) from exc

        stdout = _output_text(completed, "stdout")
        stderr = _output_text(completed, "stderr")
        if len(stdout) > self.max_output_chars or len(stderr) > self.max_stderr_chars:
            raise ClaudeCodeInvalidOutputError(
                "Claude Code output exceeded the configured limit"
            )

        if int(getattr(completed, "returncode", 1)) != 0:
            failure_type = _classify_cli_failure(stdout, stderr)
            if failure_type is ClaudeCodeUsageLimitError:
                raise ClaudeCodeUsageLimitError(
                    "Claude Code account usage limit was reached"
                )
            if failure_type is ClaudeCodeAuthenticationError:
                raise ClaudeCodeAuthenticationError(
                    "Claude Code subscription authentication failed"
                )
            try:
                failure_envelope = json.loads(stdout.strip())
            except (json.JSONDecodeError, TypeError):
                failure_envelope = None
            if (
                isinstance(failure_envelope, dict)
                and str(failure_envelope.get("subtype") or "").casefold()
                == "error_max_structured_output_retries"
            ):
                raise ClaudeCodeInvalidOutputError(
                    "Claude Code could not produce schema-valid structured output"
                )
            detail = _diagnostic(stdout, stderr)
            raise ClaudeCodeExecutionError(
                f"Claude Code CLI failed{': ' + detail if detail else ''}"
            )

        structured_output = self._parse_result_envelope(stdout, stderr=stderr)
        try:
            questions = super()._parse_and_validate(
                json.dumps(structured_output, ensure_ascii=False),
                target_count=target_count,
                evidence_rows=evidence_rows,
                internal_labels=_internal_reference_labels(source),
            )
        except CodexInvalidOutputError as exc:
            message = str(exc).replace("Codex", "Claude Code")
            raise ClaudeCodeInvalidOutputError(message) from exc
        for question in questions:
            question["question_source"] = "claude_code"

        return {
            "interview_questions": questions,
            "generation_mode": "claude_code_subscription",
            "provider": "claude_code",
            "question_generation_provider": "claude_code",
            "question_generation_policy": (
                "claude_code_behavioral_evidence_questions_v2"
            ),
        }

    generate_questions = generate

    @staticmethod
    def _parse_result_envelope(stdout: str, *, stderr: str = "") -> dict[str, Any]:
        try:
            envelope = json.loads(stdout.strip())
        except (json.JSONDecodeError, TypeError) as exc:
            raise ClaudeCodeInvalidOutputError(
                "Claude Code did not return one valid JSON result envelope"
            ) from exc
        if not isinstance(envelope, dict):
            raise ClaudeCodeInvalidOutputError(
                "Claude Code returned an unexpected result envelope"
            )

        result_type = str(envelope.get("type") or "").strip().casefold()
        subtype = str(envelope.get("subtype") or "").strip().casefold()
        is_error = envelope.get("is_error") is True
        if is_error or (subtype and subtype != "success"):
            serialized = json.dumps(envelope, ensure_ascii=False)
            failure_type = _classify_cli_failure(serialized, stderr)
            if failure_type is ClaudeCodeUsageLimitError:
                raise ClaudeCodeUsageLimitError(
                    "Claude Code account usage limit was reached"
                )
            if failure_type is ClaudeCodeAuthenticationError:
                raise ClaudeCodeAuthenticationError(
                    "Claude Code subscription authentication failed"
                )
            if subtype == "error_max_structured_output_retries":
                raise ClaudeCodeInvalidOutputError(
                    "Claude Code could not produce schema-valid structured output"
                )
            detail = _diagnostic(serialized, stderr)
            raise ClaudeCodeExecutionError(
                f"Claude Code generation failed{': ' + detail if detail else ''}"
            )
        if (
            result_type != "result"
            or subtype != "success"
            or envelope.get("is_error") is not False
        ):
            raise ClaudeCodeInvalidOutputError(
                "Claude Code returned an unexpected result envelope"
            )

        structured_output = envelope.get("structured_output")
        if not isinstance(structured_output, dict):
            raise ClaudeCodeInvalidOutputError(
                "Claude Code result did not contain structured_output"
            )
        return structured_output


def generate_interview_questions_with_claude(
    jd_text: str,
    notice_text: str,
    strengths: str,
    region: str,
    ncs_matches: list[dict[str, Any]],
    ncs_ksa: list[dict[str, Any]] | None = None,
    ncs_context: dict[str, Any] | None = None,
    duty_text: str = "",
    evaluation_text: str = "",
    desired_job: str = "",
    api_key_override: str = "",
    target_count_override: int | None = None,
    follow_up_count: int = 3,
    question_plan: dict[str, Any] | None = None,
    interview_methods: list[str] | None = None,
    extra_context: str = "",
    *,
    provider: ClaudeCodeProvider | None = None,
) -> dict[str, Any]:
    """Strategy-compatible convenience entry point for main-service wiring."""

    if provider is not None:
        active_provider = provider
    else:
        try:
            timeout_sec = float(
                os.getenv("CLAUDE_CODE_STRATEGY_TIMEOUT_SEC", "180") or "180"
            )
        except ValueError:
            timeout_sec = 180.0
        active_provider = ClaudeCodeProvider(timeout_sec=timeout_sec)
    return active_provider.generate(
        jd_text=jd_text,
        notice_text=notice_text,
        strengths=strengths,
        region=region,
        ncs_matches=ncs_matches,
        ncs_ksa=ncs_ksa,
        ncs_context=ncs_context,
        duty_text=duty_text,
        evaluation_text=evaluation_text,
        desired_job=desired_job,
        api_key_override=api_key_override,
        target_count_override=target_count_override,
        follow_up_count=follow_up_count,
        question_plan=question_plan,
        interview_methods=interview_methods,
        extra_context=extra_context,
    )


__all__ = [
    "ClaudeCodeAuthenticationError",
    "ClaudeCodeExecutionError",
    "ClaudeCodeInputTooLargeError",
    "ClaudeCodeInvalidOutputError",
    "ClaudeCodeProvider",
    "ClaudeCodeProviderError",
    "ClaudeCodeTimeoutError",
    "ClaudeCodeUnavailableError",
    "ClaudeCodeUsageLimitError",
    "generate_interview_questions_with_claude",
]
