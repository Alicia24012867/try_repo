"""Planning-owned Flow/Logic fan-out and next-round assignment creation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from scripts.agents.self_evolved_abc.cycle_context import CycleContext
from scripts.agents.self_evolved_abc.flow.contracts import (
    DEFAULT_ABC_BIN,
    DEFAULT_EVAL_FLOW_COMMANDS,
    FLOW_CANDIDATE_SOURCE_PATCH_DIFF,
    FLOWTUNE_SOURCE_ROOT,
    FLOWTUNE_SOURCE_SCOPE_PRIMARY,
    IMPL_CANDIDATE_LABEL,
)
from scripts.agents.self_evolved_abc.flow.next_cycle import (
    build_next_assignment,
    increment_cycle_id,
    write_next_assignment,
)
from scripts.agents.self_evolved_abc.flow.paths import impl_compare_root
from scripts.agents.self_evolved_abc.logic.contracts import (
    LOGIC_AGENT_NAME,
    LOGIC_EVALUATION_FLOW_COMMANDS,
)
from scripts.agents.self_evolved_abc.planning.assignment_factory import (
    build_initial_assignment,
)
from scripts.agents.self_evolved_abc.roles.registry import (
    get_coding_agent_spec,
    normalize_coding_assignment,
)
from scripts.agents.self_evolved_abc.workflow.artifacts import (
    CANDIDATE_SCOPED_LAYOUT,
    safe_repo_path,
    validate_candidate_id,
    validate_portfolio_cycle_id,
)


FLOW_BRANCH = "flow"
LOGIC_BRANCH = "logic"
BRANCH_ORDER = (FLOW_BRANCH, LOGIC_BRANCH)
BRANCH_SPECS = {
    FLOW_BRANCH: ("flow_agent", "flow_candidate_001"),
    LOGIC_BRANCH: (LOGIC_AGENT_NAME, "logic_candidate_001"),
}
PLAN_SCHEMA_VERSION = 2
EVALUATION_CONTRACT_VERSION = 1
PLANNER_ADVICE_SCHEMA_VERSION = 1
PlannerAdviceProvider = Callable[[Mapping[str, object]], Mapping[str, object]]


@dataclass(frozen=True)
class BranchDispatch:
    branch_role: str
    agent_name: str
    candidate_id: str
    assignment_path: Path

    def as_dict(self, repo_root: Path) -> dict[str, str]:
        return {
            "branch_role": self.branch_role,
            "agent_name": self.agent_name,
            "candidate_id": self.candidate_id,
            "assignment_path": self.assignment_path.relative_to(repo_root).as_posix(),
        }


@dataclass(frozen=True)
class PortfolioPlan:
    portfolio_id: str
    planner_dispatch_id: str
    cycle_id: str
    previous_cycle_id: str
    parent_plan_hash: str
    parent_review_hash: str
    baseline_ref: Mapping[str, object]
    evaluation_contract: Mapping[str, object]
    evaluation_contract_hash: str
    planner_advice_hash: str
    planner_advice_source: str
    branches: tuple[BranchDispatch, ...]

    def as_dict(self, repo_root: Path) -> dict[str, object]:
        return {
            "schema_version": PLAN_SCHEMA_VERSION,
            "portfolio_id": self.portfolio_id,
            "planner_dispatch_id": self.planner_dispatch_id,
            "cycle_id": self.cycle_id,
            "previous_cycle_id": self.previous_cycle_id,
            "parent_plan_hash": self.parent_plan_hash,
            "parent_review_hash": self.parent_review_hash,
            "baseline_ref": dict(self.baseline_ref),
            "evaluation_contract": dict(self.evaluation_contract),
            "evaluation_contract_hash": self.evaluation_contract_hash,
            "planner_advice_hash": self.planner_advice_hash,
            "planner_advice_source": self.planner_advice_source,
            "branches": [branch.as_dict(repo_root) for branch in self.branches],
        }


def create_portfolio_plan(
    *,
    repo_root: Path,
    cycle_id: str,
    previous_cycle_id: str = "cycle_000",
    portfolio_id: str = "flow_logic_campaign",
    benchmark_suite: str = "standard_30",
    benchmarks: Sequence[str] = (),
    timeout_seconds: float = 300.0,
    build_timeout_seconds: float = 900.0,
    overwrite: bool = False,
    planner_advice_provider: PlannerAdviceProvider | None = None,
) -> PortfolioPlan:
    """Create exactly one Flow and one Logic assignment from one snapshot."""

    repo_root = repo_root.resolve()
    cycle_id = validate_portfolio_cycle_id(cycle_id)
    previous_cycle_id = validate_portfolio_cycle_id(previous_cycle_id)
    portfolio_id = validate_candidate_id(portfolio_id)
    baseline_ref = _vanilla_baseline_ref(previous_cycle_id)
    dispatch_id = f"{portfolio_id}:{cycle_id}"
    common = {
        "artifact_layout": CANDIDATE_SCOPED_LAYOUT,
        "portfolio_id": portfolio_id,
        "planner_dispatch_id": dispatch_id,
    }
    assignments: dict[str, dict[str, object]] = {}
    for branch_role in BRANCH_ORDER:
        agent_name, candidate_id = BRANCH_SPECS[branch_role]
        roots: Sequence[str] = (
            (FLOWTUNE_SOURCE_SCOPE_PRIMARY,)
            if branch_role == FLOW_BRANCH
            else ()
        )
        assignments[branch_role] = build_initial_assignment(
            repo_root=repo_root,
            cycle_id=cycle_id,
            previous_cycle_id=previous_cycle_id,
            candidate_id=candidate_id,
            agent_name=agent_name,
            source_patch_mode=FLOW_CANDIDATE_SOURCE_PATCH_DIFF,
            source_patch_allowed_roots=roots,
            benchmarks=benchmarks,
            benchmark_suite=benchmark_suite,
            extra_fields={
                **common,
                "branch_role": branch_role,
                "baseline_ref": dict(baseline_ref),
                **_baseline_assignment_fields(baseline_ref),
            },
        )
        assignments[branch_role] = _enforce_branch_scope(
            assignments[branch_role], branch_role
        )

    evaluation_contract = _evaluation_contract(
        assignments[FLOW_BRANCH],
        baseline_ref=baseline_ref,
        timeout_seconds=timeout_seconds,
        build_timeout_seconds=build_timeout_seconds,
    )
    contract_hash = hash_evaluation_contract(evaluation_contract)
    for branch_role, assignment in assignments.items():
        assignment["evaluation_contract"] = dict(evaluation_contract)
        assignment["evaluation_contract_hash"] = contract_hash
        assignment["evaluation_flow_commands"] = list(DEFAULT_EVAL_FLOW_COMMANDS)
        assignment["promotion_thresholds"] = dict(
            evaluation_contract["promotion_thresholds"]
        )
        assignment["diagnostic_flow_commands"] = list(
            DEFAULT_EVAL_FLOW_COMMANDS
            if branch_role == FLOW_BRANCH
            else LOGIC_EVALUATION_FLOW_COMMANDS
        )
        assignments[branch_role] = normalize_coding_assignment(assignment)

    planner_advice = _resolve_planner_advice(
        repo_root=repo_root,
        cycle_id=cycle_id,
        previous_cycle_id=previous_cycle_id,
        portfolio_id=portfolio_id,
        dispatch_id=dispatch_id,
        baseline_ref=baseline_ref,
        evaluation_contract=evaluation_contract,
        assignments=assignments,
        provider=planner_advice_provider,
        reuse_existing=not overwrite,
    )
    planner_advice_hash = hash_planner_advice(planner_advice)
    _apply_planner_advice(assignments, planner_advice, planner_advice_hash)

    plan = _write_plan_and_assignments(
        repo_root=repo_root,
        cycle_id=cycle_id,
        previous_cycle_id=previous_cycle_id,
        parent_plan_hash="",
        parent_review_hash="",
        portfolio_id=portfolio_id,
        dispatch_id=dispatch_id,
        baseline_ref=baseline_ref,
        evaluation_contract=evaluation_contract,
        contract_hash=contract_hash,
        planner_advice=planner_advice,
        planner_advice_hash=planner_advice_hash,
        assignments=assignments,
        overwrite=overwrite,
    )
    validate_portfolio_plan(plan, repo_root=repo_root)
    return plan


def create_next_portfolio_plan(
    *,
    repo_root: Path,
    current_plan: PortfolioPlan,
    portfolio_review: Mapping[str, Any],
    next_cycle_id: str,
    timeout_seconds: float = 300.0,
    build_timeout_seconds: float = 900.0,
    overwrite: bool = False,
    planner_advice_provider: PlannerAdviceProvider | None = None,
) -> PortfolioPlan:
    """Fan out the next two tasks only after the current fan-in review."""

    repo_root = repo_root.resolve()
    next_cycle_id = validate_portfolio_cycle_id(next_cycle_id)
    if next_cycle_id != increment_cycle_id(current_plan.cycle_id):
        raise ValueError("next portfolio cycle must increment current cycle exactly once")
    summary_path = (
        repo_root
        / "experiments"
        / current_plan.cycle_id
        / "planning"
        / "portfolio_review.json"
    )
    if not summary_path.is_file():
        raise FileNotFoundError(
            "next portfolio requires the persisted fan-in review: "
            f"{summary_path}"
        )
    persisted_review = json.loads(summary_path.read_text(encoding="utf-8"))
    if not isinstance(persisted_review, dict):
        raise ValueError("persisted portfolio review is not a JSON object")
    if dict(portfolio_review) != persisted_review:
        raise ValueError("next portfolio review does not match persisted fan-in decision")
    portfolio_review = persisted_review
    review_lineage = {
        "cycle_id": current_plan.cycle_id,
        "portfolio_id": current_plan.portfolio_id,
        "planner_dispatch_id": current_plan.planner_dispatch_id,
        "baseline_ref": dict(current_plan.baseline_ref),
        "evaluation_contract_hash": current_plan.evaluation_contract_hash,
        "planner_advice_hash": current_plan.planner_advice_hash,
        "quorum_reached": True,
    }
    for key, expected in review_lineage.items():
        if portfolio_review.get(key) != expected:
            raise ValueError(f"persisted fan-in review lineage mismatch at {key}")
    if int(portfolio_review.get("reviewed_count", 0)) != len(BRANCH_ORDER):
        raise ValueError("persisted fan-in review does not contain both branches")
    parent_plan_hash = _path_sha256(
        portfolio_plan_path(repo_root, current_plan.cycle_id)
    )
    parent_review_hash = _path_sha256(summary_path)
    baseline_ref = _next_baseline_ref(
        repo_root=repo_root,
        current_plan=current_plan,
        portfolio_review=portfolio_review,
    )
    dispatch_id = f"{current_plan.portfolio_id}:{next_cycle_id}"
    assignments: dict[str, dict[str, object]] = {}
    for branch in current_plan.branches:
        context = CycleContext.from_assignment_file(repo_root, branch.assignment_path)
        assignment = build_next_assignment(
            context,
            next_cycle_id,
            branch.candidate_id,
        )
        _clear_baseline_fields(assignment)
        assignment.update(_baseline_assignment_fields(baseline_ref))
        assignment.update(
            {
                "artifact_layout": CANDIDATE_SCOPED_LAYOUT,
                "portfolio_id": current_plan.portfolio_id,
                "planner_dispatch_id": dispatch_id,
                "branch_role": branch.branch_role,
                "baseline_ref": dict(baseline_ref),
            }
        )
        assignment = _enforce_branch_scope(assignment, branch.branch_role)
        summary_relative = summary_path.relative_to(repo_root).as_posix()
        for key in ("allowed_to_read", "recent_evidence"):
            values = [str(item) for item in assignment.get(key, ())]
            if summary_relative not in values:
                values.append(summary_relative)
            assignment[key] = values
        assignments[branch.branch_role] = assignment

    evaluation_contract = _evaluation_contract(
        assignments[FLOW_BRANCH],
        baseline_ref=baseline_ref,
        timeout_seconds=timeout_seconds,
        build_timeout_seconds=build_timeout_seconds,
    )
    contract_hash = hash_evaluation_contract(evaluation_contract)
    for branch_role, assignment in assignments.items():
        assignment["evaluation_contract"] = dict(evaluation_contract)
        assignment["evaluation_contract_hash"] = contract_hash
        assignment["evaluation_flow_commands"] = list(DEFAULT_EVAL_FLOW_COMMANDS)
        assignment["promotion_thresholds"] = dict(
            evaluation_contract["promotion_thresholds"]
        )
        assignment["diagnostic_flow_commands"] = list(
            DEFAULT_EVAL_FLOW_COMMANDS
            if branch_role == FLOW_BRANCH
            else LOGIC_EVALUATION_FLOW_COMMANDS
        )
        assignments[branch_role] = normalize_coding_assignment(assignment)

    planner_advice = _resolve_planner_advice(
        repo_root=repo_root,
        cycle_id=next_cycle_id,
        previous_cycle_id=current_plan.cycle_id,
        portfolio_id=current_plan.portfolio_id,
        dispatch_id=dispatch_id,
        baseline_ref=baseline_ref,
        evaluation_contract=evaluation_contract,
        assignments=assignments,
        provider=planner_advice_provider,
        reuse_existing=not overwrite,
    )
    planner_advice_hash = hash_planner_advice(planner_advice)
    _apply_planner_advice(assignments, planner_advice, planner_advice_hash)

    plan = _write_plan_and_assignments(
        repo_root=repo_root,
        cycle_id=next_cycle_id,
        previous_cycle_id=current_plan.cycle_id,
        parent_plan_hash=parent_plan_hash,
        parent_review_hash=parent_review_hash,
        portfolio_id=current_plan.portfolio_id,
        dispatch_id=dispatch_id,
        baseline_ref=baseline_ref,
        evaluation_contract=evaluation_contract,
        contract_hash=contract_hash,
        planner_advice=planner_advice,
        planner_advice_hash=planner_advice_hash,
        assignments=assignments,
        overwrite=overwrite,
    )
    validate_portfolio_plan(plan, repo_root=repo_root)
    return plan


def load_portfolio_plan(repo_root: Path, cycle_id: str) -> PortfolioPlan:
    repo_root = repo_root.resolve()
    cycle_id = validate_portfolio_cycle_id(cycle_id)
    path = portfolio_plan_path(repo_root, cycle_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != PLAN_SCHEMA_VERSION:
        raise ValueError("unsupported portfolio plan schema_version")
    branches = tuple(
        BranchDispatch(
            branch_role=str(item["branch_role"]),
            agent_name=str(item["agent_name"]),
            candidate_id=validate_candidate_id(item["candidate_id"]),
            assignment_path=repo_root / str(item["assignment_path"]),
        )
        for item in payload.get("branches", ())
    )
    plan = PortfolioPlan(
        portfolio_id=str(payload["portfolio_id"]),
        planner_dispatch_id=str(payload["planner_dispatch_id"]),
        cycle_id=str(payload["cycle_id"]),
        previous_cycle_id=str(payload["previous_cycle_id"]),
        parent_plan_hash=str(payload.get("parent_plan_hash", "")),
        parent_review_hash=str(payload.get("parent_review_hash", "")),
        baseline_ref=dict(payload.get("baseline_ref", {})),
        evaluation_contract=dict(payload.get("evaluation_contract", {})),
        evaluation_contract_hash=str(payload["evaluation_contract_hash"]),
        planner_advice_hash=str(payload["planner_advice_hash"]),
        planner_advice_source=str(payload["planner_advice_source"]),
        branches=branches,
    )
    validate_portfolio_plan(plan, repo_root=repo_root)
    return plan


def validate_portfolio_plan(plan: PortfolioPlan, *, repo_root: Path) -> None:
    """Fail fast if a dispatch is ambiguous, mixed-baseline, or unsafe."""

    repo_root = repo_root.resolve()
    validate_portfolio_cycle_id(plan.cycle_id)
    validate_portfolio_cycle_id(plan.previous_cycle_id)
    validate_candidate_id(plan.portfolio_id)
    if plan.planner_dispatch_id != f"{plan.portfolio_id}:{plan.cycle_id}":
        raise ValueError("portfolio planner_dispatch_id mismatch")
    if plan.previous_cycle_id == "cycle_000":
        if plan.parent_plan_hash or plan.parent_review_hash:
            raise ValueError("initial portfolio must not declare parent hashes")
    else:
        parent_plan_path = portfolio_plan_path(repo_root, plan.previous_cycle_id)
        parent_review_path = (
            repo_root
            / "experiments"
            / plan.previous_cycle_id
            / "planning"
            / "portfolio_review.json"
        )
        if (
            not parent_plan_path.is_file()
            or _path_sha256(parent_plan_path) != plan.parent_plan_hash
        ):
            raise ValueError("portfolio parent plan lineage mismatch")
        if (
            not parent_review_path.is_file()
            or _path_sha256(parent_review_path) != plan.parent_review_hash
        ):
            raise ValueError("portfolio parent review lineage mismatch")
    if tuple(branch.branch_role for branch in plan.branches) != BRANCH_ORDER:
        raise ValueError("portfolio must contain Flow then Logic exactly once")
    candidate_ids = [branch.candidate_id for branch in plan.branches]
    if len(set(candidate_ids)) != len(candidate_ids):
        raise ValueError("portfolio candidate_id values must be unique")
    assignment_paths = [branch.assignment_path.resolve() for branch in plan.branches]
    if len(set(assignment_paths)) != len(assignment_paths):
        raise ValueError("portfolio assignment paths must be unique")
    expected_hash = hash_evaluation_contract(plan.evaluation_contract)
    if plan.evaluation_contract_hash != expected_hash:
        raise ValueError("portfolio evaluation_contract_hash mismatch")
    advice_path = planner_advice_path(repo_root, plan.cycle_id)
    advice = json.loads(advice_path.read_text(encoding="utf-8"))
    if not isinstance(advice, Mapping):
        raise ValueError("planner advice is not a JSON object")
    if hash_planner_advice(advice) != plan.planner_advice_hash:
        raise ValueError("portfolio planner_advice_hash mismatch")
    if advice.get("source") != plan.planner_advice_source:
        raise ValueError("portfolio planner advice source mismatch")
    for branch in plan.branches:
        expected_agent, expected_candidate = BRANCH_SPECS[branch.branch_role]
        if branch.agent_name != expected_agent or branch.candidate_id != expected_candidate:
            raise ValueError(f"invalid {branch.branch_role} branch identity")
        expected_path = safe_repo_path(
            repo_root,
            repo_root
            / "experiments"
            / plan.cycle_id
            / "agents"
            / "assignments"
            / f"{branch.candidate_id}.json",
        )
        actual_path = safe_repo_path(repo_root, branch.assignment_path)
        if actual_path != expected_path:
            raise ValueError(f"non-canonical assignment path: {branch.assignment_path}")
        payload = json.loads(actual_path.read_text(encoding="utf-8"))
        spec = get_coding_agent_spec(payload.get("agent_name"))
        if spec.name != branch.agent_name or payload.get("paper_role") != spec.paper_role:
            raise ValueError(f"role mismatch in {branch.assignment_path}")
        if payload.get("branch_role") != branch.branch_role:
            raise ValueError(f"branch_role mismatch in {branch.assignment_path}")
        identity_checks = {
            "cycle_id": plan.cycle_id,
            "candidate_id": branch.candidate_id,
            "portfolio_id": plan.portfolio_id,
            "planner_dispatch_id": plan.planner_dispatch_id,
        }
        for key, expected in identity_checks.items():
            if payload.get(key) != expected:
                raise ValueError(f"assignment {key} mismatch in {branch.assignment_path}")
        if payload.get("artifact_layout") != CANDIDATE_SCOPED_LAYOUT:
            raise ValueError("dual-agent assignments require candidate-scoped layout")
        if payload.get("source_patch_mode") != FLOW_CANDIDATE_SOURCE_PATCH_DIFF:
            raise ValueError("dual-agent assignments require source_patch_diff")
        if payload.get("baseline_ref") != dict(plan.baseline_ref):
            raise ValueError("portfolio branches do not share one baseline_ref")
        if payload.get("evaluation_contract_hash") != expected_hash:
            raise ValueError("portfolio branches do not share one evaluation contract")
        embedded_contract = payload.get("evaluation_contract")
        if not isinstance(embedded_contract, Mapping):
            raise ValueError("assignment is missing embedded evaluation contract")
        if hash_evaluation_contract(embedded_contract) != expected_hash:
            raise ValueError("assignment embedded evaluation contract hash mismatch")
        if payload.get("planner_advice_hash") != plan.planner_advice_hash:
            raise ValueError("portfolio branches do not share planner advice")
        if payload.get("planner_advice_source") != plan.planner_advice_source:
            raise ValueError("portfolio branch planner advice source mismatch")
        validate_baseline_assignment(payload, plan.baseline_ref)
        validate_assignment_contract(payload, plan.evaluation_contract)


def portfolio_plan_path(repo_root: Path, cycle_id: str) -> Path:
    cycle_id = validate_portfolio_cycle_id(cycle_id)
    return safe_repo_path(
        repo_root,
        repo_root.resolve()
        / "experiments"
        / cycle_id
        / "planning"
        / "portfolio_plan.json",
    )


def planner_advice_path(repo_root: Path, cycle_id: str) -> Path:
    cycle_id = validate_portfolio_cycle_id(cycle_id)
    return safe_repo_path(
        repo_root,
        repo_root.resolve()
        / "experiments"
        / cycle_id
        / "planning"
        / "planner_advice.json",
    )


def hash_evaluation_contract(contract: Mapping[str, object]) -> str:
    canonical = json.dumps(
        dict(contract),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def hash_planner_advice(advice: Mapping[str, object]) -> str:
    canonical = json.dumps(
        dict(advice), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _path_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_assignment_contract(
    assignment: Mapping[str, object],
    contract: Mapping[str, object],
) -> None:
    checks = (
        ("evaluation_flow_commands", "flow_commands"),
        ("evaluation_benchmark_scope", "benchmark_scope"),
        ("promotion_thresholds", "promotion_thresholds"),
        ("benchmark_frontend", "benchmark_frontend"),
        ("target_metric", "target_metric"),
    )
    for assignment_key, contract_key in checks:
        if assignment.get(assignment_key) != contract.get(contract_key):
            raise ValueError(
                "assignment diverges from frozen evaluation contract: "
                f"{assignment_key}"
            )


def validate_baseline_assignment(
    assignment: Mapping[str, object],
    baseline_ref: Mapping[str, object],
) -> None:
    """Bind the declared snapshot to the paths actually consumed by lineage.py."""

    expected = _baseline_assignment_fields(baseline_ref)
    for key, value in expected.items():
        if assignment.get(key) != value:
            raise ValueError(f"assignment runtime baseline diverges at {key}")
    if str(baseline_ref.get("kind", "vanilla")) != "champion":
        for key in (
            "champion_cycle_id",
            "champion_candidate_id",
            "champion_source_root",
            "champion_abc_bin",
        ):
            if assignment.get(key) not in (None, ""):
                raise ValueError(f"vanilla assignment unexpectedly sets {key}")


def _evaluation_contract(
    assignment: Mapping[str, object],
    *,
    baseline_ref: Mapping[str, object],
    timeout_seconds: float,
    build_timeout_seconds: float,
) -> dict[str, object]:
    if timeout_seconds <= 0 or build_timeout_seconds <= 0:
        raise ValueError("evaluation timeouts must be > 0")
    benchmark_scope = list(assignment.get("evaluation_benchmark_scope", ()))
    if not benchmark_scope:
        raise ValueError("paired portfolio requires a non-empty evaluation scope")
    if len(set(str(item) for item in benchmark_scope)) != len(benchmark_scope):
        raise ValueError("paired portfolio evaluation scope contains duplicates")
    return {
        "schema_version": EVALUATION_CONTRACT_VERSION,
        "baseline_ref": dict(baseline_ref),
        "benchmark_frontend": assignment.get("benchmark_frontend", "abc_native"),
        "benchmark_scope": benchmark_scope,
        "flow_commands": list(DEFAULT_EVAL_FLOW_COMMANDS),
        "promotion_thresholds": dict(assignment.get("promotion_thresholds", {})),
        "target_metric": assignment.get("target_metric", "and_count"),
        "timeout_seconds": float(timeout_seconds),
        "build_timeout_seconds": float(build_timeout_seconds),
    }


def _resolve_planner_advice(
    *,
    repo_root: Path,
    cycle_id: str,
    previous_cycle_id: str,
    portfolio_id: str,
    dispatch_id: str,
    baseline_ref: Mapping[str, object],
    evaluation_contract: Mapping[str, object],
    assignments: Mapping[str, dict[str, object]],
    provider: PlannerAdviceProvider | None,
    reuse_existing: bool,
) -> dict[str, object]:
    """Ask Planning for semantics while keeping the execution envelope locked."""

    locked: dict[str, object] = {
        "repo_root": str(repo_root.resolve()),
        "cycle_id": cycle_id,
        "previous_cycle_id": previous_cycle_id,
        "portfolio_id": portfolio_id,
        "planner_dispatch_id": dispatch_id,
        "baseline_ref": dict(baseline_ref),
        "evaluation_contract": dict(evaluation_contract),
        "branches": {
            role: json.loads(json.dumps(assignments[role]))
            for role in BRANCH_ORDER
        },
    }
    existing_path = planner_advice_path(repo_root, cycle_id)
    if reuse_existing and existing_path.is_file():
        persisted = json.loads(existing_path.read_text(encoding="utf-8"))
        if not isinstance(persisted, Mapping):
            raise ValueError("persisted planner advice is not a JSON object")
        replay = _replay_persisted_advice(persisted, locked=locked)
        canonical = _canonical_planner_advice(replay, locked=locked)
        if canonical != dict(persisted):
            raise ValueError("persisted planner advice does not match locked portfolio")
        return canonical
    if provider is None:
        raw: Mapping[str, object] = {
            "source": "deterministic_fallback",
            "cycle_objective": (
                "Evaluate independent Flow and Logic improvements against one "
                "frozen baseline, then compare only correctness-backed QoR."
            ),
            "dispatches": [
                _deterministic_dispatch(role, assignments[role])
                for role in BRANCH_ORDER
            ],
            "benchmark_scope": list(evaluation_contract["benchmark_scope"]),
            "evaluation_flow_commands": list(evaluation_contract["flow_commands"]),
            "risk_controls": [
                "Keep Flow and Logic source ownership disjoint.",
                "Require both reviews before promotion.",
                "Never merge patches implicitly.",
            ],
        }
    else:
        raw = provider(locked)
    return _canonical_planner_advice(raw, locked=locked)


def _replay_persisted_advice(
    persisted: Mapping[str, object],
    *,
    locked: Mapping[str, object],
) -> dict[str, object]:
    branches = locked["branches"]
    contract = locked["evaluation_contract"]
    assert isinstance(branches, Mapping)
    assert isinstance(contract, Mapping)
    persisted_dispatches = persisted.get("dispatches")
    if not isinstance(persisted_dispatches, Sequence):
        raise ValueError("persisted planner advice dispatches are invalid")
    dispatches: list[dict[str, object]] = []
    for role, item in zip(BRANCH_ORDER, persisted_dispatches):
        if not isinstance(item, Mapping):
            raise ValueError("persisted planner advice dispatch is invalid")
        assignment = branches[role]
        assert isinstance(assignment, Mapping)
        dispatches.append(
            {
                **dict(item),
                "source_patch_mode": assignment["source_patch_mode"],
                "source_patch_allowed_roots": list(
                    assignment.get("source_patch_allowed_roots", ())
                ),
            }
        )
    return {
        "source": persisted.get("source"),
        "cycle_objective": persisted.get("cycle_objective"),
        "dispatches": dispatches,
        "benchmark_scope": list(contract["benchmark_scope"]),
        "evaluation_flow_commands": list(contract["flow_commands"]),
        "risk_controls": persisted.get("risk_controls", ()),
        "rulebase_notes": persisted.get("rulebase_notes", ()),
    }


def _deterministic_dispatch(
    branch_role: str,
    assignment: Mapping[str, object],
) -> dict[str, object]:
    hypothesis = str(
        assignment.get("planner_hypothesis")
        or assignment.get("coding_agent_task")
        or f"Test one conservative {branch_role} optimization."
    )
    task_type = str(assignment.get("planner_task_type", "optimization"))
    if task_type not in {"optimization", "repair", "instrumentation"}:
        task_type = "optimization"
    return {
        "branch_role": branch_role,
        "agent_name": assignment["agent_name"],
        "candidate_id": assignment["candidate_id"],
        "task_type": task_type,
        "hypothesis": hypothesis,
        "coding_agent_task": hypothesis,
        "source_patch_mode": assignment["source_patch_mode"],
        "source_patch_allowed_roots": list(
            assignment.get("source_patch_allowed_roots", ())
        ),
        "acceptance_criteria": ["Build, exact-scope CEC, and frozen QoR gates pass."],
        "rollback_criteria": ["Any build, CEC, coverage, or regression gate fails."],
    }


def _canonical_planner_advice(
    raw: Mapping[str, object],
    *,
    locked: Mapping[str, object],
) -> dict[str, object]:
    if not isinstance(raw, Mapping):
        raise ValueError("Planning Agent advice must be a JSON object")
    allowed_top = {
        "source",
        "cycle_objective",
        "dispatches",
        "benchmark_scope",
        "allowed_to_read",
        "evaluation_flow_commands",
        "evidence_summary",
        "validation_evidence",
        "risk_controls",
        "rulebase_notes",
    }
    unexpected = set(raw) - allowed_top
    if unexpected:
        raise ValueError(
            "Planning Agent advice contains unsupported fields: "
            + ", ".join(sorted(unexpected))
        )
    contract = locked["evaluation_contract"]
    assert isinstance(contract, Mapping)
    if list(raw.get("benchmark_scope", ())) != list(contract["benchmark_scope"]):
        raise ValueError("Planning Agent attempted to change benchmark scope")
    if list(raw.get("evaluation_flow_commands", ())) != list(
        contract["flow_commands"]
    ):
        raise ValueError("Planning Agent attempted to change evaluation flow")
    dispatches = raw.get("dispatches")
    if not isinstance(dispatches, Sequence) or isinstance(dispatches, (str, bytes)):
        raise ValueError("Planning Agent dispatches must be an array")
    if [str(item.get("branch_role", "")) for item in dispatches if isinstance(item, Mapping)] != list(BRANCH_ORDER):
        raise ValueError("Planning Agent dispatch order must be Flow then Logic")
    locked_branches = locked["branches"]
    assert isinstance(locked_branches, Mapping)
    canonical_dispatches: list[dict[str, object]] = []
    allowed_task_types = {"optimization", "repair", "instrumentation"}
    required_dispatch_fields = {
        "branch_role",
        "agent_name",
        "candidate_id",
        "task_type",
        "hypothesis",
        "coding_agent_task",
        "source_patch_mode",
        "source_patch_allowed_roots",
        "acceptance_criteria",
        "rollback_criteria",
    }
    for role, item in zip(BRANCH_ORDER, dispatches):
        if not isinstance(item, Mapping):
            raise ValueError(f"Planning Agent {role} dispatch is not an object")
        if set(item) != required_dispatch_fields:
            raise ValueError(f"Planning Agent {role} dispatch fields are not exact")
        locked_assignment = locked_branches[role]
        assert isinstance(locked_assignment, Mapping)
        identity_checks = {
            "branch_role": role,
            "agent_name": locked_assignment["agent_name"],
            "candidate_id": locked_assignment["candidate_id"],
            "source_patch_mode": locked_assignment["source_patch_mode"],
            "source_patch_allowed_roots": list(
                locked_assignment.get("source_patch_allowed_roots", ())
            ),
        }
        for key, expected in identity_checks.items():
            if item.get(key) != expected:
                raise ValueError(f"Planning Agent attempted to change {role} {key}")
        task_type = str(item.get("task_type", ""))
        if task_type not in allowed_task_types:
            raise ValueError(f"Planning Agent {role} task_type is not executable")
        hypothesis = _bounded_planner_text(item.get("hypothesis"), f"{role} hypothesis")
        task = _bounded_planner_text(item.get("coding_agent_task"), f"{role} task")
        canonical_dispatches.append(
            {
                "branch_role": role,
                "agent_name": str(item["agent_name"]),
                "candidate_id": str(item["candidate_id"]),
                "task_type": task_type,
                "hypothesis": hypothesis,
                "coding_agent_task": task,
                "acceptance_criteria": _bounded_planner_list(
                    item.get("acceptance_criteria"), f"{role} acceptance criteria"
                ),
                "rollback_criteria": _bounded_planner_list(
                    item.get("rollback_criteria"), f"{role} rollback criteria"
                ),
            }
        )
    source = str(raw.get("source", "model")).strip()
    if source not in {"model", "deterministic_fallback"}:
        raise ValueError("invalid planner advice source")
    return {
        "schema_version": PLANNER_ADVICE_SCHEMA_VERSION,
        "source": source,
        "cycle_id": locked["cycle_id"],
        "planner_dispatch_id": locked["planner_dispatch_id"],
        "cycle_objective": _bounded_planner_text(
            raw.get("cycle_objective"), "cycle objective"
        ),
        "dispatches": canonical_dispatches,
        "risk_controls": _bounded_planner_list(
            raw.get("risk_controls", ()), "risk controls", allow_empty=True
        ),
        "rulebase_notes": _bounded_planner_list(
            raw.get("rulebase_notes", ()), "rulebase notes", allow_empty=True
        ),
    }


def _bounded_planner_text(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 8000:
        raise ValueError(f"Planning Agent {label} must contain 1..8000 characters")
    return text


def _bounded_planner_list(
    value: object,
    label: str,
    *,
    allow_empty: bool = False,
) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"Planning Agent {label} must be an array")
    items = [_bounded_planner_text(item, label) for item in value]
    if not allow_empty and not items:
        raise ValueError(f"Planning Agent {label} must not be empty")
    if len(items) > 32:
        raise ValueError(f"Planning Agent {label} is too large")
    return items


def _apply_planner_advice(
    assignments: Mapping[str, dict[str, object]],
    advice: Mapping[str, object],
    advice_hash: str,
) -> None:
    dispatches = advice["dispatches"]
    assert isinstance(dispatches, Sequence)
    for role, item in zip(BRANCH_ORDER, dispatches):
        assert isinstance(item, Mapping)
        assignment = assignments[role]
        assignment["planner_hypothesis"] = (
            f"{item['hypothesis']}\n\nCoding task: {item['coding_agent_task']}"
        )
        assignment["planner_task_type"] = item["task_type"]
        assignment["planner_advice_hash"] = advice_hash
        assignment["planner_advice_source"] = advice["source"]


def _write_plan_and_assignments(
    *,
    repo_root: Path,
    cycle_id: str,
    previous_cycle_id: str,
    parent_plan_hash: str,
    parent_review_hash: str,
    portfolio_id: str,
    dispatch_id: str,
    baseline_ref: Mapping[str, object],
    evaluation_contract: Mapping[str, object],
    contract_hash: str,
    planner_advice: Mapping[str, object],
    planner_advice_hash: str,
    assignments: Mapping[str, dict[str, object]],
    overwrite: bool,
) -> PortfolioPlan:
    advice_path = planner_advice_path(repo_root, cycle_id)
    advice_path.parent.mkdir(parents=True, exist_ok=True)
    serialized_advice = json.dumps(
        dict(planner_advice), indent=2, sort_keys=True
    ) + "\n"
    if advice_path.exists() and not overwrite:
        if advice_path.read_text(encoding="utf-8") != serialized_advice:
            raise FileExistsError(f"planner advice already exists: {advice_path}")
    else:
        temporary_advice = advice_path.with_suffix(advice_path.suffix + ".tmp")
        temporary_advice.write_text(serialized_advice, encoding="utf-8")
        temporary_advice.replace(advice_path)
    branches: list[BranchDispatch] = []
    for branch_role in BRANCH_ORDER:
        agent_name, candidate_id = BRANCH_SPECS[branch_role]
        assignment = assignments[branch_role]
        path = write_next_assignment(
            repo_root,
            cycle_id,
            candidate_id,
            assignment,
            overwrite=overwrite,
            allow_identical=True,
        )
        branches.append(
            BranchDispatch(
                branch_role=branch_role,
                agent_name=agent_name,
                candidate_id=candidate_id,
                assignment_path=path,
            )
        )
    plan = PortfolioPlan(
        portfolio_id=portfolio_id,
        planner_dispatch_id=dispatch_id,
        cycle_id=cycle_id,
        previous_cycle_id=previous_cycle_id,
        parent_plan_hash=parent_plan_hash,
        parent_review_hash=parent_review_hash,
        baseline_ref=dict(baseline_ref),
        evaluation_contract=dict(evaluation_contract),
        evaluation_contract_hash=contract_hash,
        planner_advice_hash=planner_advice_hash,
        planner_advice_source=str(planner_advice["source"]),
        branches=tuple(branches),
    )
    path = portfolio_plan_path(repo_root, cycle_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(plan.as_dict(repo_root), indent=2, sort_keys=True) + "\n"
    if path.exists() and not overwrite:
        if path.read_text(encoding="utf-8") == serialized:
            return plan
        raise FileExistsError(f"portfolio plan already exists: {path}")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(serialized, encoding="utf-8")
    temporary.replace(path)
    return plan


def _vanilla_baseline_ref(previous_cycle_id: str) -> dict[str, object]:
    return {
        "kind": "vanilla",
        "cycle_id": previous_cycle_id,
        "candidate_id": "",
        "source_root": FLOWTUNE_SOURCE_ROOT.as_posix(),
        "abc_bin": DEFAULT_ABC_BIN.as_posix(),
    }


def _next_baseline_ref(
    *,
    repo_root: Path,
    current_plan: PortfolioPlan,
    portfolio_review: Mapping[str, Any],
) -> dict[str, object]:
    winner_id = str(portfolio_review.get("selected_candidate_id", "")).strip()
    if not winner_id:
        return dict(current_plan.baseline_ref)
    branch = next(
        (item for item in current_plan.branches if item.candidate_id == winner_id),
        None,
    )
    if branch is None:
        raise ValueError(f"portfolio review names unknown winner: {winner_id!r}")
    branch_reviews = portfolio_review.get("branches", ())
    selected_review = next(
        (
            item
            for item in branch_reviews
            if isinstance(item, Mapping)
            and str(item.get("candidate_id", "")) == winner_id
        ),
        None,
    )
    if not isinstance(selected_review, Mapping) or not bool(
        selected_review.get("eligible_for_promotion", False)
    ):
        raise ValueError("portfolio winner is not marked eligible for promotion")
    context = CycleContext.from_assignment_file(repo_root, branch.assignment_path)
    workspace = (
        impl_compare_root(context) / IMPL_CANDIDATE_LABEL / "workspace"
    ).relative_to(repo_root).as_posix()
    source_root = f"{workspace}/third_party/FlowTune/src"
    source_path = safe_repo_path(repo_root, repo_root / source_root)
    abc_path = safe_repo_path(repo_root, source_path / "abc")
    if not source_path.is_dir() or not abc_path.is_file():
        raise FileNotFoundError(
            "portfolio winner is missing its frozen candidate workspace or ABC binary: "
            f"{abc_path}"
        )
    return {
        "kind": "champion",
        "cycle_id": current_plan.cycle_id,
        "candidate_id": winner_id,
        "source_root": source_root,
        "abc_bin": f"{source_root}/abc",
    }


def _baseline_assignment_fields(
    baseline_ref: Mapping[str, object],
) -> dict[str, object]:
    kind = str(baseline_ref.get("kind", "vanilla"))
    fields: dict[str, object] = {
        "baseline_kind": kind,
        "base_source_root": str(baseline_ref.get("source_root", "")),
        "baseline_abc_bin": str(baseline_ref.get("abc_bin", "")),
    }
    if kind == "champion":
        fields.update(
            {
                "champion_cycle_id": str(baseline_ref.get("cycle_id", "")),
                "champion_candidate_id": str(
                    baseline_ref.get("candidate_id", "")
                ),
                "champion_source_root": str(
                    baseline_ref.get("source_root", "")
                ),
                "champion_abc_bin": str(baseline_ref.get("abc_bin", "")),
            }
        )
    return fields


def _clear_baseline_fields(assignment: dict[str, object]) -> None:
    for key in (
        "baseline_kind",
        "base_source_root",
        "baseline_abc_bin",
        "champion_cycle_id",
        "champion_candidate_id",
        "champion_source_root",
        "champion_abc_bin",
    ):
        assignment.pop(key, None)


def _enforce_branch_scope(
    assignment: dict[str, object],
    branch_role: str,
) -> dict[str, object]:
    """Keep Flow in ``src/opt`` and Logic in its paper-owned ``abci`` root."""

    if branch_role == FLOW_BRANCH:
        cycle_id = str(assignment.get("cycle_id", ""))
        legacy_impl_root = f"experiments/{cycle_id}/impl_compare"
        assignment["source_patch_allowed_roots"] = [
            FLOWTUNE_SOURCE_SCOPE_PRIMARY
        ]
        assignment["allowed_to_edit"] = [
            str(value)
            for value in assignment.get("allowed_to_edit", ())
            if not str(value).startswith("third_party/FlowTune/src/src/base/abci")
            and str(value).rstrip("/") != legacy_impl_root
        ]
    return normalize_coding_assignment(assignment)
