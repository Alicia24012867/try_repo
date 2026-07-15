#!/usr/bin/env python3
"""Regression tests for coding-agent provider and validation retry semantics."""

from __future__ import annotations

import json
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
from scripts.agents.self_evolved_abc.flow.validation import flow_response_json_schema
from scripts.agents.self_evolved_abc.model_client import (
    ModelConfigurationError,
    ModelInvocation,
    ModelProviderTransientError,
    ModelResponseError,
    OpenAIModelClient,
)
from scripts.agents.self_evolved_abc.workflow.artifacts import agent_attempt_path
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

        def fake_run(command: object, **_: object) -> SimpleNamespace:
            attempt = int(self._command_value(command, "--attempt-index"))
            attempt_assignment = Path(
                self._command_value(command, "--assignment")
            )
            payload = json.loads(attempt_assignment.read_text(encoding="utf-8"))
            hypotheses.append(str(payload["planner_hypothesis"]))
            context = CycleContext.from_assignment_file(self.repo, attempt_assignment)
            context.artifact_paths().feedback.parent.mkdir(parents=True, exist_ok=True)
            context.artifact_paths().feedback.write_text(
                f"## Validation Issues\n\nunique_issue_{attempt}\n",
                encoding="utf-8",
            )
            self._status(
                attempt_assignment,
                attempt,
                status="completed",
                decision="NEEDS_HUMAN_REVIEW",
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
