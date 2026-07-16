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
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from scripts.agents.self_evolved_abc.benchmarks import (
    apply_benchmark_patterns as apply_benchmark_patterns_to_assignment,
    apply_benchmark_suite,
    benchmark_suite_names,
    with_abc_native_evaluation_scope,
)
from scripts.agents.self_evolved_abc.cycle_context import CycleContext
from scripts.agents.self_evolved_abc.flow.assignment import (
    FLOW_CYCLE_DIRS,
    normalize_flow_assignment_scope,
)
from scripts.agents.self_evolved_abc.flow.contracts import (
    CANDIDATE_BUILD_READY_STATUSES,
    DEFAULT_EVAL_FLOW_COMMANDS,
    FLOW_CANDIDATE_SOURCE_PATCH_DIFF,
    FLOW_SOURCE_TOUCHPOINTS,
    FLOWTUNE_ABCI_SCOPE,
    FLOWTUNE_SOURCE_SCOPE_PRIMARY,
)
from scripts.agents.self_evolved_abc.flow.lineage import source_context_path
from scripts.agents.self_evolved_abc.flow.paths import impl_compare_root
from scripts.agents.self_evolved_abc.flow.materialization import (
    candidate_flow_relative_path,
    render_abc_flow_script,
)
from scripts.agents.self_evolved_abc.flow.source_patch import (
    source_patch_diff_relative_path,
)


CANDIDATE_ID = "candidate_001"
BATCH_LINEAGE_SCHEMA_VERSION = 2
CSW_CORE = Path("third_party/FlowTune/src/src/opt/csw/cswCore.c")
ABC_FXU = Path("third_party/FlowTune/src/src/base/abci/abcFxu.c")
ABC_COMMANDS = Path("third_party/FlowTune/src/src/base/abci/abc.c")
FXU_SELECT = Path("third_party/FlowTune/src/src/opt/fxu/fxuSelect.c")
RWR_EVA = Path("third_party/FlowTune/src/src/opt/rwr/rwrEva.c")
DAR_CORE = Path("third_party/FlowTune/src/src/opt/dar/darCore.c")
DAR_REFACT = Path("third_party/FlowTune/src/src/opt/dar/darRefact.c")
RES_WIN = Path("third_party/FlowTune/src/src/opt/res/resWin.c")
CSW_FLOOR_PATTERN = re.compile(
    r"(clk = Abc_Clock\(\);\n)"
    r"(?:    if \( nCutsMax < (\d+) \)\n"
    r"        nCutsMax = \d+;\n)?"
    r"(?:    if \( nLeafMax < (\d+) \)\n"
    r"        nLeafMax = \d+;\n)?"
)
SUMMARY_FIELDS = (
    "batch_id",
    "cycle_id",
    "variant_id",
    "decision",
    "promotion_allowed",
    "build_status",
    "cec_pass_count",
    "cec_total_count",
    "correctness_backed_rows",
    "evaluation_benchmark_count",
    "average_and_improve_pct",
    "total_and_delta_candidate_minus_baseline",
    "improved_benchmark_count",
    "regressed_benchmark_count",
    "unchanged_benchmark_count",
    "total_depth_delta_candidate_minus_baseline",
    "structural_proxy_reward_pct",
    "retained_for_synergy",
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
        "--expected-lineage-hash",
        default="",
        help=(
            "Fail closed unless the generated or loaded manifest is bound to "
            "this SHA-256 lineage. Automatic planner batches always set this."
        ),
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
        "--target-command",
        choices=("fx", "rewrite", "resub", "dc2", "csweep", "refactor"),
        default="",
        help="Generate only variants that affect this evaluation-flow command.",
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
    parser.add_argument(
        "--benchmark-suite",
        choices=benchmark_suite_names(),
        default=None,
        help=(
            "Named benchmark suite overriding the base assignment scope. "
            "Use large_70 for the full local benchmark sample."
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
        try:
            manifest = load_manifest(manifest_path)
            validate_manifest_lineage(
                manifest,
                expected_lineage_hash=args.expected_lineage_hash,
            )
            validate_manifest_base_assignment(repo_root, manifest)
        except (OSError, ValueError) as exc:
            print(f"batch_search: {exc}")
            return 2
    else:
        if args.summarize_only:
            print("batch_search: --summarize-only requires --manifest")
            return 2
        if args.base_assignment is None:
            print("batch_search: --base-assignment is required when generating")
            return 2
        base_assignment_path = repo_path(repo_root, args.base_assignment)
        context = CycleContext.from_assignment_file(repo_root, base_assignment_path)
        if args.benchmark_suite and args.benchmark_glob:
            print("batch_search: use either --benchmark-suite or --benchmark-glob")
            return 2
        if args.benchmark_suite:
            context = CycleContext(
                repo_root,
                normalize_flow_assignment_scope(
                    apply_benchmark_suite(
                        repo_root,
                        context.assignment,
                        args.benchmark_suite,
                    )
                ),
            )
        elif args.benchmark_glob:
            context = CycleContext(
                repo_root,
                normalize_flow_assignment_scope(
                    apply_benchmark_patterns_to_assignment(
                        repo_root,
                        context.assignment,
                        args.benchmark_glob,
                    )
                ),
            )
        try:
            manifest = generate_batch(
                context=context,
                base_assignment_path=base_assignment_path,
                start_cycle=args.start_cycle,
                batch_id=args.batch_id or f"{args.start_cycle}_flow_batch",
                variant_set=args.variant_set,
                include_variants=parse_variant_filter(args.include_variants),
                target_command=args.target_command,
                force=args.force,
            )
            validate_manifest_lineage(
                manifest,
                expected_lineage_hash=args.expected_lineage_hash,
            )
            validate_manifest_base_assignment(repo_root, manifest)
        except (OSError, ValueError) as exc:
            print(f"batch_search: {exc}")
            return 2
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
    base_assignment_path: Path,
    start_cycle: str,
    batch_id: str,
    variant_set: str,
    include_variants: set[str],
    target_command: str = "",
    force: bool,
) -> dict[str, Any]:
    variants = build_variants(context, variant_set, target_command=target_command)
    if include_variants:
        variants = [
            variant for variant in variants if variant.variant_id in include_variants
        ]
    if not variants:
        raise ValueError("no batch variants were generated for the current base source")
    lineage = build_batch_lineage(
        context.assignment,
        variant_set=variant_set,
        target_command=target_command,
        include_variants=include_variants,
        variant_space=describe_variant_space(context, variants),
    )
    lineage_hash = hash_batch_lineage(lineage)

    batch_dir = context.repo_root / "experiments" / "batches" / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    items: list[BatchItem] = []
    current_number = cycle_number(start_cycle)
    width = cycle_width(start_cycle)
    prefix = cycle_prefix(start_cycle)

    for offset, variant in enumerate(variants):
        cycle_id = f"{prefix}_{current_number + offset:0{width}d}"
        cycle_dir = context.repo_root / "experiments" / cycle_id
        assignment = build_variant_assignment(
            context,
            cycle_id,
            variant,
            batch_id,
            batch_lineage_hash=lineage_hash,
        )
        assignment_path = (
            cycle_dir / "agents" / "assignments" / f"{CANDIDATE_ID}.json"
        )
        patch_path = context.repo_root / source_patch_diff_relative_path(
            CycleContext(context.repo_root, assignment)
        )
        if cycle_dir.exists():
            if not force:
                raise FileExistsError(
                    "generated cycle already exists: "
                    f"{cycle_dir.relative_to(context.repo_root)}"
                )
            # --force means regenerate the complete probe lineage.  Keeping an
            # older impl_compare review here could attach stale QoR to a new
            # assignment/patch, so remove the bounded generated cycle first.
            shutil.rmtree(cycle_dir)

        create_cycle_dirs(cycle_dir)
        write_json(assignment_path, assignment)
        patch_path.parent.mkdir(parents=True, exist_ok=True)
        patch_path.write_text(serialized_patch_text(variant), encoding="utf-8")
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
        "lineage_schema_version": BATCH_LINEAGE_SCHEMA_VERSION,
        "lineage": lineage,
        "lineage_hash": lineage_hash,
        "variant_set": variant_set,
        "target_command": target_command,
        "include_variants": sorted(include_variants),
        "base_assignment": base_assignment_path.resolve()
        .relative_to(context.repo_root)
        .as_posix(),
        "base_cycle_id": context.cycle_id,
        "benchmark_scope": list(context.assignment.get("benchmark_scope", ())),
        "evaluation_benchmark_scope": list(
            context.assignment.get("evaluation_benchmark_scope", ())
        ),
        "unsupported_benchmark_scope": list(
            context.assignment.get("unsupported_benchmark_scope", ())
        ),
        "benchmark_frontend": context.assignment.get("benchmark_frontend", ""),
        "manifest_path": str(
            (batch_dir / "manifest.json").relative_to(context.repo_root)
        ),
        "items": [asdict(item) for item in items],
    }
    write_json(batch_dir / "manifest.json", manifest)
    return manifest


def build_variants(
    context: CycleContext,
    variant_set: str,
    *,
    target_command: str = "",
) -> list[PatchVariant]:
    if variant_set not in ("flow_seed", "flow_wide"):
        raise ValueError(f"unsupported variant set: {variant_set}")
    variants: list[PatchVariant] = []
    if not target_command or target_command == "csweep":
        variants.extend(build_csw_variants(context))
    if not target_command or target_command == "fx":
        variants.extend(build_fxu_variants(context, wide=variant_set == "flow_wide"))
    if variant_set == "flow_wide":
        if not target_command or target_command == "csweep":
            variants.extend(build_abc_csweep_default_variants(context))
        if not target_command or target_command == "fx":
            variants.extend(build_fxu_select_variants(context))
        if not target_command or target_command == "rewrite":
            variants.extend(build_rewrite_core_variants(context))
        if not target_command or target_command == "dc2":
            variants.extend(build_dc2_dar_variants(context))
        if not target_command or target_command == "resub":
            variants.extend(build_resub_window_variants(context))
        if not target_command or target_command in (
            "rewrite",
            "resub",
            "dc2",
            "refactor",
        ):
            variants.extend(
                build_command_wrapper_variants(
                    context,
                    target_command=target_command,
                )
            )
    if target_command:
        variants = [
            variant
            for variant in variants
            if variant_command(variant.variant_id) == target_command
        ]
    allowed_roots = tuple(
        PurePosixPath(str(value))
        for value in context.assignment.get("source_patch_allowed_roots", ())
        if str(value).strip()
    )
    if allowed_roots:
        variants = [
            variant
            for variant in variants
            if any(
                root == PurePosixPath(variant.target_file)
                or root in PurePosixPath(variant.target_file).parents
                for root in allowed_roots
            )
        ]
    return variants


def build_csw_variants(context: CycleContext) -> list[PatchVariant]:
    source = source_text(context, CSW_CORE)
    current_cuts, current_leaves = current_csw_floors(source)
    candidates: tuple[tuple[int, int], ...] = (
        (10, 6),
        (12, 6),
        (12, 8),
        (16, 6),
        (16, 8),
        (20, 8),
        (20, 10),
        (24, 10),
        (24, 12),
    )
    variants: list[PatchVariant] = []
    for cuts, leaves in candidates:
        if (cuts, leaves) == (current_cuts, current_leaves):
            continue
        new_source = set_csw_floors(source, cuts=cuts, leaves=leaves)
        variants.append(
            PatchVariant(
                variant_id=f"csweep_floor_c{cuts}_l{leaves}",
                description=(
                    f"Set csweep cut/leaf floors to {cuts}/{leaves} "
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
    core_source = source_text(context, CSW_CORE)
    core_cut_floor, core_leaf_floor = current_csw_floors(core_source)
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
        if cuts <= core_cut_floor and leaves <= core_leaf_floor:
            continue
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


def build_rewrite_core_variants(context: CycleContext) -> list[PatchVariant]:
    """Probe reached rewrite decisions without crossing the Flow ``src/opt`` root."""

    source = source_text(context, RWR_EVA)
    specs = (
        (
            "rewrite_last_equal_gain",
            "Prefer the last equal-gain rewrite graph instead of the first.",
            "if ( pGraph != NULL && GainBest < GainCur )",
            "if ( pGraph != NULL && GainBest <= GainCur )",
            "Changes the reached equal-gain tie-break inside Rwr_NodeRewrite.",
        ),
        (
            "rewrite_positive_gain_only",
            "Reject zero-gain rewrite graphs even when the flow uses rewrite -z.",
            "if ( GainBest == -1 )\n        return -1;",
            "if ( GainBest <= 0 )\n        return -1;",
            "Measures whether zero-gain rewrites help or obstruct later passes.",
        ),
    )
    variants: list[PatchVariant] = []
    for variant_id, description, old, new, rationale in specs:
        if source.count(old) != 1:
            continue
        variants.append(
            PatchVariant(
                variant_id=variant_id,
                description=description,
                target_file=str(RWR_EVA),
                rationale=rationale,
                patch_text=unified_diff(
                    RWR_EVA,
                    source,
                    source.replace(old, new, 1),
                ),
            )
        )
    return variants


def build_dc2_dar_variants(context: CycleContext) -> list[PatchVariant]:
    """Sweep ``src/opt/dar`` defaults reached repeatedly inside bare ``dc2``."""

    specs = (
        (
            DAR_CORE,
            "Dar_ManDefaultRwrParams",
            "pPars->nCutsMax",
            (10, 12),
            "rwr_cuts",
            "rewrite cuts",
        ),
        (
            DAR_CORE,
            "Dar_ManDefaultRwrParams",
            "pPars->nSubgMax",
            (7, 8),
            "rwr_subgraphs",
            "rewrite subgraphs",
        ),
        (
            DAR_REFACT,
            "Dar_ManDefaultRefParams",
            "pPars->nMffcMin",
            (1, 3),
            "ref_mffc",
            "refactor minimum MFFC",
        ),
        (
            DAR_REFACT,
            "Dar_ManDefaultRefParams",
            "pPars->nCutsMax",
            (8, 10),
            "ref_cuts",
            "refactor cuts",
        ),
        (
            DAR_REFACT,
            "Dar_ManDefaultRefParams",
            "pPars->nLeafMax",
            (10, 15),
            "ref_leaves",
            "refactor leaves",
        ),
    )
    variants: list[PatchVariant] = []
    for path, function, field, values, stem, label in specs:
        source = source_text(context, path)
        current = numeric_assignment_in_function(source, function, field)
        for value in values:
            if value == current:
                continue
            new_source = set_numeric_assignment_in_function(
                source,
                function,
                field,
                value,
            )
            variants.append(
                PatchVariant(
                    variant_id=f"dc2_{stem}{value}",
                    description=(
                        f"Change the dc2-internal {label} default from "
                        f"{current} to {value}."
                    ),
                    target_file=str(path),
                    rationale=(
                        "The frozen recipe invokes bare dc2, whose DAR scripts "
                        "reinitialize these defaults for their rewrite/refactor "
                        "passes; this opt-only edit is reached without crossing "
                        "the concurrent Logic branch's ABCI ownership."
                    ),
                    patch_text=unified_diff(path, source, new_source),
                )
            )
    return variants


def build_resub_window_variants(context: CycleContext) -> list[PatchVariant]:
    """Sweep opt-only window defaults reached by the frozen ``resub -K 8``."""

    source = source_text(context, RES_WIN)
    specs = (
        (
            "p->nFanoutLimit",
            (6, 16),
            "fanout",
            "TFO fanout-root limit",
        ),
        (
            "p->nLevTfiMinus",
            (2, 4),
            "tfi_extra_levels",
            "extra TFI search depth",
        ),
    )
    variants: list[PatchVariant] = []
    for field, values, stem, label in specs:
        current = numeric_assignment_in_function(source, "Res_WinAlloc", field)
        for value in values:
            if value == current:
                continue
            new_source = set_numeric_assignment_in_function(
                source,
                "Res_WinAlloc",
                field,
                value,
            )
            variants.append(
                PatchVariant(
                    variant_id=f"resub_{stem}{value}",
                    description=(
                        f"Change the resub {label} default from {current} to "
                        f"{value}."
                    ),
                    target_file=str(RES_WIN),
                    rationale=(
                        "Res_WinAlloc initializes the window used by the frozen "
                        "bare resub command. Smaller and larger values probe a "
                        "real search-boundary decision inside Flow's src/opt root."
                    ),
                    patch_text=unified_diff(RES_WIN, source, new_source),
                )
            )
    return variants


def build_command_wrapper_variants(
    context: CycleContext,
    *,
    target_command: str = "",
) -> list[PatchVariant]:
    """Sweep wrapper defaults that are not overridden by the evaluation flow."""

    source = source_text(context, ABC_COMMANDS)
    specs = (
        (
            "rewrite_no_level_update",
            "Abc_CommandRewrite",
            "    fUpdateLevel = 1;",
            "    fUpdateLevel = 0;",
            "Allow rewrite to prioritize area without preserving levels.",
        ),
        (
            "resub_nodes0",
            "Abc_CommandResubstitute",
            "    nNodesMax    =  1;",
            "    nNodesMax    =  0;",
            "Restrict resubstitution to zero-added-node replacements.",
        ),
        (
            "resub_nodes2",
            "Abc_CommandResubstitute",
            "    nNodesMax    =  1;",
            "    nNodesMax    =  2;",
            "Allow resubstitution to add up to two nodes for larger net gain.",
        ),
        (
            "resub_nodes3",
            "Abc_CommandResubstitute",
            "    nNodesMax    =  1;",
            "    nNodesMax    =  3;",
            "Allow the full supported resubstitution replacement size.",
        ),
        (
            "resub_odc1",
            "Abc_CommandResubstitute",
            "    nLevelsOdc   =  0;",
            "    nLevelsOdc   =  1;",
            "Enable one level of observability don't-care context in resub.",
        ),
        (
            "dc2_balance",
            "Abc_CommandDc2",
            "    fBalance     = 0;",
            "    fBalance     = 1;",
            "Enable DC2 internal balancing.",
        ),
        (
            "dc2_update_level",
            "Abc_CommandDc2",
            "    fUpdateLevel = 0;",
            "    fUpdateLevel = 1;",
            "Enable DC2 level updates during optimization.",
        ),
        (
            "dc2_no_fanout",
            "Abc_CommandDc2",
            "    fFanout      = 1;",
            "    fFanout      = 0;",
            "Disable DC2 fanout representation to test area sensitivity.",
        ),
        (
            "refactor_node8",
            "Abc_CommandRefactor",
            "    nNodeSizeMax = 10;",
            "    nNodeSizeMax =  8;",
            "Use smaller refactor cones for more local replacements.",
        ),
        (
            "refactor_node12",
            "Abc_CommandRefactor",
            "    nNodeSizeMax = 10;",
            "    nNodeSizeMax = 12;",
            "Use larger refactor cones to expose broader divisors.",
        ),
        (
            "refactor_node15",
            "Abc_CommandRefactor",
            "    nNodeSizeMax = 10;",
            "    nNodeSizeMax = 15;",
            "Use the largest supported refactor node cone.",
        ),
    )
    variants: list[PatchVariant] = []
    for variant_id, function, old, new, description in specs:
        if target_command and variant_command(variant_id) != target_command:
            continue
        new_source = replace_in_function(source, function, old, new)
        if new_source == source:
            continue
        variants.append(
            PatchVariant(
                variant_id=variant_id,
                description=description,
                target_file=str(ABC_COMMANDS),
                rationale=(
                    f"Changes a default consumed by {function}; the evaluated "
                    "command does not override this specific field."
                ),
                patch_text=unified_diff(ABC_COMMANDS, source, new_source),
            )
        )
    return variants


def build_variant_assignment(
    base_context: CycleContext,
    cycle_id: str,
    variant: PatchVariant,
    batch_id: str,
    *,
    batch_lineage_hash: str,
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
            "lineage_hash": batch_lineage_hash,
            "variant_id": variant.variant_id,
            "target_file": variant.target_file,
            "description": variant.description,
            "rationale": variant.rationale,
        },
    }
    return normalize_flow_assignment_scope(with_abc_native_evaluation_scope(assignment))


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
    rows = collect_batch_rows(repo_root=repo_root, manifest=manifest)
    summary_path = batch_dir / "summary.csv"
    with summary_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    write_winner(
        batch_dir,
        rows,
        lineage_hash=str(manifest.get("lineage_hash", "")),
    )
    return summary_path


def collect_batch_rows(
    *,
    repo_root: Path,
    manifest: Mapping[str, object],
    require_reviews: bool = False,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in manifest.get("items", ()):
        if not isinstance(item, Mapping):
            raise ValueError("batch manifest contains a malformed item")
        cycle_id = str(item["cycle_id"])
        assignment = repo_path(repo_root, Path(item["assignment_path"]))
        context = CycleContext.from_assignment_file(repo_root, assignment)
        review = load_json(
            impl_compare_root(context) / "comparison" / "review_decision.json"
        )
        if require_reviews and (
            review is None
            or str(review.get("cycle_id", "")) != cycle_id
            or str(review.get("candidate_id", "")) != context.candidate_id
        ):
            raise ValueError("batch probe review is missing or has stale identity")
        rows.append(
            {
                "batch_id": manifest["batch_id"],
                "cycle_id": cycle_id,
                "variant_id": item["variant_id"],
                "decision": review.get("decision", "missing") if review else "missing",
                "promotion_allowed": (
                    review.get("promotion_allowed", "") if review else ""
                ),
                "build_status": review.get("build_status", "") if review else "",
                "cec_pass_count": review.get("cec_pass_count", "") if review else "",
                "cec_total_count": review.get("cec_total_count", "") if review else "",
                "correctness_backed_rows": (
                    review.get("correctness_backed_rows", "") if review else ""
                ),
                "evaluation_benchmark_count": len(
                    context.evaluation_benchmark_scope
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
                "total_depth_delta_candidate_minus_baseline": (
                    review.get("total_depth_delta_candidate_minus_baseline", "")
                    if review
                    else ""
                ),
                "structural_proxy_reward_pct": (
                    review.get("structural_proxy_reward_pct", "")
                    if review
                    else ""
                ),
                "retained_for_synergy": (
                    review.get("retained_for_synergy", "") if review else ""
                ),
                "target_file": item["target_file"],
                "description": item["description"],
            }
        )
    return rows


def expected_winner_payload(
    rows: Sequence[dict[str, object]],
    *,
    lineage_hash: str,
) -> dict[str, object]:
    eligible = [row for row in rows if _batch_row_is_evidence_eligible(row)]
    promoted = [
        row
        for row in eligible
        if str(row.get("promotion_allowed", "")).lower() == "true"
    ]
    ordered = sorted(
        promoted or eligible,
        key=lambda row: (
            float_or_neg(row.get("structural_proxy_reward_pct")),
            float_or_neg(row.get("average_and_improve_pct")),
            -float_or_pos(row.get("total_and_delta_candidate_minus_baseline")),
            float_or_neg(row.get("improved_benchmark_count")),
            str(row.get("variant_id", "")),
        ),
        reverse=True,
    )
    return {
        "lineage_schema_version": BATCH_LINEAGE_SCHEMA_VERSION,
        "lineage_hash": lineage_hash,
        "winner": ordered[0] if ordered else None,
        "promotion_found": bool(promoted),
        "diverse_frontier": _diverse_batch_frontier(eligible, limit=3),
    }


def _batch_row_is_evidence_eligible(row: Mapping[str, object]) -> bool:
    """Require the same hard gates before a probe can teach Planning."""

    decision = str(row.get("decision", "")).strip()
    expected = parse_whole_number(row.get("evaluation_benchmark_count"))
    cec_pass = parse_whole_number(row.get("cec_pass_count"))
    cec_total = parse_whole_number(row.get("cec_total_count"))
    backed = parse_whole_number(row.get("correctness_backed_rows"))
    return (
        str(row.get("build_status", "")).strip()
        in CANDIDATE_BUILD_READY_STATUSES
        and decision
        in {
            "ACCEPT_FOR_NEXT_CYCLE",
            "REPAIR_QOR",
            "RETAIN_FOR_SYNERGY",
        }
        and expected is not None
        and expected > 0
        and cec_pass == expected
        and cec_total == expected
        and backed == expected
    )


def parse_whole_number(value: object) -> int | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or not parsed.is_integer():
        return None
    return int(parsed)


def _diverse_batch_frontier(
    rows: Sequence[dict[str, object]],
    *,
    limit: int,
) -> list[dict[str, object]]:
    """Return top measured probes from distinct command families."""

    if limit < 1:
        return []
    ordered = sorted(
        rows,
        key=lambda row: (
            float_or_neg(row.get("structural_proxy_reward_pct")),
            -float_or_pos(row.get("total_and_delta_candidate_minus_baseline")),
            float_or_neg(row.get("improved_benchmark_count")),
            str(row.get("variant_id", "")),
        ),
        reverse=True,
    )
    selected: list[dict[str, object]] = []
    seen_families: set[str] = set()
    for row in ordered:
        family = variant_command(str(row.get("variant_id", ""))) or "unknown"
        if family in seen_families:
            continue
        selected.append(dict(row))
        seen_families.add(family)
        if len(selected) >= limit:
            break
    return selected


def validate_batch_measurements(
    *,
    repo_root: Path,
    manifest: Mapping[str, object],
    winner_payload: Mapping[str, object],
) -> None:
    """Cross-check summary and winner against the current reviewed probes."""

    lineage_hash = validate_manifest_lineage(manifest)
    rows = collect_batch_rows(
        repo_root=repo_root,
        manifest=manifest,
        require_reviews=True,
    )
    batch_dir = repo_path(repo_root, Path(str(manifest["manifest_path"]))).parent
    summary_path = batch_dir / "summary.csv"
    if not summary_path.is_file():
        raise ValueError("planner batch summary is missing")
    with summary_path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != SUMMARY_FIELDS:
            raise ValueError("planner batch summary columns are invalid")
        actual_rows = list(reader)
    expected_rows = [
        {key: _csv_cell(row.get(key)) for key in SUMMARY_FIELDS}
        for row in rows
    ]
    if actual_rows != expected_rows:
        raise ValueError("planner batch summary diverges from probe reviews")
    expected_winner = expected_winner_payload(rows, lineage_hash=lineage_hash)
    if dict(winner_payload) != expected_winner:
        raise ValueError("planner batch winner diverges from reviewed QoR")
    winner = expected_winner.get("winner")
    if not isinstance(winner, Mapping) or any(
        winner.get(key) in (None, "")
        for key in (
            "average_and_improve_pct",
            "total_and_delta_candidate_minus_baseline",
            "improved_benchmark_count",
            "regressed_benchmark_count",
        )
    ):
        raise ValueError("planner batch winner has no usable QoR measurements")
    winner_cycle = str(winner.get("cycle_id", ""))
    winner_item = next(
        (
            item
            for item in manifest.get("items", ())
            if isinstance(item, Mapping)
            and str(item.get("cycle_id", "")) == winner_cycle
        ),
        None,
    )
    if not isinstance(winner_item, Mapping):
        raise ValueError("planner batch winner assignment is missing")
    winner_assignment = repo_path(
        repo_root,
        Path(str(winner_item["assignment_path"])),
    )
    winner_context = CycleContext.from_assignment_file(repo_root, winner_assignment)
    winner_qor = impl_compare_root(winner_context) / "comparison" / "qor_delta.csv"
    if not winner_qor.is_file() or winner_qor.stat().st_size == 0:
        raise ValueError("planner batch winner has no QoR vector")


def write_winner(
    batch_dir: Path,
    rows: Sequence[dict[str, object]],
    *,
    lineage_hash: str,
) -> None:
    payload = expected_winner_payload(rows, lineage_hash=lineage_hash)
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


def current_csw_floors(source: str) -> tuple[int, int]:
    match = CSW_FLOOR_PATTERN.search(source)
    if match is None:
        raise ValueError("could not locate Csw_Sweep floor insertion point")
    return int(match.group(2) or 0), int(match.group(3) or 0)


def set_csw_floors(source: str, *, cuts: int, leaves: int) -> str:
    replacement = (
        r"\g<1>"
        f"    if ( nCutsMax < {cuts} )\n"
        f"        nCutsMax = {cuts};\n"
        f"    if ( nLeafMax < {leaves} )\n"
        f"        nLeafMax = {leaves};\n"
    )
    updated, count = CSW_FLOOR_PATTERN.subn(replacement, source, count=1)
    if count != 1:
        raise ValueError("could not update Csw_Sweep cut/leaf floors")
    return updated


def replace_in_function(
    source: str,
    function_name: str,
    old: str,
    new: str,
) -> str:
    """Replace one exact default inside a named ABC command wrapper."""

    start = source.find(f"int {function_name}(")
    if start < 0:
        return source
    end = source.find("/**Function", start + 1)
    if end < 0:
        end = len(source)
    function_text = source[start:end]
    if function_text.count(old) != 1:
        return source
    return source[:start] + function_text.replace(old, new, 1) + source[end:]


def numeric_assignment_in_function(
    source: str,
    function_name: str,
    field: str,
) -> int:
    """Read one integer field assignment from a named C function body."""

    start, end = function_bounds(source, function_name)
    function_text = source[start:end]
    pattern = re.compile(
        rf"(?m)^(\s*{re.escape(field)}\s*=\s*)(-?\d+)(\s*;[^\n]*)$"
    )
    matches = tuple(pattern.finditer(function_text))
    if len(matches) != 1:
        raise ValueError(
            f"expected one {field} assignment in {function_name}; "
            f"found {len(matches)}"
        )
    return int(matches[0].group(2))


def set_numeric_assignment_in_function(
    source: str,
    function_name: str,
    field: str,
    value: int,
) -> str:
    """Replace one integer field without changing its C formatting/comments."""

    start, end = function_bounds(source, function_name)
    function_text = source[start:end]
    pattern = re.compile(
        rf"(?m)^(\s*{re.escape(field)}\s*=\s*)(-?\d+)(\s*;[^\n]*)$"
    )
    updated, count = pattern.subn(
        lambda match: f"{match.group(1)}{value}{match.group(3)}",
        function_text,
        count=1,
    )
    if count != 1:
        raise ValueError(f"could not update {field} in {function_name}")
    return source[:start] + updated + source[end:]


def function_bounds(source: str, function_name: str) -> tuple[int, int]:
    """Return a bounded legacy-ABC function region for deterministic edits."""

    marker = f"{function_name}("
    start = source.find(marker)
    if start < 0:
        raise ValueError(f"could not locate C function {function_name}")
    line_start = source.rfind("\n", 0, start) + 1
    end = source.find("/**Function", start + len(marker))
    if end < 0:
        end = len(source)
    return line_start, end


def variant_command(variant_id: str) -> str:
    for command in ("csweep", "rewrite", "resub", "dc2", "refactor", "fx"):
        if variant_id.startswith(command):
            return command
    return ""


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
    repo_root = repo_root.resolve()
    resolved = path if path.is_absolute() else repo_root / path
    resolved = resolved.resolve()
    try:
        resolved.relative_to(repo_root)
    except ValueError as exc:
        raise ValueError(f"path escapes repository: {path}") from exc
    return resolved


def parse_variant_filter(text: str) -> set[str]:
    return {item.strip() for item in text.split(",") if item.strip()}


def build_batch_lineage(
    assignment: Mapping[str, object],
    *,
    variant_set: str,
    target_command: str,
    include_variants: Sequence[str] | set[str] = (),
    variant_space: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    """Build the immutable input binding for one deterministic search.

    Only coordinator-locked inputs are included.  Evidence and hypotheses are
    deliberately excluded because ``integrate_batch_winner`` updates them; a
    second invocation for the same pending Planning control must retain the
    same lineage, while a changed baseline, contract, planner dispatch, or
    upstream portfolio lineage must produce a different batch directory.
    """

    stable_fields = (
        "cycle_id",
        "candidate_id",
        "previous_cycle_id",
        "portfolio_id",
        "planner_dispatch_id",
        "branch_role",
        "agent_name",
        "paper_role",
        "baseline_ref",
        "baseline_kind",
        "base_source_root",
        "baseline_abc_bin",
        "champion_cycle_id",
        "champion_candidate_id",
        "champion_source_root",
        "champion_abc_bin",
        "evaluation_contract",
        "evaluation_contract_hash",
        "planner_advice_hash",
        "benchmark_frontend",
        "benchmark_scope",
        "evaluation_benchmark_scope",
        "unsupported_benchmark_scope",
        "evaluation_flow_commands",
        "source_patch_mode",
        "source_patch_allowed_roots",
    )
    inputs = {
        key: assignment[key]
        for key in stable_fields
        if key in assignment
    }
    raw_context = assignment.get("planner_batch_lineage_context")
    lineage_context = dict(raw_context) if isinstance(raw_context, Mapping) else {}
    lineage: dict[str, object] = {
        "schema_version": BATCH_LINEAGE_SCHEMA_VERSION,
        "variant_set": variant_set,
        "target_command": target_command,
        "include_variants": sorted(str(item) for item in include_variants),
        "variant_space": [dict(item) for item in variant_space],
        "assignment_inputs": inputs,
        "planner_context": lineage_context,
    }
    # Reject non-JSON or non-deterministic values at the binding boundary.
    json.dumps(lineage, sort_keys=True, separators=(",", ":"))
    return lineage


def hash_batch_lineage(lineage: Mapping[str, object]) -> str:
    canonical = json.dumps(
        dict(lineage),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def validate_manifest_lineage(
    manifest: Mapping[str, object],
    *,
    expected_lineage_hash: str = "",
) -> str:
    """Validate the self-hash and optional coordinator expectation."""

    if manifest.get("lineage_schema_version") != BATCH_LINEAGE_SCHEMA_VERSION:
        raise ValueError("batch manifest has an unsupported lineage schema")
    lineage = manifest.get("lineage")
    if not isinstance(lineage, Mapping):
        raise ValueError("batch manifest is missing its lineage payload")
    recorded = str(manifest.get("lineage_hash", "")).strip().lower()
    if not _is_sha256(recorded):
        raise ValueError("batch manifest lineage_hash is not SHA-256")
    actual = hash_batch_lineage(lineage)
    if recorded != actual:
        raise ValueError("batch manifest lineage self-hash mismatch")
    expected = expected_lineage_hash.strip().lower()
    if expected and (not _is_sha256(expected) or expected != recorded):
        raise ValueError("batch manifest does not match the expected lineage")
    if lineage.get("schema_version") != BATCH_LINEAGE_SCHEMA_VERSION:
        raise ValueError("batch lineage payload schema mismatch")
    if str(lineage.get("variant_set", "")) != str(
        manifest.get("variant_set", "")
    ):
        raise ValueError("batch manifest variant_set is outside its lineage")
    if str(lineage.get("target_command", "")) != str(
        manifest.get("target_command", "")
    ):
        raise ValueError("batch manifest target_command is outside its lineage")
    if lineage.get("include_variants") != sorted(
        str(item) for item in manifest.get("include_variants", ())
    ):
        raise ValueError("batch manifest variant filter is outside its lineage")
    return recorded


def validate_manifest_base_assignment(
    repo_root: Path,
    manifest: Mapping[str, object],
) -> Path:
    """Prove that a manifest and all probes belong to the current assignment."""

    lineage_hash = validate_manifest_lineage(manifest)
    raw_base = str(manifest.get("base_assignment", "")).strip()
    if not raw_base or Path(raw_base).is_absolute():
        raise ValueError("batch manifest base_assignment must be repo-relative")
    base_path = repo_path(repo_root.resolve(), Path(raw_base))
    if not base_path.is_file():
        raise ValueError("batch manifest base_assignment is missing")
    disk_context = CycleContext.from_assignment_file(repo_root.resolve(), base_path)
    effective_assignment = dict(disk_context.assignment)
    for key in (
        "benchmark_scope",
        "evaluation_benchmark_scope",
        "unsupported_benchmark_scope",
        "benchmark_frontend",
    ):
        if key in manifest:
            effective_assignment[key] = manifest[key]
    context = CycleContext(repo_root.resolve(), effective_assignment)
    variants = build_variants(
        context,
        str(manifest.get("variant_set", "")),
        target_command=str(manifest.get("target_command", "")),
    )
    include_variants = {
        str(item) for item in manifest.get("include_variants", ())
    }
    if include_variants:
        variants = [
            variant for variant in variants if variant.variant_id in include_variants
        ]
    if not variants:
        raise ValueError("batch manifest resolves to an empty variant space")
    expected_variants = {
        str(item["variant_id"]): item
        for item in describe_variant_space(context, variants)
    }
    rebuilt = build_batch_lineage(
        context.assignment,
        variant_set=str(manifest.get("variant_set", "")),
        target_command=str(manifest.get("target_command", "")),
        include_variants=include_variants,
        variant_space=describe_variant_space(context, variants),
    )
    if hash_batch_lineage(rebuilt) != lineage_hash:
        raise ValueError("batch base assignment no longer matches manifest lineage")
    if str(manifest.get("base_cycle_id", "")) != disk_context.cycle_id:
        raise ValueError("batch manifest base_cycle_id mismatch")

    batch_id = str(manifest.get("batch_id", "")).strip()
    items = manifest.get("items")
    if not batch_id or not isinstance(items, list) or not items:
        raise ValueError("batch manifest has no bound probe items")
    seen_cycles: set[str] = set()
    seen_variants: set[str] = set()
    for raw_item in items:
        if not isinstance(raw_item, Mapping):
            raise ValueError("batch manifest contains a malformed probe item")
        cycle_id = str(raw_item.get("cycle_id", "")).strip()
        variant_id = str(raw_item.get("variant_id", "")).strip()
        if (
            not cycle_id
            or not variant_id
            or cycle_id in seen_cycles
            or variant_id in seen_variants
        ):
            raise ValueError("batch manifest probe identities are missing or duplicated")
        seen_cycles.add(cycle_id)
        seen_variants.add(variant_id)
        raw_assignment = str(raw_item.get("assignment_path", "")).strip()
        if not raw_assignment or Path(raw_assignment).is_absolute():
            raise ValueError("batch probe assignment path must be repo-relative")
        probe_path = repo_path(repo_root.resolve(), Path(raw_assignment))
        probe = load_json(probe_path)
        if probe is None:
            raise ValueError("batch probe assignment is missing or invalid")
        probe_batch = probe.get("batch_search")
        if not isinstance(probe_batch, Mapping):
            raise ValueError("batch probe assignment is missing batch metadata")
        expected = {
            "batch_id": batch_id,
            "lineage_hash": lineage_hash,
            "variant_id": variant_id,
        }
        if any(str(probe_batch.get(key, "")) != value for key, value in expected.items()):
            raise ValueError("batch probe assignment lineage mismatch")
        if str(probe.get("cycle_id", "")) != cycle_id:
            raise ValueError("batch probe assignment cycle mismatch")
        for frozen_key in (
            "portfolio_id",
            "planner_dispatch_id",
            "branch_role",
            "agent_name",
            "paper_role",
            "baseline_ref",
            "baseline_kind",
            "base_source_root",
            "baseline_abc_bin",
            "champion_cycle_id",
            "champion_candidate_id",
            "champion_source_root",
            "champion_abc_bin",
            "evaluation_contract",
            "evaluation_contract_hash",
            "planner_advice_hash",
            "benchmark_frontend",
            "benchmark_scope",
            "evaluation_benchmark_scope",
            "unsupported_benchmark_scope",
            "evaluation_flow_commands",
            "source_patch_mode",
            "source_patch_allowed_roots",
        ):
            if probe.get(frozen_key) != context.assignment.get(frozen_key):
                raise ValueError(
                    f"batch probe diverges from frozen base field: {frozen_key}"
                )
        descriptor = expected_variants.get(variant_id)
        if descriptor is None:
            raise ValueError("batch manifest names a probe outside the variant space")
        target_file = str(raw_item.get("target_file", ""))
        if target_file != str(descriptor["target_file"]):
            raise ValueError("batch probe target file mismatch")
        if str(probe_batch.get("target_file", "")) != target_file:
            raise ValueError("batch probe assignment target file mismatch")
        raw_patch = str(raw_item.get("patch_path", "")).strip()
        if not raw_patch or Path(raw_patch).is_absolute():
            raise ValueError("batch probe patch path must be repo-relative")
        patch_path = repo_path(repo_root.resolve(), Path(raw_patch))
        if (
            not patch_path.is_file()
            or hashlib.sha256(patch_path.read_bytes()).hexdigest()
            != descriptor["patch_sha256"]
        ):
            raise ValueError("batch probe patch does not match its variant lineage")
    if seen_variants != set(expected_variants):
        raise ValueError("batch manifest does not contain the complete variant space")
    return base_path


def describe_variant_space(
    context: CycleContext,
    variants: Sequence[PatchVariant],
) -> list[dict[str, object]]:
    """Bind a batch to the exact generated diffs, not just a variant label."""

    return [
        {
            "variant_id": variant.variant_id,
            "target_file": variant.target_file,
            "source_sha256": hashlib.sha256(
                source_text(context, Path(variant.target_file)).encode("utf-8")
            ).hexdigest(),
            "patch_sha256": hashlib.sha256(
                serialized_patch_text(variant).encode("utf-8")
            ).hexdigest(),
        }
        for variant in sorted(variants, key=lambda item: item.variant_id)
    ]


def serialized_patch_text(variant: PatchVariant) -> str:
    return variant.patch_text.rstrip() + "\n"


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


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


def _csv_cell(value: object) -> str:
    return "" if value is None else str(value)


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
