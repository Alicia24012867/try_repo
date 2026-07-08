"""Source patch proposal artifacts for Flow Agent candidates."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from scripts.agents.self_evolved_abc.cycle_context import CycleContext
from scripts.agents.self_evolved_abc.schemas import FlowAgentResponse, markdown_bullets


def source_patch_plan_relative_path(context: CycleContext) -> Path:
    """Return the review artifact path for one source patch proposal."""

    return (
        Path("experiments")
        / context.cycle_id
        / "agents"
        / "source_patch_todos"
        / f"{context.candidate_id}.md"
    )


def source_patch_diff_relative_path(context: CycleContext) -> Path:
    """Return the machine-applicable diff artifact path."""

    return (
        Path("experiments")
        / context.cycle_id
        / "agents"
        / "source_patches"
        / f"{context.candidate_id}.diff"
    )


def source_patch_plan_path(context: CycleContext) -> Path:
    """Return the absolute source patch proposal artifact path."""

    return context.repo_root / source_patch_plan_relative_path(context)


def source_patch_diff_path(context: CycleContext) -> Path:
    """Return the absolute machine-applicable diff artifact path."""

    return context.repo_root / source_patch_diff_relative_path(context)


def write_source_patch_plan(
    *,
    context: CycleContext,
    response: FlowAgentResponse,
    evidence: Mapping[str, str],
) -> Path:
    """Write a proposal-only source patch plan artifact."""

    path = source_patch_plan_path(context)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_source_patch_plan_markdown(
            context=context,
            response=response,
            evidence=evidence,
        ),
        encoding="utf-8",
    )
    return path


def write_source_patch_diff(
    *,
    context: CycleContext,
    response: FlowAgentResponse,
) -> Path:
    """Write the validated source patch unified diff without applying it."""

    source_patch = response.source_patch or {}
    diff_text = str(source_patch.get("diff", "")).strip()
    if not diff_text:
        raise ValueError("source_patch_diff response has no diff text")
    path = source_patch_diff_path(context)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(diff_text.rstrip() + "\n", encoding="utf-8")
    return path


def render_source_patch_plan_markdown(
    *,
    context: CycleContext,
    response: FlowAgentResponse,
    evidence: Mapping[str, str],
) -> str:
    """Render a reviewable plan without applying the proposed source patch."""

    source_targets, artifact_targets = split_requested_files(context, response)
    return "\n".join(
        (
            f"# Source Patch Proposal -- {context.cycle_id} {context.candidate_id}",
            "",
            "## Status",
            "",
            "- Candidate kind: `source_patch_todo`",
            "- Materialization: proposal-only",
            "- Source patch applied: no",
            "- Proposed target source files written: no",
            "- Next gate: S4 source patch application and compile validation",
            "",
            "## Rationale",
            "",
            response.rationale,
            "",
            "## Expected Effect",
            "",
            response.expected_effect,
            "",
            "## Proposed Target Files",
            "",
            markdown_bullets(source_targets).rstrip(),
            "",
            "## Candidate Artifact Paths",
            "",
            markdown_bullets(artifact_targets).rstrip(),
            "",
            "## Candidate Steps",
            "",
            markdown_bullets(response.candidate_steps).rstrip(),
            "",
            "## Entry Points",
            "",
            markdown_bullets(response.entry_points).rstrip(),
            "",
            "## Invariants",
            "",
            markdown_bullets(response.invariants).rstrip(),
            "",
            "## Risk Hotspots",
            "",
            markdown_bullets(response.risk_hotspots).rstrip(),
            "",
            "## Validation Plan",
            "",
            markdown_bullets(response.validation_plan).rstrip(),
            "",
            "## Risks",
            "",
            markdown_bullets(response.risks).rstrip(),
            "",
            "## Rollback Plan",
            "",
            response.rollback_plan,
            "",
            "## Compatibility Notes",
            "",
            markdown_bullets(
                f"{key}: {value}" for key, value in response.compatibility_notes.items()
            ).rstrip(),
            "",
            "## Evidence Files",
            "",
            markdown_bullets(evidence.keys()).rstrip(),
            "",
            "## Guardrails",
            "",
            "- This artifact is not a patch file.",
            "- Do not treat this proposal as applied implementation evidence.",
            "- Do not compare QoR against this proposal until S4/S5 build and implementation comparison exist.",
            "- Do not modify benchmarks, previous-cycle results, or `third_party/FlowTune/` unless a later assignment explicitly expands scope.",
            "",
        )
    )


def split_requested_files(
    context: CycleContext,
    response: FlowAgentResponse,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split model-requested files into source targets and artifact paths."""

    artifact_prefix = f"experiments/{context.cycle_id}/agents/"
    source_targets: list[str] = []
    artifact_targets: list[str] = []
    for path in response.files_to_write:
        if path == str(source_patch_plan_relative_path(context)):
            artifact_targets.append(path)
        elif path.startswith(artifact_prefix):
            artifact_targets.append(path)
        else:
            source_targets.append(path)
    return tuple(source_targets), tuple(artifact_targets)
