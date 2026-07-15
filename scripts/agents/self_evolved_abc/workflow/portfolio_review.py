"""All-settled fan-in review for a paired Flow/Logic planning round."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.agents.self_evolved_abc.cycle_context import CycleContext
from scripts.agents.self_evolved_abc.flow.paths import impl_compare_root
from scripts.agents.self_evolved_abc.planning.portfolio import (
    BRANCH_ORDER,
    BranchDispatch,
    PortfolioPlan,
    hash_evaluation_contract,
    validate_assignment_contract,
    validate_baseline_assignment,
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
    )


def write_portfolio_review(
    *,
    repo_root: Path,
    plan: PortfolioPlan,
    outcomes: Sequence[BranchOutcome],
) -> dict[str, object]:
    """Persist a deterministic round decision after every branch settles."""

    ordered = _ordered_outcomes(outcomes)
    _validate_runtime_contracts(repo_root, plan)
    reviewed_count = sum(item.status == "reviewed" for item in ordered)
    failed_count = len(ordered) - reviewed_count
    quorum_reached = reviewed_count == len(BRANCH_ORDER)
    winner = _select_winner(ordered) if quorum_reached else None
    if winner is not None:
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
        "selection_reason": (
            "Highest deterministic correctness-backed scalar AND reward, then "
            "improvement breadth and average improvement."
            if winner
            else (
                "No promotion is allowed until both branch reviews settle."
                if not quorum_reached
                else (
                    "No unique winner: either no branch passed every hard gate "
                    "or the eligible branches tied on all promotion metrics."
                )
            )
        ),
        "merge_policy": (
            "Never merge branch patches implicitly; a combined candidate must "
            "run build, CEC, and QoR as a new candidate."
        ),
        "branches": [asdict(item) for item in ordered],
    }
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
        and _int(review.get("regressed_benchmark_count")) == 0
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
            -(item.scalar_and_reward if item.scalar_and_reward is not None else -10**18),
            -item.improved_benchmark_count,
            -(
                item.average_and_improve_pct
                if item.average_and_improve_pct is not None
                else -10**18
            ),
        ),
    )
    if len(ranked) > 1 and _promotion_rank(ranked[0]) == _promotion_rank(ranked[1]):
        return None
    return ranked[0]


def _promotion_rank(item: BranchOutcome) -> tuple[float, int, float]:
    return (
        float(item.scalar_and_reward if item.scalar_and_reward is not None else -10**18),
        item.improved_benchmark_count,
        float(
            item.average_and_improve_pct
            if item.average_and_improve_pct is not None
            else -10**18
        ),
    )


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
                f"- Scalar AND reward: `{item.scalar_and_reward}`",
                f"- Evidence: `{item.review_path}`",
                f"- Error: {item.error or 'none'}",
                "",
            )
        )
    lines.extend(("## Merge Policy", "", f"{payload['merge_policy']}", ""))
    return "\n".join(lines)


def _append_error(current: str, message: str) -> str:
    return f"{current}; {message}" if current else message


def _optional_int(value: object) -> int | None:
    try:
        return None if value in (None, "") else int(float(value))
    except (TypeError, ValueError):
        return None


def _int(value: object) -> int:
    parsed = _optional_int(value)
    return parsed if parsed is not None else 0


def _optional_float(value: object) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None
