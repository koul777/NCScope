from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.prepare_vercel_build_sandbox import (
    _git_tracked_deploy_files,
    _require_clean_git_snapshot,
    prepare_sandbox,
)


def _write(path: Path, content: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_prepare_sandbox_copies_only_required_inputs(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    output_dir = tmp_path / "sandbox"

    for relative in (
        "api/index.py",
        "app/main.py",
        "app/services/alio_sclass_profile.py",
        "app/data/ncs_detail_catalog.json",
        "app/data/ncs_unit_catalog.json",
        "app/data/node_package_lock_attestation.json",
        "app/data/vercel_config_attestation.json",
        "app/__pycache__/main.cpython-312.pyc",
        "scripts/kordoc_parse.mjs",
        "scripts/tool.py",
    ):
        _write(repo_root / relative)

    for relative in (
        ".python-version",
        ".vercelignore",
        "vercel.json",
        "package.json",
        "package-lock.json",
        "pyproject.toml",
        "requirements.txt",
        "uv.lock",
        "ncs_sclass_codes_with_code_no.csv",
        "STRUCTURED_INTERVIEW_GUIDE.md",
        ".vercel/project.json",
    ):
        _write(repo_root / relative)

    _write(repo_root / ".tmp" / "ignored.txt")
    _write(repo_root / "reports" / "ignored.txt")
    _write(repo_root / "tests" / "ignored.txt")
    _write(repo_root / "app" / "local_secret.py", "SECRET")
    _write(repo_root / "api" / ".env", "SECRET")

    result = prepare_sandbox(
        repo_root,
        output_dir,
        tracked_deploy_files=[
            "api/index.py",
            "app/main.py",
            "app/services/alio_sclass_profile.py",
            "app/data/ncs_detail_catalog.json",
            "app/data/ncs_unit_catalog.json",
            "app/data/node_package_lock_attestation.json",
            "app/data/vercel_config_attestation.json",
        ],
    )

    assert result["copied_dirs"] == ["api", "app"]
    assert (output_dir / "api" / "index.py").is_file()
    assert (output_dir / "app" / "main.py").is_file()
    assert (
        output_dir / "app" / "data" / "node_package_lock_attestation.json"
    ).is_file()
    assert (
        output_dir / "app" / "data" / "vercel_config_attestation.json"
    ).is_file()
    assert (output_dir / "scripts" / "kordoc_parse.mjs").is_file()
    assert not (output_dir / "scripts" / "tool.py").exists()
    assert not (output_dir / "app" / "__pycache__").exists()
    assert not (output_dir / "app" / "local_secret.py").exists()
    assert not (output_dir / "api" / ".env").exists()
    assert not (output_dir / ".python-version").exists()
    assert not (output_dir / "pyproject.toml").exists()
    assert not (output_dir / "uv.lock").exists()
    assert (output_dir / ".vercel" / "project.json").is_file()
    assert not (output_dir / ".tmp").exists()
    assert not (output_dir / "reports").exists()
    assert not (output_dir / "tests").exists()


def test_prepare_sandbox_fails_when_required_inputs_are_missing(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    output_dir = tmp_path / "sandbox"

    _write(repo_root / "api" / "index.py")
    _write(repo_root / "app" / "main.py")

    with pytest.raises(FileNotFoundError, match="Missing required Vercel sandbox inputs"):
        prepare_sandbox(
            repo_root,
            output_dir,
            tracked_deploy_files=["api/index.py", "app/main.py"],
        )


def test_git_manifest_excludes_nested_untracked_files(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    _write(repo_root / "api" / "index.py")
    _write(repo_root / "app" / "main.py")
    _write(repo_root / "app" / "local-secret.py", "SECRET")
    subprocess.run(["git", "init", "-q", str(repo_root)], check=True)
    subprocess.run(
        ["git", "-C", str(repo_root), "add", "--", "api/index.py", "app/main.py"],
        check=True,
    )

    tracked = _git_tracked_deploy_files(repo_root)

    assert tracked == ["api/index.py", "app/main.py"]


def test_clean_snapshot_gate_rejects_tracked_worktree_changes(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    _write(repo_root / "app" / "main.py", "committed")
    subprocess.run(["git", "init", "-q", str(repo_root)], check=True)
    subprocess.run(
        ["git", "-C", str(repo_root), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_root), "config", "user.name", "Test User"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_root), "add", "--", "app/main.py"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_root), "commit", "-q", "-m", "fixture"],
        check=True,
    )
    _write(repo_root / "app" / "main.py", "dirty")

    with pytest.raises(RuntimeError, match="clean tracked Git snapshot"):
        _require_clean_git_snapshot(repo_root)
