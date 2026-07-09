"""Materialize validated Flow Agent responses into reviewable artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from scripts.agents.self_evolved_abc.cycle_context import CycleContext
from scripts.agents.self_evolved_abc.flow.contracts import (
    FLOW_CANDIDATE_ABC_FLOW,
    FLOW_CANDIDATE_SOURCE_PATCH_DIFF,
    FLOW_CANDIDATE_SOURCE_PATCH_TODO,
)
from scripts.agents.self_evolved_abc.flow.artifacts import (
    render_validated_flow_artifacts,
)
from scripts.agents.self_evolved_abc.flow.source_patch import (
    source_patch_diff_relative_path,
    source_patch_plan_relative_path,
    write_source_patch_diff,
    write_source_patch_plan,
)
from scripts.agents.self_evolved_abc.schemas import AgentArtifacts, FlowAgentResponse


FLOW_STATUS_WRITTEN = "written"
FLOW_STATUS_SOURCE_PATCH_DIFF = "source_patch_diff"
FLOW_STATUS_SOURCE_PATCH_TODO = "source_patch_todo"
FLOW_STATUS_SKIPPED_BY_DECISION = "skipped_by_decision"
FLOW_STATUS_SKIPPED_BY_CANDIDATE_KIND = "skipped_by_candidate_kind"


@dataclass(frozen=True)
class FlowMaterializationResult:
    """Outcome of turning a validated Flow Agent response into artifacts."""

    artifacts: AgentArtifacts
    flow_path: Path | None
    written_files: tuple[Path, ...]
    materialization_status: str


def candidate_flow_relative_path(context: CycleContext) -> Path:
    """Return configs/flows/<cycle>_<candidate>.abc."""

    return (
        Path("configs")
        / "flows"
        / f"{context.cycle_id}_{context.candidate_id}.abc"
    )


def candidate_flow_path(context: CycleContext) -> Path:
    """Return the absolute path for the candidate ABC flow."""

    return context.repo_root / candidate_flow_relative_path(context)


def should_materialize_flow(response: FlowAgentResponse) -> bool:
    """Return True only for PROPOSE_CANDIDATE + abc_flow."""

    return (
        response.decision == "PROPOSE_CANDIDATE"
        and response.candidate_kind == FLOW_CANDIDATE_ABC_FLOW
    )


def render_abc_flow_script(commands: tuple[str, ...]) -> str:
    """Render one validated ABC command per line, each ending with ';'."""

    lines: list[str] = []
    for command in commands:
        normalized = command.strip().rstrip(";").strip()
        if not normalized:
            raise ValueError("validated ABC flow command must not be empty")
        lines.append(f"{normalized};")
    return "\n".join(lines) + "\n"


def write_abc_flow_script(path: Path, script: str) -> Path:
    """Create parent directories and write the ABC flow script."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script, encoding="utf-8")
    return path


def materialize_validated_flow_response(
    *,
    response: FlowAgentResponse,
    context: CycleContext,
    evidence: Mapping[str, str],
) -> FlowMaterializationResult:
    """Write the flow when allowed and return final artifacts."""

    relative_flow_path = candidate_flow_relative_path(context)
    absolute_flow_path = candidate_flow_path(context)
    relative_source_patch_plan = source_patch_plan_relative_path(context)
    relative_source_patch_diff = source_patch_diff_relative_path(context)
    status = _materialization_status(response)

    written_files: tuple[Path, ...] = ()
    if status == FLOW_STATUS_WRITTEN:
        script = render_abc_flow_script(response.candidate_steps)
        written_files = (write_abc_flow_script(absolute_flow_path, script),)
    elif status == FLOW_STATUS_SOURCE_PATCH_TODO:
        written_files = (
            write_source_patch_plan(
                context=context,
                response=response,
                evidence=evidence,
            ),
        )
    elif status == FLOW_STATUS_SOURCE_PATCH_DIFF:
        written_files = (write_source_patch_diff(context=context, response=response),)

    artifacts = render_validated_flow_artifacts(
        paper_role=context.paper_role,
        candidate_id=context.candidate_id,
        response=response,
        evidence=evidence,
        materialization_status=status,
        flow_path=relative_flow_path if status == FLOW_STATUS_WRITTEN else None,
        source_patch_plan_path=(
            relative_source_patch_plan
            if status == FLOW_STATUS_SOURCE_PATCH_TODO and written_files
            else None
        ),
        source_patch_diff_path=(
            relative_source_patch_diff
            if status == FLOW_STATUS_SOURCE_PATCH_DIFF and written_files
            else None
        ),
        written_files=_relative_paths(context, written_files),
    )

    return FlowMaterializationResult(
        artifacts=artifacts,
        flow_path=absolute_flow_path if status == FLOW_STATUS_WRITTEN else None,
        written_files=written_files,
        materialization_status=status,
    )


def _materialization_status(response: FlowAgentResponse) -> str:
    if should_materialize_flow(response):
        return FLOW_STATUS_WRITTEN
    if (
        response.decision == "PROPOSE_CANDIDATE"
        and response.candidate_kind == FLOW_CANDIDATE_SOURCE_PATCH_TODO
    ):
        return FLOW_STATUS_SOURCE_PATCH_TODO
    if (
        response.decision == "PROPOSE_CANDIDATE"
        and response.candidate_kind == FLOW_CANDIDATE_SOURCE_PATCH_DIFF
    ):
        return FLOW_STATUS_SOURCE_PATCH_DIFF
    if response.decision != "PROPOSE_CANDIDATE":
        return FLOW_STATUS_SKIPPED_BY_DECISION
    return FLOW_STATUS_SKIPPED_BY_CANDIDATE_KIND


def _relative_paths(
    context: CycleContext,
    paths: tuple[Path, ...],
) -> tuple[Path, ...]:
    relative_paths: list[Path] = []
    for path in paths:
        try:
            relative_paths.append(path.relative_to(context.repo_root))
        except ValueError:
            relative_paths.append(path)
    return tuple(relative_paths)
