"""Planning-owned Flow/Logic fan-out and next-round assignment creation."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, replace
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
from scripts.agents.self_evolved_abc.flow.multi_flow import (
    normalized_evaluation_flows,
    normalize_flow_aggregation,
)
from scripts.agents.self_evolved_abc.flow.paths import impl_compare_root
from scripts.agents.self_evolved_abc.logic.contracts import (
    LOGIC_ABCI_ROOT,
    LOGIC_AGENT_NAME,
    LOGIC_EVALUATION_FLOW_COMMANDS,
    LOGIC_REACHABLE_TARGET_COMMANDS,
    normalize_logic_target_command,
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
PLANNER_ADVICE_SCHEMA_VERSION = 2
CAMPAIGN_POLICY_VERSION = 6
POST_BATCH_REPLAN_JOURNAL_SCHEMA_VERSION = 1
POST_BATCH_REPLAN_TRANSACTION = "post_batch_replan"
_SHA256_HEX = frozenset("0123456789abcdef")
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

    _apply_campaign_recovery_state(assignments, consecutive_no_winner=0)

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

    _apply_campaign_recovery_state(
        assignments,
        consecutive_no_winner=_consecutive_portfolio_no_winner(
            repo_root, current_plan.cycle_id
        ),
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
    recover_post_batch_replan(repo_root, cycle_id)
    path = portfolio_plan_path(repo_root, cycle_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    plan = _portfolio_plan_from_payload(repo_root, payload)
    if plan.cycle_id != cycle_id:
        raise ValueError("portfolio plan cycle_id does not match its path")
    validate_portfolio_plan(plan, repo_root=repo_root)
    return plan


def _portfolio_plan_from_payload(
    repo_root: Path,
    payload: object,
) -> PortfolioPlan:
    if not isinstance(payload, Mapping):
        raise ValueError("portfolio plan is not a JSON object")
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
    return plan


def refresh_portfolio_planner_advice(
    *,
    repo_root: Path,
    plan: PortfolioPlan,
    planner_advice_provider: PlannerAdviceProvider | None = None,
) -> PortfolioPlan:
    """Replan both frozen branches after coordinator-injected batch evidence."""

    repo_root = repo_root.resolve()
    if recover_post_batch_replan(repo_root, plan.cycle_id):
        # The previous call had already selected and journaled one generation.
        # Do not ask a nondeterministic provider for a second answer.
        return load_portfolio_plan(repo_root, plan.cycle_id)
    validate_portfolio_plan(plan, repo_root=repo_root)
    assignments: dict[str, dict[str, object]] = {}
    for branch in plan.branches:
        payload = json.loads(branch.assignment_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("portfolio assignment is not a JSON object")
        assignments[branch.branch_role] = payload

    advice = _resolve_planner_advice(
        repo_root=repo_root,
        cycle_id=plan.cycle_id,
        previous_cycle_id=plan.previous_cycle_id,
        portfolio_id=plan.portfolio_id,
        dispatch_id=plan.planner_dispatch_id,
        baseline_ref=plan.baseline_ref,
        evaluation_contract=plan.evaluation_contract,
        assignments=assignments,
        provider=planner_advice_provider,
        reuse_existing=False,
    )
    advice_hash = hash_planner_advice(advice)
    _apply_planner_advice(assignments, advice, advice_hash)
    flow_batch = assignments[FLOW_BRANCH].get("batch_search_evidence")
    if isinstance(flow_batch, dict):
        flow_batch["planning_consumed"] = True
        assignments[FLOW_BRANCH]["batch_search_evidence"] = flow_batch

    for role in BRANCH_ORDER:
        assignment = assignments[role]
        if assignment.get("baseline_ref") != dict(plan.baseline_ref):
            raise ValueError("post-batch Planning attempted to change baseline")
        validate_baseline_assignment(assignment, plan.baseline_ref)
        validate_assignment_contract(assignment, plan.evaluation_contract)

    updated = replace(
        plan,
        planner_advice_hash=advice_hash,
        planner_advice_source=str(advice["source"]),
    )
    staged: list[tuple[Path, str]] = [
        (
            planner_advice_path(repo_root, plan.cycle_id),
            json.dumps(advice, indent=2, sort_keys=True) + "\n",
        ),
        *[
            (
                branch.assignment_path,
                json.dumps(
                    assignments[branch.branch_role], indent=2, sort_keys=True
                )
                + "\n",
            )
            for branch in plan.branches
        ],
        (
            portfolio_plan_path(repo_root, plan.cycle_id),
            json.dumps(updated.as_dict(repo_root), indent=2, sort_keys=True) + "\n",
        ),
    ]
    _commit_post_batch_replan(
        repo_root=repo_root,
        cycle_id=plan.cycle_id,
        planner_dispatch_id=plan.planner_dispatch_id,
        staged=staged,
    )
    validate_portfolio_plan(updated, repo_root=repo_root)
    return updated


def post_batch_replan_journal_path(repo_root: Path, cycle_id: str) -> Path:
    """Return the only journal location accepted for one paired Planning round."""

    cycle_id = validate_portfolio_cycle_id(cycle_id)
    return safe_repo_path(
        repo_root,
        repo_root.resolve()
        / "experiments"
        / cycle_id
        / "planning"
        / "post_batch_replan_journal.json",
    )


def _commit_post_batch_replan(
    *,
    repo_root: Path,
    cycle_id: str,
    planner_dispatch_id: str,
    staged: Sequence[tuple[Path, str]],
) -> None:
    """Journal one validated four-artifact generation, then roll it forward."""

    repo_root = repo_root.resolve()
    cycle_id = validate_portfolio_cycle_id(cycle_id)
    journal_path = post_batch_replan_journal_path(repo_root, cycle_id)
    if journal_path.is_file():
        # Completing the prior generation is safer than overwriting its intent.
        recover_post_batch_replan(repo_root, cycle_id)
        raise ValueError(
            "recovered an earlier post-batch replan; reload the portfolio plan"
        )
    expected_targets = _post_batch_replan_targets(repo_root, cycle_id)
    provided = {safe_repo_path(repo_root, path): text for path, text in staged}
    if set(provided) != set(expected_targets.values()) or len(provided) != 4:
        raise ValueError("post-batch replan must replace exactly four artifacts")

    serialized: dict[str, bytes] = {
        artifact: provided[target].encode("utf-8")
        for artifact, target in expected_targets.items()
    }
    _validate_post_batch_replan_generation(
        repo_root=repo_root,
        cycle_id=cycle_id,
        planner_dispatch_id=planner_dispatch_id,
        artifact_bytes=serialized,
    )
    descriptors = [
        {
            "artifact": artifact,
            "target_path": target.relative_to(repo_root).as_posix(),
            "sha256": hashlib.sha256(serialized[artifact]).hexdigest(),
            "size": len(serialized[artifact]),
        }
        for artifact, target in expected_targets.items()
    ]
    generation_id = _post_batch_replan_generation_id(
        cycle_id=cycle_id,
        planner_dispatch_id=planner_dispatch_id,
        descriptors=descriptors,
    )
    entries: list[dict[str, object]] = []
    for descriptor in descriptors:
        artifact = str(descriptor["artifact"])
        target = expected_targets[artifact]
        temporary = _post_batch_replan_staged_path(target, generation_id)
        temporary.parent.mkdir(parents=True, exist_ok=True)
        _write_bytes_durable(temporary, serialized[artifact])
        _fsync_directory(temporary.parent)
        entries.append(
            {
                **descriptor,
                "staged_path": temporary.relative_to(repo_root).as_posix(),
            }
        )

    journal = {
        "schema_version": POST_BATCH_REPLAN_JOURNAL_SCHEMA_VERSION,
        "transaction": POST_BATCH_REPLAN_TRANSACTION,
        "cycle_id": cycle_id,
        "planner_dispatch_id": planner_dispatch_id,
        "generation_id": generation_id,
        "entries": entries,
    }
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_journal = journal_path.with_suffix(journal_path.suffix + ".tmp")
    _write_bytes_durable(
        temporary_journal,
        (json.dumps(journal, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    temporary_journal.replace(journal_path)
    _fsync_directory(journal_path.parent)
    recover_post_batch_replan(repo_root, cycle_id)


def _validate_post_batch_replan_journal(
    *,
    repo_root: Path,
    cycle_id: str,
    journal: object,
) -> tuple[list[dict[str, object]], dict[str, bytes]]:
    if not isinstance(journal, Mapping):
        raise ValueError("post-batch replan journal is not a JSON object")
    if journal.get("schema_version") != POST_BATCH_REPLAN_JOURNAL_SCHEMA_VERSION:
        raise ValueError("unsupported post-batch replan journal schema")
    if journal.get("transaction") != POST_BATCH_REPLAN_TRANSACTION:
        raise ValueError("unexpected post-batch replan transaction type")
    if journal.get("cycle_id") != cycle_id:
        raise ValueError("post-batch replan journal cycle mismatch")
    dispatch_id = str(journal.get("planner_dispatch_id", ""))
    if not dispatch_id or dispatch_id.rpartition(":")[2] != cycle_id:
        raise ValueError("post-batch replan journal dispatch mismatch")
    generation_id = str(journal.get("generation_id", ""))
    if not _is_sha256_hex(generation_id):
        raise ValueError("post-batch replan generation id is invalid")
    raw_entries = journal.get("entries")
    if not isinstance(raw_entries, list) or len(raw_entries) != 4:
        raise ValueError("post-batch replan journal must contain four entries")

    expected_targets = _post_batch_replan_targets(repo_root, cycle_id)
    raw_by_artifact: dict[str, Mapping[str, object]] = {}
    for raw in raw_entries:
        if not isinstance(raw, Mapping):
            raise ValueError("post-batch replan journal entry is malformed")
        artifact = str(raw.get("artifact", ""))
        if artifact in raw_by_artifact or artifact not in expected_targets:
            raise ValueError("post-batch replan journal artifact set is invalid")
        raw_by_artifact[artifact] = raw
    if set(raw_by_artifact) != set(expected_targets):
        raise ValueError("post-batch replan journal artifact set is incomplete")

    entries: list[dict[str, object]] = []
    descriptors: list[dict[str, object]] = []
    artifact_bytes: dict[str, bytes] = {}
    for artifact, expected_target in expected_targets.items():
        raw = raw_by_artifact[artifact]
        target = _journal_repo_path(repo_root, raw.get("target_path"))
        if target != expected_target:
            raise ValueError("post-batch replan journal target path mismatch")
        staged_path = _journal_repo_path(repo_root, raw.get("staged_path"))
        if staged_path != _post_batch_replan_staged_path(target, generation_id):
            raise ValueError("post-batch replan journal staged path mismatch")
        sha256 = str(raw.get("sha256", ""))
        size = raw.get("size")
        if not _is_sha256_hex(sha256) or not isinstance(size, int):
            raise ValueError("post-batch replan journal hash metadata is invalid")
        if size <= 0 or size > 8 * 1024 * 1024:
            raise ValueError("post-batch replan artifact size is out of bounds")
        content = _read_post_batch_replan_artifact(
            staged_path=staged_path,
            target_path=target,
            expected_hash=sha256,
            expected_size=size,
        )
        artifact_bytes[artifact] = content
        descriptor = {
            "artifact": artifact,
            "target_path": target.relative_to(repo_root).as_posix(),
            "sha256": sha256,
            "size": size,
        }
        descriptors.append(descriptor)
        entries.append(
            {
                **descriptor,
                "staged_path": staged_path,
                "target_path": target,
            }
        )
    expected_generation = _post_batch_replan_generation_id(
        cycle_id=cycle_id,
        planner_dispatch_id=dispatch_id,
        descriptors=descriptors,
    )
    if generation_id != expected_generation:
        raise ValueError("post-batch replan journal generation hash mismatch")
    return entries, artifact_bytes


def _validate_post_batch_replan_generation(
    *,
    repo_root: Path,
    cycle_id: str,
    planner_dispatch_id: str,
    artifact_bytes: Mapping[str, bytes],
) -> None:
    expected = set(_post_batch_replan_targets(repo_root, cycle_id))
    if set(artifact_bytes) != expected:
        raise ValueError("post-batch replan generation is incomplete")
    payloads: dict[str, Mapping[str, object]] = {}
    for artifact, content in artifact_bytes.items():
        try:
            payload = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"post-batch replan {artifact} is not valid UTF-8 JSON"
            ) from exc
        if not isinstance(payload, Mapping):
            raise ValueError(f"post-batch replan {artifact} is not a JSON object")
        payloads[artifact] = payload
    plan = _portfolio_plan_from_payload(repo_root, payloads["portfolio_plan"])
    if plan.cycle_id != cycle_id or plan.planner_dispatch_id != planner_dispatch_id:
        raise ValueError("post-batch replan plan identity mismatch")
    assignments = {
        FLOW_BRANCH: payloads["flow_assignment"],
        LOGIC_BRANCH: payloads["logic_assignment"],
    }
    _validate_portfolio_plan_payloads(
        plan,
        repo_root=repo_root,
        advice=payloads["planner_advice"],
        assignments=assignments,
    )


def _post_batch_replan_targets(repo_root: Path, cycle_id: str) -> dict[str, Path]:
    base = repo_root.resolve() / "experiments" / cycle_id
    return {
        "planner_advice": planner_advice_path(repo_root, cycle_id),
        "flow_assignment": safe_repo_path(
            repo_root,
            base / "agents" / "assignments" / f"{BRANCH_SPECS[FLOW_BRANCH][1]}.json",
        ),
        "logic_assignment": safe_repo_path(
            repo_root,
            base / "agents" / "assignments" / f"{BRANCH_SPECS[LOGIC_BRANCH][1]}.json",
        ),
        "portfolio_plan": portfolio_plan_path(repo_root, cycle_id),
    }


def _post_batch_replan_generation_id(
    *,
    cycle_id: str,
    planner_dispatch_id: str,
    descriptors: Sequence[Mapping[str, object]],
) -> str:
    canonical = json.dumps(
        {
            "cycle_id": cycle_id,
            "planner_dispatch_id": planner_dispatch_id,
            "entries": [dict(item) for item in descriptors],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _post_batch_replan_staged_path(target: Path, generation_id: str) -> Path:
    return target.with_name(f".{target.name}.post-batch-replan.{generation_id}.tmp")


def _journal_repo_path(repo_root: Path, raw_path: object) -> Path:
    text = str(raw_path or "").strip()
    if not text or Path(text).is_absolute():
        raise ValueError("post-batch replan journal paths must be repo-relative")
    return safe_repo_path(repo_root, repo_root.resolve() / text)


def _read_post_batch_replan_artifact(
    *,
    staged_path: Path,
    target_path: Path,
    expected_hash: str,
    expected_size: int,
) -> bytes:
    for path in (staged_path, target_path):
        if not path.is_file():
            continue
        content = path.read_bytes()
        if len(content) == expected_size and hashlib.sha256(content).hexdigest() == expected_hash:
            return content
        if path == staged_path:
            raise ValueError("post-batch replan staged artifact hash mismatch")
    raise ValueError("post-batch replan artifact content is unavailable")


def _replace_post_batch_replan_artifact(staged_path: Path, target_path: Path) -> None:
    staged_path.replace(target_path)
    _fsync_directory(target_path.parent)


def _write_bytes_durable(path: Path, content: bytes) -> None:
    with path.open("wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        # Some network and synthetic filesystems do not expose directory fsync.
        pass
    finally:
        os.close(descriptor)


def _is_sha256_hex(value: str) -> bool:
    return len(value) == 64 and all(char in _SHA256_HEX for char in value)


def recover_post_batch_replan(repo_root: Path, cycle_id: str) -> bool:
    """Roll a published post-batch replan journal forward recoverably.

    A process may stop between any two of the four artifact replacements.  The
    journal is published before the first replacement and contains content
    hashes for exactly the Planning advice, Flow assignment, Logic assignment,
    and portfolio plan for ``cycle_id``.  Recovery accepts no other paths and
    completes the same generation before normal portfolio validation resumes.
    """

    repo_root = repo_root.resolve()
    cycle_id = validate_portfolio_cycle_id(cycle_id)
    journal_path = post_batch_replan_journal_path(repo_root, cycle_id)
    if not journal_path.is_file():
        return False
    if journal_path.stat().st_size > 64 * 1024:
        raise ValueError("post-batch replan journal is too large")
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("post-batch replan journal is not valid JSON") from exc
    entries, artifact_bytes = _validate_post_batch_replan_journal(
        repo_root=repo_root,
        cycle_id=cycle_id,
        journal=journal,
    )
    _validate_post_batch_replan_generation(
        repo_root=repo_root,
        cycle_id=cycle_id,
        planner_dispatch_id=str(journal["planner_dispatch_id"]),
        artifact_bytes=artifact_bytes,
    )

    for entry in entries:
        staged_path = entry["staged_path"]
        target_path = entry["target_path"]
        expected_hash = entry["sha256"]
        if not isinstance(staged_path, Path) or not isinstance(target_path, Path):
            raise ValueError("post-batch replan journal path type is invalid")
        if not isinstance(expected_hash, str):
            raise ValueError("post-batch replan journal hash type is invalid")
        if staged_path.is_file():
            _replace_post_batch_replan_artifact(staged_path, target_path)
        elif not target_path.is_file() or _path_sha256(target_path) != expected_hash:
            raise ValueError(
                "post-batch replan artifact has neither staged nor committed content: "
                f"{entry['artifact']}"
            )

    for entry in entries:
        target_path = entry["target_path"]
        expected_hash = entry["sha256"]
        if not isinstance(target_path, Path) or not isinstance(expected_hash, str):
            raise ValueError("post-batch replan verified entry type is invalid")
        if not target_path.is_file() or _path_sha256(target_path) != expected_hash:
            raise ValueError(
                f"post-batch replan roll-forward hash mismatch: {entry['artifact']}"
            )

    plan_payload = json.loads(
        portfolio_plan_path(repo_root, cycle_id).read_text(encoding="utf-8")
    )
    recovered_plan = _portfolio_plan_from_payload(repo_root, plan_payload)
    validate_portfolio_plan(recovered_plan, repo_root=repo_root)
    journal_path.unlink()
    _fsync_directory(journal_path.parent)
    return True


def validate_portfolio_plan(plan: PortfolioPlan, *, repo_root: Path) -> None:
    """Fail fast if a dispatch is ambiguous, mixed-baseline, or unsafe."""

    repo_root = repo_root.resolve()
    advice = json.loads(
        planner_advice_path(repo_root, plan.cycle_id).read_text(encoding="utf-8")
    )
    assignments: dict[str, Mapping[str, object]] = {}
    if tuple(branch.branch_role for branch in plan.branches) != BRANCH_ORDER:
        raise ValueError("portfolio must contain Flow then Logic exactly once")
    for branch in plan.branches:
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
        if not isinstance(payload, Mapping):
            raise ValueError("portfolio assignment is not a JSON object")
        assignments[branch.branch_role] = payload
    if not isinstance(advice, Mapping):
        raise ValueError("planner advice is not a JSON object")
    _validate_portfolio_plan_payloads(
        plan,
        repo_root=repo_root,
        advice=advice,
        assignments=assignments,
    )


def _validate_portfolio_plan_payloads(
    plan: PortfolioPlan,
    *,
    repo_root: Path,
    advice: Mapping[str, object],
    assignments: Mapping[str, Mapping[str, object]],
) -> None:
    """Validate a complete generation from disk or from journaled bytes."""

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
        payload = assignments.get(branch.branch_role)
        if not isinstance(payload, Mapping):
            raise ValueError(f"missing {branch.branch_role} assignment payload")
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
        ("evaluation_flows", "evaluation_flows"),
        ("flow_aggregation", "flow_aggregation"),
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
        "evaluation_flows": normalized_evaluation_flows(
            assignment.get("evaluation_flows")
        ),
        "flow_aggregation": normalize_flow_aggregation(
            assignment.get("flow_aggregation")
        ),
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
        replay = _replay_persisted_advice(persisted)
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
) -> dict[str, object]:
    persisted_dispatches = persisted.get("dispatches")
    if not isinstance(persisted_dispatches, Sequence):
        raise ValueError("persisted planner advice dispatches are invalid")
    dispatches: list[dict[str, object]] = []
    for item in persisted_dispatches:
        if not isinstance(item, Mapping):
            raise ValueError("persisted planner advice dispatch is invalid")
        dispatches.append(dict(item))
    return {
        "source": persisted.get("source"),
        "cycle_objective": persisted.get("cycle_objective"),
        "dispatches": dispatches,
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
        "task_type": task_type,
        "hypothesis": hypothesis,
        "coding_agent_task": hypothesis,
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
        "risk_controls",
        "rulebase_notes",
    }
    # Model output is untrusted.  A provider operating in plain json_object
    # mode may echo coordinator-owned context or obsolete diagnostic metadata
    # even though the current schema omits it.  Discard those non-authoritative
    # fields: execution always uses the frozen evaluation contract and
    # assignment capabilities created before Planning.
    ignored_non_authoritative_fields = {
        "allowed_to_read",
        "benchmark_scope",
        "evidence_summary",
        "evaluation_flow_commands",
        "validation_evidence",
    }
    unexpected = set(raw) - allowed_top - ignored_non_authoritative_fields
    if unexpected:
        raise ValueError(
            "Planning Agent advice contains unsupported fields: "
            + ", ".join(sorted(unexpected))
        )
    dispatches = raw.get("dispatches")
    if not isinstance(dispatches, Sequence) or isinstance(dispatches, (str, bytes)):
        raise ValueError("Planning Agent dispatches must be an array")
    observed_order = [
        str(item.get("branch_role", ""))
        for item in dispatches
        if isinstance(item, Mapping)
    ]
    if observed_order != list(BRANCH_ORDER):
        raise ValueError("Planning Agent dispatch order must be Flow then Logic")
    canonical_dispatches: list[dict[str, object]] = []
    allowed_task_types = {"optimization", "repair", "instrumentation"}
    required_dispatch_fields = {
        "branch_role",
        "task_type",
        "hypothesis",
        "coding_agent_task",
        "acceptance_criteria",
        "rollback_criteria",
    }
    ignored_dispatch_capabilities = {
        "agent_name",
        "candidate_id",
        "source_patch_mode",
        "source_patch_allowed_roots",
    }
    for role, item in zip(BRANCH_ORDER, dispatches):
        if not isinstance(item, Mapping):
            raise ValueError(f"Planning Agent {role} dispatch is not an object")
        if not required_dispatch_fields.issubset(item) or (
            set(item) - required_dispatch_fields - ignored_dispatch_capabilities
        ):
            raise ValueError(f"Planning Agent {role} dispatch fields are not exact")
        if item.get("branch_role") != role:
            raise ValueError(f"Planning Agent attempted to change {role} branch role")
        task_type = str(item.get("task_type", ""))
        if task_type not in allowed_task_types:
            raise ValueError(f"Planning Agent {role} task_type is not executable")
        hypothesis = _bounded_planner_text(item.get("hypothesis"), f"{role} hypothesis")
        task = _bounded_planner_text(item.get("coding_agent_task"), f"{role} task")
        canonical_dispatches.append(
            {
                "branch_role": role,
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
        hypothesis = (
            f"{item['hypothesis']}\n\nCoding task: {item['coding_agent_task']}"
        )
        batch_evidence = assignment.get("batch_search_evidence")
        if (
            role == FLOW_BRANCH
            and isinstance(batch_evidence, Mapping)
            and bool(batch_evidence.get("exact_replay_required"))
        ):
            patch_path = str(batch_evidence.get("winner_patch_path", "")).strip()
            patch_sha256 = str(
                batch_evidence.get("winner_patch_sha256", "")
            ).strip()
            hypothesis = (
                "COORDINATOR-LOCKED PROMOTED BATCH REPLAY: materialize the "
                f"already measured Flow patch `{patch_path}` with sha256 "
                f"`{patch_sha256}` unchanged, then repeat the frozen build, "
                "full CEC, and QoR gates so it enters this round's paired "
                "fan-in. Planning advice may explain the evidence but may not "
                "replace this replay task.\n\n"
                + hypothesis
            )
        assignment["planner_hypothesis"] = hypothesis
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
    if not isinstance(selected_review, Mapping) or not (
        _portfolio_branch_promotion_claim_is_self_consistent(selected_review)
    ):
        raise ValueError(
            "portfolio winner does not carry a self-consistent promotion claim"
        )
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


def _portfolio_branch_promotion_claim_is_self_consistent(
    branch_review: Mapping[str, object],
) -> bool:
    """Recheck terminal hard gates instead of trusting one eligibility bit."""

    expected = branch_review.get("expected_benchmark_count")
    if not isinstance(expected, int) or isinstance(expected, bool) or expected < 1:
        return False
    return (
        branch_review.get("status") == "reviewed"
        and branch_review.get("return_code") == 0
        and not str(branch_review.get("error", "")).strip()
        and branch_review.get("decision") == "ACCEPT_FOR_NEXT_CYCLE"
        and branch_review.get("eligible_for_promotion") is True
        and branch_review.get("build_status") == "candidate_binary_build_passed"
        and branch_review.get("cec_total_count") == expected
        and branch_review.get("cec_pass_count") == expected
        and branch_review.get("correctness_backed_rows") == expected
    )


def finalize_portfolio_champion(
    *,
    repo_root: Path,
    current_plan: PortfolioPlan,
    portfolio_review: Mapping[str, Any],
) -> dict[str, object]:
    """Persist the terminal champion without manufacturing a next dispatch.

    Normally a selected candidate becomes the baseline while cycle N+1 is
    created.  At an absolute target cycle there is deliberately no N+1, so a
    separate terminal manifest must perform and record the same workspace/
    binary validation.  This keeps the frozen target plan immutable.
    """

    repo_root = repo_root.resolve()
    if portfolio_review.get("cycle_id") != current_plan.cycle_id:
        raise ValueError("terminal portfolio review cycle mismatch")
    if portfolio_review.get("portfolio_id") != current_plan.portfolio_id:
        raise ValueError("terminal portfolio review portfolio mismatch")
    if portfolio_review.get("planner_dispatch_id") != current_plan.planner_dispatch_id:
        raise ValueError("terminal portfolio review dispatch mismatch")
    if not bool(portfolio_review.get("quorum_reached")):
        raise ValueError("terminal champion requires a complete paired fan-in")

    selected = str(portfolio_review.get("selected_candidate_id", "")).strip()
    final_ref = _next_baseline_ref(
        repo_root=repo_root,
        current_plan=current_plan,
        portfolio_review=portfolio_review,
    )
    achieved = str(final_ref.get("kind", "")) == "champion"
    if achieved:
        source_root = safe_repo_path(
            repo_root, repo_root / str(final_ref.get("source_root", ""))
        )
        abc_bin = safe_repo_path(
            repo_root, repo_root / str(final_ref.get("abc_bin", ""))
        )
        if not source_root.is_dir() or not abc_bin.is_file():
            raise FileNotFoundError(
                "terminal champion source workspace or ABC binary is missing"
            )

    plan_path = portfolio_plan_path(repo_root, current_plan.cycle_id)
    review_path = (
        repo_root
        / "experiments"
        / current_plan.cycle_id
        / "planning"
        / "portfolio_review.json"
    )
    if not plan_path.is_file() or not review_path.is_file():
        raise FileNotFoundError("terminal champion lineage artifacts are missing")
    payload: dict[str, object] = {
        "schema_version": 1,
        "portfolio_id": current_plan.portfolio_id,
        "cycle_id": current_plan.cycle_id,
        "planner_dispatch_id": current_plan.planner_dispatch_id,
        "objective_achieved": achieved,
        "status": "champion_finalized" if achieved else "no_champion",
        "winner_selected_this_cycle": bool(selected),
        "selected_candidate_id": selected,
        "final_baseline_ref": dict(final_ref),
        "source_plan_sha256": _path_sha256(plan_path),
        "source_review_sha256": _path_sha256(review_path),
    }
    output = review_path.parent / "final_champion.json"
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    return payload


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


def _apply_campaign_recovery_state(
    assignments: Mapping[str, dict[str, object]],
    *,
    consecutive_no_winner: int,
) -> None:
    """Give both branches one coordinator-owned stagnation/phase envelope."""

    raw_state = assignments[FLOW_BRANCH].get("campaign_state")
    state = dict(raw_state) if isinstance(raw_state, Mapping) else {
        "cycle_number": 1,
        "consecutive_qor_stagnation": 0,
        "evolution_phase": "conservative",
        "exploration_phase": "conservative",
    }
    state["policy_version"] = CAMPAIGN_POLICY_VERSION
    branch_stagnation = int(state.get("consecutive_qor_stagnation", 0) or 0)
    portfolio_stagnation = max(0, int(consecutive_no_winner))
    stagnation = max(branch_stagnation, portfolio_stagnation)
    phase = str(state.get("evolution_phase", "conservative"))
    if stagnation >= 4:
        phase = "structural"
    elif stagnation >= 3 and phase == "conservative":
        phase = "diversify"
    state["branch_consecutive_qor_stagnation"] = branch_stagnation
    state["consecutive_no_winner"] = portfolio_stagnation
    state["consecutive_qor_stagnation"] = stagnation
    state["evolution_phase"] = phase
    state["exploration_phase"] = phase
    cycle_number = int(state.get("cycle_number", 1) or 1)
    if stagnation >= 3:
        for assignment in assignments.values():
            thresholds = dict(assignment.get("promotion_thresholds", {}))
            thresholds.update(
                {
                    "min_average_and_improve_pct": 0.0,
                    "min_total_and_reduction": 1,
                    "min_improved_benchmarks": 1,
                }
            )
            assignment["promotion_thresholds"] = thresholds
    if phase == "structural":
        flow_assignment = assignments[FLOW_BRANCH]
        flow_assignment["target_command"] = ""
        flow_assignment["target_source_dir"] = FLOWTUNE_SOURCE_SCOPE_PRIMARY
        flow_assignment["planner_should_skip_llm"] = True
        flow_meta_raw = flow_assignment.get("_planning_meta")
        flow_recovery_meta = (
            dict(flow_meta_raw) if isinstance(flow_meta_raw, Mapping) else {}
        )
        flow_recovery_meta.update(
            {
                "task_type": "batch_search",
                "target_command": "",
                "target_source_dir": FLOWTUNE_SOURCE_SCOPE_PRIMARY,
                "should_skip_llm": True,
            }
        )
        flow_assignment["_planning_meta"] = flow_recovery_meta
    flow_meta = assignments[FLOW_BRANCH].get("_planning_meta")
    flow_target = (
        str(flow_meta.get("target_command", "")).strip()
        if isinstance(flow_meta, Mapping)
        else str(assignments[FLOW_BRANCH].get("target_command", "")).strip()
    )
    logic_target = _select_logic_recovery_target(
        phase=phase,
        cycle_number=cycle_number,
        stagnation=stagnation,
        flow_target=flow_target,
    )
    logic_assignment = assignments[LOGIC_BRANCH]
    logic_assignment["target_command"] = logic_target
    logic_assignment["target_source_dir"] = LOGIC_ABCI_ROOT
    logic_meta_raw = logic_assignment.get("_planning_meta")
    logic_meta = (
        dict(logic_meta_raw) if isinstance(logic_meta_raw, Mapping) else {}
    )
    logic_meta["target_command"] = logic_target
    logic_meta["target_source_dir"] = LOGIC_ABCI_ROOT
    logic_assignment["_planning_meta"] = logic_meta
    state["flow_target_command"] = flow_target
    state["logic_target_command"] = logic_target
    for role in BRANCH_ORDER:
        assignment = assignments[role]
        assignment["campaign_state"] = dict(state)
        assignment["exploration_lane"] = (
            "flow_measured_search" if role == FLOW_BRANCH else "logic_orthogonal_search"
        )
        hypothesis = str(assignment.get("planner_hypothesis", "")).strip()
        if phase == "structural":
            role_guidance = (
                "Run the bounded rotating cross-family sensitivity stage first, then "
                "change a reached scoring/tie-break/stopping mechanism anchored "
                "in an existing ABC precedent; reject capacity-only edits."
                if role == FLOW_BRANCH
                else f"Use the independently frozen `{logic_target}` Logic "
                "target, recombine existing safe ABC precedents, and avoid the "
                "Flow branch's measured family and all repeated patch targets."
            )
            recovery = (
                "PAPER-ALIGNED STRUCTURAL RECOVERY: "
                f"{stagnation} consecutive correctness-backed QoR misses. "
                + role_guidance
            )
        elif phase == "diversify":
            recovery = (
                "PAPER-ALIGNED DIVERSIFICATION: select a reached decision family "
                "that is distinct from the sibling branch and recent failed targets."
            )
        else:
            recovery = (
                "PAPER-ALIGNED CONSERVATIVE PHASE: test one reversible, reached "
                "mechanism with full compile/CEC/QoR gates."
            )
        assignment["planner_hypothesis"] = (
            recovery + ("\n\n" + hypothesis if hypothesis else "")
        )


def _consecutive_portfolio_no_winner(repo_root: Path, cycle_id: str) -> int:
    """Count complete trailing rounds with valid QoR evidence but no champion."""

    current = int(validate_portfolio_cycle_id(cycle_id).rsplit("_", 1)[1])
    width = len(cycle_id.rsplit("_", 1)[1])
    count = 0
    for number in range(current, 0, -1):
        review_path = (
            repo_root
            / "experiments"
            / f"cycle_{number:0{width}d}"
            / "planning"
            / "portfolio_review.json"
        )
        try:
            review = json.loads(review_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            break
        if (
            not isinstance(review, Mapping)
            or review.get("round_status") != "no_promotion"
            or not bool(review.get("quorum_reached"))
            or str(review.get("selected_candidate_id", "")).strip()
            or not _portfolio_review_has_full_qor_branch(review)
        ):
            break
        count += 1
    return count


def _portfolio_review_has_full_qor_branch(review: Mapping[str, object]) -> bool:
    branches = review.get("branches")
    if not isinstance(branches, Sequence):
        return False
    for branch in branches:
        if not isinstance(branch, Mapping):
            continue
        expected = int(branch.get("expected_benchmark_count", 0) or 0)
        if (
            expected > 0
            and int(branch.get("cec_pass_count", 0) or 0) == expected
            and int(branch.get("cec_total_count", 0) or 0) == expected
            and int(branch.get("correctness_backed_rows", 0) or 0) == expected
            and str(branch.get("decision", ""))
            in {"REPAIR_QOR", "RETAIN_FOR_SYNERGY"}
        ):
            return True
    return False


def _select_logic_recovery_target(
    *,
    phase: str,
    cycle_number: int,
    stagnation: int,
    flow_target: str,
) -> str:
    """Choose a stable Logic family independently of the Flow planner target."""

    if phase == "structural":
        ordered = ("orchestrate", "refactor", "resub", "rewrite")
        offset = max(stagnation, cycle_number - 1)
    elif phase == "diversify":
        ordered = ("refactor", "resub", "rewrite", "orchestrate")
        offset = max(0, cycle_number - 1)
    else:
        ordered = tuple(LOGIC_REACHABLE_TARGET_COMMANDS)
        offset = max(0, cycle_number - 1)

    flow_family = str(flow_target).strip().lower().split(maxsplit=1)
    excluded = flow_family[0] if flow_family else ""
    choices = tuple(command for command in ordered if command != excluded)
    if not choices:
        return normalize_logic_target_command("")
    return normalize_logic_target_command(choices[offset % len(choices)])
