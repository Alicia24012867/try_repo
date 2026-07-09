"""Benchmark suite expansion helpers for self-evolved ABC experiments."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence


DEFAULT_BENCHMARK_SUITE = "standard_30"
ABC_NATIVE_EXTENSIONS = frozenset((".aig", ".bench", ".blif"))

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
    benchmark_scope = expand_benchmark_suite(repo_root, suite_name)
    updated["benchmark_suite"] = suite_name
    updated["benchmark_scope"] = benchmark_scope
    return with_abc_native_evaluation_scope(updated)


def apply_benchmark_patterns(
    repo_root: Path,
    assignment: Mapping[str, object],
    patterns: Sequence[str],
) -> dict[str, object]:
    updated = dict(assignment)
    benchmark_scope = expand_benchmark_patterns(repo_root, patterns)
    updated["benchmark_suite"] = "custom"
    updated["benchmark_scope"] = benchmark_scope
    return with_abc_native_evaluation_scope(updated)


def with_abc_native_evaluation_scope(
    assignment: Mapping[str, object],
) -> dict[str, object]:
    """Add current ABC-native evaluation scope metadata to an assignment.

    The repository also contains Verilog samples, but the current S5/F7 runner
    invokes ABC directly and reliably supports only ABC-native network files.
    Unsupported frontends are tracked instead of being counted as CEC failures.
    """

    updated = dict(assignment)
    benchmark_scope = [str(item) for item in updated.get("benchmark_scope", ())]
    evaluation_scope = [
        item for item in benchmark_scope if Path(item).suffix.lower() in ABC_NATIVE_EXTENSIONS
    ]
    unsupported_scope = [item for item in benchmark_scope if item not in evaluation_scope]
    updated["benchmark_frontend"] = "abc_native"
    updated["evaluation_benchmark_scope"] = evaluation_scope
    updated["unsupported_benchmark_scope"] = unsupported_scope
    return updated


def promotion_benchmark_count(assignment: Mapping[str, object]) -> int:
    evaluation_scope = assignment.get("evaluation_benchmark_scope", ())
    try:
        count = len(evaluation_scope)  # type: ignore[arg-type]
    except TypeError:
        count = 0
    if count:
        return count
    benchmark_scope = assignment.get("benchmark_scope", ())
    try:
        return len(benchmark_scope)  # type: ignore[arg-type]
    except TypeError:
        return 0


def _repo_relative(repo_root: Path, path: Path) -> Path:
    resolved = path.resolve()
    try:
        return resolved.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise ValueError(f"path escapes repository: {path}") from exc
