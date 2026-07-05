"""Markdown artifact rendering for Flow Agent replies."""

from __future__ import annotations

import json
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
) -> AgentArtifacts:
    """Create markdown artifacts from a validated Flow Agent response."""

    compatibility = json.dumps(
        dict(response.compatibility_notes),
        indent=2,
        sort_keys=True,
    )

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
            "- Local status: validated_not_materialized\n"
            "- `.abc` flow file: not written yet\n\n"
            "## Candidate Steps\n\n"
            f"{markdown_bullets(response.candidate_steps)}\n"
            "## Files To Write Later\n\n"
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
            "- materialization_status: not_run\n"
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
