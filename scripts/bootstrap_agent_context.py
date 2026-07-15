#!/usr/bin/env python3
"""Provision the pinned, read-only repository context used by coding agents.

The manifest and compact profiles are tracked by this project. Large upstream
checkouts live under ignored local paths and are fetched at exact commits so
prompt construction is reproducible without vendoring third-party histories.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.agents.self_evolved_abc.repository_context import (
    DEFAULT_MANIFEST_PATH,
    RepositorySpec,
    load_repository_specs,
)


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str
    ok: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch/check pinned repositories used as read-only agent context."
    )
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST_PATH,
        help="Repository-relative context manifest.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Read-only verification; never clone, fetch, or check out.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Fetch and move a clean existing context checkout to its pinned commit.",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="NAME",
        help="Provision only a named manifest repository; repeatable.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = args.repo_root.resolve()
    try:
        manifest = _repo_path(repo_root, args.manifest)
        manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
        specs = load_repository_specs(repo_root, manifest)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"context manifest error: {exc}", file=sys.stderr)
        return 2
    selected = set(args.only)
    unknown = sorted(selected - {spec.name for spec in specs})
    if unknown:
        raise SystemExit("unknown repository name(s): " + ", ".join(unknown))
    if selected:
        specs = tuple(spec for spec in specs if spec.name in selected)
    required = len(specs) if selected else _minimum_available(
        manifest_payload.get("minimum_available"),
        configured=len(specs),
    )

    results = tuple(
        ensure_repository(
            repo_root,
            spec,
            check_only=args.check,
            refresh=args.refresh,
        )
        for spec in specs
    )
    for result in results:
        print(f"[{result.status}] {result.name}: {result.detail}")
    ready = sum(result.ok for result in results)
    print(
        f"context repositories ready: {ready}/{len(results)} "
        f"(required: {required})"
    )
    if ready < required:
        print(
            "Prompt profiles remain available, but the Logic Agent's default "
            "minimum-context gate will reject incomplete or revision-mismatched "
            "checkouts.",
            file=sys.stderr,
        )
        return 1
    if ready != len(results):
        print(
            "Minimum prompt context is satisfied; unavailable optional "
            "repositories use their checked-in profile-only fallback."
        )
    return 0


def ensure_repository(
    repo_root: Path,
    spec: RepositorySpec,
    *,
    check_only: bool,
    refresh: bool,
) -> CheckResult:
    profile = _repo_path(repo_root, Path(spec.profile_path))
    if not profile.is_file():
        return CheckResult(
            spec.name,
            "PROFILE_MISSING",
            f"checked-in fallback is missing: {spec.profile_path}",
            False,
        )
    destination = _repo_path(repo_root, Path(spec.local_path))
    if not (destination / ".git").exists():
        if check_only:
            return CheckResult(spec.name, "MISSING", spec.local_path, False)
        if destination.exists() and any(destination.iterdir()):
            return CheckResult(
                spec.name,
                "BLOCKED",
                f"non-empty non-git path: {spec.local_path}",
                False,
            )
        try:
            _initialize_checkout(destination, spec)
        except (OSError, subprocess.CalledProcessError) as exc:
            return CheckResult(spec.name, "FAILED", _command_error(exc), False)

    actual = _git_output(destination, "rev-parse", "HEAD")
    if actual != spec.revision:
        if check_only or not refresh:
            return CheckResult(
                spec.name,
                "REVISION",
                f"expected {spec.revision}, found {actual or 'unavailable'}; use --refresh",
                False,
            )
        dirty = _git_output(destination, "status", "--porcelain")
        if dirty:
            return CheckResult(
                spec.name,
                "BLOCKED",
                "checkout has local changes; refusing to overwrite",
                False,
            )
        try:
            if spec.checkout_mode == "sparse":
                _configure_sparse_checkout(destination, spec.focus_paths)
            _fetch_and_checkout(destination, spec.revision)
        except (OSError, subprocess.CalledProcessError) as exc:
            return CheckResult(spec.name, "FAILED", _command_error(exc), False)

    dirty = _git_output(destination, "status", "--porcelain")
    if dirty:
        return CheckResult(
            spec.name,
            "DIRTY",
            "tracked or untracked local changes make prompt context untrusted",
            False,
        )
    sparse_enabled = _git_output(
        destination, "config", "--bool", "core.sparseCheckout"
    ) == "true"
    if spec.checkout_mode == "full" and sparse_enabled:
        if check_only or not refresh:
            return CheckResult(
                spec.name,
                "INCOMPLETE",
                "full checkout required; rerun with --refresh",
                False,
            )
        try:
            _run(
                (
                    "git", "-C", str(destination), "sparse-checkout", "disable",
                )
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            return CheckResult(spec.name, "FAILED", _command_error(exc), False)

    missing_focus = tuple(
        focus
        for focus in spec.focus_paths
        if not (destination / focus).exists()
    )
    if missing_focus:
        return CheckResult(
            spec.name,
            "INCOMPLETE",
            "missing focus path(s): " + ", ".join(missing_focus[:3]),
            False,
        )
    return CheckResult(
        spec.name,
        "READY",
        f"{spec.revision[:12]} at {spec.local_path}",
        True,
    )


def _initialize_checkout(destination: Path, spec: RepositorySpec) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir(parents=True, exist_ok=True)
    _run(("git", "init", "--quiet", str(destination)))
    _run(("git", "-C", str(destination), "remote", "add", "origin", spec.url))
    _fetch_revision(destination, spec.revision)
    if spec.checkout_mode == "sparse":
        _configure_sparse_checkout(destination, spec.focus_paths)
    _checkout_fetched_revision(destination)


def _fetch_and_checkout(destination: Path, revision: str) -> None:
    _fetch_revision(destination, revision)
    _checkout_fetched_revision(destination)


def _fetch_revision(destination: Path, revision: str) -> None:
    _run(
        (
            "git", "-C", str(destination), "fetch", "--quiet", "--depth", "1",
            "--filter=blob:none", "origin", revision,
        )
    )


def _checkout_fetched_revision(destination: Path) -> None:
    _run(
        (
            "git", "-C", str(destination), "checkout", "--quiet", "--detach",
            "FETCH_HEAD",
        )
    )


def _configure_sparse_checkout(
    destination: Path,
    focus_paths: tuple[str, ...],
) -> None:
    directories = _focus_directories(focus_paths)
    if not directories:
        return
    _run(
        (
            "git", "-C", str(destination), "sparse-checkout", "init", "--cone",
        )
    )
    _run(
        (
            "git", "-C", str(destination), "sparse-checkout", "set", "--cone",
            *directories,
        )
    )


def _focus_directories(focus_paths: tuple[str, ...]) -> tuple[str, ...]:
    directories: list[str] = []
    for raw in focus_paths:
        path = Path(raw)
        name = path.name
        looks_like_file = bool(path.suffix) or name in (
            "Makefile",
            "CMakeLists.txt",
            "module.make",
        ) or name.startswith(("LICENSE", "README", "COPYING"))
        directory = path.parent if looks_like_file else path
        text = str(directory).strip()
        if text not in ("", ".") and text not in directories:
            directories.append(text)
    return tuple(directories)


def _run(command: tuple[str, ...]) -> None:
    subprocess.run(command, check=True)


def _git_output(destination: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ("git", "-C", str(destination), *args),
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _repo_path(repo_root: Path, value: Path) -> Path:
    path = value.resolve() if value.is_absolute() else (repo_root / value).resolve()
    try:
        path.relative_to(repo_root)
    except ValueError as exc:
        raise ValueError(f"context path escapes repository: {value}") from exc
    return path


def _command_error(exc: BaseException) -> str:
    if isinstance(exc, subprocess.CalledProcessError):
        return f"git command exited {exc.returncode}"
    return str(exc)


def _minimum_available(value: object, *, configured: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = configured
    return min(configured, max(0, parsed))


if __name__ == "__main__":
    raise SystemExit(main())
