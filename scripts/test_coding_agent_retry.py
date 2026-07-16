#!/usr/bin/env python3
"""Regression tests for coding-agent provider and validation retry semantics."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.agents.self_evolved_abc.cycle_context import CycleContext
from scripts.agents.self_evolved_abc import cycle_driver
from scripts.agents.self_evolved_abc.flow.validation import flow_response_json_schema
from scripts.agents.self_evolved_abc.flow.source_patch import source_patch_diff_path
from scripts.agents.self_evolved_abc.model_client import (
    ModelConfigurationError,
    ModelInvocation,
    ModelProviderTransientError,
    ModelResponseError,
    OpenAIModelClient,
)
from scripts.agents.self_evolved_abc.workflow.artifacts import (
    agent_attempt_path,
    agent_attempt_root,
    implementation_root,
)
from scripts.agents.self_evolved_abc.workflow import candidate_pipeline


def _assignment() -> dict[str, object]:
    return {
        "schema_version": 1,
        "cycle_id": "cycle_001",
        "previous_cycle_id": "cycle_000",
        "candidate_id": "flow_candidate_001",
        "agent_name": "flow_agent",
        "paper_role": "Flow Agent",
        "artifact_layout": "candidate_scoped_v2",
        "planner_hypothesis": "Test the original frozen hypothesis.",
        "benchmark_scope": [],
        "evaluation_benchmark_scope": [],
    }


PATCH_TARGET = "third_party/FlowTune/src/src/opt/demo.c"
BASELINE_SOURCE = "int choose(void)\n{\n    return 1;\n}\n"
VALID_PATCH = """diff --git a/{target} b/{target}
--- a/{target}
+++ b/{target}
@@ -1,4 +1,4 @@
 int choose(void)
 {{
-    return 1;
+    return 2;
 }}
""".format(target=PATCH_TARGET)
FUZZ_ONLY_PATCH = """diff --git a/{target} b/{target}
--- a/{target}
+++ b/{target}
@@ -1,4 +1,4 @@
 int wrong_context(void)
 {{
-    return 1;
+    return 2;
 }}
""".format(target=PATCH_TARGET)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


class CodingAgentRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        self.assignment = (
            self.repo
            / "experiments/cycle_001/agents/assignments/flow_candidate_001.json"
        )
        _write_json(self.assignment, _assignment())
        self.original = self.assignment.read_bytes()

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _command_value(command: object, flag: str) -> str:
        values = list(command)  # type: ignore[arg-type]
        return str(values[values.index(flag) + 1])

    def _status(
        self,
        attempt_assignment: Path,
        attempt: int,
        *,
        status: str,
        decision: str = "",
        failure_kind: str = "",
        retryable: bool = False,
        error_message: str = "",
    ) -> None:
        context = CycleContext.from_assignment_file(self.repo, attempt_assignment)
        _write_json(
            agent_attempt_path(context, attempt, "status"),
            {
                "schema_version": 1,
                "cycle_id": context.cycle_id,
                "candidate_id": context.candidate_id,
                "agent_name": context.agent_name,
                "attempt": attempt,
                "status": status,
                "failure_kind": failure_kind,
                "retryable": retryable,
                "decision": decision,
                "error_type": "SyntheticError" if error_message else "",
                "error_message": error_message,
            },
        )

    def _enable_source_patch_mode(self) -> CycleContext:
        payload = json.loads(self.assignment.read_text(encoding="utf-8"))
        payload.update(
            {
                "source_patch_mode": "source_patch_diff",
                "source_patch_allowed_roots": [
                    "third_party/FlowTune/src/src/opt"
                ],
                "allowed_to_edit": ["third_party/FlowTune/src/src/opt"],
            }
        )
        _write_json(self.assignment, payload)
        self.original = self.assignment.read_bytes()
        source = self.repo / PATCH_TARGET
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(BASELINE_SOURCE, encoding="utf-8")
        return CycleContext.from_assignment_file(self.repo, self.assignment)

    def _run_patch_attempts(self, patches: list[str]) -> candidate_pipeline.AgentRunResult:
        context = CycleContext.from_assignment_file(self.repo, self.assignment)
        hypotheses: list[str] = []
        real_subprocess_run = subprocess.run

        def fake_run(command: object, **kwargs: object) -> object:
            values = list(command)  # type: ignore[arg-type]
            if "scripts.agents.self_evolved_abc.cycle_driver" not in values:
                return real_subprocess_run(values, **kwargs)
            attempt = int(self._command_value(values, "--attempt-index"))
            attempt_assignment = Path(
                self._command_value(values, "--assignment")
            )
            payload = json.loads(attempt_assignment.read_text(encoding="utf-8"))
            hypotheses.append(str(payload["planner_hypothesis"]))
            patch_path = source_patch_diff_path(context)
            patch_path.parent.mkdir(parents=True, exist_ok=True)
            patch_path.write_text(patches[attempt - 1], encoding="utf-8")
            self._status(
                attempt_assignment,
                attempt,
                status="completed",
                decision="PROPOSE_CANDIDATE",
            )
            return SimpleNamespace(returncode=0)

        with patch.object(candidate_pipeline.subprocess, "run", side_effect=fake_run):
            result = candidate_pipeline._run_agent_with_retry(
                repo_root=self.repo,
                assignment=self.assignment,
                max_retries=len(patches) - 1,
            )
        self.patch_hypotheses = hypotheses
        return result

    def test_transient_provider_failures_retry_without_mutating_assignment(self) -> None:
        hypotheses: list[str] = []

        def fake_run(command: object, **_: object) -> SimpleNamespace:
            attempt = int(self._command_value(command, "--attempt-index"))
            attempt_assignment = Path(
                self._command_value(command, "--assignment")
            )
            payload = json.loads(attempt_assignment.read_text(encoding="utf-8"))
            hypotheses.append(str(payload["planner_hypothesis"]))
            if attempt < 3:
                self._status(
                    attempt_assignment,
                    attempt,
                    status="failed",
                    failure_kind="provider_transient",
                    retryable=True,
                    error_message=f"temporary outage {attempt}",
                )
                return SimpleNamespace(returncode=10)
            self._status(
                attempt_assignment,
                attempt,
                status="completed",
                decision="PROPOSE_CANDIDATE",
            )
            return SimpleNamespace(returncode=0)

        with patch.object(candidate_pipeline.subprocess, "run", side_effect=fake_run):
            result = candidate_pipeline._run_agent_with_retry(
                repo_root=self.repo,
                assignment=self.assignment,
                max_retries=2,
            )

        self.assertTrue(result.should_evaluate)
        self.assertEqual(result.attempts, 3)
        self.assertEqual(self.assignment.read_bytes(), self.original)
        self.assertEqual(hypotheses[0], "Test the original frozen hypothesis.")
        for hypothesis in hypotheses[1:]:
            self.assertEqual(
                hypothesis.count("--- ORIGINAL PLANNING HYPOTHESIS ---"), 1
            )
            self.assertEqual(
                hypothesis.count("Test the original frozen hypothesis."), 1
            )

    def test_validation_repair_uses_only_the_latest_feedback(self) -> None:
        hypotheses: list[str] = []
        feedback_by_attempt: dict[int, str] = {}

        def fake_run(command: object, **_: object) -> SimpleNamespace:
            attempt = int(self._command_value(command, "--attempt-index"))
            attempt_assignment = Path(
                self._command_value(command, "--assignment")
            )
            payload = json.loads(attempt_assignment.read_text(encoding="utf-8"))
            hypotheses.append(str(payload["planner_hypothesis"]))
            context = CycleContext.from_assignment_file(self.repo, attempt_assignment)
            context.artifact_paths().feedback.parent.mkdir(parents=True, exist_ok=True)
            feedback = f"## Validation Issues\n\nunique_issue_{attempt}\n"
            feedback_by_attempt[attempt] = feedback
            context.artifact_paths().feedback.write_text(feedback, encoding="utf-8")
            self._status(
                attempt_assignment,
                attempt,
                status="failed",
                decision="NEEDS_HUMAN_REVIEW",
                failure_kind="response_validation",
                retryable=True,
            )
            return SimpleNamespace(returncode=0)

        with patch.object(candidate_pipeline.subprocess, "run", side_effect=fake_run):
            result = candidate_pipeline._run_agent_with_retry(
                repo_root=self.repo,
                assignment=self.assignment,
                max_retries=2,
            )

        self.assertFalse(result.succeeded)
        self.assertEqual(result.failure_kind, "response_validation")
        self.assertEqual(result.attempts, 3)
        self.assertEqual(self.assignment.read_bytes(), self.original)
        self.assertIn("unique_issue_1", hypotheses[1])
        self.assertNotIn("unique_issue_1", hypotheses[2])
        self.assertIn("unique_issue_2", hypotheses[2])
        self.assertEqual(
            hypotheses[2].count("--- ORIGINAL PLANNING HYPOTHESIS ---"), 1
        )
        context = CycleContext.from_assignment_file(self.repo, self.assignment)
        for attempt, feedback in feedback_by_attempt.items():
            snapshot = agent_attempt_path(context, attempt, "feedback")
            self.assertEqual(snapshot.read_text(encoding="utf-8"), feedback)
            status = json.loads(
                agent_attempt_path(context, attempt, "status").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(status["status"], "failed")
            self.assertEqual(status["failure_kind"], "response_validation")
            self.assertEqual(status["decision"], "NEEDS_HUMAN_REVIEW")
            self.assertEqual(status["error_type"], "LocalResponseValidationError")
            self.assertEqual(
                status["validation_feedback_sha256"],
                hashlib.sha256(feedback.encode("utf-8")).hexdigest(),
            )
            self.assertEqual(
                (self.repo / status["validation_feedback_path"]).resolve(),
                snapshot.resolve(),
            )

        self.assertEqual(
            candidate_pipeline._write_agent_failure_review(context, result),
            1,
        )
        review = json.loads(
            candidate_pipeline.review_decision_path(context).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            review["validation_issues_markdown"], "unique_issue_3"
        )
        self.assertEqual(
            review["validation_evidence_sha256"],
            hashlib.sha256(b"unique_issue_3").hexdigest(),
        )

    def test_new_run_removes_stale_candidate_attempt_artifacts(self) -> None:
        context = CycleContext.from_assignment_file(self.repo, self.assignment)
        stale = agent_attempt_path(context, 9, "feedback")
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_text("stale validation evidence\n", encoding="utf-8")
        (agent_attempt_root(context) / "obsolete.tmp").write_text(
            "stale\n", encoding="utf-8"
        )

        def fake_run(command: object, **_: object) -> SimpleNamespace:
            attempt = int(self._command_value(command, "--attempt-index"))
            attempt_assignment = Path(
                self._command_value(command, "--assignment")
            )
            self._status(
                attempt_assignment,
                attempt,
                status="completed",
                decision="PROPOSE_CANDIDATE",
            )
            return SimpleNamespace(returncode=0)

        with patch.object(candidate_pipeline.subprocess, "run", side_effect=fake_run):
            result = candidate_pipeline._run_agent_with_retry(
                repo_root=self.repo,
                assignment=self.assignment,
                max_retries=0,
            )

        self.assertTrue(result.should_evaluate)
        self.assertFalse(stale.exists())
        self.assertFalse((agent_attempt_root(context) / "obsolete.tmp").exists())
        self.assertTrue(agent_attempt_path(context, 1, "assignment").is_file())
        self.assertTrue(agent_attempt_path(context, 1, "status").is_file())

    def test_promoted_batch_replay_materializes_exact_hashed_flow_patch(self) -> None:
        probe_patch = self.repo / "experiments/probe_001/agents/source_patches/candidate_001.diff"
        probe_patch.parent.mkdir(parents=True, exist_ok=True)
        probe_patch.write_text(VALID_PATCH, encoding="utf-8")
        payload = json.loads(self.assignment.read_text(encoding="utf-8"))
        payload["batch_search_evidence"] = {
            "batch_id": "cycle_001_planner_flow_wide",
            "promotion_found": True,
            "planning_consumed": True,
            "exact_replay_required": True,
            "winner_cycle_id": "probe_001",
            "variant_id": "rewrite_validated",
            "winner_patch_path": probe_patch.relative_to(self.repo).as_posix(),
            "winner_patch_sha256": hashlib.sha256(probe_patch.read_bytes()).hexdigest(),
        }
        _write_json(self.assignment, payload)
        context = CycleContext.from_assignment_file(self.repo, self.assignment)

        result = candidate_pipeline._materialize_promoted_batch_replay(context)

        self.assertIsNotNone(result)
        self.assertTrue(result.should_evaluate)  # type: ignore[union-attr]
        self.assertEqual(source_patch_diff_path(context).read_bytes(), probe_patch.read_bytes())
        self.assertIn(
            "Promoted Batch Replay",
            context.artifact_paths().plan.read_text(encoding="utf-8"),
        )

    def test_cycle_driver_marks_local_validation_as_typed_failure(self) -> None:
        feedback = "## Validation Issues\n\nfield source_patch_diff is invalid\n"

        class FakeAgent:
            def __init__(self, **_: object) -> None:
                pass

            def run(self) -> SimpleNamespace:
                return SimpleNamespace(
                    decision="NEEDS_HUMAN_REVIEW",
                    feedback_markdown=feedback,
                )

        with patch.object(
            cycle_driver,
            "build_model_client_from_env",
            return_value=object(),
        ), patch.object(
            cycle_driver,
            "resolve_agent_class",
            return_value=FakeAgent,
        ):
            return_code = cycle_driver.main(
                (
                    "--repo-root",
                    str(self.repo),
                    "--assignment",
                    str(self.assignment),
                    "--agent",
                    "flow_agent",
                    "--attempt-index",
                    "1",
                )
            )

        self.assertEqual(return_code, 0)
        context = CycleContext.from_assignment_file(self.repo, self.assignment)
        status = json.loads(
            agent_attempt_path(context, 1, "status").read_text(encoding="utf-8")
        )
        self.assertEqual(status["status"], "failed")
        self.assertEqual(status["failure_kind"], "response_validation")
        self.assertTrue(status["retryable"])
        self.assertEqual(status["decision"], "NEEDS_HUMAN_REVIEW")
        self.assertEqual(status["error_type"], "LocalResponseValidationError")
        self.assertIn("source_patch_diff is invalid", status["error_message"])

    def test_patch_apply_check_repairs_bad_context_on_second_attempt(self) -> None:
        context = self._enable_source_patch_mode()
        source = self.repo / PATCH_TARGET
        source_before = source.read_bytes()

        result = self._run_patch_attempts([FUZZ_ONLY_PATCH, VALID_PATCH])

        self.assertTrue(result.should_evaluate)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(self.assignment.read_bytes(), self.original)
        self.assertEqual(source.read_bytes(), source_before)
        self.assertIn("strict frozen-baseline", self.patch_hypotheses[1].lower())
        self.assertIn(PATCH_TARGET, self.patch_hypotheses[1])
        second_assignment = json.loads(
            agent_attempt_path(context, 2, "assignment").read_text(encoding="utf-8")
        )
        self.assertEqual(
            second_assignment["source_context_requested_files"], [PATCH_TARGET]
        )
        self.assertNotIn(
            "source_context_requested_files",
            json.loads(self.assignment.read_text(encoding="utf-8")),
        )
        first_status = json.loads(
            agent_attempt_path(context, 1, "status").read_text(encoding="utf-8")
        )
        second_status = json.loads(
            agent_attempt_path(context, 2, "status").read_text(encoding="utf-8")
        )
        self.assertEqual(first_status["failure_kind"], "patch_apply_check")
        self.assertEqual(first_status["patch_apply_check"], "patch_apply_check_failed")
        self.assertIn("patch failed", first_status["error_message"].lower())
        self.assertEqual(
            second_status["patch_apply_check"], "patch_apply_check_passed"
        )
        self.assertFalse(
            (
                self.repo
                / "experiments/cycle_001/candidates/flow_candidate_001/"
                "impl_compare/candidate_modified/patch_apply_checks"
            ).exists()
        )

    def test_three_strict_apply_failures_have_precise_terminal_review(self) -> None:
        context = self._enable_source_patch_mode()
        source_before = (self.repo / PATCH_TARGET).read_bytes()

        result = self._run_patch_attempts(
            [FUZZ_ONLY_PATCH, FUZZ_ONLY_PATCH, FUZZ_ONLY_PATCH]
        )

        self.assertFalse(result.succeeded)
        self.assertEqual(result.failure_kind, "patch_apply_check")
        self.assertEqual(result.attempts, 3)
        self.assertIn(PATCH_TARGET, result.detail)
        self.assertIn("patch failed", result.detail.lower())
        self.assertEqual(self.assignment.read_bytes(), self.original)
        self.assertEqual((self.repo / PATCH_TARGET).read_bytes(), source_before)
        return_code = candidate_pipeline._write_agent_failure_review(context, result)
        self.assertEqual(return_code, 1)
        review = json.loads(
            (
                self.repo
                / "experiments/cycle_001/candidates/flow_candidate_001/"
                "impl_compare/comparison/review_decision.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(review["decision"], "REPAIR_PATCH")
        self.assertEqual(review["build_status"], "agent_patch_apply_check_failed")
        self.assertIn("did not apply exactly", review["reason"])
        self.assertIn("3 attempt(s)", review["reason"])
        final_status = json.loads(
            agent_attempt_path(context, 3, "status").read_text(encoding="utf-8")
        )
        self.assertEqual(final_status["failure_kind"], "patch_apply_check")
        self.assertNotIn(str(self.repo), final_status["error_message"])

    def test_compile_failure_is_repaired_inside_the_same_candidate(self) -> None:
        context = self._enable_source_patch_mode()
        hypotheses: list[str] = []
        real_subprocess_run = subprocess.run

        def fake_run(command: object, **kwargs: object) -> object:
            values = list(command)  # type: ignore[arg-type]
            if "scripts.agents.self_evolved_abc.cycle_driver" not in values:
                return real_subprocess_run(values, **kwargs)
            attempt = int(self._command_value(values, "--attempt-index"))
            attempt_assignment = Path(
                self._command_value(values, "--assignment")
            )
            payload = json.loads(
                attempt_assignment.read_text(encoding="utf-8")
            )
            hypotheses.append(str(payload["planner_hypothesis"]))
            patch_path = source_patch_diff_path(context)
            patch_path.parent.mkdir(parents=True, exist_ok=True)
            patch_path.write_text(VALID_PATCH, encoding="utf-8")
            self._status(
                attempt_assignment,
                attempt,
                status="completed",
                decision="PROPOSE_CANDIDATE",
            )
            return SimpleNamespace(returncode=0)

        with patch.object(
            candidate_pipeline.subprocess,
            "run",
            side_effect=fake_run,
        ), patch.object(
            candidate_pipeline,
            "_run_candidate_compile_check",
            side_effect=(
                (False, "demo.c:3: error: unknown symbol"),
                (True, "candidate_binary_build_passed"),
            ),
        ) as compile_check:
            result = candidate_pipeline._run_agent_with_retry(
                repo_root=self.repo,
                assignment=self.assignment,
                max_retries=1,
                build_candidate_binary=True,
            )

        self.assertTrue(result.should_evaluate)
        self.assertTrue(result.build_materialized)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(compile_check.call_count, 2)
        self.assertIn("c/c++ build failed", hypotheses[1].lower())
        self.assertIn("unknown symbol", hypotheses[1])
        retry_payload = json.loads(
            agent_attempt_path(context, 2, "assignment").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            retry_payload["source_context_requested_files"],
            [PATCH_TARGET],
        )
        self.assertEqual(self.assignment.read_bytes(), self.original)

    def test_later_nonbuild_outcome_cannot_reuse_stale_compile_manifest(self) -> None:
        context = self._enable_source_patch_mode()
        build_info = (
            implementation_root(context)
            / "candidate_modified"
            / "build_info.json"
        )
        cases = (
            (
                "provider",
                "CODING_INFRASTRUCTURE_FAILURE",
                "agent_provider_configuration_failed",
            ),
            (
                "validation",
                "REPAIR_VALIDATION",
                "agent_response_validation_failed",
            ),
            ("defer", "DEFERRED_BY_AGENT", "agent_deferred"),
        )
        real_subprocess_run = subprocess.run

        for terminal_kind, expected_decision, expected_build_status in cases:
            with self.subTest(terminal_kind=terminal_kind):
                def fake_run(command: object, **kwargs: object) -> object:
                    values = list(command)  # type: ignore[arg-type]
                    if "scripts.agents.self_evolved_abc.cycle_driver" not in values:
                        return real_subprocess_run(values, **kwargs)
                    attempt = int(self._command_value(values, "--attempt-index"))
                    attempt_assignment = Path(
                        self._command_value(values, "--assignment")
                    )
                    if attempt == 1:
                        patch_path = source_patch_diff_path(context)
                        patch_path.parent.mkdir(parents=True, exist_ok=True)
                        patch_path.write_text(VALID_PATCH, encoding="utf-8")
                        self._status(
                            attempt_assignment,
                            attempt,
                            status="completed",
                            decision="PROPOSE_CANDIDATE",
                        )
                        return SimpleNamespace(returncode=0)

                    # The failed build belongs to attempt 1 and must be gone
                    # before attempt 2 calls the model.
                    self.assertFalse(build_info.exists())
                    if terminal_kind == "provider":
                        self._status(
                            attempt_assignment,
                            attempt,
                            status="failed",
                            failure_kind="provider_configuration",
                            error_message="synthetic provider rejection",
                        )
                        return SimpleNamespace(returncode=10)
                    if terminal_kind == "validation":
                        context.artifact_paths().feedback.parent.mkdir(
                            parents=True,
                            exist_ok=True,
                        )
                        context.artifact_paths().feedback.write_text(
                            "## Validation Issues\n\nstale-build regression\n",
                            encoding="utf-8",
                        )
                        self._status(
                            attempt_assignment,
                            attempt,
                            status="failed",
                            decision="NEEDS_HUMAN_REVIEW",
                            failure_kind="response_validation",
                            retryable=True,
                        )
                        return SimpleNamespace(returncode=0)
                    self._status(
                        attempt_assignment,
                        attempt,
                        status="completed",
                        decision="DEFER",
                    )
                    return SimpleNamespace(returncode=0)

                def fail_compile(
                    _context: CycleContext,
                    **_: object,
                ) -> tuple[bool, str]:
                    build_info.parent.mkdir(parents=True, exist_ok=True)
                    _write_json(
                        build_info,
                        {"status": "candidate_binary_build_failed"},
                    )
                    (build_info.parent / "build.log").write_text(
                        "demo.c:3: error: synthetic compile failure\n",
                        encoding="utf-8",
                    )
                    return False, "demo.c:3: error: synthetic compile failure"

                with patch.object(
                    candidate_pipeline.subprocess,
                    "run",
                    side_effect=fake_run,
                ), patch.object(
                    candidate_pipeline,
                    "_run_candidate_compile_check",
                    side_effect=fail_compile,
                ):
                    result = candidate_pipeline._run_agent_with_retry(
                        repo_root=self.repo,
                        assignment=self.assignment,
                        max_retries=1,
                        build_candidate_binary=True,
                    )

                self.assertFalse(result.should_evaluate)
                self.assertFalse(build_info.exists())
                self.assertEqual(
                    candidate_pipeline._write_agent_failure_review(context, result),
                    1,
                )
                review = json.loads(
                    candidate_pipeline.review_decision_path(context).read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(review["decision"], expected_decision)
                self.assertEqual(review["build_status"], expected_build_status)

    def test_defer_is_settled_without_starting_patch_or_compare(self) -> None:
        result = candidate_pipeline.AgentRunResult(
            succeeded=True,
            decision="DEFER",
            failure_kind="",
            attempts=1,
            detail="Need a per-pass saturation counter before choosing a threshold.",
        )
        with patch.object(
            candidate_pipeline,
            "_run_agent_with_retry",
            return_value=result,
        ), patch.object(candidate_pipeline.subprocess, "run") as run:
            return_code = candidate_pipeline.main(
                (
                    "--repo-root",
                    str(self.repo),
                    "--assignment",
                    str(self.assignment),
                    "--skip-next-cycle",
                )
            )

        self.assertEqual(return_code, 1)
        run.assert_not_called()
        review = json.loads(
            (
                self.repo
                / "experiments/cycle_001/candidates/flow_candidate_001/"
                "impl_compare/comparison/review_decision.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(review["decision"], "DEFERRED_BY_AGENT")
        self.assertEqual(review["build_status"], "agent_deferred")
        self.assertIn("explicitly deferred", review["reason"])
        self.assertIn("saturation counter", review["reason"])
        self.assertNotIn("provider", review["reason"].lower())

    def test_provider_failure_is_not_reported_as_json_validation(self) -> None:
        result = candidate_pipeline.AgentRunResult(
            succeeded=False,
            decision="",
            failure_kind="provider_configuration",
            attempts=1,
            detail="model access was rejected",
        )
        with patch.object(
            candidate_pipeline,
            "_run_agent_with_retry",
            return_value=result,
        ), patch.object(candidate_pipeline.subprocess, "run") as run:
            return_code = candidate_pipeline.main(
                (
                    "--repo-root",
                    str(self.repo),
                    "--assignment",
                    str(self.assignment),
                    "--skip-next-cycle",
                )
            )

        self.assertEqual(return_code, 1)
        run.assert_not_called()
        review = json.loads(
            (
                self.repo
                / "experiments/cycle_001/candidates/flow_candidate_001/"
                "impl_compare/comparison/review_decision.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(review["decision"], "CODING_INFRASTRUCTURE_FAILURE")
        self.assertEqual(
            review["build_status"], "agent_provider_configuration_failed"
        )
        self.assertNotIn("JSON response fields", review["next_action"])


class ModelClientBoundaryTests(unittest.TestCase):
    @staticmethod
    def _client(completion: object) -> tuple[OpenAIModelClient, dict[str, object]]:
        captured: dict[str, object] = {}

        def create(**kwargs: object) -> object:
            captured.update(kwargs)
            return completion

        client = object.__new__(OpenAIModelClient)
        client.client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        client.default_model = "test-model"
        client.max_output_tokens = 4096
        client.temperature = 0.0
        client.response_format_mode = "json_object"
        client.strict_schema = False
        client.token_parameter = "max_tokens"
        return client, captured

    def test_json_object_request_contains_authoritative_schema(self) -> None:
        completion = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content='{"value": 1}', refusal=None),
                )
            ]
        )
        client, captured = self._client(completion)
        schema = {
            "type": "object",
            "required": ["value"],
            "properties": {"value": {"type": "integer"}},
        }
        reply = client.complete_json(
            ModelInvocation("system", "user", schema, model=None)
        )
        self.assertEqual(reply.parsed_json, {"value": 1})
        messages = captured["messages"]
        system = messages[0]["content"]  # type: ignore[index]
        self.assertIn("JSON Schema is authoritative", system)
        self.assertIn('"required":["value"]', system)
        self.assertEqual(captured["response_format"], {"type": "json_object"})

    def test_empty_and_length_responses_are_retryable(self) -> None:
        for finish_reason, content, expected_kind in (
            ("stop", "", "empty_content"),
            ("length", '{"partial":', "length"),
        ):
            completion = SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason=finish_reason,
                        message=SimpleNamespace(content=content, refusal=None),
                    )
                ]
            )
            client, _ = self._client(completion)
            with self.assertRaises(ModelResponseError) as caught:
                client.complete_json(ModelInvocation("s", "u", {}))
            self.assertTrue(caught.exception.retryable)
            self.assertEqual(caught.exception.failure_kind, expected_kind)

    def test_insufficient_system_resource_is_transient(self) -> None:
        completion = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="insufficient_system_resource",
                    message=SimpleNamespace(content=None, refusal=None),
                )
            ]
        )
        client, _ = self._client(completion)
        with self.assertRaises(ModelProviderTransientError):
            client.complete_json(ModelInvocation("s", "u", {}))

    def test_incompatible_strict_schema_fails_before_provider_call(self) -> None:
        completion = SimpleNamespace(choices=[])
        client, captured = self._client(completion)
        client.response_format_mode = "json_schema"
        client.strict_schema = True
        with self.assertRaises(ModelConfigurationError):
            client.complete_json(
                ModelInvocation("s", "u", flow_response_json_schema())
            )
        self.assertEqual(captured, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
