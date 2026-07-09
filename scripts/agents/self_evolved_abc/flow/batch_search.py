"""Batch source-patch search for low-token Flow Agent evolution.

This runner turns one reviewed assignment into several deterministic source
patch variants, then optionally evaluates them with the existing S4/S5/review
gates. It is intentionally model-free: use an LLM to propose or revise search
spaces, but let this script expand and test the concrete candidates.
"""

from __future__ import annotations

import argparse
import csv
import difflib
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

from scripts.agents.self_evolved_abc.cycle_context import CycleContext
from scripts.agents.self_evolved_abc.flow.assignment import (
    FLOW_CYCLE_DIRS,
    normalize_flow_assignment_scope,
)
from scripts.agents.self_evolved_abc.flow.contracts import (
    DEFAULT_EVAL_FLOW_COMMANDS,
    FLOW_CANDIDATE_SOURCE_PATCH_DIFF,
    FLOW_SOURCE_TOUCHPOINTS,
    FLOWTUNE_ABCI_SCOPE,
    FLOWTUNE_SOURCE_SCOPE_PRIMARY,
)
from scripts.agents.self_evolved_abc.flow.lineage import source_context_path
from scripts.agents.self_evolved_abc.flow.materialization import (
    candidate_flow_relative_path,
    render_abc_flow_script,
)
from scripts.agents.self_evolved_abc.flow.source_patch import (
    source_patch_diff_relative_path,
)


CANDIDATE_ID = "candidate_001"
CSW_CORE = Path("third_party/FlowTune/src/src/opt/csw/cswCore.c")
ABC_FXU = Path("third_party/FlowTune/src/src/base/abci/abcFxu.c")
ABC_COMMANDS = Path("third_party/FlowTune/src/src/base/abci/abc.c")
FXU_SELECT = Path("third_party/FlowTune/src/src/opt/fxu/fxuSelect.c")
SUMMARY_FIELDS = (
    "batch_id",
    "cycle_id",
    "variant_id",
    "decision",
    "promotion_allowed",
    "average_and_improve_pct",
    "total_and_delta_candidate_minus_baseline",
    "improved_benchmark_count",
    "regressed_benchmark_count",
    "unchanged_benchmark_count",
    "target_file",
    "description",
)


@dataclass(frozen=True)
class PatchVariant:
    variant_id: str
    description: str
    target_file: str
    rationale: str
    patch_text: str


@dataclass(frozen=True)
class BatchItem:
    cycle_id: str
    candidate_id: str
    variant_id: str
    description: str
    target_file: str
    assignment_path: str
    patch_path: str


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate and optionally run deterministic Flow patch batches."
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--base-assignment",
        type=Path,
        help="Assignment whose scope, benchmark set, and champion baseline are reused.",
    )
    parser.add_argument(
        "--start-cycle",
        default="cycle_010",
        help="First generated cycle id. Later variants increment this id.",
    )
    parser.add_argument(
        "--batch-id",
        default=None,
        help="Directory name under experiments/batches/. Defaults to <start-cycle>_flow_batch.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Existing batch manifest to run or summarize.",
    )
    parser.add_argument(
        "--variant-set",
        choices=("flow_seed", "flow_wide"),
        default="flow_seed",
        help="Built-in deterministic search space. Use flow_wide after no champion.",
    )
    parser.add_argument(
        "--include-variants",
        default="",
        help=(
            "Comma-separated variant ids to generate. Leave empty to generate "
            "the full variant set."
        ),
    )
    parser.add_argument(
        "--benchmark-glob",
        action="append",
        default=None,
        help=(
            "Repo-relative benchmark glob overriding the base assignment's "
            "benchmark_scope. Can be repeated."
        ),
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--run",
        action="store_true",
        help="Run S4/S5/review for generated or manifest candidates.",
    )
    parser.add_argument(
        "--summarize-only",
        action="store_true",
        help="Only summarize an existing manifest; do not generate or run.",
    )
    parser.add_argument("--build-candidate-binary", action="store_true")
    parser.add_argument("--build-jobs", type=int, default=4)
    parser.add_argument("--build-timeout-seconds", type=float, default=900.0)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--cec-timeout-seconds", type=float, default=300.0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    repo_root = args.repo_root.resolve()
    manifest_path: Path

    if args.run and not args.summarize_only and not args.build_candidate_binary:
        print("batch_search: --run requires --build-candidate-binary")
        return 2

    if args.manifest is not None:
        manifest_path = repo_path(repo_root, args.manifest)
        manifest = load_manifest(manifest_path)
    else:
        if args.summarize_only:
            print("batch_search: --summarize-only requires --manifest")
            return 2
        if args.base_assignment is None:
            print("batch_search: --base-assignment is required when generating")
            return 2
        context = CycleContext.from_assignment_file(
            repo_root,
            repo_path(repo_root, args.base_assignment),
        )
        if args.benchmark_glob:
            context = CycleContext(
                repo_root,
                apply_benchmark_globs(
                    repo_root,
                    context.assignment,
                    args.benchmark_glob,
                ),
            )
        manifest = generate_batch(
            context=context,
            start_cycle=args.start_cycle,
            batch_id=args.batch_id or f"{args.start_cycle}_flow_batch",
            variant_set=args.variant_set,
            include_variants=parse_variant_filter(args.include_variants),
            force=args.force,
        )
        manifest_path = repo_root / manifest["manifest_path"]

    if args.run and not args.summarize_only:
        run_batch(
            repo_root=repo_root,
            manifest=manifest,
            build_candidate_binary=args.build_candidate_binary,
            build_jobs=max(1, args.build_jobs),
            build_timeout_seconds=args.build_timeout_seconds,
            timeout_seconds=args.timeout_seconds,
            cec_timeout_seconds=args.cec_timeout_seconds,
        )

    summary_path = summarize_batch(repo_root=repo_root, manifest=manifest)
    print(f"batch_manifest: {repo_root / manifest['manifest_path']}")
    print(f"batch_summary: {summary_path}")
    return 0


def generate_batch(
    *,
    context: CycleContext,
    start_cycle: str,
    batch_id: str,
    variant_set: str,
    include_variants: set[str],
    force: bool,
) -> dict[str, Any]:
    variants = build_variants(context, variant_set)
    if include_variants:
        variants = [
            variant for variant in variants if variant.variant_id in include_variants
        ]
    if not variants:
        raise ValueError("no batch variants were generated for the current base source")

    batch_dir = context.repo_root / "experiments" / "batches" / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    items: list[BatchItem] = []
    current_number = cycle_number(start_cycle)
    width = cycle_width(start_cycle)
    prefix = cycle_prefix(start_cycle)

    for offset, variant in enumerate(variants):
        cycle_id = f"{prefix}_{current_number + offset:0{width}d}"
        cycle_dir = context.repo_root / "experiments" / cycle_id
        assignment = build_variant_assignment(context, cycle_id, variant, batch_id)
        assignment_path = (
            cycle_dir / "agents" / "assignments" / f"{CANDIDATE_ID}.json"
        )
        patch_path = context.repo_root / source_patch_diff_relative_path(
            CycleContext(context.repo_root, assignment)
        )
        if cycle_dir.exists() and not force:
            raise FileExistsError(
                f"generated cycle already exists: {cycle_dir.relative_to(context.repo_root)}"
            )

        create_cycle_dirs(cycle_dir)
        write_json(assignment_path, assignment)
        patch_path.parent.mkdir(parents=True, exist_ok=True)
        patch_path.write_text(variant.patch_text.rstrip() + "\n", encoding="utf-8")
        write_candidate_notes(
            context.repo_root,
            cycle_id=cycle_id,
            variant=variant,
            assignment=assignment,
        )
        write_flow_recipe(context.repo_root, assignment)
        items.append(
            BatchItem(
                cycle_id=cycle_id,
                candidate_id=CANDIDATE_ID,
                variant_id=variant.variant_id,
                description=variant.description,
                target_file=variant.target_file,
                assignment_path=str(assignment_path.relative_to(context.repo_root)),
                patch_path=str(patch_path.relative_to(context.repo_root)),
            )
        )

    manifest = {
        "batch_id": batch_id,
        "variant_set": variant_set,
        "include_variants": sorted(include_variants),
        "base_assignment": str(
            context.repo_root
            / "experiments"
            / context.cycle_id
            / "agents"
            / "assignments"
            / f"{context.candidate_id}.json"
        ),
        "base_cycle_id": context.cycle_id,
        "benchmark_scope": list(context.assignment.get("benchmark_scope", ())),
        "manifest_path": str(
            (batch_dir / "manifest.json").relative_to(context.repo_root)
        ),
        "items": [asdict(item) for item in items],
    }
    write_json(batch_dir / "manifest.json", manifest)
    return manifest


def build_variants(context: CycleContext, variant_set: str) -> list[PatchVariant]:
    if variant_set not in ("flow_seed", "flow_wide"):
        raise ValueError(f"unsupported variant set: {variant_set}")
    variants: list[PatchVariant] = []
    variants.extend(build_csw_variants(context))
    variants.extend(build_fxu_variants(context, wide=variant_set == "flow_wide"))
    if variant_set == "flow_wide":
        variants.extend(build_abc_csweep_default_variants(context))
        variants.extend(build_fxu_select_variants(context))
    return variants


def build_csw_variants(context: CycleContext) -> list[PatchVariant]:
    source = source_text(context, CSW_CORE)
    cut_floor = max_int_match(source, r"nCutsMax\s*<\s*(\d+)")
    leaf_floor = max_int_match(source, r"nLeafMax\s*<\s*(\d+)")
    candidates: tuple[tuple[int | None, int | None], ...] = (
        (10, None),
        (12, None),
        (16, None),
        (None, 8),
        (12, 8),
        (16, 8),
        (20, 10),
    )
    variants: list[PatchVariant] = []
    for cuts, leaves in candidates:
        if cuts is not None and cuts <= cut_floor:
            continue
        if leaves is not None and leaves <= leaf_floor:
            continue
        insertion = []
        if cuts is not None:
            insertion.append(
                f"    if ( nCutsMax < {cuts} )\n"
                f"        nCutsMax = {cuts};\n"
            )
        if leaves is not None:
            insertion.append(
                f"    if ( nLeafMax < {leaves} )\n"
                f"        nLeafMax = {leaves};\n"
            )
        new_source = insert_after_clock(
            source,
            "".join(insertion),
        )
        cut_label = str(cuts) if cuts is not None else "keep"
        leaf_label = str(leaves) if leaves is not None else "keep"
        variants.append(
            PatchVariant(
                variant_id=f"csweep_floor_c{cut_label}_l{leaf_label}",
                description=(
                    f"Raise csweep cut/leaf floors to {cut_label}/{leaf_label} "
                    "before Csw_ManStart."
                ),
                target_file=str(CSW_CORE),
                rationale=(
                    "Expands the cut-sweeping search space using an existing "
                    "csweep parameter path reached by the evaluation flow."
                ),
                patch_text=unified_diff(CSW_CORE, source, new_source),
            )
        )
    return variants


def build_fxu_variants(context: CycleContext, *, wide: bool) -> list[PatchVariant]:
    source = source_text(context, ABC_FXU)
    specs: list[tuple[str, str, str, str]] = [
        (
            "fx_litcount3",
            "Decrease fx LitCountMax from 4 to 3 to prefer smaller divisors.",
            "p->LitCountMax=      4;",
            "p->LitCountMax=      3;",
        ),
        (
            "fx_litcount6",
            "Increase fx LitCountMax from 4 to 6.",
            "p->LitCountMax=      4;",
            "p->LitCountMax=      6;",
        ),
        (
            "fx_weightmin1",
            "Require positive-gain fx divisors by setting WeightMin to 1.",
            "p->WeightMin  =      0;",
            "p->WeightMin  =      1;",
        ),
        (
            "fx_use_zero_gain",
            "Allow fx zero-gain divisors by enabling fUse0.",
            "p->fUse0      =      0;",
            "p->fUse0      =      1;",
        ),
    ]
    if wide:
        specs.extend(
            (
                (
                    "fx_only_single",
                    "Restrict fx to single-cube divisors.",
                    "p->fOnlyS     =      0;",
                    "p->fOnlyS     =      1;",
                ),
                (
                    "fx_only_double",
                    "Restrict fx to double-cube divisors.",
                    "p->fOnlyD     =      0;",
                    "p->fOnlyD     =      1;",
                ),
                (
                    "fx_no_complement",
                    "Disable fx complement-pair selection.",
                    "p->fUseCompl  =      1;",
                    "p->fUseCompl  =      0;",
                ),
            )
        )
    variants: list[PatchVariant] = []
    for variant_id, description, old, new in specs:
        if old not in source:
            continue
        new_source = source.replace(old, new, 1)
        if new_source == source:
            continue
        variants.append(
            PatchVariant(
                variant_id=variant_id,
                description=description,
                target_file=str(ABC_FXU),
                rationale=(
                    "Changes an existing fx command default parameter reached "
                    "at the start of the evaluation flow."
                ),
                patch_text=unified_diff(ABC_FXU, source, new_source),
            )
        )
    return variants


def build_abc_csweep_default_variants(context: CycleContext) -> list[PatchVariant]:
    source = source_text(context, ABC_COMMANDS)
    old = "    nCutsMax  =  8;\n    nLeafMax  =  6;"
    candidates = (
        (6, 5),
        (10, 6),
        (12, 6),
        (12, 8),
        (16, 6),
        (16, 8),
    )
    variants: list[PatchVariant] = []
    if old not in source:
        return variants
    for cuts, leaves in candidates:
        new = f"    nCutsMax  = {cuts:2d};\n    nLeafMax  = {leaves:2d};"
        new_source = source.replace(old, new, 1)
        variants.append(
            PatchVariant(
                variant_id=f"csweep_default_c{cuts}_l{leaves}",
                description=(
                    f"Change the csweep command default cut/leaf limits to "
                    f"{cuts}/{leaves}."
                ),
                target_file=str(ABC_COMMANDS),
                rationale=(
                    "Tests the command-level default used by the evaluation "
                    "flow's bare `csweep` command, including less-aggressive "
                    "settings that can preserve structure for later passes."
                ),
                patch_text=unified_diff(ABC_COMMANDS, source, new_source),
            )
        )
    return variants


def build_fxu_select_variants(context: CycleContext) -> list[PatchVariant]:
    source = source_text(context, FXU_SELECT)
    old = "#define MAX_SIZE_LOOKAHEAD      20"
    variants: list[PatchVariant] = []
    if old not in source:
        return variants
    for value in (5, 10, 40, 80):
        new = f"#define MAX_SIZE_LOOKAHEAD      {value}"
        variants.append(
            PatchVariant(
                variant_id=f"fx_lookahead{value}",
                description=f"Set fx complement lookahead window to {value}.",
                target_file=str(FXU_SELECT),
                rationale=(
                    "Sweeps the fx selector breadth in both smaller and larger "
                    "directions; prior larger-only probing produced zero delta."
                ),
                patch_text=unified_diff(
                    FXU_SELECT,
                    source,
                    source.replace(old, new, 1),
                ),
            )
        )
    return variants


def build_variant_assignment(
    base_context: CycleContext,
    cycle_id: str,
    variant: PatchVariant,
    batch_id: str,
) -> dict[str, object]:
    current = dict(base_context.assignment)
    current.pop("allowed_to_edit", None)
    assignment = {
        **current,
        "cycle_id": cycle_id,
        "candidate_id": CANDIDATE_ID,
        "previous_cycle_id": base_context.cycle_id,
        "agent_name": current.get("agent_name", "flow_agent"),
        "paper_role": current.get("paper_role", "Flow Agent"),
        "source_patch_mode": FLOW_CANDIDATE_SOURCE_PATCH_DIFF,
        "subsystem": current.get("subsystem", FLOWTUNE_SOURCE_SCOPE_PRIMARY),
        "source_patch_allowed_roots": current.get(
            "source_patch_allowed_roots",
            [FLOWTUNE_SOURCE_SCOPE_PRIMARY, FLOWTUNE_ABCI_SCOPE],
        ),
        "evaluation_flow_commands": current.get(
            "evaluation_flow_commands",
            list(DEFAULT_EVAL_FLOW_COMMANDS),
        ),
        "flow_source_touchpoints": current.get(
            "flow_source_touchpoints",
            dict(FLOW_SOURCE_TOUCHPOINTS),
        ),
        "planner_hypothesis": (
            "Model-free batch search candidate. "
            f"Batch={batch_id}; variant={variant.variant_id}. "
            f"{variant.rationale} {variant.description}"
        ),
        "batch_search": {
            "batch_id": batch_id,
            "variant_id": variant.variant_id,
            "target_file": variant.target_file,
            "description": variant.description,
            "rationale": variant.rationale,
        },
    }
    return normalize_flow_assignment_scope(assignment)


def run_batch(
    *,
    repo_root: Path,
    manifest: dict[str, Any],
    build_candidate_binary: bool,
    build_jobs: int,
    build_timeout_seconds: float,
    timeout_seconds: float,
    cec_timeout_seconds: float,
) -> None:
    for item in manifest.get("items", ()):
        assignment = repo_path(repo_root, Path(item["assignment_path"]))
        cycle_id = str(item["cycle_id"])
        print(f"\n=== batch candidate {cycle_id} {item['variant_id']} ===")
        write_flow_recipe_from_assignment(repo_root, assignment)

        source_cmd = [
            sys.executable,
            "-B",
            "-m",
            "scripts.agents.self_evolved_abc.flow.source_patch_runner",
            "--repo-root",
            str(repo_root),
            "--assignment",
            str(assignment.relative_to(repo_root)),
            "--apply-candidate-patch",
            "--record-build-gate",
        ]
        if build_candidate_binary:
            source_cmd.extend(
                (
                    "--build-candidate-binary",
                    "--build-jobs",
                    str(build_jobs),
                    "--build-timeout-seconds",
                    f"{build_timeout_seconds:g}",
                )
            )
        run_command(repo_root, source_cmd)

        compare_cmd = [
            sys.executable,
            "-B",
            "-m",
            "scripts.agents.self_evolved_abc.flow.implementation_compare",
            "--repo-root",
            str(repo_root),
            "--assignment",
            str(assignment.relative_to(repo_root)),
            "--timeout-seconds",
            f"{timeout_seconds:g}",
            "--cec-timeout-seconds",
            f"{cec_timeout_seconds:g}",
        ]
        run_command(repo_root, compare_cmd)

        review_cmd = [
            sys.executable,
            "-B",
            "-m",
            "scripts.agents.self_evolved_abc.flow.review",
            "--repo-root",
            str(repo_root),
            "--assignment",
            str(assignment.relative_to(repo_root)),
        ]
        run_command(repo_root, review_cmd)


def summarize_batch(*, repo_root: Path, manifest: dict[str, Any]) -> Path:
    batch_dir = repo_path(repo_root, Path(manifest["manifest_path"])).parent
    rows: list[dict[str, object]] = []
    for item in manifest.get("items", ()):
        cycle_id = str(item["cycle_id"])
        review = load_json(
            repo_root
            / "experiments"
            / cycle_id
            / "impl_compare"
            / "comparison"
            / "review_decision.json"
        )
        rows.append(
            {
                "batch_id": manifest["batch_id"],
                "cycle_id": cycle_id,
                "variant_id": item["variant_id"],
                "decision": review.get("decision", "missing") if review else "missing",
                "promotion_allowed": (
                    review.get("promotion_allowed", "") if review else ""
                ),
                "average_and_improve_pct": (
                    review.get("average_and_improve_pct", "") if review else ""
                ),
                "total_and_delta_candidate_minus_baseline": (
                    review.get("total_and_delta_candidate_minus_baseline", "")
                    if review
                    else ""
                ),
                "improved_benchmark_count": (
                    review.get("improved_benchmark_count", "") if review else ""
                ),
                "regressed_benchmark_count": (
                    review.get("regressed_benchmark_count", "") if review else ""
                ),
                "unchanged_benchmark_count": (
                    review.get("unchanged_benchmark_count", "") if review else ""
                ),
                "target_file": item["target_file"],
                "description": item["description"],
            }
        )

    summary_path = batch_dir / "summary.csv"
    with summary_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    write_winner(batch_dir, rows)
    return summary_path


def write_winner(batch_dir: Path, rows: Sequence[dict[str, object]]) -> None:
    promoted = [
        row for row in rows if str(row.get("promotion_allowed", "")).lower() == "true"
    ]
    ordered = sorted(
        promoted or rows,
        key=lambda row: (
            float_or_neg(row.get("average_and_improve_pct")),
            -float_or_pos(row.get("total_and_delta_candidate_minus_baseline")),
            float_or_neg(row.get("improved_benchmark_count")),
        ),
        reverse=True,
    )
    payload = {
        "winner": ordered[0] if ordered else None,
        "promotion_found": bool(promoted),
    }
    write_json(batch_dir / "winner.json", payload)


def write_candidate_notes(
    repo_root: Path,
    *,
    cycle_id: str,
    variant: PatchVariant,
    assignment: dict[str, object],
) -> None:
    base = repo_root / "experiments" / cycle_id / "agents"
    text = "\n".join(
        (
            f"# Batch Flow Candidate -- {cycle_id} {CANDIDATE_ID}",
            "",
            f"- Variant: `{variant.variant_id}`",
            f"- Target: `{variant.target_file}`",
            f"- Description: {variant.description}",
            "- Source: deterministic batch search, no model call",
            "",
            "## Rationale",
            "",
            variant.rationale,
            "",
            "## Baseline",
            "",
            f"- Baseline kind: `{assignment.get('baseline_kind', 'vanilla')}`",
            f"- Base source root: `{assignment.get('base_source_root', 'repo source')}`",
            f"- Baseline ABC binary: `{assignment.get('baseline_abc_bin', 'default')}`",
            "",
        )
    )
    for subdir in ("plans", "candidate_changes", "feedback"):
        path = base / subdir / f"{CANDIDATE_ID}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    rules = base / "rule_updates" / f"{CANDIDATE_ID}.md"
    rules.parent.mkdir(parents=True, exist_ok=True)
    rules.write_text(
        "# Batch Rule Updates\n\n- No active rulebase update was applied.\n",
        encoding="utf-8",
    )


def write_flow_recipe_from_assignment(repo_root: Path, assignment_path: Path) -> None:
    payload = json.loads(assignment_path.read_text(encoding="utf-8"))
    write_flow_recipe(repo_root, payload)


def write_flow_recipe(repo_root: Path, assignment: dict[str, object]) -> None:
    context = CycleContext(repo_root, assignment)
    path = repo_root / candidate_flow_relative_path(context)
    commands = tuple(
        str(command)
        for command in assignment.get("evaluation_flow_commands", DEFAULT_EVAL_FLOW_COMMANDS)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_abc_flow_script(commands), encoding="utf-8")


def create_cycle_dirs(cycle_dir: Path) -> None:
    for relative in FLOW_CYCLE_DIRS:
        path = cycle_dir / relative
        path.mkdir(parents=True, exist_ok=True)
        (path / ".gitkeep").touch(exist_ok=True)


def source_text(context: CycleContext, repo_relative: Path) -> str:
    return source_path(context, repo_relative).read_text(
        encoding="utf-8",
        errors="replace",
    )


def source_path(context: CycleContext, repo_relative: Path) -> Path:
    return source_context_path(context, repo_relative)


def insert_after_clock(source: str, insertion: str) -> str:
    needle = "clk = Abc_Clock();\n"
    if needle not in source:
        raise ValueError("could not locate Csw_Sweep clock initialization")
    return source.replace(needle, needle + insertion, 1)


def unified_diff(path: Path, old: str, new: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


def max_int_match(text: str, pattern: str) -> int:
    values = [int(match) for match in re.findall(pattern, text)]
    return max(values) if values else 0


def cycle_prefix(cycle_id: str) -> str:
    prefix, _, _number = cycle_id.rpartition("_")
    if not prefix:
        raise ValueError(f"invalid cycle id: {cycle_id}")
    return prefix


def cycle_number(cycle_id: str) -> int:
    _prefix, _sep, number = cycle_id.rpartition("_")
    if not number.isdigit():
        raise ValueError(f"invalid cycle id: {cycle_id}")
    return int(number)


def cycle_width(cycle_id: str) -> int:
    _prefix, _sep, number = cycle_id.rpartition("_")
    return len(number)


def repo_path(repo_root: Path, path: Path) -> Path:
    resolved = path if path.is_absolute() else repo_root / path
    resolved = resolved.resolve()
    try:
        resolved.relative_to(repo_root)
    except ValueError as exc:
        raise ValueError(f"path escapes repository: {path}") from exc
    return resolved


def parse_variant_filter(text: str) -> set[str]:
    return {item.strip() for item in text.split(",") if item.strip()}


def apply_benchmark_globs(
    repo_root: Path,
    assignment: dict[str, Any],
    patterns: Sequence[str],
) -> dict[str, Any]:
    matches: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        if not pattern.strip():
            continue
        for path in sorted(repo_root.glob(pattern)):
            if not path.is_file():
                continue
            relative = str(repo_path(repo_root, path).relative_to(repo_root))
            if relative in seen:
                continue
            seen.add(relative)
            matches.append(relative)
    if not matches:
        joined = ", ".join(patterns)
        raise ValueError(f"benchmark glob matched no files: {joined}")
    updated = dict(assignment)
    updated["benchmark_scope"] = matches
    return normalize_flow_assignment_scope(updated)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def load_manifest(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    if payload is None:
        raise ValueError(f"invalid batch manifest: {path}")
    return payload


def run_command(repo_root: Path, command: Sequence[str]) -> None:
    print("running:", " ".join(command))
    completed = subprocess.run(tuple(command), cwd=repo_root, check=False)
    if completed.returncode != 0:
        print(f"batch_search: command returned {completed.returncode}; continuing")


def float_or_neg(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("-inf")


def float_or_pos(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("inf")


if __name__ == "__main__":
    raise SystemExit(main())
