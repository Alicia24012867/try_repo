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
from scripts.agents.self_evolved_abc.flow.promotion import (
    and_delta_stats,
    average,
    format_float,
    format_optional_int,
    meets_promotion_thresholds,
    normalize_promotion_thresholds,
    parse_float,
)


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
    total_and_delta_candidate_minus_baseline: int | None
    improved_benchmark_count: int
    regressed_benchmark_count: int
    unchanged_benchmark_count: int
    min_average_and_improve_pct: float
    min_total_and_reduction: int
    min_improved_benchmarks: int
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
    delta_stats = and_delta_stats(backed_rows)
    thresholds = normalize_promotion_thresholds(
        context.assignment.get("promotion_thresholds")
    )
    all_structural_deltas_zero = _all_structural_deltas_zero(backed_rows)

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
    elif _meets_bootstrap_champion_policy(context, avg_and, delta_stats):
        decision = "ACCEPT_FOR_NEXT_CYCLE"
        reason = (
            "All CEC rows passed and this is the first positive champion "
            "candidate: no existing champion is recorded, total AND delta "
            f"{delta_stats.total_delta}, improved rows "
            f"{delta_stats.improved_count}, regressed rows "
            f"{delta_stats.regressed_count}. Bootstrap champion promotion "
            "keeps the best known correctness-backed implementation as the "
            "incumbent for later cycles."
        )
        next_action = (
            "Promote this candidate as the bootstrap champion; later cycles "
            "must beat the champion under the configured QoR thresholds."
        )
        promotion = True
    elif meets_promotion_thresholds(
        avg_and=avg_and,
        delta_stats=delta_stats,
        thresholds=thresholds,
    ):
        decision = "ACCEPT_FOR_NEXT_CYCLE"
        reason = (
            "All CEC rows passed and the QoR improvement exceeded promotion "
            f"thresholds: average AND improvement {avg_and:.6f}%, "
            f"total AND delta {delta_stats.total_delta}, "
            f"improved rows {delta_stats.improved_count}."
        )
        next_action = "Use this candidate as positive evidence for the next Flow Agent cycle."
        promotion = True
    else:
        decision = "REPAIR_QOR"
        if all_structural_deltas_zero:
            reason = (
                "Correctness passed but every correctness-backed row had zero "
                "AND/depth delta; the patch may not have affected code reached "
                "by the evaluation flow or may have been behavior-neutral."
            )
            next_action = (
                "Feed zero-delta QoR back to the Flow Agent as a reachability "
                "signal and request a different target file or strategy."
            )
        else:
            reason = (
                "Correctness passed but QoR did not exceed promotion thresholds "
                f"(avg AND improvement={format_float(avg_and)}%, "
                f"total AND delta={format_optional_int(delta_stats.total_delta)}, "
                f"improved rows={delta_stats.improved_count}, "
                f"required avg>={thresholds.min_average_and_improve_pct:.6f}%, "
                f"total reduction>={thresholds.min_total_and_reduction}, "
                f"improved rows>={thresholds.min_improved_benchmarks})."
            )
            next_action = (
                "Treat this as weak evidence, not a champion. Ask the Flow "
                "Agent to change strategy or target a different reachable "
                "decision point with a larger expected effect."
            )

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
        total_and_delta_candidate_minus_baseline=delta_stats.total_delta,
        improved_benchmark_count=delta_stats.improved_count,
        regressed_benchmark_count=delta_stats.regressed_count,
        unchanged_benchmark_count=delta_stats.unchanged_count,
        min_average_and_improve_pct=thresholds.min_average_and_improve_pct,
        min_total_and_reduction=thresholds.min_total_and_reduction,
        min_improved_benchmarks=thresholds.min_improved_benchmarks,
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
            f"- Total AND delta candidate-minus-baseline: `{format_optional_int(decision.total_and_delta_candidate_minus_baseline)}`",
            f"- AND improved/regressed/unchanged rows: {decision.improved_benchmark_count}/{decision.regressed_benchmark_count}/{decision.unchanged_benchmark_count}",
            f"- Promotion thresholds: avg >= `{decision.min_average_and_improve_pct:.6f}%`, total reduction >= `{decision.min_total_and_reduction}`, improved rows >= `{decision.min_improved_benchmarks}`",
            "",
            "## Evidence",
            "",
            f"- `{impl_root.relative_to(context.repo_root) / 'comparison' / 'impl_compare_summary.md'}`",
            f"- `{impl_root.relative_to(context.repo_root) / 'comparison' / 'cec_summary.csv'}`",
            f"- `{impl_root.relative_to(context.repo_root) / 'comparison' / 'qor_delta.csv'}`",
            f"- `{impl_root.relative_to(context.repo_root) / IMPL_CANDIDATE_LABEL / 'build.log'}`",
            f"- `{impl_root.relative_to(context.repo_root) / IMPL_CANDIDATE_LABEL / 'build_info.json'}`",
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
            "Keep Flow Agent source-patch edits out of the champion lineage until "
            "implementation comparison produces correctness-backed QoR improvement "
            "above the configured promotion thresholds."
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
            f"S4 Python smoke/fixture gate failed (status={status}). "
            "This gate runs py_compile and Flow response validation fixtures "
            "before ABC CEC/QoR starts; check candidate_modified/build.log.",
            "Fix the harness, validator, fixture, or assignment-scope issue "
            "before requesting another Flow Agent source patch.",
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


def _meets_bootstrap_champion_policy(
    context: CycleContext,
    avg_and: float | None,
    delta_stats: object,
) -> bool:
    """Allow the first champion to be the first correctness-backed improvement."""

    if _has_existing_champion(context):
        return False
    total_delta = getattr(delta_stats, "total_delta", None)
    improved_count = int(getattr(delta_stats, "improved_count", 0))
    regressed_count = int(getattr(delta_stats, "regressed_count", 0))
    return (
        avg_and is not None
        and avg_and > 0.0
        and total_delta is not None
        and total_delta < 0
        and improved_count > 0
        and regressed_count == 0
    )


def _has_existing_champion(context: CycleContext) -> bool:
    assignment = context.assignment
    return bool(
        assignment.get("champion_cycle_id")
        or str(assignment.get("baseline_kind", "")).strip() == "champion"
    )


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _all_structural_deltas_zero(rows: Sequence[dict[str, str]]) -> bool:
    if not rows:
        return False
    for row in rows:
        and_delta = parse_float(row.get("and_delta_candidate_minus_baseline"))
        depth_delta = parse_float(row.get("depth_delta_candidate_minus_baseline"))
        if and_delta not in (0, 0.0) or depth_delta not in (0, 0.0):
            return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
