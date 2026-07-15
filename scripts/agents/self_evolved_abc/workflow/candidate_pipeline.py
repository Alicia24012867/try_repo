"""Run one role-neutral source-patch candidate through all review gates."""

from __future__ import annotations

import argparse
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
from scripts.agents.self_evolved_abc.flow.review import (
    review_impl_compare,
    write_review_artifacts,
)
from scripts.agents.self_evolved_abc.workflow.artifacts import (
    agent_attempt_path,
    implementation_root,
    review_decision_path,
)
from scripts.agents.self_evolved_abc.workflow.failure_status import (
    is_coding_infrastructure_failure_status,
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
    feedback_path = context.artifact_paths().feedback
    repair_hint = ""
    total_attempts = max_retries + 1

    for attempt in range(1, total_attempts + 1):
        attempt_assignment = agent_attempt_path(context, attempt, "assignment")
        attempt_status = agent_attempt_path(context, attempt, "status")
        attempt_status.unlink(missing_ok=True)
        payload = _attempt_assignment_payload(
            original_payload,
            repair_hint=repair_hint,
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

        if completed.returncode != 0:
            failure_kind = str(status.get("failure_kind", "")).strip()
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

        decision = str(status.get("decision", "")).strip()
        if decision == "NEEDS_HUMAN_REVIEW":
            feedback_text = _read_bounded_text(feedback_path, max_chars=5000)
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
                detail=feedback_text or "model response failed local validation",
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
) -> dict[str, Any]:
    payload = json.loads(json.dumps(dict(original)))
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
    if not args.skip_agent:
        try:
            agent_result = _run_agent_with_retry(
                repo_root=repo_root,
                assignment=assignment_path,
                max_retries=2,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"iteration_loop: invalid assignment: {exc}", file=sys.stderr)
            return 2
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
    if not args.skip_patch_apply:
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
    if args.build_candidate_binary:
        source_patch_command.extend(
            (
                "--build-candidate-binary",
                "--build-jobs",
                str(max(1, args.build_jobs)),
                "--build-timeout-seconds",
                f"{args.build_timeout_seconds:g}",
            )
        )
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
    if decision.decision != "REPAIR_VALIDATION":
        raise RuntimeError(
            "agent failure without build evidence must classify as "
            f"REPAIR_VALIDATION, got {decision.decision}"
        )
    build_status, reason, next_action = _agent_failure_review_fields(result)
    review_decision = {
        "DEFER": "DEFERRED_BY_AGENT",
        "NEEDS_PLANNER_APPROVAL": "NEEDS_PLANNER_APPROVAL",
    }.get(result.decision, decision.decision)
    if is_coding_infrastructure_failure_status(build_status):
        review_decision = "CODING_INFRASTRUCTURE_FAILURE"
    decision = replace(
        decision,
        decision=review_decision,
        build_status=build_status,
        reason=reason,
        next_action=next_action,
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
