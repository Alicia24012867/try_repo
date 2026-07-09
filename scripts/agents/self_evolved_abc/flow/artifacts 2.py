"""Markdown artifact rendering for Flow Agent replies."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from scripts.agents.self_evolved_abc.model_client import ModelReply
from scripts.agents.self_evolved_abc.schemas import (
    AgentArtifacts,
    FlowAgentResponse,
    ValidationIssue,
    markdown_bullets,
)


def render_flow_validation_failure_artifacts(
    *,
    paper_role: str,
    candidate_id: str,
    reply: ModelReply,
    issues: tuple[ValidationIssue, ...],
    evidence: Mapping[str, str],
) -> AgentArtifacts:
    """Create NEEDS_HUMAN_REVIEW artifacts for invalid Flow Agent output."""

    issue_lines = [
        f"- `{issue.severity}` `{issue.field}`: {issue.message}"
        for issue in issues
    ]
    issue_markdown = "\n".join(issue_lines) + "\n" if issue_lines else "- None.\n"
    raw_preview = reply.raw_text[:2000]
    parsed_keys = sorted(str(key) for key in reply.parsed_json.keys())

    return AgentArtifacts(
        plan_markdown=(
            f"# {paper_role} Plan -- {candidate_id}\n\n"
            "## Status\n\n"
            "Validation failed before a candidate plan was accepted.\n\n"
            "## Evidence Files\n\n"
            f"{markdown_bullets(evidence.keys())}"
        ),
        candidate_markdown=(
            f"# {paper_role} Candidate -- {candidate_id}\n\n"
            "- Decision: NEEDS_HUMAN_REVIEW\n"
            "- Candidate materialization: not_run\n"
            "- Flow file written: no\n\n"
            "## Parsed JSON Keys\n\n"
            f"{markdown_bullets(parsed_keys)}"
        ),
        feedback_markdown=(
            f"# {paper_role} Feedback -- {candidate_id}\n\n"
            "## Validation Issues\n\n"
            f"{issue_markdown}\n"
            "## Raw Model Text Preview\n\n"
            "```json\n"
            f"{raw_preview}\n"
            "```\n\n"
            "## Local Status\n\n"
            "- validation_status: failed\n"
            "- decision: NEEDS_HUMAN_REVIEW\n"
            "- `.abc` flow file: not written\n"
        ),
        rule_update_markdown=(
            f"# {paper_role} Rule Updates -- {candidate_id}\n\n"
            "- No active rulebase update was applied.\n"
            "- Validation failed before rule proposals could be accepted.\n"
        ),
        decision="NEEDS_HUMAN_REVIEW",
    )


def render_validated_flow_artifacts(
    *,
    paper_role: str,
    candidate_id: str,
    response: FlowAgentResponse,
    evidence: Mapping[str, str],
    materialization_status: str = "not_run",
    flow_path: Path | None = None,
    source_patch_plan_path: Path | None = None,
    source_patch_diff_path: Path | None = None,
    written_files: tuple[Path, ...] = (),
) -> AgentArtifacts:
    """Create markdown artifacts from a validated Flow Agent response."""

    compatibility = json.dumps(
        dict(response.compatibility_notes),
        indent=2,
        sort_keys=True,
    )
    flow_path_text = str(flow_path) if flow_path is not None else "not written"
    source_patch_plan_text = (
        str(source_patch_plan_path)
        if source_patch_plan_path is not None
        else "not written"
    )
    source_patch_diff_text = (
        str(source_patch_diff_path)
        if source_patch_diff_path is not None
        else "not written"
    )
    flow_file_written = "yes" if written_files else "no"

    return AgentArtifacts(
        plan_markdown=(
            f"# {paper_role} Plan -- {candidate_id}\n\n"
            "## Rationale\n\n"
            f"{response.rationale}\n\n"
            "## Source Design\n\n"
            f"{response.source_design or 'None specified.'}\n\n"
            "## Entry Points\n\n"
            f"{markdown_bullets(response.entry_points)}\n"
            "## Invariants\n\n"
            f"{markdown_bullets(response.invariants)}\n"
            "## Risk Hotspots\n\n"
            f"{markdown_bullets(response.risk_hotspots)}"
        ),
        candidate_markdown=(
            f"# {paper_role} Candidate -- {candidate_id}\n\n"
            f"- Decision: {response.decision}\n"
            f"- Candidate kind: {response.candidate_kind}\n"
            "- Local status: validated\n"
            f"- Candidate materialization: {materialization_status}\n"
            f"- `.abc` flow file: {flow_path_text}\n"
            f"- Source patch plan: {source_patch_plan_text}\n"
            f"- Source patch diff: {source_patch_diff_text}\n"
            f"- Flow file written: {flow_file_written}\n\n"
            "## Materialization Notes\n\n"
            f"{_materialization_notes(materialization_status)}\n"
            "## Candidate Steps\n\n"
            f"{markdown_bullets(response.candidate_steps)}\n"
            "## Written Files\n\n"
            f"{markdown_bullets(written_files)}\n"
            "## Model Requested Files\n\n"
            f"{markdown_bullets(response.files_to_write)}\n"
            "## Expected Effect\n\n"
            f"{response.expected_effect}\n\n"
            "## Compatibility Notes\n\n"
            "```json\n"
            f"{compatibility}\n"
            "```\n\n"
            "## Evidence Files\n\n"
            f"{markdown_bullets(evidence.keys())}"
        ),
        feedback_markdown=(
            f"# {paper_role} Feedback -- {candidate_id}\n\n"
            "## Local Status\n\n"
            "- validation_status: passed\n"
            f"- materialization_status: {materialization_status}\n"
            f"- candidate_flow_path: {flow_path_text}\n"
            f"- source_patch_plan_path: {source_patch_plan_text}\n"
            f"- source_patch_diff_path: {source_patch_diff_text}\n"
            f"- flow_file_written: {flow_file_written}\n"
            "- correctness_status: provisional_until_CEC\n\n"
            "## Validation Plan\n\n"
            f"{markdown_bullets(response.validation_plan)}\n"
            "## Risks\n\n"
            f"{markdown_bullets(response.risks)}\n"
            "## Rollback Plan\n\n"
            f"{response.rollback_plan}\n"
        ),
        rule_update_markdown=(
            f"# {paper_role} Rule Updates -- {candidate_id}\n\n"
            "Active rulebase was not modified.\n\n"
            "## Proposed Updates\n\n"
            f"{markdown_bullets(response.rule_updates)}"
        ),
        decision=response.decision,
    )


def _materialization_notes(status: str) -> str:
    notes = {
        "written": (
            "- Wrote a runner-owned ABC flow script under `configs/flows/`.\n"
            "- Benchmark `read` and result `write` commands remain outside the script.\n"
            "- Re-running the same candidate overwrites the deterministic flow path.\n"
        ),
        "skipped_by_decision": (
            "- The validated response did not authorize a candidate file.\n"
            "- No `.abc` flow script was written.\n"
        ),
        "skipped_by_candidate_kind": (
            "- The validated response is not an `abc_flow` candidate.\n"
            "- No `.abc` flow script was written.\n"
        ),
        "source_patch_todo": (
            "- Wrote a source patch proposal artifact under the active cycle agent directory.\n"
            "- The proposed target source files were not modified.\n"
            "- Review this plan before any S4 source patch application or build comparison.\n"
        ),
        "source_patch_diff": (
            "- Wrote a machine-applicable unified diff under the active cycle agent directory.\n"
            "- The source tree was not modified during materialization.\n"
            "- Apply only through the isolated S4d source patch runner before build comparison.\n"
        ),
    }
    return notes.get(status, "- No materialization action was taken.\n")
