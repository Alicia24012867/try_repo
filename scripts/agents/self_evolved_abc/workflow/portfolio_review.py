"""All-settled fan-in review for a paired Flow/Logic planning round."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.agents.self_evolved_abc.cycle_context import CycleContext
from scripts.agents.self_evolved_abc.flow.paths import impl_compare_root
from scripts.agents.self_evolved_abc.flow.review import (
    REVIEW_DECISIONS,
    REVIEW_REQUIRED_FIELDS,
)
from scripts.agents.self_evolved_abc.planning.portfolio import (
    BRANCH_ORDER,
    BranchDispatch,
    PortfolioPlan,
    hash_evaluation_contract,
    validate_assignment_contract,
    validate_baseline_assignment,
)
from scripts.agents.self_evolved_abc.workflow.failure_evidence import (
    validation_feedback_payload,
)
from scripts.agents.self_evolved_abc.workflow.failure_status import (
    is_coding_infrastructure_failure_status,
)


@dataclass(frozen=True)
class BranchOutcome:
    branch_role: str
    agent_name: str
    candidate_id: str
    status: str
    return_code: int | None
    decision: str
    eligible_for_promotion: bool
    artifact_root: str
    review_path: str
    elapsed_seconds: float
    error: str
    scalar_and_reward: int | None = None
    improved_benchmark_count: int = 0
    regressed_benchmark_count: int = 0
    average_and_improve_pct: float | None = None
    cec_pass_count: int = 0
    cec_total_count: int = 0
    correctness_backed_rows: int = 0
    expected_benchmark_count: int = 0
    build_status: str = ""
    review_reason: str = ""
    next_action: str = ""
    validation_issues_markdown: str = ""
    total_depth_delta: int | None = None
    depth_improved_benchmark_count: int = 0
    depth_regressed_benchmark_count: int = 0
    structural_proxy_reward_pct: float | None = None
    max_node_regression_pct: float = 0.0
    max_depth_regression_pct: float = 0.0
    promotion_basis: str = ""
    retained_for_synergy: bool = False


def collect_branch_outcome(
    *,
    repo_root: Path,
    branch: BranchDispatch,
    return_code: int | None,
    elapsed_seconds: float,
    runner_error: str = "",
) -> BranchOutcome:
    """Read one branch review without allowing it to cancel its sibling."""

    context = CycleContext.from_assignment_file(repo_root, branch.assignment_path)
    expected_benchmark_count = len(context.evaluation_benchmark_scope)
    impl_root = impl_compare_root(context)
    review_path = impl_root / "comparison" / "review_decision.json"
    review: dict[str, Any] = {}
    error = runner_error
    if review_path.is_file():
        try:
            payload = json.loads(review_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                review = payload
            else:
                error = _append_error(error, "review JSON is not an object")
        except (json.JSONDecodeError, OSError) as exc:
            error = _append_error(error, f"unreadable review: {exc}")
    else:
        error = _append_error(error, "review_decision.json was not produced")

    if review:
        identity_errors = []
        if str(review.get("cycle_id", "")) != context.cycle_id:
            identity_errors.append("review cycle_id does not match branch")
        if str(review.get("candidate_id", "")) != branch.candidate_id:
            identity_errors.append("review candidate_id does not match branch")
        if identity_errors:
            for message in identity_errors:
                error = _append_error(error, message)
            review = {}
    if review:
        schema_errors = _review_schema_errors(review)
        if schema_errors:
            for message in schema_errors:
                error = _append_error(error, message)
            review = {}
    status = "reviewed" if review else "failed"
    decision = str(review.get("decision", "MISSING_REVIEW"))
    eligible = (
        _eligible_for_promotion(
            review,
            return_code=return_code,
            runner_error=error,
            expected_benchmark_count=expected_benchmark_count,
        )
        if review
        else False
    )
    validation_feedback = validation_feedback_payload(context)
    return BranchOutcome(
        branch_role=branch.branch_role,
        agent_name=branch.agent_name,
        candidate_id=branch.candidate_id,
        status=status,
        return_code=return_code,
        decision=decision,
        eligible_for_promotion=eligible,
        artifact_root=impl_root.relative_to(repo_root).as_posix(),
        review_path=review_path.relative_to(repo_root).as_posix(),
        elapsed_seconds=round(max(0.0, elapsed_seconds), 6),
        error=error,
        scalar_and_reward=_optional_int(review.get("scalar_and_reward")),
        improved_benchmark_count=_int(review.get("improved_benchmark_count")),
        regressed_benchmark_count=_int(review.get("regressed_benchmark_count")),
        average_and_improve_pct=_optional_float(
            review.get("average_and_improve_pct")
        ),
        cec_pass_count=_int(review.get("cec_pass_count")),
        cec_total_count=_int(review.get("cec_total_count")),
        correctness_backed_rows=_int(review.get("correctness_backed_rows")),
        expected_benchmark_count=expected_benchmark_count,
        build_status=str(review.get("build_status", "")),
        review_reason=str(review.get("reason", "")),
        next_action=str(review.get("next_action", "")),
        validation_issues_markdown=(
            str(validation_feedback.get("issues_markdown", ""))
            if validation_feedback is not None
            else ""
        ),
        total_depth_delta=_optional_int(
            review.get("total_depth_delta_candidate_minus_baseline")
        ),
        depth_improved_benchmark_count=_int(
            review.get("depth_improved_benchmark_count")
        ),
        depth_regressed_benchmark_count=_int(
            review.get("depth_regressed_benchmark_count")
        ),
        structural_proxy_reward_pct=_optional_float(
            review.get("structural_proxy_reward_pct")
        ),
        max_node_regression_pct=(
            _optional_float(review.get("max_node_regression_pct")) or 0.0
        ),
        max_depth_regression_pct=(
            _optional_float(review.get("max_depth_regression_pct")) or 0.0
        ),
        promotion_basis=str(review.get("promotion_basis", "")),
        retained_for_synergy=bool(review.get("retained_for_synergy", False)),
    )


def write_portfolio_review(
    *,
    repo_root: Path,
    plan: PortfolioPlan,
    outcomes: Sequence[BranchOutcome],
) -> dict[str, object]:
    """Persist a deterministic round decision after every branch settles."""

    payload = build_portfolio_review_payload(
        repo_root=repo_root,
        plan=plan,
        outcomes=outcomes,
    )
    ordered = _ordered_outcomes(outcomes)
    root = repo_root / "experiments" / plan.cycle_id / "planning"
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "portfolio_review.json"
    temporary = json_path.with_suffix(json_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(json_path)
    (root / "portfolio_review.md").write_text(
        _render_markdown(payload, ordered),
        encoding="utf-8",
    )
    return payload


def build_portfolio_review_payload(
    *,
    repo_root: Path,
    plan: PortfolioPlan,
    outcomes: Sequence[BranchOutcome],
) -> dict[str, object]:
    """Build the canonical fan-in decision without mutating checkpoint bytes."""

    ordered = _ordered_outcomes(outcomes)
    _validate_runtime_contracts(repo_root, plan)
    reviewed_count = sum(item.status == "reviewed" for item in ordered)
    infrastructure_failed_count = sum(
        is_coding_infrastructure_failure_status(item.build_status)
        for item in ordered
    )
    failed_count = len(ordered) - reviewed_count + infrastructure_failed_count
    quorum_reached = (
        reviewed_count == len(BRANCH_ORDER)
        and infrastructure_failed_count == 0
    )
    winner = _select_winner(ordered) if quorum_reached else None
    frontier = [
        {
            "branch_role": item.branch_role,
            "agent_name": item.agent_name,
            "candidate_id": item.candidate_id,
            "review_path": item.review_path,
            "patch_path": (
                f"{item.artifact_root}/candidate_modified/patch.diff"
            ),
            "scalar_and_reward": item.scalar_and_reward,
            "total_depth_delta": item.total_depth_delta,
            "structural_proxy_reward_pct": item.structural_proxy_reward_pct,
            "max_node_regression_pct": item.max_node_regression_pct,
            "max_depth_regression_pct": item.max_depth_regression_pct,
        }
        for item in ordered
        if item.retained_for_synergy
    ]
    if infrastructure_failed_count:
        round_status = "infrastructure_failed"
    elif winner is not None:
        round_status = "promotion_selected"
    elif not quorum_reached and reviewed_count:
        round_status = "incomplete"
    elif reviewed_count:
        round_status = "no_promotion"
    else:
        round_status = "round_failed"
    payload: dict[str, object] = {
        "schema_version": 1,
        "portfolio_id": plan.portfolio_id,
        "planner_dispatch_id": plan.planner_dispatch_id,
        "cycle_id": plan.cycle_id,
        "baseline_ref": dict(plan.baseline_ref),
        "evaluation_contract_hash": plan.evaluation_contract_hash,
        "planner_advice_hash": plan.planner_advice_hash,
        "round_status": round_status,
        "reviewed_count": reviewed_count,
        "failed_count": failed_count,
        "quorum_reached": quorum_reached,
        "eligible_count": sum(item.eligible_for_promotion for item in ordered),
        "selected_candidate_id": winner.candidate_id if winner else "",
        "selected_agent_name": winner.agent_name if winner else "",
        "frontier_candidates": frontier,
        "frontier_count": len(frontier),
        "frontier_policy": (
            "Frontier candidates never update the baseline. A combined or "
            "follow-up candidate must use a fresh isolated workspace and repeat "
            "build, exact-scope CEC, and QoR before promotion."
        ),
        "selection_reason": (
            "Highest deterministic correctness-backed structural proxy reward, "
            "then scalar AND reward, depth gain, breadth, and stable candidate id."
            if winner
            else (
                "No promotion is allowed while a coding-agent infrastructure "
                "failure is present. Repair it and resume the frozen dispatch."
                if infrastructure_failed_count
                else (
                    "No promotion is allowed until both branch reviews settle."
                    if not quorum_reached
                    else (
                        "No branch passed every correctness, coverage, build, and "
                        "QoR promotion gate."
                    )
                )
            )
        ),
        "merge_policy": (
            "Never merge branch patches implicitly; a combined candidate must "
            "run build, CEC, and QoR as a new candidate."
        ),
        "branches": [asdict(item) for item in ordered],
    }
    return payload


def _eligible_for_promotion(
    review: Mapping[str, Any],
    *,
    return_code: int | None,
    runner_error: str,
    expected_benchmark_count: int,
) -> bool:
    return (
        return_code == 0
        and not runner_error
        and expected_benchmark_count > 0
        and str(review.get("decision", "")) == "ACCEPT_FOR_NEXT_CYCLE"
        and bool(review.get("promotion_allowed", False))
        and str(review.get("build_status", "")) == "candidate_binary_build_passed"
        and _int(review.get("cec_total_count")) == expected_benchmark_count
        and _int(review.get("cec_pass_count")) == expected_benchmark_count
        and _int(review.get("correctness_backed_rows")) == expected_benchmark_count
    )


def _ordered_outcomes(outcomes: Sequence[BranchOutcome]) -> tuple[BranchOutcome, ...]:
    by_role = {item.branch_role: item for item in outcomes}
    if set(by_role) != set(BRANCH_ORDER) or len(outcomes) != len(BRANCH_ORDER):
        raise ValueError("portfolio review requires exactly one Flow and one Logic outcome")
    return tuple(by_role[role] for role in BRANCH_ORDER)


def _select_winner(outcomes: Sequence[BranchOutcome]) -> BranchOutcome | None:
    eligible = [item for item in outcomes if item.eligible_for_promotion]
    if not eligible:
        return None
    ranked = sorted(
        eligible,
        key=lambda item: (
            -(
                item.structural_proxy_reward_pct
                if item.structural_proxy_reward_pct is not None
                else -10**18
            ),
            -(item.scalar_and_reward if item.scalar_and_reward is not None else -10**18),
            item.total_depth_delta if item.total_depth_delta is not None else 10**18,
            -item.improved_benchmark_count,
            -(
                item.average_and_improve_pct
                if item.average_and_improve_pct is not None
                else -10**18
            ),
            item.candidate_id,
        ),
    )
    return ranked[0]


def _validate_runtime_contracts(repo_root: Path, plan: PortfolioPlan) -> None:
    for branch in plan.branches:
        payload = json.loads(branch.assignment_path.read_text(encoding="utf-8"))
        contract = payload.get("evaluation_contract")
        if not isinstance(contract, dict):
            raise ValueError(f"missing evaluation contract for {branch.candidate_id}")
        if hash_evaluation_contract(contract) != plan.evaluation_contract_hash:
            raise ValueError(
                f"evaluation contract changed during {branch.candidate_id} execution"
            )
        validate_assignment_contract(payload, plan.evaluation_contract)
        if payload.get("baseline_ref") != dict(plan.baseline_ref):
            raise ValueError(f"baseline changed during {branch.candidate_id} execution")
        validate_baseline_assignment(payload, plan.baseline_ref)


def _render_markdown(
    payload: Mapping[str, object],
    outcomes: Sequence[BranchOutcome],
) -> str:
    lines = [
        f"# Planning Portfolio Review — {payload['cycle_id']}",
        "",
        f"- Round status: `{payload['round_status']}`",
        f"- Selected candidate: `{payload['selected_candidate_id'] or 'none'}`",
        f"- Evaluation contract: `{payload['evaluation_contract_hash']}`",
        "",
        "## Branches",
        "",
    ]
    for item in outcomes:
        lines.extend(
            (
                f"### {item.branch_role}",
                "",
                f"- Agent: `{item.agent_name}`",
                f"- Candidate: `{item.candidate_id}`",
                f"- Status / decision: `{item.status}` / `{item.decision}`",
                f"- Eligible: `{str(item.eligible_for_promotion).lower()}`",
                f"- Build status: `{item.build_status or 'unknown'}`",
                f"- Scalar AND reward: `{item.scalar_and_reward}`",
                f"- Structural proxy reward pct: `{item.structural_proxy_reward_pct}`",
                f"- Total depth delta: `{item.total_depth_delta}`",
                f"- Promotion basis: `{item.promotion_basis or 'none'}`",
                f"- Retained for synergy: `{str(item.retained_for_synergy).lower()}`",
                f"- Review reason: {item.review_reason or 'none'}",
                f"- Next action: {item.next_action or 'none'}",
                f"- Evidence: `{item.review_path}`",
                f"- Error: {item.error or 'none'}",
                "",
            )
        )
        if item.validation_issues_markdown:
            lines.extend(
                (
                    "#### Exact Validation Issues",
                    "",
                    item.validation_issues_markdown,
                    "",
                )
            )
    if payload.get("frontier_candidates"):
        lines.extend(
            (
                "## Synergy Frontier",
                "",
                f"{payload['frontier_policy']}",
                "",
            )
        )
        for item in payload["frontier_candidates"]:  # type: ignore[index]
            if isinstance(item, Mapping):
                lines.append(
                    f"- `{item.get('candidate_id', 'unknown')}` "
                    f"({item.get('branch_role', 'unknown')}): proxy reward "
                    f"`{item.get('structural_proxy_reward_pct')}`, patch "
                    f"`{item.get('patch_path')}`"
                )
        lines.append("")
    lines.extend(("## Merge Policy", "", f"{payload['merge_policy']}", ""))
    return "\n".join(lines)


def _append_error(current: str, message: str) -> str:
    return f"{current}; {message}" if current else message


def _review_schema_errors(review: Mapping[str, Any]) -> tuple[str, ...]:
    """Validate the persisted review before it can satisfy fan-in quorum."""

    errors: list[str] = []
    missing = sorted(REVIEW_REQUIRED_FIELDS - set(review))
    if missing:
        errors.append("review is missing required fields: " + ", ".join(missing))

    decision = review.get("decision")
    if decision not in REVIEW_DECISIONS:
        errors.append(f"review decision is invalid: {decision!r}")

    for field in ("champion_update", "promotion_allowed"):
        if not isinstance(review.get(field), bool):
            errors.append(f"review {field} must be boolean")
    if "retained_for_synergy" in review and not isinstance(
        review.get("retained_for_synergy"), bool
    ):
        errors.append("review retained_for_synergy must be boolean")

    count_fields = (
        "cec_pass_count",
        "cec_total_count",
        "correctness_backed_rows",
        "improved_benchmark_count",
        "regressed_benchmark_count",
        "unchanged_benchmark_count",
        "min_total_and_reduction",
        "min_improved_benchmarks",
    )
    for field in count_fields:
        value = review.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            errors.append(f"review {field} must be a non-negative integer")
    optional_count_fields = (
        "depth_improved_benchmark_count",
        "depth_regressed_benchmark_count",
        "depth_unchanged_benchmark_count",
        "paired_structural_metric_count",
    )
    for field in optional_count_fields:
        if field not in review:
            continue
        value = review.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            errors.append(f"review {field} must be a non-negative integer")

    for field in ("build_status", "reason", "next_action"):
        value = review.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"review {field} must be a non-empty string")

    average = review.get("average_and_improve_pct")
    if average is not None and (
        not isinstance(average, (int, float)) or isinstance(average, bool)
        or not math.isfinite(float(average))
    ):
        errors.append("review average_and_improve_pct must be numeric or null")
    for field in (
        "total_and_delta_candidate_minus_baseline",
        "scalar_and_reward",
        "total_depth_delta_candidate_minus_baseline",
    ):
        if field not in review and field == "total_depth_delta_candidate_minus_baseline":
            continue
        value = review.get(field)
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool)
        ):
            errors.append(f"review {field} must be an integer or null")
    threshold = review.get("min_average_and_improve_pct")
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
        errors.append("review min_average_and_improve_pct must be numeric")
    elif not math.isfinite(float(threshold)):
        errors.append("review min_average_and_improve_pct must be finite")
    for field in (
        "structural_proxy_reward_pct",
        "max_node_regression_pct",
        "max_depth_regression_pct",
    ):
        if field not in review:
            continue
        value = review.get(field)
        if value is not None and (
            not isinstance(value, (int, float)) or isinstance(value, bool)
            or not math.isfinite(float(value))
        ):
            errors.append(f"review {field} must be numeric or null")

    promotion = review.get("promotion_allowed")
    champion_update = review.get("champion_update")
    if isinstance(promotion, bool) and isinstance(champion_update, bool):
        if promotion != champion_update:
            errors.append("review champion_update must match promotion_allowed")
        if (decision == "ACCEPT_FOR_NEXT_CYCLE") != promotion:
            errors.append(
                "review ACCEPT_FOR_NEXT_CYCLE must exactly match promotion_allowed"
            )
    return tuple(errors)


def _optional_int(value: object) -> int | None:
    try:
        if value in (None, ""):
            return None
        parsed = float(value)
        return int(parsed) if math.isfinite(parsed) and parsed.is_integer() else None
    except (TypeError, ValueError, OverflowError):
        return None


def _int(value: object) -> int:
    parsed = _optional_int(value)
    return parsed if parsed is not None else 0


def _optional_float(value: object) -> float | None:
    try:
        if value in (None, ""):
            return None
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None
