"""Source patch implementation setup, patch recording, and smoke gating.

S4a records the baseline and candidate implementation identities. S4b records a
reviewed source patch already present in the working tree as a patch diff. S4c
records a local build/smoke gate before S5 is allowed to compare QoR.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from scripts.agents.self_evolved_abc.cycle_context import CycleContext
from scripts.agents.self_evolved_abc.flow.contracts import (
    CANDIDATE_BINARY_BUILD_COMMAND_LABEL,
    FLOW_INFRA_ALLOWED_ROOTS,
    FLOW_SOURCE_PATCH_DIFF_ALLOWED_ROOTS,
    FLOWTUNE_SOURCE_ABC_BIN,
    FLOWTUNE_SOURCE_ROOT,
    IMPL_BASELINE_LABEL,
    IMPL_CANDIDATE_LABEL,
    PATCH_DIFF_NAME,
    PYTHON_SMOKE_FILES,
    SMOKE_GATE_COMMAND_LABEL,
    SOURCE_PATCH_TARGET_SECTION,
    VALIDATION_FIXTURE_EXPECTATIONS,
)
from scripts.agents.self_evolved_abc.flow.lineage import (
    resolve_base_source_root,
    resolve_baseline_abc_bin,
)
from scripts.agents.self_evolved_abc.flow.paths import (
    candidate_workspace_root,
    impl_compare_root,
    repo_path,
    repo_relative_path,
)
from scripts.agents.self_evolved_abc.flow.source_patch import (
    source_patch_diff_relative_path,
    source_patch_plan_relative_path,
)
from scripts.agents.self_evolved_abc.flow.validation import (
    validate_flow_agent_response,
)


SOURCE_PATCH_ALLOWED_ROOTS = FLOW_INFRA_ALLOWED_ROOTS
SOURCE_PATCH_DIFF_ALLOWED_ROOTS = FLOW_SOURCE_PATCH_DIFF_ALLOWED_ROOTS


@dataclass(frozen=True)
class ImplementationBuildInfo:
    """Manifest for one implementation side of a future comparison."""

    implementation_label: str
    cycle_id: str
    candidate_id: str
    repo_root: str
    git_commit: str
    git_dirty: bool
    git_status_short: tuple[str, ...]
    patch_plan: str
    patch_plan_exists: bool
    patch_plan_sha256: str | None
    patch_applied: bool
    patch_diff_path: str | None
    build_command: str
    binary_path: str
    binary_exists: bool
    binary_sha256: str | None
    build_exit_code: int | None
    status: str
    notes: tuple[str, ...]


@dataclass(frozen=True)
class BuildGateResult:
    """Result of the local S4c build/smoke gate."""

    command_label: str
    exit_code: int
    status: str
    log_lines: tuple[str, ...]


@dataclass(frozen=True)
class PatchApplyResult:
    """Result of applying a source patch into an isolated workspace."""

    patch_path: Path
    workspace_root: Path
    target_paths: tuple[str, ...]
    exit_code: int
    status: str
    log_lines: tuple[str, ...]


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create S4 baseline/candidate implementation manifests."
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--assignment",
        type=Path,
        required=True,
        help="Cycle assignment JSON.",
    )
    parser.add_argument(
        "--patch-plan",
        type=Path,
        default=None,
        help="Source patch plan artifact. Defaults to experiments/<cycle>/agents/source_patch_todos/<candidate>.md.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Implementation comparison root. Defaults to experiments/<cycle>/impl_compare.",
    )
    parser.add_argument(
        "--baseline-abc-bin",
        type=Path,
        default=None,
        help=(
            "Baseline ABC binary path for later implementation comparison. "
            "Defaults to assignment baseline/champion, then the vanilla binary."
        ),
    )
    parser.add_argument(
        "--candidate-abc-bin",
        type=Path,
        default=None,
        help="Candidate ABC binary path for later implementation comparison.",
    )
    parser.add_argument(
        "--record-applied-patch",
        action="store_true",
        help=(
            "S4b mode: record the reviewed source patch currently present in "
            "the working tree as candidate_modified/patch.diff. Does not apply "
            "a patch."
        ),
    )
    parser.add_argument(
        "--record-build-gate",
        action="store_true",
        help=(
            "S4c mode: record the reviewed source patch, run the local Python "
            "build/smoke gate, and write pass/fail manifests. Does not run QoR."
        ),
    )
    parser.add_argument(
        "--candidate-patch",
        type=Path,
        default=None,
        help="Validated source_patch_diff artifact. Defaults to experiments/<cycle>/agents/source_patches/<candidate>.diff.",
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Isolated candidate workspace for applying source patches. Defaults to candidate_modified/workspace.",
    )
    parser.add_argument(
        "--apply-candidate-patch",
        action="store_true",
        help=(
            "S4d mode: apply a validated source_patch_diff only inside the "
            "candidate_modified workspace and record the applied patch."
        ),
    )
    parser.add_argument(
        "--build-candidate-binary",
        action="store_true",
        help=(
            "After isolated patch application and Python smoke, build the "
            "candidate ABC binary inside candidate_modified/workspace."
        ),
    )
    parser.add_argument(
        "--build-jobs",
        type=int,
        default=4,
        help="Parallel jobs for the candidate FlowTune make build.",
    )
    parser.add_argument(
        "--build-timeout-seconds",
        type=float,
        default=900.0,
        help="Timeout for the candidate FlowTune make build.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    repo_root = args.repo_root.resolve()
    context = CycleContext.from_assignment_file(repo_root, args.assignment)
    patch_plan = repo_relative_path(
        context,
        args.patch_plan or source_patch_plan_relative_path(context),
    )
    output_root = repo_path(
        context,
        args.output_root or impl_compare_root(context),
    )

    baseline_dir = output_root / IMPL_BASELINE_LABEL
    candidate_dir = output_root / IMPL_CANDIDATE_LABEL
    comparison_dir = output_root / "comparison"
    for path in (baseline_dir, candidate_dir, comparison_dir):
        path.mkdir(parents=True, exist_ok=True)

    git_commit = _git_output(repo_root, ("rev-parse", "HEAD")) or "unknown"
    git_status_lines = tuple(_git_status(repo_root))
    git_dirty = bool(git_status_lines)
    patch_diff_path: Path | None = None
    build_gate: BuildGateResult | None = None
    candidate_binary_build: BuildGateResult | None = None
    patch_apply: PatchApplyResult | None = None
    record_patch = args.record_applied_patch or args.record_build_gate
    workspace_root = repo_path(
        context,
        args.workspace_root or candidate_workspace_root(context),
    )
    baseline_abc_bin = resolve_baseline_abc_bin(
        context,
        explicit=args.baseline_abc_bin,
    )
    candidate_abc_bin = args.candidate_abc_bin or baseline_abc_bin
    candidate_binary_path = (
        workspace_root / FLOWTUNE_SOURCE_ABC_BIN
        if args.build_candidate_binary
        else candidate_abc_bin
    )
    baseline_status = "manifest_only"
    baseline_build_command = "not_run"
    baseline_build_exit_code: int | None = None
    baseline_notes = (
        "Baseline is the unmodified implementation identity at git_commit.",
        "No build was run by S4.",
    )
    candidate_patch_applied = False
    candidate_status = "patch_not_applied"
    candidate_build_command = "not_run"
    candidate_build_exit_code: int | None = None
    candidate_notes = (
        "Candidate source patch has not been applied yet.",
        "Do not run QoR comparison until S4b/S5 produce a real candidate binary.",
    )

    if args.apply_candidate_patch:
        candidate_patch = repo_relative_path(
            context,
            args.candidate_patch or source_patch_diff_relative_path(context),
        )
        patch_apply = apply_candidate_patch_to_workspace(
            context=context,
            patch_path=context.repo_root / candidate_patch,
            workspace_root=workspace_root,
        )
        patch_diff_path = candidate_dir / PATCH_DIFF_NAME
        patch_diff_path.write_text(
            (context.repo_root / candidate_patch).read_text(
                encoding="utf-8",
                errors="replace",
            ).rstrip()
            + "\n",
            encoding="utf-8",
        )
        candidate_patch_applied = patch_apply.exit_code == 0
        candidate_status = (
            "patch_applied_to_workspace"
            if patch_apply.exit_code == 0
            else "patch_apply_failed"
        )
        candidate_notes = (
            f"Validated source patch was applied inside {workspace_root.relative_to(context.repo_root)}.",
            "The repository source tree was not modified by S4d.",
        )
        if patch_apply.exit_code != 0:
            candidate_notes = (
                "Validated source patch failed to apply in the isolated workspace.",
                "Repair the patch before build/smoke or implementation comparison.",
            )
    elif record_patch:
        target_paths = extract_source_patch_targets(context.repo_root / patch_plan)
        validate_source_patch_targets(context, target_paths)
        patch_diff_path = candidate_dir / PATCH_DIFF_NAME
        write_patch_diff(
            repo_root=context.repo_root,
            target_paths=target_paths,
            output_path=patch_diff_path,
        )
        candidate_patch_applied = True
        candidate_status = "patch_applied_build_not_run"
        candidate_notes = (
            "Reviewed source patch is recorded in candidate_modified/patch.diff.",
            "Build has not been run; do not enter S5 until S4c passes.",
        )

    if args.record_build_gate and (
        patch_apply is None or patch_apply.exit_code == 0
    ):
        build_gate = run_python_smoke_gate(context)
        baseline_status = (
            "baseline_smoke_passed"
            if build_gate.exit_code == 0
            else "baseline_smoke_failed"
        )
        candidate_status = (
            "build_smoke_passed"
            if build_gate.exit_code == 0
            else "build_smoke_failed"
        )
        baseline_build_command = build_gate.command_label
        candidate_build_command = build_gate.command_label
        baseline_build_exit_code = build_gate.exit_code
        candidate_build_exit_code = build_gate.exit_code
        baseline_notes = (
            "Baseline smoke is a local harness sanity check for S4.",
            "No separate clean checkout or ABC rebuild is performed in S4c.",
        )
        if build_gate.exit_code == 0 and patch_apply is not None:
            candidate_notes = (
                "Validated source patch is recorded in candidate_modified/patch.diff.",
                "Patch was applied only inside candidate_modified/workspace.",
                "Local Python build/smoke gate passed; S5 may start implementation comparison.",
            )
        elif build_gate.exit_code == 0:
            candidate_notes = (
                "Reviewed source patch is recorded in candidate_modified/patch.diff.",
                "Local Python build/smoke gate passed; S5 may start implementation comparison.",
            )
        elif patch_apply is not None:
            candidate_notes = (
                "Validated source patch is recorded in candidate_modified/patch.diff.",
                "Patch was applied only inside candidate_modified/workspace.",
                "Local Python build/smoke gate failed; repair before S5.",
            )
        else:
            candidate_notes = (
                "Reviewed source patch is recorded in candidate_modified/patch.diff.",
                "Local Python build/smoke gate failed; repair before S5.",
            )

        if args.build_candidate_binary and build_gate.exit_code == 0:
            if patch_apply is None:
                candidate_binary_build = BuildGateResult(
                    command_label=CANDIDATE_BINARY_BUILD_COMMAND_LABEL,
                    exit_code=1,
                    status="failed",
                    log_lines=(
                        "S4e candidate FlowTune binary build",
                        "status: skipped_missing_isolated_patch_workspace",
                        "reason: --build-candidate-binary requires --apply-candidate-patch",
                    ),
                )
            else:
                candidate_binary_build = run_candidate_binary_build(
                    context=context,
                    workspace_root=workspace_root,
                    jobs=max(1, args.build_jobs),
                    timeout_seconds=args.build_timeout_seconds,
                )
            candidate_build_command = (
                f"{build_gate.command_label} && "
                f"{candidate_binary_build.command_label}"
            )
            candidate_build_exit_code = candidate_binary_build.exit_code
            if candidate_binary_build.exit_code == 0:
                candidate_status = "candidate_binary_build_passed"
                candidate_notes = (
                    "Validated source patch is recorded in candidate_modified/patch.diff.",
                    "Patch was applied only inside candidate_modified/workspace.",
                    "Candidate ABC binary was built from the isolated workspace.",
                    "S5/F7 should compare baseline ABC against the candidate workspace ABC.",
                )
            else:
                candidate_status = "candidate_binary_build_failed"
                candidate_notes = (
                    "Validated source patch is recorded in candidate_modified/patch.diff.",
                    "Patch was applied only inside candidate_modified/workspace.",
                    "Candidate ABC binary build failed; repair before S5/F7.",
                )
    elif args.record_build_gate and patch_apply is not None:
        candidate_build_command = "skipped_after_patch_apply_failure"
        candidate_build_exit_code = patch_apply.exit_code

    baseline = build_manifest(
        context=context,
        label=IMPL_BASELINE_LABEL,
        patch_plan=patch_plan,
        binary_path=baseline_abc_bin,
        git_commit=git_commit,
        git_dirty=git_dirty,
        git_status_short=git_status_lines,
        patch_applied=False,
        patch_diff_path=None,
        build_command=baseline_build_command,
        build_exit_code=baseline_build_exit_code,
        status=baseline_status,
        notes=baseline_notes,
    )
    candidate = build_manifest(
        context=context,
        label=IMPL_CANDIDATE_LABEL,
        patch_plan=patch_plan,
        binary_path=candidate_binary_path,
        git_commit=git_commit,
        git_dirty=git_dirty,
        git_status_short=git_status_lines,
        patch_applied=candidate_patch_applied,
        patch_diff_path=(
            patch_diff_path.relative_to(context.repo_root)
            if patch_diff_path is not None
            else None
        ),
        build_command=candidate_build_command,
        build_exit_code=candidate_build_exit_code,
        status=candidate_status,
        notes=candidate_notes,
    )

    baseline_info = write_build_info(baseline_dir, baseline)
    candidate_info = write_build_info(candidate_dir, candidate)
    baseline_gate_log = tuple(build_gate.log_lines if build_gate is not None else ())
    candidate_gate_log = (
        *(patch_apply.log_lines if patch_apply is not None else ()),
        *(build_gate.log_lines if build_gate is not None else ()),
        *(candidate_binary_build.log_lines if candidate_binary_build is not None else ()),
    )
    baseline_log = write_build_log(
        baseline_dir,
        baseline,
        extra_lines=baseline_gate_log,
    )
    candidate_log = write_build_log(
        candidate_dir,
        candidate,
        extra_lines=candidate_gate_log,
    )
    readme = write_comparison_readme(
        context=context,
        output_root=output_root,
        baseline=baseline,
        candidate=candidate,
    )

    print(f"baseline_build_info: {baseline_info}")
    print(f"candidate_build_info: {candidate_info}")
    print(f"baseline_build_log: {baseline_log}")
    print(f"candidate_build_log: {candidate_log}")
    print(f"comparison_readme: {readme}")
    print(f"status: {candidate.status}")
    if patch_apply is not None and patch_apply.exit_code != 0:
        return patch_apply.exit_code or 1
    if build_gate is not None and build_gate.exit_code != 0:
        return build_gate.exit_code or 1
    if candidate_binary_build is not None and candidate_binary_build.exit_code != 0:
        return candidate_binary_build.exit_code or 1
    return 0


def build_manifest(
    *,
    context: CycleContext,
    label: str,
    patch_plan: Path,
    binary_path: Path,
    git_commit: str,
    git_dirty: bool,
    git_status_short: tuple[str, ...],
    patch_applied: bool,
    patch_diff_path: Path | None,
    build_command: str,
    build_exit_code: int | None,
    status: str,
    notes: tuple[str, ...],
) -> ImplementationBuildInfo:
    """Build one manifest without mutating the implementation."""

    absolute_patch_plan = context.repo_root / patch_plan
    binary_relative = _display_path(context, binary_path)
    binary_absolute = _absolute_maybe_repo_path(context, binary_path)
    return ImplementationBuildInfo(
        implementation_label=label,
        cycle_id=context.cycle_id,
        candidate_id=context.candidate_id,
        repo_root=str(context.repo_root),
        git_commit=git_commit,
        git_dirty=git_dirty,
        git_status_short=git_status_short,
        patch_plan=str(patch_plan),
        patch_plan_exists=absolute_patch_plan.exists(),
        patch_plan_sha256=sha256_file(absolute_patch_plan),
        patch_applied=patch_applied,
        patch_diff_path=str(patch_diff_path) if patch_diff_path is not None else None,
        build_command=build_command,
        binary_path=binary_relative,
        binary_exists=binary_absolute.exists(),
        binary_sha256=sha256_file(binary_absolute),
        build_exit_code=build_exit_code,
        status=status,
        notes=notes,
    )


def write_build_info(directory: Path, info: ImplementationBuildInfo) -> Path:
    """Write build_info.json for one implementation side."""

    path = directory / "build_info.json"
    path.write_text(
        json.dumps(asdict(info), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def write_build_log(
    directory: Path,
    info: ImplementationBuildInfo,
    *,
    extra_lines: Sequence[str] = (),
) -> Path:
    """Write a human-readable S4 build log placeholder."""

    path = directory / "build.log"
    lines = [
        f"implementation_label: {info.implementation_label}",
        f"status: {info.status}",
        f"build_command: {info.build_command}",
        f"build_exit_code: {info.build_exit_code}",
        f"binary_path: {info.binary_path}",
        f"binary_exists: {str(info.binary_exists).lower()}",
        f"patch_plan: {info.patch_plan}",
        f"patch_applied: {str(info.patch_applied).lower()}",
        f"patch_diff_path: {info.patch_diff_path or 'none'}",
        "",
        "notes:",
        *(f"- {note}" for note in info.notes),
        "",
    ]
    if extra_lines:
        lines.extend(("gate_output:", *extra_lines, ""))
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_comparison_readme(
    *,
    context: CycleContext,
    output_root: Path,
    baseline: ImplementationBuildInfo,
    candidate: ImplementationBuildInfo,
) -> Path:
    """Write the S4 comparison handoff README."""

    path = output_root / "comparison" / "README.md"
    build_run = candidate.build_exit_code is not None
    if candidate.status == "build_smoke_passed":
        stage = "S4c build/smoke passed"
        next_gate = "- S5 can start implementation comparison with the same benchmark harness."
    elif candidate.status == "candidate_binary_build_passed":
        stage = "S4e candidate binary build passed"
        next_gate = "- S5/F7 can compare baseline ABC against the candidate workspace ABC."
    elif candidate.status == "build_smoke_failed":
        stage = "S4c build/smoke failed"
        next_gate = "- Repair the source patch before running S5."
    elif candidate.status == "candidate_binary_build_failed":
        stage = "S4e candidate binary build failed"
        next_gate = "- Repair the source patch or build environment before running S5/F7."
    elif candidate.status == "patch_applied_to_workspace":
        stage = "S4d patch applied to isolated workspace"
        next_gate = "- Run S4c build/smoke before S5 benchmark comparison."
    elif candidate.status == "patch_apply_failed":
        stage = "S4d patch apply failed"
        next_gate = "- Repair the source patch diff before build/smoke."
    elif candidate.patch_applied:
        stage = "S4b patch recorded"
        next_gate = "- S4c must record candidate build success before S5 benchmark comparison."
    else:
        stage = "S4a manifest only"
        next_gate = "- S4b must apply a reviewed source patch and produce a real candidate implementation."
    path.write_text(
        "\n".join(
            (
                f"# Implementation Compare Setup -- {context.cycle_id} {context.candidate_id}",
                "",
                "## Status",
                "",
                f"- Stage: {stage}",
                f"- Source patch applied: {str(candidate.patch_applied).lower()}",
                f"- Build run: {str(build_run).lower()}",
                "- Benchmark/QoR run: no",
                "- Promotion allowed: no",
                "",
                "## Baseline Implementation",
                "",
                f"- Label: `{baseline.implementation_label}`",
                f"- Binary: `{baseline.binary_path}`",
                f"- Binary exists: `{str(baseline.binary_exists).lower()}`",
                f"- Build status: `{baseline.status}`",
                f"- Build command: `{baseline.build_command}`",
                f"- Build exit code: `{baseline.build_exit_code if baseline.build_exit_code is not None else 'none'}`",
                f"- Build info: `../baseline_unmodified/build_info.json`",
                "",
                "## Candidate Implementation",
                "",
                f"- Label: `{candidate.implementation_label}`",
                f"- Binary: `{candidate.binary_path}`",
                f"- Binary exists: `{str(candidate.binary_exists).lower()}`",
                f"- Patch applied: `{str(candidate.patch_applied).lower()}`",
                f"- Patch diff: `{candidate.patch_diff_path or 'none'}`",
                f"- Build status: `{candidate.status}`",
                f"- Build command: `{candidate.build_command}`",
                f"- Build exit code: `{candidate.build_exit_code if candidate.build_exit_code is not None else 'none'}`",
                f"- Build info: `../candidate_modified/build_info.json`",
                "",
                "## Patch Plan",
                "",
                f"- Path: `{candidate.patch_plan}`",
                f"- Exists: `{str(candidate.patch_plan_exists).lower()}`",
                f"- SHA256: `{candidate.patch_plan_sha256 or 'missing'}`",
                "",
                "## Next Gate",
                "",
                next_gate,
                "- Promotion stays blocked until S5/F7 pass the CEC-first QoR gate.",
                "",
            )
        ),
        encoding="utf-8",
    )
    return path


def extract_source_patch_targets(patch_plan: Path) -> tuple[str, ...]:
    """Extract proposed source targets from the S3 patch plan markdown."""

    if not patch_plan.exists():
        raise FileNotFoundError(f"source patch plan is missing: {patch_plan}")

    lines = patch_plan.read_text(encoding="utf-8", errors="replace").splitlines()
    in_section = False
    targets: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            heading = stripped.removeprefix("## ").strip()
            in_section = heading == SOURCE_PATCH_TARGET_SECTION
            continue
        if not in_section or not stripped.startswith("- "):
            continue
        target = stripped.removeprefix("- ").strip()
        if target and target != "None.":
            targets.append(target)

    if not targets:
        raise ValueError(
            f"source patch plan does not list any {SOURCE_PATCH_TARGET_SECTION!r}"
        )
    return tuple(targets)


def validate_source_patch_targets(
    context: CycleContext,
    target_paths: tuple[str, ...],
) -> None:
    """Validate S4b target files stay in the first source-patch scope."""

    allowed_roots = tuple(
        context.resolve_repo_path(root) for root in SOURCE_PATCH_ALLOWED_ROOTS
    )
    assignment_roots = tuple(
        context.resolve_repo_path(str(root))
        for root in context.assignment.get("allowed_to_edit", ())
        if str(root).strip()
    )

    for target in target_paths:
        path = Path(target)
        if path.is_absolute():
            raise ValueError(f"absolute source patch target is not allowed: {target}")

        resolved = context.resolve_repo_path(target)
        if not any(_path_is_under_or_equal(resolved, root) for root in allowed_roots):
            raise ValueError(f"source patch target is outside S4b scope: {target}")
        if assignment_roots and not any(
            _path_is_under_or_equal(resolved, root) for root in assignment_roots
        ):
            raise ValueError(
                f"source patch target is outside assignment allowed_to_edit: {target}"
            )
        if not resolved.exists():
            raise FileNotFoundError(
                f"source patch target does not exist in working tree: {target}"
            )


def write_patch_diff(
    *,
    repo_root: Path,
    target_paths: tuple[str, ...],
    output_path: Path,
) -> Path:
    """Write a reviewable diff for tracked and untracked target files."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    chunks: list[str] = []
    for target in target_paths:
        target_path = repo_root / target
        if _is_tracked(repo_root, target):
            diff_text = _git_output(
                repo_root,
                ("diff", "--binary", "HEAD", "--", target),
                allow_diff_exit=True,
            )
        else:
            diff_text = _diff_untracked_file(repo_root, target_path)

        if not diff_text:
            raise ValueError(f"source patch target has no diff from baseline: {target}")
        chunks.append(diff_text.rstrip())

    output_path.write_text("\n\n".join(chunks) + "\n", encoding="utf-8")
    return output_path


def apply_candidate_patch_to_workspace(
    *,
    context: CycleContext,
    patch_path: Path,
    workspace_root: Path,
) -> PatchApplyResult:
    """Apply a source_patch_diff only inside the candidate workspace."""

    log_lines = [
        "S4d isolated source patch application",
        f"patch_path: {_display_path(context, patch_path)}",
        f"workspace_root: {_display_path(context, workspace_root)}",
        "",
    ]
    if not patch_path.is_file():
        log_lines.append("status: missing_patch")
        return PatchApplyResult(
            patch_path=patch_path,
            workspace_root=workspace_root,
            target_paths=(),
            exit_code=1,
            status="missing_patch",
            log_lines=tuple(log_lines),
        )

    patch_text = patch_path.read_text(encoding="utf-8", errors="replace")
    target_paths = extract_unified_diff_targets(patch_text)
    validate_source_patch_diff_targets(context, target_paths)
    validate_workspace_root(context, workspace_root)
    base_source_root = resolve_base_source_root(context)
    reset_patch_workspace(
        context,
        workspace_root,
        target_paths,
        base_source_root=base_source_root,
    )

    workspace_relative = workspace_root.relative_to(context.repo_root)
    patch_rel = str(patch_path.relative_to(context.repo_root))
    check_command = (
        "git",
        "apply",
        "--check",
        "--recount",
        "--ignore-space-change",
        f"--directory={workspace_relative}",
        patch_rel,
    )
    apply_command = (
        "git",
        "apply",
        "--recount",
        "--ignore-space-change",
        f"--directory={workspace_relative}",
        patch_rel,
    )
    log_lines.extend(
        (
            "targets:",
            *(f"- {target}" for target in target_paths),
            "",
            f"check_command: {shlex.join(check_command)}",
        )
    )
    check_result = subprocess.run(
        check_command,
        cwd=context.repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    log_lines.append(f"check_return_code: {check_result.returncode}")
    if check_result.stdout:
        log_lines.extend(check_result.stdout.rstrip().splitlines())

    # Try git-apply first; fall back to GNU patch with fuzz for LLM diffs.
    apply_tool: str
    if check_result.returncode == 0:
        apply_tool = "git"
        log_lines.append(f"apply_command: {shlex.join(apply_command)}")
        apply_result = subprocess.run(
            apply_command,
            cwd=context.repo_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    else:
        patch_abs = str(patch_path.resolve())
        fallback_check = (
            "patch", "--dry-run", "-p1", "-F5",
            "-d", str(workspace_relative),
            "-i", patch_abs,
        )
        log_lines.append(f"git-apply failed, trying: {shlex.join(fallback_check)}")
        fb_check = subprocess.run(
            fallback_check,
            cwd=context.repo_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        log_lines.append(f"patch_check_return_code: {fb_check.returncode}")
        if fb_check.stdout:
            log_lines.extend(fb_check.stdout.rstrip().splitlines())
        if fb_check.returncode != 0:
            return PatchApplyResult(
                patch_path=patch_path,
                workspace_root=workspace_root,
                target_paths=target_paths,
                exit_code=fb_check.returncode or 1,
                status="patch_check_failed",
                log_lines=tuple(log_lines),
            )
        apply_tool = "patch"
        fallback_apply = (
            "patch", "-p1", "-F5", "-N",
            "-d", str(workspace_relative),
            "-i", patch_abs,
        )
        log_lines.append(f"apply_command: {shlex.join(fallback_apply)}")
        apply_result = subprocess.run(
            fallback_apply,
            cwd=context.repo_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

    log_lines.append(f"apply_return_code ({apply_tool}): {apply_result.returncode}")
    if apply_result.stdout:
        log_lines.extend(apply_result.stdout.rstrip().splitlines())
    status = "patch_applied_to_workspace" if apply_result.returncode == 0 else "patch_apply_failed"
    return PatchApplyResult(
        patch_path=patch_path,
        workspace_root=workspace_root,
        target_paths=target_paths,
        exit_code=apply_result.returncode,
        status=status,
        log_lines=tuple(log_lines),
    )


def extract_unified_diff_targets(diff_text: str) -> tuple[str, ...]:
    """Extract repository-relative target paths from a unified diff."""

    paths: list[str] = []
    for raw_line in diff_text.splitlines():
        line = raw_line.strip()
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                paths.extend(
                    path
                    for path in (
                        _strip_diff_path_prefix(parts[2]),
                        _strip_diff_path_prefix(parts[3]),
                    )
                    if path and path != "/dev/null"
                )
        elif line.startswith("--- ") or line.startswith("+++ "):
            value = line[4:].split("\t", 1)[0].strip()
            path = _strip_diff_path_prefix(value)
            if path and path != "/dev/null":
                paths.append(path)

    unique = tuple(sorted(set(paths)))
    if not unique:
        raise ValueError("source patch diff has no parseable target paths")
    return unique


def validate_source_patch_diff_targets(
    context: CycleContext,
    target_paths: tuple[str, ...],
) -> None:
    """Validate S4d target files stay in Flow Agent source-patch scope."""

    allowed_roots = tuple(
        context.resolve_repo_path(root) for root in SOURCE_PATCH_DIFF_ALLOWED_ROOTS
    )
    assignment_roots = tuple(
        context.resolve_repo_path(str(root))
        for root in context.assignment.get("allowed_to_edit", ())
        if str(root).strip()
    )
    for target in target_paths:
        path = Path(target)
        if path.is_absolute():
            raise ValueError(f"absolute source patch target is not allowed: {target}")
        resolved = context.resolve_repo_path(target)
        if not any(_path_is_under_or_equal(resolved, root) for root in allowed_roots):
            raise ValueError(f"source patch target is outside S4d scope: {target}")
        if assignment_roots and not any(
            _path_is_under_or_equal(resolved, root) for root in assignment_roots
        ):
            raise ValueError(
                f"source patch target is outside assignment allowed_to_edit: {target}"
            )


def validate_workspace_root(context: CycleContext, workspace_root: Path) -> None:
    expected_root = (
        context.repo_root
        / "experiments"
        / context.cycle_id
        / "impl_compare"
        / "candidate_modified"
    ).resolve()
    resolved = workspace_root.resolve()
    if not _path_is_under_or_equal(resolved, expected_root):
        raise ValueError(
            "workspace root must stay under "
            f"{expected_root.relative_to(context.repo_root)}"
        )


def reset_patch_workspace(
    context: CycleContext,
    workspace_root: Path,
    target_paths: tuple[str, ...],
    *,
    base_source_root: Path | None = None,
) -> None:
    """Recreate the isolated workspace with source needed by the patch."""

    if workspace_root.exists():
        validate_workspace_root(context, workspace_root)
        shutil.rmtree(workspace_root)
    workspace_root.mkdir(parents=True, exist_ok=True)

    copied_flowtune_source = False
    if any(_path_text_is_under(target, FLOWTUNE_SOURCE_ROOT) for target in target_paths):
        copy_flowtune_source_tree(
            context,
            workspace_root,
            source_root=base_source_root,
        )
        copied_flowtune_source = True

    for target in target_paths:
        if copied_flowtune_source and _path_text_is_under(target, FLOWTUNE_SOURCE_ROOT):
            continue
        source = context.repo_root / target
        destination = workspace_root / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_file():
            shutil.copy2(source, destination)


def copy_flowtune_source_tree(
    context: CycleContext,
    workspace_root: Path,
    *,
    source_root: Path | None = None,
) -> None:
    """Copy the FlowTune ABC source tree for an isolated candidate build."""

    source_root = source_root or context.repo_root / FLOWTUNE_SOURCE_ROOT
    destination = workspace_root / FLOWTUNE_SOURCE_ROOT
    if not source_root.is_dir():
        raise FileNotFoundError(f"missing FlowTune source root: {FLOWTUNE_SOURCE_ROOT}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source_root,
        destination,
        symlinks=True,
        ignore=shutil.ignore_patterns(
            "build",
            "*.o",
            "*.d",
            "libabc.a",
            "arch_flags",
            "abc_arch_flags_program.exe",
            ".DS_Store",
        ),
    )


def run_candidate_binary_build(
    *,
    context: CycleContext,
    workspace_root: Path,
    jobs: int,
    timeout_seconds: float,
) -> BuildGateResult:
    """Build the candidate ABC binary inside the isolated workspace."""

    source_dir = workspace_root / FLOWTUNE_SOURCE_ROOT
    binary_path = workspace_root / FLOWTUNE_SOURCE_ABC_BIN
    relative_source_dir = source_dir.relative_to(context.repo_root)
    relative_binary = binary_path.relative_to(context.repo_root)
    log_lines: list[str] = [
        "S4e candidate FlowTune binary build",
        f"source_dir: {relative_source_dir}",
        f"binary_path: {relative_binary}",
        "",
    ]
    if not (source_dir / "Makefile").is_file():
        log_lines.append("status: missing_makefile")
        return BuildGateResult(
            command_label=CANDIDATE_BINARY_BUILD_COMMAND_LABEL,
            exit_code=1,
            status="failed",
            log_lines=tuple(log_lines),
        )

    if binary_path.exists() and binary_path.is_file():
        binary_path.unlink()

    command = (
        "make",
        "-C",
        str(relative_source_dir),
        f"-j{jobs}",
        "ABC_USE_NO_READLINE=1",
    )
    log_lines.append(f"command: {shlex.join(command)}")
    try:
        completed = subprocess.run(
            command,
            cwd=context.repo_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
            check=False,
        )
    except OSError as exc:
        log_lines.extend(
            (
                f"return_code: none",
                f"status: exec_error:{exc.__class__.__name__}",
                str(exc),
            )
        )
        return BuildGateResult(
            command_label=CANDIDATE_BINARY_BUILD_COMMAND_LABEL,
            exit_code=1,
            status="failed",
            log_lines=tuple(log_lines),
        )
    except subprocess.TimeoutExpired as exc:
        log_lines.extend(
            (
                f"return_code: none",
                f"status: timeout_after_{timeout_seconds:g}s",
            )
        )
        if exc.stdout:
            log_lines.extend(str(exc.stdout).rstrip().splitlines())
        return BuildGateResult(
            command_label=CANDIDATE_BINARY_BUILD_COMMAND_LABEL,
            exit_code=1,
            status="failed",
            log_lines=tuple(log_lines),
        )

    log_lines.append(f"return_code: {completed.returncode}")
    if completed.stdout:
        log_lines.extend(completed.stdout.rstrip().splitlines())
    if completed.returncode != 0:
        return BuildGateResult(
            command_label=CANDIDATE_BINARY_BUILD_COMMAND_LABEL,
            exit_code=completed.returncode,
            status="failed",
            log_lines=tuple(log_lines),
        )
    if not binary_path.is_file():
        log_lines.append("status: missing_candidate_binary_after_build")
        return BuildGateResult(
            command_label=CANDIDATE_BINARY_BUILD_COMMAND_LABEL,
            exit_code=1,
            status="failed",
            log_lines=tuple(log_lines),
        )

    log_lines.append("status: candidate_binary_built")
    return BuildGateResult(
        command_label=CANDIDATE_BINARY_BUILD_COMMAND_LABEL,
        exit_code=0,
        status="passed",
        log_lines=tuple(log_lines),
    )


def run_python_smoke_gate(context: CycleContext) -> BuildGateResult:
    """Run S4c local build/smoke checks for the Flow Agent Python layer."""

    log_lines: list[str] = [
        "S4c Python smoke gate",
        f"repo_root: {context.repo_root}",
        "",
        "py_compile:",
    ]
    relative_files = tuple(Path(path) for path in PYTHON_SMOKE_FILES)
    missing_files = tuple(
        str(path)
        for path in relative_files
        if not (context.repo_root / path).is_file()
    )
    if missing_files:
        log_lines.extend(f"missing: {path}" for path in missing_files)
        return BuildGateResult(
            command_label=SMOKE_GATE_COMMAND_LABEL,
            exit_code=1,
            status="failed",
            log_lines=tuple(log_lines),
        )

    command = (
        sys.executable,
        "-B",
        "-m",
        "py_compile",
        *(str(path) for path in relative_files),
    )
    log_lines.append(f"command: {shlex.join(command)}")
    completed = subprocess.run(
        command,
        cwd=context.repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    log_lines.append(f"return_code: {completed.returncode}")
    if completed.stdout:
        log_lines.extend(completed.stdout.rstrip().splitlines())
    if completed.returncode != 0:
        return BuildGateResult(
            command_label=SMOKE_GATE_COMMAND_LABEL,
            exit_code=completed.returncode,
            status="failed",
            log_lines=tuple(log_lines),
        )

    fixture_exit_code = run_validation_fixture_smoke(context, log_lines)
    return BuildGateResult(
        command_label=SMOKE_GATE_COMMAND_LABEL,
        exit_code=fixture_exit_code,
        status="passed" if fixture_exit_code == 0 else "failed",
        log_lines=tuple(log_lines),
    )


def run_validation_fixture_smoke(
    context: CycleContext,
    log_lines: list[str],
) -> int:
    """Run valid/invalid response fixtures through the Flow validator.

    Fixtures are validated against a permissive context so that
    cycle-agnostic fixture paths (e.g. ``scripts/…``) never fail due
    to the current assignment's ``allowed_to_edit`` scope.
    """

    fixture_root = (
        context.repo_root / "scripts" / "agents" / "self_evolved_abc" / "fixtures"
    )
    failures = 0
    log_lines.extend(("", "validation_fixtures:"))
    for fixture_name, expected_ok in VALIDATION_FIXTURE_EXPECTATIONS:
        fixture_path = fixture_root / fixture_name
        if not fixture_path.is_file():
            log_lines.append(f"FAIL {fixture_name}: missing fixture")
            failures += 1
            continue

        try:
            payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log_lines.append(f"FAIL {fixture_name}: could not read JSON: {exc}")
            failures += 1
            continue

        if not isinstance(payload, Mapping):
            log_lines.append(f"FAIL {fixture_name}: top-level JSON is not an object")
            failures += 1
            continue

        fixture_context = build_fixture_validation_context(context, payload)
        result = validate_flow_agent_response(payload, fixture_context)
        if result.ok != expected_ok:
            log_lines.append(
                f"FAIL {fixture_name}: expected_ok={expected_ok} actual_ok={result.ok}"
            )
            for issue in result.issues:
                log_lines.append(f"  issue: {issue.field}: {issue.message}")
            failures += 1
            continue

        log_lines.append(f"PASS {fixture_name}: ok={result.ok}")

    log_lines.append(f"fixture_failures: {failures}")
    return 1 if failures else 0


def build_fixture_validation_context(
    context: CycleContext,
    payload: Mapping[str, object],
) -> CycleContext:
    """Create a permissive context for cycle-agnostic validation fixtures."""

    fixture_assignment = dict(context.assignment)
    candidate_kind = str(payload.get("candidate_kind", "")).strip()
    if candidate_kind in ("abc_flow", "source_patch_todo", "source_patch_diff"):
        fixture_assignment["source_patch_mode"] = candidate_kind

    permissive_allowed = list(fixture_assignment.get("allowed_to_edit", ()))
    for entry in (
        "scripts/agents/self_evolved_abc/flow",
        "scripts/agents/self_evolved_abc/coding_agents/flow_agent.py",
        "configs/agents/prompts",
        "configs/flows",
        "third_party/FlowTune/src/src/opt",
        "third_party/FlowTune/src/src/opt/nwk",
        "third_party/FlowTune/src/src/base/abci",
    ):
        if entry not in permissive_allowed:
            permissive_allowed.append(entry)
    fixture_assignment["allowed_to_edit"] = permissive_allowed
    return CycleContext(
        repo_root=context.repo_root,
        assignment=fixture_assignment,
    )


def sha256_file(path: Path) -> str | None:
    """Return a file SHA256 digest, or None when the file is absent."""

    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _display_path(context: CycleContext, path: Path) -> str:
    if path.is_absolute():
        try:
            return str(path.resolve().relative_to(context.repo_root))
        except ValueError:
            return str(path)
    return str(path)


def _absolute_maybe_repo_path(context: CycleContext, path: Path) -> Path:
    if path.is_absolute():
        return path.resolve()
    return context.resolve_repo_path(str(path))


def _path_text_is_under(path_text: str, root: Path) -> bool:
    path = Path(path_text)
    return path == root or root in path.parents


def _path_is_under_or_equal(path: Path, allowed_root: Path) -> bool:
    if path == allowed_root:
        return True
    try:
        path.relative_to(allowed_root)
    except ValueError:
        return False
    return True


def _git_output(
    repo_root: Path,
    args: tuple[str, ...],
    *,
    allow_diff_exit: bool = False,
) -> str | None:
    try:
        completed = subprocess.run(
            ("git", *args),
            cwd=repo_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return None
    if completed.returncode != 0 and not (
        allow_diff_exit and completed.returncode == 1
    ):
        return None
    return completed.stdout.strip()


def _git_status(repo_root: Path) -> tuple[str, ...]:
    output = _git_output(repo_root, ("status", "--short"))
    if not output:
        return ()
    return tuple(line for line in output.splitlines() if line.strip())


def _is_tracked(repo_root: Path, target: str) -> bool:
    try:
        completed = subprocess.run(
            ("git", "ls-files", "--error-unmatch", target),
            cwd=repo_root,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return False
    return completed.returncode == 0


def _diff_untracked_file(repo_root: Path, path: Path) -> str:
    try:
        completed = subprocess.run(
            ("git", "diff", "--no-index", "--", "/dev/null", str(path)),
            cwd=repo_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(f"failed to diff untracked file: {path}") from exc
    if completed.returncode not in (0, 1):
        raise RuntimeError(f"failed to diff untracked file: {path}")
    return completed.stdout.strip()


def _strip_diff_path_prefix(path: str) -> str:
    if path in ("/dev/null", "dev/null"):
        return "/dev/null"
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path


if __name__ == "__main__":
    raise SystemExit(main())
