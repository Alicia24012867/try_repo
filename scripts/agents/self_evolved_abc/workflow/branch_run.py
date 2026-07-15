"""Durable provenance for one branch of a paired Planning round."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from scripts.agents.self_evolved_abc.planning.portfolio import (
    BranchDispatch,
    PortfolioPlan,
)
from scripts.agents.self_evolved_abc.workflow.artifacts import safe_repo_path
from scripts.agents.self_evolved_abc.workflow.failure_status import (
    is_coding_infrastructure_failure_status,
)


BRANCH_RUN_SCHEMA_VERSION = 1


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def branch_run_manifest_path(
    repo_root: Path,
    plan: PortfolioPlan,
    branch: BranchDispatch,
) -> Path:
    return safe_repo_path(
        repo_root,
        repo_root.resolve()
        / "experiments"
        / plan.cycle_id
        / "planning"
        / "branch_runs"
        / f"{branch.candidate_id}.json",
    )


def load_valid_branch_run(
    *,
    repo_root: Path,
    plan: PortfolioPlan,
    branch: BranchDispatch,
    review_path: Path,
) -> dict[str, Any] | None:
    """Return a resumable run only when every immutable input still matches."""

    manifest_path = branch_run_manifest_path(repo_root, plan, branch)
    if not manifest_path.is_file() or not review_path.is_file():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    expected: Mapping[str, object] = {
        "schema_version": BRANCH_RUN_SCHEMA_VERSION,
        "cycle_id": plan.cycle_id,
        "candidate_id": branch.candidate_id,
        "branch_role": branch.branch_role,
        "planner_dispatch_id": plan.planner_dispatch_id,
        "evaluation_contract_hash": plan.evaluation_contract_hash,
        "planner_advice_hash": plan.planner_advice_hash,
        "baseline_ref": dict(plan.baseline_ref),
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        return None
    try:
        if payload.get("assignment_sha256") != file_sha256(branch.assignment_path):
            return None
        if payload.get("review_sha256") != file_sha256(review_path):
            return None
    except OSError:
        return None
    if payload.get("status") != "reviewed":
        return None
    try:
        review = json.loads(review_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(review, dict):
        return None
    build_status = str(review.get("build_status", "")).strip()
    # Pre-classification runs used "missing" for provider, JSON, validation,
    # and preparation failures alike.  Re-run that lane once under the new
    # structured attempt contract instead of caching the ambiguous outcome.
    if build_status == "missing" or is_coding_infrastructure_failure_status(
        build_status
    ):
        return None
    return payload


def write_branch_run_manifest(
    *,
    repo_root: Path,
    plan: PortfolioPlan,
    branch: BranchDispatch,
    review_path: Path,
    return_code: int | None,
    elapsed_seconds: float,
    error: str,
    status: str,
) -> Path | None:
    """Atomically bind a valid review to its assignment and frozen contract."""

    if status != "reviewed" or not review_path.is_file():
        return None
    payload: dict[str, object] = {
        "schema_version": BRANCH_RUN_SCHEMA_VERSION,
        "cycle_id": plan.cycle_id,
        "candidate_id": branch.candidate_id,
        "branch_role": branch.branch_role,
        "planner_dispatch_id": plan.planner_dispatch_id,
        "assignment_sha256": file_sha256(branch.assignment_path),
        "review_sha256": file_sha256(review_path),
        "evaluation_contract_hash": plan.evaluation_contract_hash,
        "planner_advice_hash": plan.planner_advice_hash,
        "baseline_ref": dict(plan.baseline_ref),
        "return_code": return_code,
        "elapsed_seconds": round(max(0.0, elapsed_seconds), 6),
        "error": error,
        "status": status,
    }
    path = branch_run_manifest_path(repo_root, plan, branch)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path
