"""Exact, role-scoped validation evidence passed between agent rounds."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Mapping

from scripts.agents.self_evolved_abc.cycle_context import CycleContext
from scripts.agents.self_evolved_abc.workflow.artifacts import (
    agent_attempt_root,
    review_decision_path,
    safe_repo_path,
)


VALIDATION_ISSUES_HEADING = "Validation Issues"
_VALIDATION_HEADING_RE = re.compile(
    r"(?m)^##[ \t]+Validation Issues[ \t]*$"
)
_NEXT_SECTION_RE = re.compile(r"(?m)^##[ \t]+[^\n]+$")


def extract_validation_issues_markdown(feedback_path: Path) -> str:
    """Return the complete local Validation Issues section without truncation."""

    if not feedback_path.is_file():
        return ""
    text = feedback_path.read_text(encoding="utf-8", errors="replace")
    return _extract_validation_issues_text(text)


def _extract_validation_issues_text(text: str) -> str:
    heading = _VALIDATION_HEADING_RE.search(text)
    if heading is None:
        return ""
    following = text[heading.end() :]
    next_section = _NEXT_SECTION_RE.search(following)
    if next_section is not None:
        following = following[: next_section.start()]
    return following.strip()


def validation_feedback_payload(
    context: CycleContext,
) -> dict[str, object] | None:
    """Build one role-tagged record from the strongest durable source.

    The branch manifest hashes the review, so review-embedded evidence is
    authoritative. A SHA-verified terminal-attempt snapshot is next. The
    shared feedback Markdown remains a fallback for legacy runs and while the
    terminal review is being materialized.
    """

    review_payload = _review_validation_payload(context)
    if review_payload is not None:
        return review_payload
    attempt_payload = _terminal_attempt_validation_payload(context)
    if attempt_payload is not None:
        return attempt_payload

    feedback_path = context.artifact_paths().feedback
    issues = extract_validation_issues_markdown(feedback_path)
    if not issues:
        return None
    return _validation_payload(
        context,
        source=feedback_path.relative_to(context.repo_root).as_posix(),
        issues=issues,
    )


def _review_validation_payload(
    context: CycleContext,
) -> dict[str, object] | None:
    path = review_decision_path(context)
    payload = _read_json_object(path)
    if payload is None:
        return None
    if (
        payload.get("cycle_id") != context.cycle_id
        or payload.get("candidate_id") != context.candidate_id
        or payload.get("decision") != "REPAIR_VALIDATION"
        or payload.get("build_status") != "agent_response_validation_failed"
    ):
        return None
    issues = str(payload.get("validation_issues_markdown", "")).strip()
    if not issues:
        return None
    expected_hash = str(payload.get("validation_evidence_sha256", "")).strip()
    actual_hash = hashlib.sha256(issues.encode("utf-8")).hexdigest()
    if not expected_hash or expected_hash != actual_hash:
        return None
    return _validation_payload(
        context,
        source=str(payload.get("validation_evidence_source", "")).strip()
        or path.relative_to(context.repo_root).as_posix(),
        issues=issues,
        evidence_type=str(
            payload.get(
                "validation_evidence_type", "local_response_validation"
            )
        ).strip(),
    )


def _terminal_attempt_validation_payload(
    context: CycleContext,
) -> dict[str, object] | None:
    root = agent_attempt_root(context)
    statuses: list[tuple[int, Mapping[str, object]]] = []
    for path in root.glob("attempt_*.status.json") if root.is_dir() else ():
        payload = _read_json_object(path)
        if payload is None:
            continue
        attempt = payload.get("attempt")
        if not isinstance(attempt, int) or isinstance(attempt, bool):
            continue
        if (
            payload.get("cycle_id") == context.cycle_id
            and payload.get("candidate_id") == context.candidate_id
            and payload.get("agent_name") == context.agent_name
        ):
            statuses.append((attempt, payload))
    if not statuses:
        return None
    _, terminal = max(statuses, key=lambda item: item[0])
    if (
        terminal.get("decision") != "NEEDS_HUMAN_REVIEW"
        or terminal.get("failure_kind") != "response_validation"
    ):
        return None
    relative = str(terminal.get("validation_feedback_path", "")).strip()
    expected_hash = str(terminal.get("validation_feedback_sha256", "")).strip()
    if not relative or not expected_hash:
        return None
    try:
        path = safe_repo_path(context.repo_root, context.repo_root / relative)
        data = path.read_bytes()
    except (OSError, ValueError):
        return None
    if hashlib.sha256(data).hexdigest() != expected_hash:
        return None
    issues = _extract_validation_issues_text(
        data.decode("utf-8", errors="replace")
    )
    if not issues:
        return None
    return _validation_payload(context, source=relative, issues=issues)


def _validation_payload(
    context: CycleContext,
    *,
    source: str,
    issues: str,
    evidence_type: str = "local_response_validation",
) -> dict[str, object]:
    branch_role = str(context.assignment.get("branch_role", "")).strip()
    if not branch_role:
        branch_role = (
            "logic"
            if context.agent_name == "logic_minimization_agent"
            else "flow"
        )
    return {
        "schema_version": 1,
        "evidence_type": evidence_type or "local_response_validation",
        "cycle_id": context.cycle_id,
        "branch_role": branch_role,
        "agent_name": context.agent_name,
        "candidate_id": context.candidate_id,
        "source": source,
        "issues_markdown": issues,
        "issues_sha256": hashlib.sha256(issues.encode("utf-8")).hexdigest(),
    }


def _read_json_object(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def format_validation_feedback(
    feedback: Mapping[str, object],
    *,
    branch_role: str | None = None,
    agent_name: str | None = None,
    candidate_id: str | None = None,
) -> str:
    """Render exact issues with coordinator-owned role and identity labels."""

    issues = str(feedback.get("issues_markdown", "")).strip()
    if not issues:
        return ""
    role = (branch_role or str(feedback.get("branch_role", ""))).strip()
    agent = (agent_name or str(feedback.get("agent_name", ""))).strip()
    candidate = (
        candidate_id or str(feedback.get("candidate_id", ""))
    ).strip()
    cycle = str(feedback.get("cycle_id", "")).strip()
    source = str(feedback.get("source", "")).strip()
    evidence_type = str(
        feedback.get("evidence_type", "local_response_validation")
    ).strip()
    lines = [
        f"### {(role or 'unknown').upper()} branch exact validation issues",
        f"- evidence_type: {evidence_type or 'local_response_validation'}",
        f"- cycle_id: {cycle or 'missing'}",
        f"- agent_name: {agent or 'missing'}",
        f"- candidate_id: {candidate or 'missing'}",
        f"- source: {source or 'missing'}",
        "- issues (verbatim local validator output):",
        issues,
    ]
    return "\n".join(lines)
