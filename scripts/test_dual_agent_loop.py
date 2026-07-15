#!/usr/bin/env python3
"""Focused tests for the Planning → (Flow || Logic) portfolio loop."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from scripts.agents.self_evolved_abc.cycle_context import CycleContext
from scripts.agents.self_evolved_abc.flow.paths import impl_compare_root
from scripts.agents.self_evolved_abc.flow.contracts import IMPL_CANDIDATE_LABEL
from scripts.agents.self_evolved_abc.flow.contracts import DEFAULT_EVAL_FLOW_COMMANDS
from scripts.agents.self_evolved_abc.planning.portfolio import (
    CANDIDATE_SCOPED_LAYOUT,
    create_next_portfolio_plan,
    create_portfolio_plan,
    load_portfolio_plan,
    planner_advice_path,
)
from scripts.agents.self_evolved_abc.roles.registry import (
    coding_agent_names,
    get_coding_agent_spec,
)
from scripts.agents.self_evolved_abc.model_client import ModelReply
from scripts.agents.self_evolved_abc.planning_agent import PlanningAgent
from scripts.agents.self_evolved_abc.prompt_rendering import (
    find_forbidden_secret_markers,
)
from scripts.agents.self_evolved_abc.repository_context import (
    RepositoryContextBundle,
    build_repository_context,
    load_repository_specs,
)
from scripts.agents.self_evolved_abc.workflow.artifacts import implementation_root
from scripts.agents.self_evolved_abc.workflow.dual_agent_loop import (
    _branch_execution_failed,
    _branch_log_path,
    _default_command_runner,
    _print_branch_outcomes,
    execute_portfolio_plan,
)
from scripts.agents.self_evolved_abc.workflow.candidate_pipeline import (
    main as candidate_pipeline_main,
)
from scripts.agents.self_evolved_abc.workflow.portfolio_review import (
    collect_branch_outcome,
    write_portfolio_review,
)


def _write_review(
    repo_root: Path,
    assignment_path: Path,
    *,
    reward: int = 20,
    decision: str = "ACCEPT_FOR_NEXT_CYCLE",
) -> None:
    context = CycleContext.from_assignment_file(repo_root, assignment_path)
    path = impl_compare_root(context) / "comparison" / "review_decision.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    accepted = decision == "ACCEPT_FOR_NEXT_CYCLE"
    if accepted:
        source_root = (
            impl_compare_root(context)
            / IMPL_CANDIDATE_LABEL
            / "workspace"
            / "third_party"
            / "FlowTune"
            / "src"
        )
        source_root.mkdir(parents=True, exist_ok=True)
        (source_root / "abc").write_text("test binary\n", encoding="utf-8")
    path.write_text(
        json.dumps(
            {
                "cycle_id": context.cycle_id,
                "candidate_id": context.candidate_id,
                "decision": decision,
                "promotion_allowed": accepted,
                "champion_update": accepted,
                "build_status": "candidate_binary_build_passed",
                "cec_pass_count": 2,
                "cec_total_count": 2,
                "correctness_backed_rows": 2,
                "average_and_improve_pct": 4.0 if accepted else 0.0,
                "total_and_delta_candidate_minus_baseline": -reward,
                "scalar_and_reward": reward,
                "improved_benchmark_count": 2 if accepted else 0,
                "regressed_benchmark_count": 0,
                "unchanged_benchmark_count": 0 if accepted else 2,
                "min_average_and_improve_pct": 3.0,
                "min_total_and_reduction": 10,
                "min_improved_benchmarks": 2,
                "reason": "test review",
                "next_action": "continue",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _assignment_from_command(repo_root: Path, command: object) -> Path:
    argv = list(command)  # type: ignore[arg-type]
    return repo_root / argv[argv.index("--assignment") + 1]


def _branch_manifest_path(repo_root: Path, assignment_path: Path) -> Path:
    payload = json.loads(assignment_path.read_text(encoding="utf-8"))
    return (
        repo_root
        / "experiments"
        / str(payload["cycle_id"])
        / "planning"
        / "branch_runs"
        / f"{payload['candidate_id']}.json"
    )


def _install_planner_templates(repo_root: Path) -> None:
    for relative in (
        Path("configs/agents/prompts/planner_prompt.md"),
        Path("configs/agents/shared/rulebase.md"),
    ):
        destination = repo_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative, destination)


def _planner_reply_payload() -> dict[str, object]:
    return {
        "cycle_objective": "Evaluate two isolated, correctness-gated candidates.",
        "dispatches": [
            {
                "branch_role": "flow",
                "task_type": "optimization",
                "hypothesis": "Test one reached Flow decision.",
                "coding_agent_task": "Edit one reached Flow mechanism.",
                "acceptance_criteria": ["Build, CEC, and QoR pass."],
                "rollback_criteria": ["Any hard gate fails."],
            },
            {
                "branch_role": "logic",
                "task_type": "optimization",
                "hypothesis": "Test one reached Logic decision.",
                "coding_agent_task": "Edit one reached Logic mechanism.",
                "acceptance_criteria": ["Build, CEC, and QoR pass."],
                "rollback_criteria": ["Any hard gate fails."],
            },
        ],
        "risk_controls": ["No implicit patch merge."],
        "rulebase_notes": [],
    }


def _ready_planner_prior() -> RepositoryContextBundle:
    names = tuple(f"prior-{index}" for index in range(10))
    return RepositoryContextBundle(
        text="# Pinned planner prior\n\nVerified test repository context.",
        configured_count=10,
        available_count=10,
        available_names=names,
        missing_names=(),
        revision_mismatches=(),
        incomplete_names=(),
        dirty_names=(),
    )


class _RecordingModelClient:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls = 0
        self.invocations = []

    def complete_json(self, invocation):
        self.calls += 1
        self.invocations.append(invocation)
        return ModelReply(raw_text=json.dumps(self.payload), parsed_json=self.payload)


class DualAgentLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _plan(self):
        return create_portfolio_plan(
            repo_root=self.repo,
            cycle_id="cycle_001",
            previous_cycle_id="cycle_000",
            benchmarks=("benchmarks/a.blif", "benchmarks/b.blif"),
        )

    def test_registry_and_dispatch_are_exactly_flow_plus_logic(self) -> None:
        self.assertEqual(
            coding_agent_names(),
            ("flow_agent", "logic_minimization_agent"),
        )
        with self.assertRaises(ValueError):
            get_coding_agent_spec("mapper_agent")

        plan = self._plan()
        self.assertEqual(
            [branch.branch_role for branch in plan.branches],
            ["flow", "logic"],
        )
        self.assertEqual(
            [branch.candidate_id for branch in plan.branches],
            ["flow_candidate_001", "logic_candidate_001"],
        )
        reloaded = load_portfolio_plan(self.repo, "cycle_001")
        self.assertEqual(reloaded.evaluation_contract_hash, plan.evaluation_contract_hash)

    def test_secret_scan_allows_environment_api_code_but_blocks_dotenv(self) -> None:
        self.assertEqual(find_forbidden_secret_markers("os.environ['HOME']"), ())
        self.assertIn(".env", find_forbidden_secret_markers("load .env/config"))

    def test_repository_context_schema_is_fail_closed(self) -> None:
        manifest = self.repo / "repositories.json"
        manifest.write_text(
            json.dumps({"schema_version": 3, "repositories": []}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "schema_version"):
            load_repository_specs(self.repo, manifest)

    def test_missing_profile_cannot_satisfy_repository_minimum(self) -> None:
        checkout = self.repo / ".local/context_repos/example"
        checkout.mkdir(parents=True)
        (checkout / "focus.c").write_text("int example(void);\n", encoding="utf-8")
        manifest = self.repo / "repositories.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "repositories": [
                        {
                            "name": "example",
                            "url": "https://example.invalid/example.git",
                            "revision": "a" * 40,
                            "local_path": ".local/context_repos/example",
                            "profile_path": "profiles/missing.md",
                            "license": "test",
                            "category": "test",
                            "description": "test",
                            "quality": "test",
                            "extensibility": "test",
                            "self_evolution_synergy": "test",
                            "abc_integration": "test",
                            "roles": ["flow_agent"],
                            "focus_paths": ["focus.c"],
                            "query_terms": ["example"],
                            "priority": 1,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        assignment = {
            "repository_context_manifest": "repositories.json",
            "repository_context_min_available": 1,
        }
        with patch(
            "scripts.agents.self_evolved_abc.repository_context._git_revision",
            return_value="a" * 40,
        ), patch(
            "scripts.agents.self_evolved_abc.repository_context._git_is_clean",
            return_value=True,
        ):
            bundle = build_repository_context(
                self.repo,
                assignment,
                role="flow_agent",
            )
        self.assertEqual(bundle.available_count, 0)
        self.assertEqual(bundle.profile_missing_names, ("example",))

    def test_planning_agent_json_interface_requires_two_role_dispatches(self) -> None:
        plan = self._plan()
        context = CycleContext.from_assignment_file(
            self.repo, plan.branches[0].assignment_path
        )
        agent = PlanningAgent(context=context, model_client=None)  # type: ignore[arg-type]
        schema = agent.response_schema()
        self.assertIn("dispatches", schema["required"])
        self.assertNotIn("selected_agent", schema["properties"])
        self.assertNotIn("benchmark_scope", schema["properties"])
        self.assertNotIn("evaluation_flow_commands", schema["properties"])
        dispatch_properties = schema["properties"]["dispatches"]["items"][
            "properties"
        ]
        for coordinator_field in (
            "agent_name",
            "candidate_id",
            "source_patch_mode",
            "source_patch_allowed_roots",
        ):
            self.assertNotIn(coordinator_field, dispatch_properties)
        reply = ModelReply(
            raw_text="{}",
            parsed_json=_planner_reply_payload(),
        )
        artifacts = agent.materialize_reply(reply, {})
        self.assertEqual(artifacts.decision, "PROPOSE_CANDIDATES")
        self.assertIn("flow_candidate_001", artifacts.candidate_markdown)
        self.assertIn("logic_candidate_001", artifacts.candidate_markdown)

    def test_model_planner_runs_once_and_cannot_change_locked_envelope(self) -> None:
        _install_planner_templates(self.repo)
        client = _RecordingModelClient(_planner_reply_payload())
        with patch(
            "scripts.agents.self_evolved_abc.planning_agent.build_repository_context",
            return_value=_ready_planner_prior(),
        ):
            plan = PlanningAgent.create_parallel_coding_dispatch(
                repo_root=self.repo,
                cycle_id="cycle_001",
                previous_cycle_id="cycle_000",
                benchmarks=("benchmarks/a.blif", "benchmarks/b.blif"),
                planner_mode="model",
                model_client=client,
            )
        self.assertEqual(client.calls, 1)
        self.assertIn("Pinned planner prior", client.invocations[0].user_prompt)
        self.assertEqual(plan.planner_advice_source, "model")
        advice = json.loads(
            planner_advice_path(self.repo, "cycle_001").read_text(encoding="utf-8")
        )
        self.assertEqual(advice["source"], "model")
        for branch in plan.branches:
            assignment = json.loads(branch.assignment_path.read_text(encoding="utf-8"))
            self.assertEqual(assignment["planner_advice_hash"], plan.planner_advice_hash)
            self.assertEqual(
                assignment["evaluation_contract_hash"],
                plan.evaluation_contract_hash,
            )
        self.assertEqual(
            json.loads(plan.branches[0].assignment_path.read_text(encoding="utf-8"))[
                "source_patch_allowed_roots"
            ],
            ["third_party/FlowTune/src/src/opt"],
        )

    def test_model_semantics_do_not_echo_standard_or_large_suite_scope(self) -> None:
        for suite, tracked_count, evaluated_count in (
            ("standard_30", 30, 30),
            ("large_70", 70, 30),
        ):
            with self.subTest(suite=suite):
                repo = self.repo / suite
                repo.mkdir()
                shutil.copytree(PROJECT_ROOT / "benchmarks", repo / "benchmarks")
                _install_planner_templates(repo)
                client = _RecordingModelClient(_planner_reply_payload())
                with patch(
                    "scripts.agents.self_evolved_abc.planning_agent."
                    "build_repository_context",
                    return_value=_ready_planner_prior(),
                ):
                    plan = PlanningAgent.create_parallel_coding_dispatch(
                        repo_root=repo,
                        cycle_id="cycle_001",
                        previous_cycle_id="cycle_000",
                        benchmark_suite=suite,
                        planner_mode="model",
                        model_client=client,
                    )
                assignment = json.loads(
                    plan.branches[0].assignment_path.read_text(encoding="utf-8")
                )
                self.assertEqual(len(assignment["benchmark_scope"]), tracked_count)
                self.assertEqual(
                    len(plan.evaluation_contract["benchmark_scope"]),
                    evaluated_count,
                )
                response_properties = client.invocations[0].response_schema[
                    "properties"
                ]
                self.assertNotIn("benchmark_scope", response_properties)
                self.assertNotIn("evaluation_flow_commands", response_properties)
                required_output = client.invocations[0].user_prompt.split(
                    "## Required Output", 1
                )[1]
                for coordinator_field in (
                    '"benchmark_scope"',
                    '"evaluation_flow_commands"',
                    '"agent_name"',
                    '"candidate_id"',
                    '"source_patch_allowed_roots"',
                ):
                    self.assertNotIn(coordinator_field, required_output)

    def test_deterministic_planner_never_calls_injected_model(self) -> None:
        class ExplodingClient:
            def complete_json(self, _invocation):
                raise AssertionError("deterministic planner called model")

        plan = PlanningAgent.create_parallel_coding_dispatch(
            repo_root=self.repo,
            cycle_id="cycle_001",
            previous_cycle_id="cycle_000",
            benchmarks=("benchmarks/a.blif", "benchmarks/b.blif"),
            planner_mode="deterministic",
            model_client=ExplodingClient(),
        )
        self.assertEqual(plan.planner_advice_source, "deterministic_fallback")

    def test_model_envelope_echo_is_ignored_and_coordinator_contract_wins(self) -> None:
        _install_planner_templates(self.repo)
        payload = _planner_reply_payload()
        payload["benchmark_scope"] = ["benchmarks/forged.blif"]
        payload["evaluation_flow_commands"] = ["forged_command"]
        payload["evidence_summary"] = {"compile": "forged_evidence"}
        payload["validation_evidence"] = {"compile": "forged_validation"}
        dispatches = payload["dispatches"]
        assert isinstance(dispatches, list)
        flow_dispatch = dispatches[0]
        assert isinstance(flow_dispatch, dict)
        flow_dispatch.update(
            {
                "agent_name": "forged_agent",
                "candidate_id": "forged_candidate",
                "source_patch_mode": "forged_mode",
                "source_patch_allowed_roots": ["forged/root"],
            }
        )
        client = _RecordingModelClient(payload)
        with patch(
            "scripts.agents.self_evolved_abc.planning_agent.build_repository_context",
            return_value=_ready_planner_prior(),
        ):
            plan = PlanningAgent.create_parallel_coding_dispatch(
                repo_root=self.repo,
                cycle_id="cycle_001",
                previous_cycle_id="cycle_000",
                benchmarks=("benchmarks/a.blif", "benchmarks/b.blif"),
                planner_mode="model",
                model_client=client,
            )
        expected_scope = ["benchmarks/a.blif", "benchmarks/b.blif"]
        self.assertEqual(plan.evaluation_contract["benchmark_scope"], expected_scope)
        self.assertEqual(
            plan.evaluation_contract["flow_commands"],
            list(DEFAULT_EVAL_FLOW_COMMANDS),
        )
        serialized = json.dumps(plan.as_dict(self.repo.resolve()), sort_keys=True)
        for branch in plan.branches:
            assignment = json.loads(branch.assignment_path.read_text(encoding="utf-8"))
            self.assertEqual(assignment["evaluation_benchmark_scope"], expected_scope)
            self.assertEqual(
                assignment["evaluation_flow_commands"],
                list(DEFAULT_EVAL_FLOW_COMMANDS),
            )
            serialized += json.dumps(assignment, sort_keys=True)
        advice = planner_advice_path(self.repo, "cycle_001").read_text(
            encoding="utf-8"
        )
        serialized += advice
        for forged in (
            "benchmarks/forged.blif",
            "forged_command",
            "forged_agent",
            "forged_candidate",
            "forged/root",
            "forged_evidence",
            "forged_validation",
        ):
            self.assertNotIn(forged, serialized)

    def test_assignments_share_contract_but_keep_role_ownership(self) -> None:
        plan = self._plan()
        payloads = [
            json.loads(branch.assignment_path.read_text(encoding="utf-8"))
            for branch in plan.branches
        ]
        self.assertEqual(payloads[0]["baseline_ref"], payloads[1]["baseline_ref"])
        self.assertEqual(
            payloads[0]["evaluation_contract_hash"],
            payloads[1]["evaluation_contract_hash"],
        )
        self.assertEqual(
            payloads[0]["evaluation_flow_commands"],
            payloads[1]["evaluation_flow_commands"],
        )
        self.assertEqual(payloads[0]["artifact_layout"], CANDIDATE_SCOPED_LAYOUT)
        self.assertEqual(payloads[0]["repository_context_max_repositories"], 6)
        self.assertEqual(payloads[0]["repository_context_min_available"], 6)
        self.assertEqual(payloads[0]["repository_context_max_chars"], 60_000)
        self.assertTrue(payloads[0]["repository_context_enforce_minimum"])
        self.assertEqual(payloads[1]["repository_context_max_repositories"], 9)
        self.assertEqual(payloads[1]["repository_context_min_available"], 9)
        self.assertEqual(payloads[1]["repository_context_max_chars"], 72_000)
        self.assertTrue(payloads[1]["repository_context_enforce_minimum"])
        self.assertEqual(
            payloads[0]["source_patch_allowed_roots"],
            ["third_party/FlowTune/src/src/opt"],
        )
        self.assertEqual(
            payloads[1]["source_patch_allowed_roots"],
            ["third_party/FlowTune/src/src/base/abci"],
        )
        for payload in payloads:
            self.assertNotIn(
                "experiments/cycle_001/impl_compare",
                payload["allowed_to_edit"],
            )

    def test_candidate_artifacts_are_isolated_and_ids_cannot_escape(self) -> None:
        plan = self._plan()
        contexts = [
            CycleContext.from_assignment_file(self.repo, branch.assignment_path)
            for branch in plan.branches
        ]
        roots = [implementation_root(context) for context in contexts]
        self.assertNotEqual(roots[0], roots[1])
        self.assertIn("flow_candidate_001", roots[0].as_posix())
        self.assertIn("logic_candidate_001", roots[1].as_posix())

        payload = dict(contexts[0].assignment)
        payload["candidate_id"] = "../escape"
        with self.assertRaises(ValueError):
            implementation_root(CycleContext(repo_root=self.repo, assignment=payload))

    def test_frozen_evaluation_contract_rejects_assignment_or_runtime_drift(self) -> None:
        plan = self._plan()
        with self.assertRaisesRegex(ValueError, "runtime timeout"):
            execute_portfolio_plan(
                repo_root=self.repo,
                plan=plan,
                timeout_seconds=301.0,
                command_runner=lambda _command, _cwd: 0,
            )

        path = plan.branches[1].assignment_path
        payload = json.loads(path.read_text(encoding="utf-8"))
        original = dict(payload)
        payload["evaluation_benchmark_scope"] = ["benchmarks/forged.blif"]
        path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "evaluation_benchmark_scope"):
            load_portfolio_plan(self.repo, "cycle_001")

        payload = original
        payload["evaluation_flow_commands"] = ["strash", "print_stats"]
        path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "evaluation_flow_commands"):
            load_portfolio_plan(self.repo, "cycle_001")

    def test_both_branches_really_overlap_in_time(self) -> None:
        plan = self._plan()
        barrier = threading.Barrier(2)
        lock = threading.Lock()
        active = 0
        max_active = 0

        def runner(command, cwd):
            nonlocal active, max_active
            assignment = cwd / command[command.index("--assignment") + 1]
            with lock:
                active += 1
                max_active = max(max_active, active)
            barrier.wait(timeout=3)
            _write_review(cwd, assignment)
            time.sleep(0.02)
            with lock:
                active -= 1
            return 0

        outcomes = execute_portfolio_plan(
            repo_root=self.repo,
            plan=plan,
            max_workers=2,
            command_runner=runner,
        )
        self.assertEqual(max_active, 2)
        self.assertEqual([item.status for item in outcomes], ["reviewed", "reviewed"])

    def test_runner_failure_does_not_cancel_sibling(self) -> None:
        plan = self._plan()
        seen: list[str] = []

        def runner(command, cwd):
            assignment = cwd / command[command.index("--assignment") + 1]
            payload = json.loads(assignment.read_text(encoding="utf-8"))
            seen.append(payload["branch_role"])
            if payload["branch_role"] == "flow":
                raise RuntimeError("synthetic flow failure")
            _write_review(cwd, assignment, decision="REPAIR_QOR")
            return 1

        outcomes = execute_portfolio_plan(
            repo_root=self.repo,
            plan=plan,
            max_workers=2,
            command_runner=runner,
        )
        self.assertCountEqual(seen, ["flow", "logic"])
        by_role = {item.branch_role: item for item in outcomes}
        self.assertEqual(by_role["flow"].status, "failed")
        self.assertIn("synthetic flow failure", by_role["flow"].error)
        self.assertEqual(by_role["logic"].status, "reviewed")
        self.assertEqual(by_role["logic"].decision, "REPAIR_QOR")
        self.assertFalse(_branch_execution_failed(by_role["logic"]))
        review = write_portfolio_review(
            repo_root=self.repo,
            plan=plan,
            outcomes=outcomes,
        )
        self.assertEqual(review["round_status"], "incomplete")
        output = StringIO()
        with redirect_stdout(output):
            _print_branch_outcomes(
                repo_root=self.repo,
                cycle_id=plan.cycle_id,
                outcomes=outcomes,
            )
        rendered = output.getvalue()
        self.assertIn("role=flow", rendered)
        self.assertIn("status=failed", rendered)
        self.assertIn("review_valid=false", rendered)
        self.assertIn("synthetic flow failure", rendered)
        self.assertIn("role=logic", rendered)
        self.assertIn("review_valid=true", rendered)
        self.assertIn("decision=REPAIR_QOR", rendered)
        self.assertIn("reason: test review", rendered)
        self.assertIn("next_action: continue", rendered)
        self.assertIn("review_decision.json", rendered)

    def test_negative_reviews_are_settled_and_reach_fan_in_quorum(self) -> None:
        plan = self._plan()

        def runner(command, cwd):
            assignment = _assignment_from_command(cwd, command)
            _write_review(cwd, assignment, decision="REPAIR_QOR")
            # The review CLI uses one to signal a non-promoting decision.
            return 1

        outcomes = execute_portfolio_plan(
            repo_root=self.repo,
            plan=plan,
            command_runner=runner,
        )
        self.assertEqual([item.status for item in outcomes], ["reviewed", "reviewed"])
        self.assertEqual([item.return_code for item in outcomes], [1, 1])
        self.assertFalse(any(_branch_execution_failed(item) for item in outcomes))
        review = write_portfolio_review(
            repo_root=self.repo,
            plan=plan,
            outcomes=outcomes,
        )
        self.assertTrue(review["quorum_reached"])
        self.assertEqual(review["reviewed_count"], 2)
        self.assertEqual(review["failed_count"], 0)
        self.assertEqual(review["round_status"], "no_promotion")
        next_plan = create_next_portfolio_plan(
            repo_root=self.repo,
            current_plan=plan,
            portfolio_review=review,
            next_cycle_id="cycle_002",
        )
        self.assertEqual(next_plan.cycle_id, "cycle_002")
        self.assertEqual(next_plan.baseline_ref, plan.baseline_ref)
        self.assertEqual(
            [branch.branch_role for branch in next_plan.branches],
            ["flow", "logic"],
        )

    def test_invalid_coding_reply_materializes_a_settled_negative_review(self) -> None:
        plan = self._plan()
        logic_branch = plan.branches[1]
        with patch(
            "scripts.agents.self_evolved_abc.workflow.candidate_pipeline."
            "_run_agent_with_retry",
            return_value=False,
        ):
            return_code = candidate_pipeline_main(
                (
                    "--repo-root",
                    str(self.repo),
                    "--assignment",
                    str(logic_branch.assignment_path),
                    "--skip-next-cycle",
                )
            )
        self.assertEqual(return_code, 1)
        outcome = collect_branch_outcome(
            repo_root=self.repo.resolve(),
            branch=logic_branch,
            return_code=return_code,
            elapsed_seconds=0.0,
            runner_error="pipeline return code 1",
        )
        self.assertEqual(outcome.status, "reviewed")
        self.assertEqual(outcome.decision, "REPAIR_VALIDATION")
        self.assertEqual(outcome.build_status, "missing")
        self.assertIn("response validation", outcome.review_reason)
        self.assertFalse(outcome.eligible_for_promotion)
        feedback = (
            self.repo
            / "experiments"
            / "cycle_001"
            / "agents"
            / "feedback"
            / "logic_candidate_001.md"
        ).read_text(encoding="utf-8")
        self.assertIn("Logic Minimization Agent Feedback", feedback)

    def test_default_runner_prefixes_and_persists_each_branch_output(self) -> None:
        plan = self._plan()
        branch = plan.branches[0]
        relative_assignment = branch.assignment_path.relative_to(self.repo.resolve())
        command = (
            sys.executable,
            "-B",
            "-c",
            "import sys; print('branch stdout'); print('branch stderr', file=sys.stderr)",
            "--assignment",
            str(relative_assignment),
        )
        output = StringIO()
        with redirect_stdout(output):
            return_code = _default_command_runner(command, self.repo.resolve())
        self.assertEqual(return_code, 0)
        rendered = output.getvalue()
        self.assertIn("[flow_candidate_001] branch stdout", rendered)
        self.assertIn("[flow_candidate_001] branch stderr", rendered)
        log_path = (
            self.repo
            / "experiments"
            / "cycle_001"
            / "planning"
            / "branch_logs"
            / "flow_candidate_001.log"
        )
        self.assertEqual(
            log_path.read_text(encoding="utf-8"),
            "branch stdout\nbranch stderr\n",
        )

    def test_parallel_default_runner_keeps_branch_logs_isolated(self) -> None:
        plan = self._plan()

        def run(branch):
            relative = branch.assignment_path.relative_to(self.repo.resolve())
            command = (
                sys.executable,
                "-B",
                "-c",
                (
                    "import sys,time; label=sys.argv[1]; "
                    "print(label+'-first', flush=True); time.sleep(0.02); "
                    "print(label+'-second', flush=True)"
                ),
                branch.branch_role,
                "--assignment",
                str(relative),
            )
            return _default_command_runner(command, self.repo.resolve())

        output = StringIO()
        with redirect_stdout(output):
            with ThreadPoolExecutor(max_workers=2) as executor:
                return_codes = list(executor.map(run, plan.branches))
        self.assertEqual(return_codes, [0, 0])
        for branch in plan.branches:
            log = _branch_log_path(
                self.repo,
                plan.cycle_id,
                branch.candidate_id,
            ).read_text(encoding="utf-8")
            self.assertIn(f"{branch.branch_role}-first", log)
            self.assertIn(f"{branch.branch_role}-second", log)
            other = "logic" if branch.branch_role == "flow" else "flow"
            self.assertNotIn(f"{other}-first", log)

        rendered = output.getvalue()
        for branch in plan.branches:
            self.assertIn(
                f"[{branch.candidate_id}] {branch.branch_role}-first",
                rendered,
            )

    def test_branch_log_path_rejects_unsafe_identity(self) -> None:
        with self.assertRaisesRegex(ValueError, "cycle_id"):
            _branch_log_path(self.repo, "../../escape", "candidate")
        with self.assertRaisesRegex(ValueError, "candidate_id"):
            _branch_log_path(self.repo, "cycle_001", "../escape")

    def test_nonzero_runner_cannot_promote_an_accept_review(self) -> None:
        plan = self._plan()

        def runner(command, cwd):
            assignment = _assignment_from_command(cwd, command)
            payload = json.loads(assignment.read_text(encoding="utf-8"))
            _write_review(cwd, assignment, reward=40)
            return 7 if payload["branch_role"] == "flow" else 0

        outcomes = execute_portfolio_plan(
            repo_root=self.repo,
            plan=plan,
            command_runner=runner,
        )
        by_role = {item.branch_role: item for item in outcomes}
        self.assertEqual(by_role["flow"].return_code, 7)
        self.assertFalse(by_role["flow"].eligible_for_promotion)
        self.assertTrue(_branch_execution_failed(by_role["flow"]))

        review = write_portfolio_review(
            repo_root=self.repo,
            plan=plan,
            outcomes=outcomes,
        )
        self.assertNotEqual(review["selected_candidate_id"], "flow_candidate_001")
        self.assertNotEqual(review["selected_agent_name"], "flow_agent")

    def test_single_valid_review_does_not_satisfy_promotion_quorum(self) -> None:
        plan = self._plan()

        def runner(command, cwd):
            assignment = _assignment_from_command(cwd, command)
            payload = json.loads(assignment.read_text(encoding="utf-8"))
            if payload["branch_role"] == "flow":
                _write_review(cwd, assignment, reward=40)
                return 0
            return 1

        outcomes = execute_portfolio_plan(
            repo_root=self.repo,
            plan=plan,
            command_runner=runner,
        )
        by_role = {item.branch_role: item for item in outcomes}
        self.assertTrue(by_role["flow"].eligible_for_promotion)
        self.assertEqual(by_role["logic"].status, "failed")

        review = write_portfolio_review(
            repo_root=self.repo,
            plan=plan,
            outcomes=outcomes,
        )
        self.assertEqual(review["reviewed_count"], 1)
        self.assertEqual(review["selected_candidate_id"], "")
        self.assertEqual(review["selected_agent_name"], "")

    def test_partial_or_unknown_review_cannot_satisfy_quorum(self) -> None:
        plan = self._plan()
        branch = plan.branches[0]
        context = CycleContext.from_assignment_file(
            self.repo, branch.assignment_path
        )
        path = impl_compare_root(context) / "comparison" / "review_decision.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "cycle_id": context.cycle_id,
                    "candidate_id": context.candidate_id,
                }
            ),
            encoding="utf-8",
        )
        partial = collect_branch_outcome(
            repo_root=self.repo.resolve(),
            branch=branch,
            return_code=0,
            elapsed_seconds=0.0,
        )
        self.assertEqual(partial.status, "failed")
        self.assertIn("missing required fields", partial.error)

        _write_review(self.repo, branch.assignment_path, decision="REPAIR_QOR")
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["decision"] = "UNKNOWN_DECISION"
        path.write_text(json.dumps(payload), encoding="utf-8")
        unknown = collect_branch_outcome(
            repo_root=self.repo.resolve(),
            branch=branch,
            return_code=1,
            elapsed_seconds=0.0,
            runner_error="pipeline return code 1",
        )
        self.assertEqual(unknown.status, "failed")
        self.assertIn("review decision is invalid", unknown.error)

    def test_fan_in_is_deterministic_and_next_round_shares_winner(self) -> None:
        plan = self._plan()

        def runner(command, cwd):
            assignment = _assignment_from_command(cwd, command)
            payload = json.loads(assignment.read_text(encoding="utf-8"))
            reward = 25 if payload["branch_role"] == "flow" else 40
            _write_review(cwd, assignment, reward=reward)
            return 0

        outcomes = execute_portfolio_plan(
            repo_root=self.repo,
            plan=plan,
            command_runner=runner,
        )
        review = write_portfolio_review(
            repo_root=self.repo,
            plan=plan,
            outcomes=tuple(reversed(outcomes)),
        )
        self.assertEqual(review["selected_candidate_id"], "logic_candidate_001")
        self.assertEqual(
            [item["branch_role"] for item in review["branches"]],
            ["flow", "logic"],
        )

        next_plan = create_next_portfolio_plan(
            repo_root=self.repo,
            current_plan=plan,
            portfolio_review=review,
            next_cycle_id="cycle_002",
        )
        self.assertEqual(next_plan.baseline_ref["candidate_id"], "logic_candidate_001")
        next_payloads = [
            json.loads(branch.assignment_path.read_text(encoding="utf-8"))
            for branch in next_plan.branches
        ]
        self.assertEqual(next_payloads[0]["baseline_ref"], next_payloads[1]["baseline_ref"])
        self.assertEqual(
            next_payloads[0]["champion_candidate_id"],
            "logic_candidate_001",
        )
        self.assertEqual(
            next_payloads[1]["champion_candidate_id"],
            "logic_candidate_001",
        )
        self.assertTrue(
            any(
                value.endswith("planning/portfolio_review.json")
                for value in next_payloads[0]["recent_evidence"]
            )
        )

        forged = dict(review)
        forged["selected_candidate_id"] = "flow_candidate_001"
        with self.assertRaisesRegex(ValueError, "persisted fan-in"):
            create_next_portfolio_plan(
                repo_root=self.repo,
                current_plan=plan,
                portfolio_review=forged,
                next_cycle_id="cycle_002",
            )

    def test_resume_runs_only_the_missing_branch(self) -> None:
        plan = self._plan()
        called: list[str] = []

        def runner(command, cwd):
            assignment = _assignment_from_command(cwd, command)
            payload = json.loads(assignment.read_text(encoding="utf-8"))
            called.append(payload["branch_role"])
            _write_review(cwd, assignment)
            return 0

        execute_portfolio_plan(
            repo_root=self.repo,
            plan=plan,
            command_runner=runner,
        )
        self.assertCountEqual(called, ["flow", "logic"])

        called.clear()
        logic_context = CycleContext.from_assignment_file(
            self.repo, plan.branches[1].assignment_path
        )
        (impl_compare_root(logic_context) / "comparison" / "review_decision.json").unlink()

        outcomes = execute_portfolio_plan(
            repo_root=self.repo,
            plan=plan,
            command_runner=runner,
        )
        self.assertEqual(called, ["logic"])
        self.assertEqual([item.status for item in outcomes], ["reviewed", "reviewed"])

    def test_resume_requires_matching_manifest_and_review_lineage(self) -> None:
        plan = self._plan()
        called: list[str] = []

        # A naked review is not a completed branch. Both lanes must execute and
        # let the coordinator create assignment/review-bound run manifests.
        _write_review(self.repo, plan.branches[0].assignment_path)

        def runner(command, cwd):
            assignment = _assignment_from_command(cwd, command)
            payload = json.loads(assignment.read_text(encoding="utf-8"))
            called.append(payload["branch_role"])
            _write_review(cwd, assignment)
            return 0

        execute_portfolio_plan(
            repo_root=self.repo,
            plan=plan,
            command_runner=runner,
        )
        self.assertCountEqual(called, ["flow", "logic"])
        for branch in plan.branches:
            manifest = json.loads(
                _branch_manifest_path(self.repo, branch.assignment_path).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(manifest["candidate_id"], branch.candidate_id)
            self.assertEqual(
                manifest["evaluation_contract_hash"],
                plan.evaluation_contract_hash,
            )
            self.assertIn("assignment_sha256", manifest)
            self.assertIn("review_sha256", manifest)

        # Changing a review invalidates review_sha256; only that lane reruns.
        called.clear()
        flow_context = CycleContext.from_assignment_file(
            self.repo, plan.branches[0].assignment_path
        )
        flow_review_path = (
            impl_compare_root(flow_context) / "comparison" / "review_decision.json"
        )
        flow_review = json.loads(flow_review_path.read_text(encoding="utf-8"))
        flow_review["candidate_id"] = "logic_candidate_001"
        flow_review_path.write_text(json.dumps(flow_review), encoding="utf-8")
        execute_portfolio_plan(
            repo_root=self.repo,
            plan=plan,
            command_runner=runner,
        )
        self.assertEqual(called, ["flow"])

        # A coordinator manifest with a different contract lineage is likewise
        # not resumable, even when the underlying review still parses.
        called.clear()
        logic_manifest_path = _branch_manifest_path(
            self.repo, plan.branches[1].assignment_path
        )
        logic_manifest = json.loads(logic_manifest_path.read_text(encoding="utf-8"))
        logic_manifest["evaluation_contract_hash"] = "0" * 64
        logic_manifest_path.write_text(json.dumps(logic_manifest), encoding="utf-8")
        execute_portfolio_plan(
            repo_root=self.repo,
            plan=plan,
            command_runner=runner,
        )
        self.assertEqual(called, ["logic"])

    def test_illegal_cycle_paths_and_assignment_identity_collisions_are_rejected(
        self,
    ) -> None:
        for cycle_id in ("../escape", "cycle_1", "/tmp/cycle_001", "cycle_001/x"):
            with self.subTest(cycle_id=cycle_id):
                with self.assertRaises(ValueError):
                    create_portfolio_plan(
                        repo_root=self.repo,
                        cycle_id=cycle_id,
                        benchmarks=("benchmarks/a.blif",),
                    )

        plan = self._plan()
        plan_path = (
            self.repo
            / "experiments"
            / "cycle_001"
            / "planning"
            / "portfolio_plan.json"
        )
        plan_payload = json.loads(plan_path.read_text(encoding="utf-8"))

        # The plan cannot redirect an assignment outside its canonical branch
        # path, including aliasing both branches to one assignment file.
        original_logic_path = plan_payload["branches"][1]["assignment_path"]
        plan_payload["branches"][1]["assignment_path"] = plan_payload["branches"][0][
            "assignment_path"
        ]
        plan_path.write_text(json.dumps(plan_payload), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_portfolio_plan(self.repo, "cycle_001")

        plan_payload["branches"][1]["assignment_path"] = "../../outside.json"
        plan_path.write_text(json.dumps(plan_payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "escapes repository"):
            load_portfolio_plan(self.repo, "cycle_001")

        plan_payload["branches"][1]["assignment_path"] = original_logic_path
        plan_path.write_text(json.dumps(plan_payload), encoding="utf-8")
        logic_assignment = plan.branches[1].assignment_path
        assignment_payload = json.loads(logic_assignment.read_text(encoding="utf-8"))
        assignment_payload["candidate_id"] = "flow_candidate_001"
        logic_assignment.write_text(json.dumps(assignment_payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "candidate_id mismatch"):
            load_portfolio_plan(self.repo, "cycle_001")


if __name__ == "__main__":
    unittest.main(verbosity=2)
