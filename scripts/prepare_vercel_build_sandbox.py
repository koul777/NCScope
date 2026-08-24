from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from pathlib import PurePosixPath


ROOT_DIRS = ("api", "app")
ROOT_FILES = (
    ".vercelignore",
    "vercel.json",
    "package.json",
    "package-lock.json",
    "requirements.txt",
    "scripts/kordoc_parse.mjs",
    "ncs_sclass_codes_with_code_no.csv",
    "STRUCTURED_INTERVIEW_GUIDE.md",
)
OPTIONAL_FILES = (".vercel/project.json",)
REQUIRED_RUNTIME_FILES = (
    "app/services/alio_sclass_profile.py",
    "app/data/ncs_detail_catalog.json",
    "app/data/ncs_unit_catalog.json",
)


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _git_tracked_deploy_files(repo_root: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-z", "--", *ROOT_DIRS],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("Unable to enumerate Git-tracked Vercel source files")
    output: list[str] = []
    for raw in completed.stdout.decode("utf-8", errors="strict").split("\0"):
        if not raw:
            continue
        if raw != raw.strip() or any(ord(char) < 32 for char in raw):
            raise RuntimeError("Git returned an unsafe Vercel source path")
        relative = raw
        path = PurePosixPath(relative)
        if path.is_absolute() or ".." in path.parts or path.parts[0] not in ROOT_DIRS:
            raise RuntimeError("Git returned an unsafe Vercel source path")
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        output.append(path.as_posix())
    return sorted(set(output))


def _require_clean_git_snapshot(repo_root: Path) -> None:
    """Reject source sandboxes that could mix Git and worktree contents."""

    for arguments in (("diff", "--quiet"), ("diff", "--cached", "--quiet")):
        completed = subprocess.run(
            ["git", "-C", str(repo_root), *arguments],
            capture_output=True,
            check=False,
        )
        if completed.returncode == 1:
            raise RuntimeError(
                "Vercel source sandbox requires a clean tracked Git snapshot"
            )
        if completed.returncode != 0:
            raise RuntimeError("Unable to verify the Git snapshot for deployment")


def _copy_file(source: Path, destination: Path, *, source_root: Path) -> None:
    if source.is_symlink():
        raise RuntimeError("Vercel sandbox inputs must not be symbolic links")
    try:
        source.resolve(strict=True).relative_to(source_root)
    except (FileNotFoundError, ValueError) as exc:
        raise RuntimeError("Vercel sandbox input resolves outside the repository") from exc
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def prepare_sandbox(
    repo_root: Path,
    output_dir: Path,
    *,
    tracked_deploy_files: list[str] | None = None,
) -> dict[str, object]:
    repo_root = repo_root.resolve()
    output_dir = output_dir.resolve()
    if tracked_deploy_files is None:
        _require_clean_git_snapshot(repo_root)
    output_dir.mkdir(parents=True, exist_ok=False)

    copied_dirs: list[str] = []
    copied_files: list[str] = []
    missing_required: list[str] = []
    missing_optional: list[str] = []

    for root_dir in ROOT_DIRS:
        if not (repo_root / root_dir).is_dir():
            missing_required.append(root_dir)

    tracked_files = (
        _git_tracked_deploy_files(repo_root)
        if tracked_deploy_files is None
        else sorted(set(tracked_deploy_files))
    )
    tracked_roots: set[str] = set()
    for relative in tracked_files:
        path = PurePosixPath(str(relative))
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise RuntimeError("Unsafe Vercel source path")
        root_dir = path.parts[0]
        if root_dir not in ROOT_DIRS:
            raise RuntimeError("Vercel source path is outside the deploy roots")
        source = repo_root.joinpath(*path.parts)
        if not source.is_file():
            missing_required.append(path.as_posix())
            continue
        _copy_file(source, output_dir.joinpath(*path.parts), source_root=repo_root)
        tracked_roots.add(root_dir)

    for root_dir in ROOT_DIRS:
        if root_dir not in tracked_roots:
            if root_dir not in missing_required:
                missing_required.append(root_dir)
        else:
            copied_dirs.append(root_dir)

    for relative in REQUIRED_RUNTIME_FILES:
        if not output_dir.joinpath(*PurePosixPath(relative).parts).is_file():
            missing_required.append(relative)

    for relative in ROOT_FILES:
        source = repo_root / relative
        if not source.is_file():
            missing_required.append(relative)
            continue
        _copy_file(source, output_dir / relative, source_root=repo_root)
        copied_files.append(relative)

    for relative in OPTIONAL_FILES:
        source = repo_root / relative
        if source.is_file():
            _copy_file(source, output_dir / relative, source_root=repo_root)
            copied_files.append(relative)
        else:
            missing_optional.append(relative)

    if missing_required:
        raise FileNotFoundError(
            "Missing required Vercel sandbox inputs: " + ", ".join(sorted(missing_required))
        )

    return {
        "repo_root": str(repo_root),
        "output_dir": str(output_dir),
        "copied_dirs": copied_dirs,
        "copied_files": copied_files,
        "tracked_deploy_file_count": len(tracked_files),
        "missing_optional_files": missing_optional,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create a clean Vercel build sandbox with only the files required "
            "for NCScope deployment."
        )
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="repository root to snapshot (default: current directory)",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="destination directory (default: .local/vercel-build-sandbox-<utc timestamp>)",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    repo_root = Path(args.repo_root)
    output_dir = Path(args.output_dir) if args.output_dir else repo_root / ".local" / f"vercel-build-sandbox-{_timestamp()}"
    result = prepare_sandbox(repo_root, output_dir)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
