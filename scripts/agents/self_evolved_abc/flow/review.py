"""Review CEC-first implementation comparison results for the next cycle."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

from scripts.agents.self_evolved_abc.cycle_context import CycleContext
from scripts.agents.self_evolved_abc.flow.contracts import (
    CANDIDATE_BUILD_READY_STATUSES,
    IMPL_CANDIDATE_LABEL,
)
from scripts.agents.self_evolved_abc.flow.paths import impl_compare_root, repo_path


@dataclass(frozen=True)
class ReviewDecision:
    cycle_id: str
    candidate_id: str
    decision: str
    champion_update: bool
    promotion_allowed: bool
    build_status: str
    cec_pass_count: int
    cec_total_count: int
    correctness_backed_rows: int
    average_and_improve_pct: float | None
    reason: str
    next_action: str


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create review feedback from implementation comparison artifacts."
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--assignment", type=Path, required=True)
    parser.add_argument(
        "--impl-compare-root",
        type=Path,
        default=None,
        help="Defaults to experiments/<cycle>/impl_compare.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    context = CycleContext.from_assignment_file(args.repo_root.resolve(), args.assignment)
    impl_root = (
        repo_path(context, args.impl_compare_root)
        if args.impl_compare_root is not None
        else impl_compare_root(context)
    )
    decision = review_impl_compare(context, impl_root)
    paths = write_review_artifacts(context, impl_root, decision)
    print(f"review_decision: {paths['decision']}")
    print(f"feedback: {paths['feedback']}")
    print(f"rule_update: {paths['rule_update']}")
    print(f"decision: {decision.decision}")
    return 0 if decision.promotion_allowed else 1


def review_impl_compare(context: CycleContext, impl_root: Path) -> ReviewDecision:
    build_status = read_build_status(impl_root)
    cec_rows = read_csv(impl_root / "comparison" / "cec_summary.csv")
    qor_rows = read_csv(impl_root / "comparison" / "qor_delta.csv")
    cec_pass = sum(1 for row in cec_rows if row.get("cec_status") == "cec_pass")
    backed_rows = [row for row in qor_rows if str(row.get("correctness_backed")).lower() == "true"]
    avg_and = average(
        parse_float(row.get("and_improve_pct"))
        for row in backed_rows
        if row.get("and_improve_pct") not in ("", None)
    )

    promotion = False
    if build_status not in CANDIDATE_BUILD_READY_STATUSES:
        decision, reason, next_action = _classify_build_failure(build_status)
    elif not cec_rows:
        decision = "REPAIR_EVALUATION"
        reason = "CEC summary is missing or empty"
        next_action = "Run S5/F7 implementation comparison before judging QoR."
    elif cec_pass != len(cec_rows):
        decision = "REJECT_CEC"
        reason = f"CEC passed {cec_pass}/{len(cec_rows)} rows"
        next_action = "Reject or repair the candidate before any QoR discussion."
    elif not backed_rows:
        decision = "REPAIR_EVALUATION"
        reason = "No correctness-backed QoR rows are available"
        next_action = "Re-run S5/F7 and ensure qor_delta rows are CEC-backed."
    elif avg_and is not None and avg_and > 0:
        decision = "ACCEPT_FOR_NEXT_CYCLE"
        reason = f"All CEC rows passed and average AND improvement is {avg_and:.6f}%"
        next_action = "Use this candidate as positive evidence for the next Flow Agent cycle."
        promotion = True
    else:
        decision = "REPAIR_QOR"
        reason = "Correctness passed but QoR did not improve on the target metric"
        next_action = "Feed QoR deltas back to the Flow Agent and request a smaller repair."

    return ReviewDecision(
        cycle_id=context.cycle_id,
        candidate_id=context.candidate_id,
        decision=decision,
        champion_update=promotion,
        promotion_allowed=promotion,
        build_status=build_status or "missing",
        cec_pass_count=cec_pass,
        cec_total_count=len(cec_rows),
        correctness_backed_rows=len(backed_rows),
        average_and_improve_pct=avg_and,
        reason=reason,
        next_action=next_action,
    )


def write_review_artifacts(
    context: CycleContext,
    impl_root: Path,
    decision: ReviewDecision,
) -> dict[str, Path]:
    paths = context.artifact_paths()
    paths.ensure_parent_dirs()
    decision_path = impl_root / "comparison" / "review_decision.json"
    decision_path.parent.mkdir(parents=True, exist_ok=True)
    decision_path.write_text(
        json.dumps(asdict(decision), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    # Merge: preserve agent-level diagnostics when review adds its own feedback.
    # This ensures model validation errors aren't lost when the review gate
    # reports a build/smoke failure.
    agent_feedback = _read_existing_feedback(paths.feedback)
    review_body = render_feedback(context, impl_root, decision)
    if agent_feedback and _feedback_has_diagnostics(agent_feedback):
        paths.feedback.write_text(
            agent_feedback.rstrip()
            + "\n\n---\n\n"
            + "## Review Gate (below — appended by review.py)\n\n"
            + review_body,
            encoding="utf-8",
        )
    else:
        paths.feedback.write_text(review_body, encoding="utf-8")
    # Merge rule_updates: preserve agent-proposed rules alongside review rules
    agent_rules = _read_existing_feedback(paths.rule_update)
    review_rules = render_rule_update(context, impl_root, decision)
    if agent_rules and "## Proposed Updates" in agent_rules:
        paths.rule_update.write_text(
            agent_rules.rstrip()
            + "\n\n---\n\n"
            + "## Review Rule Update (below)\n\n"
            + review_rules,
            encoding="utf-8",
        )
    else:
        paths.rule_update.write_text(review_rules, encoding="utf-8")
    return {
        "decision": decision_path,
        "feedback": paths.feedback,
        "rule_update": paths.rule_update,
    }


def _read_existing_feedback(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _feedback_has_diagnostics(text: str) -> bool:
    """True when the feedback contains agent-level info worth preserving."""
    return (
        "## Validation Issues" in text
        or "## Local Status" in text
        or "## Raw Model Text Preview" in text
    )


def render_feedback(
    context: CycleContext,
    impl_root: Path,
    decision: ReviewDecision,
) -> str:
    return "\n".join(
        (
            f"# Flow Agent Feedback -- {context.candidate_id}",
            "",
            "## Review Decision",
            "",
            f"- Decision: `{decision.decision}`",
            f"- Promotion allowed: `{str(decision.promotion_allowed).lower()}`",
            f"- Champion update: `{str(decision.champion_update).lower()}`",
            f"- Reason: {decision.reason}",
            f"- Next action: {decision.next_action}",
            "",
            "## Gates",
            "",
            f"- Build status: `{decision.build_status}`",
            f"- CEC pass: {decision.cec_pass_count}/{decision.cec_total_count}",
            f"- Correctness-backed QoR rows: {decision.correctness_backed_rows}",
            f"- Average AND improvement pct: `{format_float(decision.average_and_improve_pct)}`",
            "",
            "## Evidence",
            "",
            f"- `{impl_root.relative_to(context.repo_root) / 'comparison' / 'impl_compare_summary.md'}`",
            f"- `{impl_root.relative_to(context.repo_root) / 'comparison' / 'cec_summary.csv'}`",
            f"- `{impl_root.relative_to(context.repo_root) / 'comparison' / 'qor_delta.csv'}`",
            f"- `{impl_root.relative_to(context.repo_root) / IMPL_CANDIDATE_LABEL / 'patch.diff'}`",
            "",
        )
    )


def render_rule_update(
    context: CycleContext,
    impl_root: Path,
    decision: ReviewDecision,
) -> str:
    if decision.promotion_allowed:
        rule = (
            "Flow Agent source patches may be used as positive next-cycle evidence "
            "only after build/smoke, full CEC, and correctness-backed QoR deltas pass."
        )
    elif decision.decision == "REJECT_CEC":
        rule = (
            "Any source patch that fails or skips CEC must be rejected or repaired "
            "before QoR deltas are considered."
        )
    else:
        rule = (
            "Keep Flow Agent source-patch edits conservative until implementation "
            "comparison produces correctness-backed QoR improvement."
        )
    return "\n".join(
        (
            f"# Flow Agent Rule Updates -- {context.candidate_id}",
            "",
            "Active rulebase was not modified.",
            "",
            "## Proposed Update",
            "",
            f"- {rule}",
            "",
            "## Evidence",
            "",
            f"- `{impl_root.relative_to(context.repo_root) / 'comparison' / 'review_decision.json'}`",
            "",
        )
    )


def read_build_status(impl_root: Path) -> str | None:
    path = impl_root / IMPL_CANDIDATE_LABEL / "build_info.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return str(payload.get("status", "")).strip() or None


def _classify_build_failure(build_status: str | None) -> tuple[str, str, str]:
    """Map a concrete build status to a precise review decision.

    This replaces the old catch-all ``REPAIR_BUILD`` with actionable
    labels that tell the Flow Agent *what* went wrong.
    """
    status = (build_status or "missing").strip()
    if status in ("missing",):
        return (
            "REPAIR_VALIDATION",
            "Build manifest is missing — model validation likely failed before "
            "any patch was materialized. Check feedback for validation issues.",
            "Fix the JSON response fields flagged in the validation issues above.",
        )
    if status in ("patch_not_applied", "patch_apply_failed"):
        return (
            "REPAIR_PATCH",
            f"Source patch failed to apply (status={status}). "
            "The unified diff context does not match the target source file. "
            "Check that function names, line context, and indentation match "
            "the source files shown in the prompt exactly.",
            "Produce a corrected unified diff that matches the real source code.",
        )
    if status in ("build_smoke_failed",):
        return (
            "REPAIR_SMOKE",
            f"Python build/smoke gate failed (status={status}). "
            "Check the build log for fixture or py_compile errors.",
            "Fix the smoke gate failure before requesting implementation comparison.",
        )
    if status in ("candidate_binary_build_failed",):
        return (
            "REPAIR_COMPILE",
            f"Candidate ABC binary build failed (status={status}). "
            "The C source patch likely introduced a compile error. "
            "Check the build log for compiler messages.",
            "Fix the compile error in the patched source file.",
        )
    return (
        "REPAIR_BUILD",
        f"candidate build gate is {build_status or 'missing'}",
        "Return build/smoke logs to Flow Agent and request a repair patch.",
    )


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def parse_float(value: object) -> float | None:
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def average(values: Sequence[float | None]) -> float | None:
    parsed = [value for value in values if value is not None]
    if not parsed:
        return None
    return sum(parsed) / len(parsed)


def format_float(value: float | None) -> str:
    return "" if value is None else f"{value:.6f}"


if __name__ == "__main__":
    raise SystemExit(main())
