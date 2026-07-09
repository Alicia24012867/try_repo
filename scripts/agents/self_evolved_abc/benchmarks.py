"""Benchmark suite expansion helpers for self-evolved ABC experiments."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence


DEFAULT_BENCHMARK_SUITE = "standard_30"

BENCHMARK_SUITES: Mapping[str, tuple[str, ...]] = {
    "epfl_10": (
        "benchmarks/epfl/*.blif",
    ),
    "standard_30": (
        "benchmarks/epfl/*.blif",
        "benchmarks/iscas85/*.blif",
        "benchmarks/iscas89/*.blif",
    ),
    "large_70": (
        "benchmarks/epfl/*.blif",
        "benchmarks/iscas85/*.blif",
        "benchmarks/iscas89/*.blif",
        "benchmarks/iscas99/*.v",
        "benchmarks/itc99/*.v",
        "benchmarks/vtr/*.v",
        "benchmarks/arithmetic/*.v",
    ),
}


def benchmark_suite_names() -> tuple[str, ...]:
    return tuple(BENCHMARK_SUITES)


def benchmark_suite_patterns(name: str) -> tuple[str, ...]:
    try:
        return BENCHMARK_SUITES[name]
    except KeyError as exc:
        choices = ", ".join(benchmark_suite_names())
        raise ValueError(f"unknown benchmark suite {name!r}; choices: {choices}") from exc


def expand_benchmark_suite(repo_root: Path, name: str) -> list[str]:
    return expand_benchmark_patterns(repo_root, benchmark_suite_patterns(name))


def expand_benchmark_patterns(
    repo_root: Path,
    patterns: Sequence[str],
) -> list[str]:
    """Expand repo-relative glob patterns into sorted, de-duplicated paths."""

    matches: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        if not pattern.strip():
            continue
        for path in sorted(repo_root.glob(pattern)):
            if not path.is_file():
                continue
            relative = str(_repo_relative(repo_root, path))
            if relative in seen:
                continue
            seen.add(relative)
            matches.append(relative)
    if not matches:
        joined = ", ".join(patterns)
        raise ValueError(f"benchmark pattern matched no files: {joined}")
    return matches


def apply_benchmark_suite(
    repo_root: Path,
    assignment: Mapping[str, object],
    suite_name: str,
) -> dict[str, object]:
    updated = dict(assignment)
    updated["benchmark_suite"] = suite_name
    updated["benchmark_scope"] = expand_benchmark_suite(repo_root, suite_name)
    return updated


def apply_benchmark_patterns(
    repo_root: Path,
    assignment: Mapping[str, object],
    patterns: Sequence[str],
) -> dict[str, object]:
    updated = dict(assignment)
    updated["benchmark_suite"] = "custom"
    updated["benchmark_scope"] = expand_benchmark_patterns(repo_root, patterns)
    return updated


def _repo_relative(repo_root: Path, path: Path) -> Path:
    resolved = path.resolve()
    try:
        return resolved.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise ValueError(f"path escapes repository: {path}") from exc
