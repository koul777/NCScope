from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if (
            key
            and value
            and "YOUR_" not in value
            and "SET_STRONG" not in value
            and key not in os.environ
        ):
            os.environ[key] = value


_load_env_file(Path(".env"))
_load_env_file(Path(".env.example"))


@dataclass(frozen=True)
class Settings:
    public_inst_base_url: str = os.getenv("PUBLIC_INST_BASE_URL", "https://apis.data.go.kr/1051000/public_inst/")
    recruitment_base_url: str = os.getenv("RECRUITMENT_BASE_URL", "https://apis.data.go.kr/1051000/recruitment/")
    ncs_base_url: str = os.getenv("NCS_BASE_URL", "https://www.ncs.go.kr/api/")
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    def public_inst_key(self) -> str:
        return (
            os.getenv("PUBLIC_INST_SERVICE_KEY", "").strip()
            or os.getenv("DATA_GO_KR_SERVICE_KEY", "").strip()
        )

    def ncs_key(self) -> str:
        return (
            os.getenv("NCS_SERVICE_KEY", "").strip()
            or os.getenv("DATA_GO_KR_SERVICE_KEY", "").strip()
        )

    def openai_key(self) -> str:
        return os.getenv("OPENAI_API_KEY", "").strip()

    def admin_token(self) -> str:
        return os.getenv("ADMIN_TOKEN", "").strip()

    def auto_sync_public_inst(self) -> bool:
        return os.getenv("AUTO_SYNC_PUBLIC_INST", "false").strip().lower() in {"1", "true", "yes", "y"}

    def auto_sync_ncs(self) -> bool:
        return os.getenv("AUTO_SYNC_NCS", "false").strip().lower() in {"1", "true", "yes", "y"}

    def auto_use_openai_in_report(self) -> bool:
        return os.getenv("AUTO_USE_OPENAI_IN_REPORT", "true").strip().lower() in {"1", "true", "yes", "y"}

    def sync_interval_minutes(self) -> int:
        value = os.getenv("SYNC_INTERVAL_MINUTES", "60").strip()
        try:
            return max(5, int(value))
        except Exception:
            return 60

    def ncs_sync_path(self) -> str:
        return os.getenv("NCS_SYNC_PATH", "Ncs1info/ncsinfo.do").strip()


settings = Settings()
