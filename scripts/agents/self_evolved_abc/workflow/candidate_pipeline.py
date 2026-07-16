"""Run one role-neutral source-patch candidate through all review gates."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.agents.self_evolved_abc.cycle_context import CycleContext
from scripts.agents.self_evolved_abc.roles.registry import get_coding_agent_spec
from scripts.agents.self_evolved_abc.flow.source_patch import (
    source_patch_diff_path,
    source_patch_plan_path,
)
from scripts.agents.self_evolved_abc.flow.source_patch_runner import (
    PatchApplyCheckResult,
    check_candidate_patch_against_frozen_baseline,
)
from scripts.agents.self_evolved_abc.flow.contracts import (
    FLOW_CANDIDATE_SOURCE_PATCH_DIFF,
)
from scripts.agents.self_evolved_abc.flow.review import (
    review_impl_compare,
    write_review_artifacts,
)
from scripts.agents.self_evolved_abc.workflow.artifacts import (
    agent_attempt_path,
    agent_attempt_root,
    implementation_root,
    review_decision_path,
)
from scripts.agents.self_evolved_abc.workflow.failure_status import (
    is_coding_infrastructure_failure_status,
)
from scripts.agents.self_evolved_abc.workflow.failure_evidence import (
    validation_feedback_payload,
)


PROPOSAL_DECISION = "PROPOSE_CANDIDATE"
SETTLED_NONPROPOSAL_DECISIONS = frozenset(("DEFER", "NEEDS_PLANNER_APPROVAL"))


@dataclass(frozen=True)
class AgentRunResult:
    """Structured outcome of the bounded coding-agent repair loop."""

    succeeded: bool
    decision: str
    failure_kind: str
    attempts: int
    retryable: bool = False
    detail: str = ""
    build_materialized: bool = False

    @property
    def should_evaluate(self) -> bool:
        return self.succeeded and self.decision == PROPOSAL_DECISION


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one coding-agent source-patch feedback loop."
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--assignment", type=Path, required=True)
    parser.add_argument(
        "--abc-bin",
        default=None,
        help="Compatibility shortcut: use the same ABC binary for baseline and candidate.",
    )
    parser.add_argument(
        "--baseline-abc-bin",
        default=None,
        help="Explicit baseline ABC binary for S5/F7. Defaults to S4 manifest.",
    )
    parser.add_argument(
        "--candidate-abc-bin",
        default=None,
        help="Explicit candidate ABC binary for S5/F7. Defaults to S4 manifest.",
    )
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--build-timeout-seconds", type=float, default=900.0)
    parser.add_argument("--build-jobs", type=int, default=4)
    parser.add_argument("--next-cycle", default=None)
    parser.add_argument("--force-next-assignment", action="store_true")
    parser.add_argument(
        "--skip-next-cycle",
        action="store_true",
        help=(
            "Stop after branch review. A multi-agent coordinator can then "
            "fan in all reviews before creating the next assignments."
        ),
    )
    parser.add_argument(
        "--skip-agent",
        action="store_true",
        help="Use already materialized candidate artifacts instead of calling the model.",
    )
    parser.add_argument(
        "--skip-patch-apply",
        action="store_true",
        help="Skip S4d patch application; useful for abc_flow-only cycles.",
    )
    parser.add_argument(
        "--build-candidate-binary",
        action="store_true",
        help="Build candidate ABC inside the isolated workspace before S5/F7.",
    )
    return parser.parse_args(argv)


def _run_agent_with_retry(
    *,
    repo_root: Path,
    assignment: Path,
    max_retries: int,
    build_candidate_binary: bool = False,
    build_jobs: int = 4,
    build_timeout_seconds: float = 900.0,
) -> AgentRunResult:
    """Run bounded attempts without mutating the frozen Planning assignment."""

    if max_retries < 0:
        raise ValueError("max_retries must be >= 0")
    canonical = assignment if assignment.is_absolute() else repo_root / assignment
    canonical = canonical.resolve()
    original_bytes = canonical.read_bytes()
    original_payload = json.loads(original_bytes.decode("utf-8"))
    if not isinstance(original_payload, dict):
        raise ValueError("assignment must contain one JSON object")
    agent_name = _assignment_agent_name(repo_root, canonical)
    context = CycleContext.from_assignment_file(repo_root, canonical)
    _reset_agent_attempts(context)
    feedback_path = context.artifact_paths().feedback
    repair_hint = ""
    requested_source_files: tuple[str, ...] = ()
    total_attempts = max_retries + 1

    for attempt in range(1, total_attempts + 1):
        # Each model attempt owns a fresh implementation/evaluation namespace.
        # In particular, a compile failure from attempt N must not survive into
        # attempt N+1 when that later attempt ends at provider/validation/DEFER
        # before materializing a new build manifest.  Per-attempt assignments,
        # statuses, feedback, and compiler-log snapshots live outside this root
        # and remain available for audit and repair prompts.
        _reset_attempt_implementation_outputs(context)
        attempt_assignment = agent_attempt_path(context, attempt, "assignment")
        attempt_status = agent_attempt_path(context, attempt, "status")
        attempt_feedback = agent_attempt_path(context, attempt, "feedback")
        attempt_status.unlink(missing_ok=True)
        attempt_feedback.unlink(missing_ok=True)
        if _requires_patch_apply_check(context):
            # A provider failure on a later attempt must never leave an older
            # diff looking like the result of that attempt.
            source_patch_diff_path(context).unlink(missing_ok=True)
        payload = _attempt_assignment_payload(
            original_payload,
            repair_hint=repair_hint,
            requested_source_files=requested_source_files,
        )
        _write_json_atomic(attempt_assignment, payload)

        agent_cmd = (
            sys.executable,
            "-B",
            "-m",
            "scripts.agents.self_evolved_abc.cycle_driver",
            "--repo-root",
            str(repo_root),
            "--assignment",
            str(attempt_assignment),
            "--agent",
            agent_name,
            "--attempt-index",
            str(attempt),
        )
        print(
            f"running: {' '.join(agent_cmd)}  "
            f"(attempt {attempt}/{total_attempts})"
        )
        try:
            completed = subprocess.run(agent_cmd, cwd=repo_root, check=False)
        except OSError as exc:
            return AgentRunResult(
                succeeded=False,
                decision="",
                failure_kind="agent_preparation",
                attempts=attempt,
                detail=f"could not start cycle_driver: {exc}",
            )
        status = _read_attempt_status(
            attempt_status,
            context=context,
            attempt=attempt,
        )

        if canonical.read_bytes() != original_bytes:
            raise RuntimeError("frozen Planning assignment changed during agent retry")

        decision = str(status.get("decision", "")).strip()
        failure_kind = str(status.get("failure_kind", "")).strip()
        if decision == "NEEDS_HUMAN_REVIEW" and (
            completed.returncode == 0 or failure_kind == "response_validation"
        ):
            feedback_text, feedback_sha256 = _snapshot_validation_feedback(
                source=feedback_path,
                destination=attempt_feedback,
            )
            status = _write_validation_attempt_status(
                attempt_status,
                status,
                context=context,
                feedback_path=attempt_feedback,
                feedback_sha256=feedback_sha256,
                feedback_text=feedback_text,
            )
            if attempt < total_attempts:
                repair_hint = _validation_repair_hint(feedback_text)
                print(
                    "iteration_loop: retrying with structured validation feedback"
                )
                continue
            return AgentRunResult(
                succeeded=False,
                decision=decision,
                failure_kind="response_validation",
                attempts=attempt,
                retryable=True,
                detail=feedback_text or "model response failed local validation",
            )

        if completed.returncode != 0:
            retryable = bool(status.get("retryable", False))
            detail = str(status.get("error_message", "")).strip()
            if not failure_kind:
                failure_kind = "agent_preparation"
                detail = detail or (
                    "cycle_driver exited without a valid attempt status "
                    f"(return_code={completed.returncode})"
                )
                retryable = False
            if retryable and attempt < total_attempts:
                repair_hint = _provider_repair_hint(failure_kind, detail)
                print(
                    "iteration_loop: retrying transient/response failure "
                    f"kind={failure_kind}"
                )
                continue
            return AgentRunResult(
                succeeded=False,
                decision="",
                failure_kind=failure_kind,
                attempts=attempt,
                retryable=retryable,
                detail=detail,
            )

        if status.get("status") != "completed":
            detail = "cycle_driver returned zero without a completed attempt status"
            if attempt < total_attempts:
                repair_hint = _provider_repair_hint("missing_attempt_status", detail)
                continue
            return AgentRunResult(
                succeeded=False,
                decision="",
                failure_kind="agent_preparation",
                attempts=attempt,
                detail=detail,
            )

        if decision == PROPOSAL_DECISION and _requires_patch_apply_check(context):
            patch_check = _run_frozen_baseline_patch_check(context)
            if canonical.read_bytes() != original_bytes:
                raise RuntimeError(
                    "frozen Planning assignment changed during patch apply-check"
                )
            detail = _patch_apply_check_detail(context, patch_check)
            if not patch_check.ok:
                _write_patch_check_attempt_status(
                    attempt_status,
                    status,
                    detail=detail,
                    patch_check=patch_check,
                )
                _append_patch_check_feedback(
                    context,
                    attempt=attempt,
                    detail=detail,
                )
                if attempt < total_attempts:
                    repair_hint = _patch_apply_repair_hint(detail)
                    requested_source_files = patch_check.target_paths
                    print(
                        "iteration_loop: retrying after strict frozen-baseline "
                        "patch apply-check failed"
                    )
                    continue
                return AgentRunResult(
                    succeeded=False,
                    decision=decision,
                    failure_kind="patch_apply_check",
                    attempts=attempt,
                    retryable=True,
                    detail=detail,
                )
            _write_patch_check_attempt_status(
                attempt_status,
                status,
                detail=detail,
                patch_check=patch_check,
            )
            requested_source_files = patch_check.target_paths

            if build_candidate_binary:
                compile_ok, compile_detail = _run_candidate_compile_check(
                    context,
                    assignment_path=attempt_assignment,
                    attempt=attempt,
                    build_jobs=build_jobs,
                    build_timeout_seconds=build_timeout_seconds,
                )
                if not compile_ok:
                    _append_compile_check_feedback(
                        context,
                        attempt=attempt,
                        detail=compile_detail,
                    )
                    if attempt < total_attempts:
                        repair_hint = _compile_repair_hint(compile_detail)
                        print(
                            "iteration_loop: retrying after candidate C/C++ "
                            "compile failed"
                        )
                        continue
                    return AgentRunResult(
                        succeeded=False,
                        decision=decision,
                        failure_kind="compile_check",
                        attempts=attempt,
                        retryable=True,
                        detail=compile_detail,
                        build_materialized=True,
                    )
                return AgentRunResult(
                    succeeded=True,
                    decision=decision,
                    failure_kind="",
                    attempts=attempt,
                    build_materialized=True,
                )

        if decision == PROPOSAL_DECISION or decision in SETTLED_NONPROPOSAL_DECISIONS:
            return AgentRunResult(
                succeeded=True,
                decision=decision,
                failure_kind="",
                attempts=attempt,
                detail=(
                    _read_agent_rationale(context.artifact_paths().plan)
                    if decision in SETTLED_NONPROPOSAL_DECISIONS
                    else ""
                ),
            )

        detail = f"unexpected coding-agent decision: {decision or '<empty>'}"
        if attempt < total_attempts:
            repair_hint = _provider_repair_hint("unexpected_decision", detail)
            continue
        return AgentRunResult(
            succeeded=False,
            decision=decision,
            failure_kind="response_validation",
            attempts=attempt,
            detail=detail,
        )

    raise AssertionError("unreachable coding-agent retry state")


def _attempt_assignment_payload(
    original: Mapping[str, Any],
    *,
    repair_hint: str,
    requested_source_files: Sequence[str] = (),
) -> dict[str, Any]:
    payload = json.loads(json.dumps(dict(original)))
    if requested_source_files:
        # Retry-only source selection hint.  Flow/Logic may use this to put an
        # otherwise index-only target into Key Source Context.  It never enters
        # the coordinator-owned frozen assignment.
        payload["source_context_requested_files"] = list(
            dict.fromkeys(str(path) for path in requested_source_files)
        )
    if repair_hint:
        original_hypothesis = str(original.get("planner_hypothesis", "")).strip()
        payload["planner_hypothesis"] = (
            repair_hint.rstrip()
            + "\n\n--- ORIGINAL PLANNING HYPOTHESIS ---\n"
            + original_hypothesis
        ).strip()
    return payload


def _validation_repair_hint(feedback_text: str) -> str:
    detail = feedback_text.strip() or "No validation detail was materialized."
    return (
        "PREVIOUS ATTEMPT FAILED LOCAL RESPONSE VALIDATION. Return a fresh JSON "
        "object that preserves the original hypothesis while fixing only these "
        "contract issues:\n\n"
        + detail[:5000]
    )


def _provider_repair_hint(failure_kind: str, detail: str) -> str:
    return (
        "PREVIOUS MODEL ATTEMPT DID NOT PRODUCE A USABLE JSON RESPONSE. Preserve "
        "the original hypothesis, emit exactly one complete JSON object, and "
        "keep the patch concise enough to finish within the output budget.\n\n"
        f"failure_kind: {failure_kind}\n"
        f"detail: {detail[:2000]}"
    )


def _reset_agent_attempts(context: CycleContext) -> None:
    """Remove artifacts from an older execution of the same candidate lane."""

    root = agent_attempt_root(context)
    if root.is_dir():
        shutil.rmtree(root)
    elif root.exists():
        root.unlink()


def _reset_attempt_implementation_outputs(context: CycleContext) -> None:
    """Remove build/evaluation evidence owned by an older model attempt."""

    root = implementation_root(context)
    if root.is_dir():
        shutil.rmtree(root)
    elif root.exists():
        root.unlink()


def _snapshot_validation_feedback(
    *,
    source: Path,
    destination: Path,
) -> tuple[str, str]:
    """Persist exact per-attempt feedback and return bounded text plus its hash."""

    data = source.read_bytes() if source.is_file() else b""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(destination)
    return (
        data.decode("utf-8", errors="replace")[:5000],
        hashlib.sha256(data).hexdigest(),
    )


def _write_validation_attempt_status(
    path: Path,
    status: Mapping[str, Any],
    *,
    context: CycleContext,
    feedback_path: Path,
    feedback_sha256: str,
    feedback_text: str,
) -> Mapping[str, Any]:
    """Finalize a locally rejected response as a typed, auditable failure."""

    detail = _safe_error_text(
        feedback_text or "validation feedback artifact was empty",
        context=context,
        max_chars=4000,
    )
    payload = dict(status)
    payload.update(
        {
            "status": "failed",
            "failure_kind": "response_validation",
            "retryable": True,
            "decision": "NEEDS_HUMAN_REVIEW",
            "error_type": "LocalResponseValidationError",
            "error_message": detail,
            "validation_feedback_path": feedback_path.relative_to(
                context.repo_root
            ).as_posix(),
            "validation_feedback_sha256": feedback_sha256,
        }
    )
    _write_json_atomic(path, payload)
    return payload


def _requires_patch_apply_check(context: CycleContext) -> bool:
    return (
        str(context.assignment.get("source_patch_mode", "")).strip()
        == FLOW_CANDIDATE_SOURCE_PATCH_DIFF
    )


def _run_frozen_baseline_patch_check(
    context: CycleContext,
) -> PatchApplyCheckResult:
    patch_path = source_patch_diff_path(context)
    try:
        return check_candidate_patch_against_frozen_baseline(
            context=context,
            patch_path=patch_path,
        )
    except (OSError, ValueError) as exc:
        detail = _safe_error_text(str(exc), context=context, max_chars=2000)
        return PatchApplyCheckResult(
            patch_path=patch_path,
            target_paths=(),
            exit_code=1,
            status="patch_apply_check_failed",
            log_lines=(f"preflight_error: {type(exc).__name__}: {detail}",),
        )


def _patch_apply_check_detail(
    context: CycleContext,
    result: PatchApplyCheckResult,
) -> str:
    targets = ", ".join(result.target_paths) or "unavailable"
    useful_lines: list[str] = []
    for raw_line in result.log_lines:
        line = _safe_error_text(raw_line, context=context, max_chars=1000)
        lowered = line.lower()
        if not line or lowered.startswith(("workspace_root:", "check_command:")):
            continue
        if (
            "error:" in lowered
            or "failed" in lowered
            or "does not apply" in lowered
            or "corrupt patch" in lowered
            or "return_code" in lowered
            or lowered.startswith("preflight_error:")
        ):
            useful_lines.append(line)
    if not useful_lines:
        useful_lines.append(f"strict git apply check exited {result.exit_code}")
    diagnostics = " | ".join(useful_lines)[-2500:]
    return (
        f"status={result.status}; targets={targets}; "
        f"diagnostics={diagnostics}"
    )


def _safe_error_text(
    value: str,
    *,
    context: CycleContext,
    max_chars: int,
) -> str:
    text = " ".join(value.split()).replace(str(context.repo_root), "<repo>")
    return text[:max_chars]


def _patch_apply_repair_hint(detail: str) -> str:
    return (
        "PREVIOUS SOURCE_PATCH_DIFF FAILED THE STRICT FROZEN-BASELINE "
        "APPLY-CHECK. Return a fresh complete JSON object with a regenerated "
        "unified diff. Keep the same narrow hypothesis and target, but copy "
        "the exact surrounding context, indentation, and symbols from the "
        "source excerpt; do not use fuzzy or approximate hunks.\n\n"
        f"patch_apply_check: {detail[:3000]}"
    )


def _run_candidate_compile_check(
    context: CycleContext,
    *,
    assignment_path: Path,
    attempt: int,
    build_jobs: int,
    build_timeout_seconds: float,
) -> tuple[bool, str]:
    """Materialize/apply/build inside the same model repair attempt."""

    command = (
        sys.executable,
        "-B",
        "-m",
        "scripts.agents.self_evolved_abc.flow.source_patch_runner",
        "--repo-root",
        str(context.repo_root),
        "--assignment",
        str(assignment_path),
        "--apply-candidate-patch",
        "--record-build-gate",
        "--build-candidate-binary",
        "--build-jobs",
        str(max(1, build_jobs)),
        "--build-timeout-seconds",
        f"{build_timeout_seconds:g}",
    )
    completed = subprocess.run(command, cwd=context.repo_root, check=False)
    impl_root = implementation_root(context)
    build_info = impl_root / "candidate_modified" / "build_info.json"
    build_log = impl_root / "candidate_modified" / "build.log"
    status = "missing"
    if build_info.is_file():
        try:
            payload = json.loads(build_info.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        status = str(payload.get("status", "missing"))
    snapshot = agent_attempt_root(context) / f"attempt_{attempt:02d}.compile.log"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    if build_log.is_file():
        shutil.copy2(build_log, snapshot)
        log_text = build_log.read_text(encoding="utf-8", errors="replace")
    else:
        log_text = "candidate build log was not produced"
        snapshot.write_text(log_text + "\n", encoding="utf-8")
    detail = (
        f"status={status}; return_code={completed.returncode}; "
        f"attempt_log={snapshot.relative_to(context.repo_root)}; "
        "compiler_tail=" + " ".join(log_text[-6000:].split())
    )[:7000]
    return (
        completed.returncode == 0
        and status == "candidate_binary_build_passed",
        detail,
    )


def _append_compile_check_feedback(
    context: CycleContext,
    *,
    attempt: int,
    detail: str,
) -> None:
    path = context.artifact_paths().feedback
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_bounded_text(path, max_chars=20_000).rstrip()
    section = (
        "## Candidate Compile Self-Debug\n\n"
        f"- attempt: {attempt}\n"
        "- status: failed\n"
        f"- bounded compiler evidence: {detail}\n"
    )
    path.write_text(
        (existing + "\n\n" if existing else "") + section,
        encoding="utf-8",
    )


def _compile_repair_hint(detail: str) -> str:
    return (
        "PREVIOUS PATCH APPLIED BUT THE ISOLATED CANDIDATE C/C++ BUILD FAILED. "
        "Preserve the original hypothesis and role scope. Regenerate the full "
        "JSON/diff while fixing the actionable compiler tail below; use only "
        "symbols, types, includes, and APIs visible in the supplied source.\n\n"
        f"compile_check: {detail[:5000]}"
    )


def _write_patch_check_attempt_status(
    path: Path,
    status: Mapping[str, Any],
    *,
    detail: str,
    patch_check: PatchApplyCheckResult,
) -> None:
    payload = dict(status)
    payload.update(
        {
            "patch_apply_check": patch_check.status,
            "patch_apply_check_exit_code": patch_check.exit_code,
            "patch_apply_check_targets": list(patch_check.target_paths),
        }
    )
    if not patch_check.ok:
        payload.update(
            {
                "status": "failed",
                "failure_kind": "patch_apply_check",
                "retryable": True,
                "decision": "NEEDS_HUMAN_REVIEW",
                "error_type": "PatchApplyCheckError",
                "error_message": detail[:4000],
            }
        )
    _write_json_atomic(path, payload)


def _append_patch_check_feedback(
    context: CycleContext,
    *,
    attempt: int,
    detail: str,
) -> None:
    path = context.artifact_paths().feedback
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_bounded_text(path, max_chars=20_000).rstrip()
    section = (
        "## Frozen Baseline Patch Apply Check\n\n"
        f"- attempt: {attempt}\n"
        "- status: failed\n"
        "- semantics: strict git apply; no fuzz or whitespace relaxation\n"
        f"- detail: {detail}\n"
    )
    path.write_text(
        (existing + "\n\n" if existing else "") + section,
        encoding="utf-8",
    )


def _read_attempt_status(
    path: Path,
    *,
    context: CycleContext,
    attempt: int,
) -> Mapping[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    expected = {
        "cycle_id": context.cycle_id,
        "candidate_id": context.candidate_id,
        "agent_name": context.agent_name,
        "attempt": attempt,
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        return {}
    return payload


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _read_bounded_text(path: Path, *, max_chars: int) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")[:max_chars]


def _read_agent_rationale(path: Path) -> str:
    text = _read_bounded_text(path, max_chars=4000)
    marker = "## Rationale"
    if marker not in text:
        return ""
    body = text.split(marker, 1)[1].lstrip("\n")
    if "\n## " in body:
        body = body.split("\n## ", 1)[0]
    return " ".join(body.split())[:1500]


def _assignment_agent_name(repo_root: Path, assignment: Path) -> str:
    path = assignment if assignment.is_absolute() else repo_root / assignment
    payload = json.loads(path.read_text(encoding="utf-8"))
    agent_name = str(payload.get("agent_name", "")).strip()
    return get_coding_agent_spec(agent_name).name


def _materialize_promoted_batch_replay(
    context: CycleContext,
) -> AgentRunResult | None:
    """Replay a fully validated batch patch without another lossy model hop.

    The deterministic batch already compiled and evaluated this exact diff
    against the same frozen baseline.  Re-materializing it in the Flow branch
    lets the normal paired fan-in judge the candidate while preserving all
    build/CEC/QoR gates and avoiding provider/JSON/patch transcription failure.
    """

    raw = context.assignment.get("batch_search_evidence")
    if not isinstance(raw, Mapping) or not bool(raw.get("exact_replay_required")):
        return None
    if context.agent_name != "flow_agent":
        raise ValueError("promoted batch replay is restricted to the Flow lane")
    if not bool(raw.get("promotion_found")):
        raise ValueError("batch replay was requested without a promoted probe")
    if not bool(raw.get("planning_consumed")):
        raise ValueError("batch replay started before post-batch Planning completed")

    relative = str(raw.get("winner_patch_path", "")).strip()
    expected_sha256 = str(raw.get("winner_patch_sha256", "")).strip().lower()
    if not relative or len(expected_sha256) != 64:
        raise ValueError("batch replay patch path or sha256 is missing")
    source = context.resolve_repo_path(relative)
    if not source.is_file():
        raise ValueError(f"batch replay patch is missing: {relative}")
    patch_bytes = source.read_bytes()
    actual_sha256 = hashlib.sha256(patch_bytes).hexdigest()
    if not patch_bytes or actual_sha256 != expected_sha256:
        raise ValueError("batch replay patch no longer matches its validated sha256")

    destination = source_patch_diff_path(context)
    if source.resolve() == destination.resolve():
        raise ValueError("batch replay source aliases the active candidate patch")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(patch_bytes)

    batch_id = str(raw.get("batch_id", "unknown"))
    variant_id = str(raw.get("variant_id", "unknown"))
    winner_cycle = str(raw.get("winner_cycle_id", "unknown"))
    audit = "\n".join(
        (
            f"# Promoted Batch Replay — {context.cycle_id} {context.candidate_id}",
            "",
            "## Rationale",
            "",
            "Planning selected a deterministic Flow probe that already passed "
            "build, full CEC, and QoR. The exact validated diff is replayed "
            "against this round's unchanged frozen baseline so it can enter "
            "the paired Flow/Logic fan-in.",
            "",
            f"- Batch: `{batch_id}`",
            f"- Probe cycle: `{winner_cycle}`",
            f"- Variant: `{variant_id}`",
            f"- Source patch: `{relative}`",
            f"- SHA-256: `{actual_sha256}`",
            "- New optimization hypothesis introduced: `false`",
            "- Required gates: isolated build, exact benchmark coverage, full "
            "CEC, and correctness-backed QoR",
            "",
        )
    )
    paths = context.artifact_paths()
    paths.ensure_parent_dirs()
    paths.plan.write_text(audit, encoding="utf-8")
    paths.candidate_change.write_text(audit, encoding="utf-8")
    paths.feedback.write_text(
        "# Promoted Batch Replay Feedback\n\n"
        "Awaiting fresh build, CEC, and QoR review in the paired candidate lane.\n",
        encoding="utf-8",
    )
    paths.rule_update.write_text(
        "# Promoted Batch Replay Rule Update\n\n"
        "No rulebase change; the measured patch remains subject to fresh gates.\n",
        encoding="utf-8",
    )
    replay_plan = source_patch_plan_path(context)
    replay_plan.parent.mkdir(parents=True, exist_ok=True)
    replay_plan.write_text(audit, encoding="utf-8")
    print(
        "iteration_loop: materialized coordinator-locked promoted batch replay "
        f"variant={variant_id} sha256={actual_sha256}"
    )
    return AgentRunResult(
        succeeded=True,
        decision=PROPOSAL_DECISION,
        failure_kind="",
        attempts=0,
        detail="promoted_batch_replay",
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    repo_root = args.repo_root.resolve()
    assignment = args.assignment
    assignment_path = assignment if assignment.is_absolute() else repo_root / assignment
    try:
        context = CycleContext.from_assignment_file(repo_root, assignment_path)
        _reset_candidate_outputs(context, preserve_agent_artifacts=args.skip_agent)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"iteration_loop: invalid assignment: {exc}", file=sys.stderr)
        return 2
    expected_review_path = review_decision_path(context)
    commands: list[tuple[tuple[str, ...], bool]] = []
    build_materialized = False
    if not args.skip_agent:
        try:
            agent_result = _materialize_promoted_batch_replay(context)
            if agent_result is None:
                agent_result = _run_agent_with_retry(
                    repo_root=repo_root,
                    assignment=assignment_path,
                    max_retries=2,
                    build_candidate_binary=args.build_candidate_binary,
                    build_jobs=max(1, args.build_jobs),
                    build_timeout_seconds=args.build_timeout_seconds,
                )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            agent_result = AgentRunResult(
                succeeded=False,
                decision="",
                failure_kind="agent_preparation",
                attempts=0,
                detail=f"promoted batch replay validation failed: {exc}",
            )
        if not agent_result.should_evaluate:
            if agent_result.succeeded:
                print(
                    "iteration_loop: coding agent settled without a patch "
                    f"decision={agent_result.decision}"
                )
            else:
                print(
                    "iteration_loop: coding agent failed before candidate "
                    f"evaluation kind={agent_result.failure_kind} "
                    f"attempts={agent_result.attempts}"
                )
            return _write_agent_failure_review(context, agent_result)
        build_materialized = agent_result.build_materialized
    if build_materialized:
        print(
            "iteration_loop: candidate build already passed inside the coding "
            "self-debug loop"
        )
    elif not args.skip_patch_apply:
        source_patch_command = [
            sys.executable,
            "-B",
            "-m",
            "scripts.agents.self_evolved_abc.flow.source_patch_runner",
            "--repo-root",
            str(repo_root),
            "--assignment",
            str(assignment),
            "--apply-candidate-patch",
            "--record-build-gate",
        ]
    else:
        source_patch_command = [
            sys.executable,
            "-B",
            "-m",
            "scripts.agents.self_evolved_abc.flow.source_patch_runner",
            "--repo-root",
            str(repo_root),
            "--assignment",
            str(assignment),
            "--record-build-gate",
        ]
    if args.build_candidate_binary and not build_materialized:
        source_patch_command.extend(
            (
                "--build-candidate-binary",
                "--build-jobs",
                str(max(1, args.build_jobs)),
                "--build-timeout-seconds",
                f"{args.build_timeout_seconds:g}",
            )
        )
    if not build_materialized:
        commands.append((tuple(source_patch_command), True))

    baseline_abc_bin = args.baseline_abc_bin or args.abc_bin
    candidate_abc_bin = args.candidate_abc_bin or args.abc_bin
    compare_command = [
        sys.executable,
        "-B",
        "-m",
        "scripts.agents.self_evolved_abc.flow.implementation_compare",
        "--repo-root",
        str(repo_root),
        "--assignment",
        str(assignment),
        "--timeout-seconds",
        f"{args.timeout_seconds:g}",
    ]
    if baseline_abc_bin:
        compare_command.extend(("--baseline-abc-bin", baseline_abc_bin))
    if candidate_abc_bin:
        compare_command.extend(("--candidate-abc-bin", candidate_abc_bin))
    commands.extend(
        (
            (tuple(compare_command), True),
            ((
                sys.executable,
                "-B",
                "-m",
                "scripts.agents.self_evolved_abc.flow.review",
                "--repo-root",
                str(repo_root),
                "--assignment",
                str(assignment),
            ), True),
        )
    )
    next_command = [
        sys.executable,
        "-B",
        "-m",
        "scripts.agents.self_evolved_abc.flow.next_cycle",
        "--repo-root",
        str(repo_root),
        "--assignment",
        str(assignment),
    ]
    if args.next_cycle:
        next_command.extend(("--next-cycle", args.next_cycle))
    if args.force_next_assignment:
        next_command.append("--force")
    if not args.skip_next_cycle:
        commands.append((tuple(next_command), False))

    final_return_code = 0
    for command, continue_on_failure in commands:
        print(f"running: {' '.join(command)}")
        completed = subprocess.run(command, cwd=repo_root, check=False)
        if completed.returncode != 0:
            final_return_code = completed.returncode
        if _is_review_command(command) and not expected_review_path.is_file():
            print(
                "stopped: review did not write "
                f"{expected_review_path.relative_to(repo_root)}"
            )
            return completed.returncode or 1
        if completed.returncode != 0 and not continue_on_failure:
            print(f"stopped: return_code={completed.returncode}")
            return completed.returncode
    return final_return_code


def _cycle_id_from_assignment(assignment: Path) -> str:
    return assignment.parent.parent.parent.name


def _review_decision_path(repo_root: Path, assignment: Path) -> Path:
    path = assignment if assignment.is_absolute() else repo_root / assignment
    context = CycleContext.from_assignment_file(repo_root, path)
    return review_decision_path(context)


def _reset_candidate_outputs(
    context: CycleContext,
    *,
    preserve_agent_artifacts: bool,
) -> None:
    """Start evaluation from a clean lane so old evidence cannot be reused."""

    root = implementation_root(context)
    if root.exists():
        shutil.rmtree(root)
    if preserve_agent_artifacts:
        return
    paths = context.artifact_paths()
    for path in (
        paths.plan,
        paths.candidate_change,
        paths.feedback,
        paths.rule_update,
        source_patch_plan_path(context),
        source_patch_diff_path(context),
    ):
        path.unlink(missing_ok=True)


def _is_review_command(command: Sequence[str]) -> bool:
    return "scripts.agents.self_evolved_abc.flow.review" in command


def _write_agent_failure_review(
    context: CycleContext,
    result: AgentRunResult,
) -> int:
    """Persist a precise non-promoting review when no patch is evaluated."""

    impl_root = implementation_root(context)
    decision = review_impl_compare(context, impl_root)
    expected_local_decision = (
        "REPAIR_COMPILE"
        if result.failure_kind == "compile_check"
        else "REPAIR_VALIDATION"
    )
    if decision.decision != expected_local_decision:
        raise RuntimeError(
            "agent failure review classification mismatch: expected "
            f"{expected_local_decision}, got {decision.decision}"
        )
    build_status, reason, next_action = _agent_failure_review_fields(result)
    review_decision = {
        "DEFER": "DEFERRED_BY_AGENT",
        "NEEDS_PLANNER_APPROVAL": "NEEDS_PLANNER_APPROVAL",
    }.get(result.decision, decision.decision)
    if result.failure_kind == "patch_apply_check":
        review_decision = "REPAIR_PATCH"
    if is_coding_infrastructure_failure_status(build_status):
        review_decision = "CODING_INFRASTRUCTURE_FAILURE"
    validation_feedback = (
        validation_feedback_payload(context)
        if result.failure_kind == "response_validation"
        else None
    )
    decision = replace(
        decision,
        decision=review_decision,
        build_status=build_status,
        reason=reason,
        next_action=next_action,
        validation_evidence_type=(
            str(validation_feedback.get("evidence_type", ""))
            if validation_feedback is not None
            else ""
        ),
        validation_issues_markdown=(
            str(validation_feedback.get("issues_markdown", ""))
            if validation_feedback is not None
            else ""
        ),
        validation_evidence_source=(
            str(validation_feedback.get("source", ""))
            if validation_feedback is not None
            else ""
        ),
        validation_evidence_sha256=(
            str(validation_feedback.get("issues_sha256", ""))
            if validation_feedback is not None
            else ""
        ),
    )
    paths = write_review_artifacts(context, impl_root, decision)
    print(f"review_decision: {paths['decision']}")
    print(f"feedback: {paths['feedback']}")
    print(f"rule_update: {paths['rule_update']}")
    print(f"decision: {decision.decision}")
    return 1


def _agent_failure_review_fields(
    result: AgentRunResult,
) -> tuple[str, str, str]:
    attempts = max(1, result.attempts)
    if result.succeeded and result.decision == "DEFER":
        rationale = " ".join(result.detail.split())[:1000]
        detail = f" Agent rationale: {rationale}" if rationale else ""
        return (
            "agent_deferred",
            "The coding agent explicitly deferred because the supplied evidence "
            "did not justify a safe candidate; no source patch was expected."
            f"{detail}",
            "Give Planning the agent rationale and the exact missing evidence, "
            "then dispatch a narrower evidence-backed hypothesis.",
        )
    if result.succeeded and result.decision == "NEEDS_PLANNER_APPROVAL":
        rationale = " ".join(result.detail.split())[:1000]
        detail = f" Agent rationale: {rationale}" if rationale else ""
        return (
            "agent_needs_planner_approval",
            "The coding agent found that the smallest valid change lies outside "
            "the frozen assignment scope; no source patch was expected."
            f"{detail}",
            "Planning must approve the requested path or replace the hypothesis "
            "without weakening the role boundary.",
        )

    detail = " ".join(result.detail.split())[:1000]
    suffix = f" Last error: {detail}" if detail else ""
    mapping = {
        "provider_configuration": (
            "agent_provider_configuration_failed",
            "The model provider configuration failed before a coding response "
            f"was available after {attempts} attempt(s).{suffix}",
            "Fix the API key, model, endpoint, or response-format configuration "
            "before resuming the campaign.",
        ),
        "provider_transient": (
            "agent_provider_transient_failed",
            "The model provider remained unavailable after the bounded retry "
            f"budget ({attempts} attempt(s)).{suffix}",
            "Inspect the attempt status and provider availability, then resume "
            "the same frozen Planning dispatch.",
        ),
        "provider_permanent": (
            "agent_provider_permanent_failed",
            "The provider rejected the coding request as non-retryable after "
            f"{attempts} attempt(s).{suffix}",
            "Fix authentication, request parameters, model access, or provider "
            "policy before resuming the campaign.",
        ),
        "agent_preparation": (
            "agent_preparation_failed",
            "The coding agent could not prepare or record a complete attempt "
            f"after {attempts} attempt(s).{suffix}",
            "Inspect the attempt assignment/status and repair the local agent "
            "runtime before resuming.",
        ),
        "response_validation": (
            "agent_response_validation_failed",
            "The model returned JSON, but the coding response still violated "
            f"the local role/patch contract after {attempts} attempt(s).{suffix}",
            "Feed the exact validation issues to Planning and narrow the next "
            "hypothesis or patch scope.",
        ),
        "patch_apply_check": (
            "agent_patch_apply_check_failed",
            "The model produced a structurally valid source_patch_diff, but "
            "its unified-diff context did not apply exactly to the frozen "
            f"baseline after {attempts} attempt(s).{suffix}",
            "Regenerate the diff from the exact frozen-baseline source context; "
            "do not use fuzzy hunks or approximate whitespace.",
        ),
        "compile_check": (
            "candidate_binary_build_failed",
            "The patch applied, but the isolated candidate C/C++ build still "
            f"failed after {attempts} same-candidate self-debug attempt(s).{suffix}",
            "Use the persisted per-attempt compiler log to repair the exact "
            "file/line diagnostics; do not spend a new optimization cycle on "
            "an unrelated hypothesis.",
        ),
    }
    if result.failure_kind.startswith("model_response_"):
        return (
            "agent_model_response_failed",
            "The provider returned an unusable coding response after the "
            f"bounded retry budget ({attempts} attempt(s)); this is not a QoR "
            f"experiment result.{suffix}",
            "Inspect the attempt status, output limit, and JSON response mode "
            "before resuming the frozen dispatch.",
        )
    return mapping.get(
        result.failure_kind,
        (
            "agent_preparation_failed",
            "The coding-agent lifecycle ended without an evaluable patch after "
            f"{attempts} attempt(s), failure_kind={result.failure_kind or 'unknown'}."
            f"{suffix}",
            "Inspect the structured attempt status and repair the local coding "
            "pipeline before resuming.",
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
