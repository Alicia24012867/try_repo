#!/usr/bin/env python3
"""Focused tests for the Planning → (Flow || Logic) portfolio loop."""

from __future__ import annotations

import json
import shutil
import subprocess
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
from scripts.agents.self_evolved_abc.flow.batch_search import (
    build_batch_lineage,
    build_variants,
    describe_variant_space,
    generate_batch,
    hash_batch_lineage,
    summarize_batch,
    variant_command,
)
from scripts.agents.self_evolved_abc.flow.contracts import IMPL_CANDIDATE_LABEL
from scripts.agents.self_evolved_abc.flow.contracts import DEFAULT_EVAL_FLOW_COMMANDS
from scripts.agents.self_evolved_abc.flow.planner_batch import (
    integrate_batch_winner,
    run_and_integrate_planner_batch,
)
from scripts.agents.self_evolved_abc.planning.portfolio import (
    CANDIDATE_SCOPED_LAYOUT,
    create_next_portfolio_plan,
    create_portfolio_plan,
    load_portfolio_plan,
    planner_advice_path,
    portfolio_plan_path,
    post_batch_replan_journal_path,
    refresh_portfolio_planner_advice,
)
from scripts.agents.self_evolved_abc.planning import portfolio as portfolio_module
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
from scripts.agents.self_evolved_abc.workflow.artifacts import (
    implementation_root,
    review_decision_path,
)
from scripts.agents.self_evolved_abc.workflow.dual_agent_loop import (
    _branch_execution_failed,
    _branch_log_path,
    _default_command_runner,
    _honor_flow_planner_control,
    _load_completed_portfolio_review,
    _load_or_create_next_plan,
    _print_branch_outcomes,
    _run_campaign,
    execute_portfolio_plan,
    parse_args,
)
from scripts.agents.self_evolved_abc.workflow.candidate_pipeline import (
    AgentRunResult,
    main as candidate_pipeline_main,
)
from scripts.agents.self_evolved_abc.workflow.portfolio_review import (
    BranchOutcome,
    _select_winner,
    collect_branch_outcome,
    write_portfolio_review,
)


def _write_review(
    repo_root: Path,
    assignment_path: Path,
    *,
    reward: int = 20,
    decision: str = "ACCEPT_FOR_NEXT_CYCLE",
    build_status: str = "candidate_binary_build_passed",
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
                "build_status": build_status,
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


def _install_flow_batch_sources(repo_root: Path) -> None:
    for relative in (
        Path("third_party/FlowTune/src/src/base/abci/abcFxu.c"),
        Path("third_party/FlowTune/src/src/opt/fxu/fxuSelect.c"),
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

    def test_post_batch_model_planning_reads_measurements_for_both_branches(
        self,
    ) -> None:
        _install_planner_templates(self.repo)
        plan = self._plan()
        flow_path = plan.branches[0].assignment_path
        flow = json.loads(flow_path.read_text(encoding="utf-8"))
        evidence_path = (
            self.repo
            / "experiments/batches/cycle_001_planner_flow_wide/summary.csv"
        )
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            "variant_id,total_and_delta_candidate_minus_baseline\n"
            "rewrite_last_equal_gain,-17\n"
            "POST_BATCH_MEASUREMENT_SENTINEL\n",
            encoding="utf-8",
        )
        relative = evidence_path.relative_to(self.repo).as_posix()
        for key in ("allowed_to_read", "recent_evidence"):
            values = [str(value) for value in flow.get(key, ())]
            values.append(relative)
            flow[key] = values
        flow["batch_search_evidence"] = {
            "winner_cycle_id": "probe_001",
            "requires_replanning": True,
            "planning_consumed": False,
        }
        flow_path.write_text(
            json.dumps(flow, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        client = _RecordingModelClient(_planner_reply_payload())

        with patch(
            "scripts.agents.self_evolved_abc.planning_agent."
            "build_repository_context",
            return_value=_ready_planner_prior(),
        ):
            refreshed = PlanningAgent.refresh_parallel_coding_dispatch(
                repo_root=self.repo,
                plan=plan,
                planner_mode="model",
                model_client=client,
            )

        self.assertEqual(client.calls, 1)
        self.assertIn(
            "POST_BATCH_MEASUREMENT_SENTINEL",
            client.invocations[0].user_prompt,
        )
        self.assertEqual(refreshed.baseline_ref, plan.baseline_ref)
        self.assertEqual(
            refreshed.evaluation_contract_hash,
            plan.evaluation_contract_hash,
        )
        payloads = [
            json.loads(branch.assignment_path.read_text(encoding="utf-8"))
            for branch in refreshed.branches
        ]
        self.assertTrue(
            payloads[0]["batch_search_evidence"]["planning_consumed"]
        )
        self.assertEqual(
            [payload["planner_advice_hash"] for payload in payloads],
            [refreshed.planner_advice_hash] * 2,
        )

    def test_post_batch_replan_recovers_each_interrupted_replace(self) -> None:
        for failure_index in range(5):
            with self.subTest(failure_index=failure_index):
                repo = self.repo / f"transaction_{failure_index}"
                plan = create_portfolio_plan(
                    repo_root=repo,
                    cycle_id="cycle_001",
                    previous_cycle_id="cycle_000",
                    benchmarks=("benchmarks/a.blif", "benchmarks/b.blif"),
                )
                flow_path = plan.branches[0].assignment_path
                flow = json.loads(flow_path.read_text(encoding="utf-8"))
                flow["planner_hypothesis"] = (
                    f"Measured transaction generation {failure_index}."
                )
                if failure_index == 0:
                    flow["transaction_padding"] = "X" * 70_000
                flow["batch_search_evidence"] = {
                    "winner_cycle_id": "probe_001",
                    "requires_replanning": True,
                    "planning_consumed": False,
                }
                flow_path.write_text(
                    json.dumps(flow, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )

                original_replace = (
                    portfolio_module._replace_post_batch_replan_artifact
                )
                calls = 0
                provider_calls = 0

                def planner_provider(_locked):
                    nonlocal provider_calls
                    provider_calls += 1
                    return _planner_reply_payload()

                def interrupted_replace(staged_path, target_path):
                    nonlocal calls
                    if failure_index < 4 and calls == failure_index:
                        raise OSError("injected post-batch replace interruption")
                    original_replace(staged_path, target_path)
                    calls += 1
                    if failure_index == 4 and calls == 4:
                        raise OSError("injected post-batch replace interruption")

                with patch.object(
                    portfolio_module,
                    "_replace_post_batch_replan_artifact",
                    side_effect=interrupted_replace,
                ):
                    with self.assertRaisesRegex(OSError, "injected"):
                        refresh_portfolio_planner_advice(
                            repo_root=repo,
                            plan=plan,
                            planner_advice_provider=planner_provider,
                        )

                journal = post_batch_replan_journal_path(repo, "cycle_001")
                self.assertTrue(journal.is_file())
                self.assertLess(journal.stat().st_size, 64 * 1024)
                recovered = refresh_portfolio_planner_advice(
                    repo_root=repo,
                    plan=plan,
                    planner_advice_provider=planner_provider,
                )
                self.assertEqual(provider_calls, 1)
                self.assertFalse(journal.exists())
                self.assertNotEqual(
                    recovered.planner_advice_hash,
                    plan.planner_advice_hash,
                )
                recovered_flow = json.loads(flow_path.read_text(encoding="utf-8"))
                self.assertTrue(
                    recovered_flow["batch_search_evidence"]["planning_consumed"]
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
        self.assertEqual(payloads[0]["campaign_state"], payloads[1]["campaign_state"])
        self.assertEqual(
            payloads[1]["target_command"],
            payloads[1]["_planning_meta"]["target_command"],
        )
        self.assertEqual(
            payloads[1]["campaign_state"]["logic_target_command"],
            payloads[1]["target_command"],
        )
        self.assertIn(
            payloads[1]["target_command"],
            payloads[1]["repository_context_query_terms"],
        )
        for payload in payloads:
            self.assertNotIn(
                "experiments/cycle_001/impl_compare",
                payload["allowed_to_edit"],
            )

    def test_structural_logic_targets_rotate_and_avoid_flow_family(self) -> None:
        targets = {
            portfolio_module._select_logic_recovery_target(
                phase="structural",
                cycle_number=cycle_number,
                stagnation=cycle_number - 1,
                flow_target="",
            )
            for cycle_number in range(6, 10)
        }
        self.assertEqual(
            targets,
            {"rewrite", "resub", "refactor", "orchestrate"},
        )
        for flow_target in ("rewrite", "resub", "refactor"):
            logic_target = portfolio_module._select_logic_recovery_target(
                phase="structural",
                cycle_number=6,
                stagnation=5,
                flow_target=flow_target,
            )
            self.assertNotEqual(logic_target, flow_target)

    def test_portfolio_no_winner_streak_survives_one_branch_repair(self) -> None:
        for number in range(1, 5):
            review_path = (
                self.repo
                / f"experiments/cycle_{number:03d}/planning/portfolio_review.json"
            )
            review_path.parent.mkdir(parents=True, exist_ok=True)
            review_path.write_text(
                json.dumps({
                    "cycle_id": f"cycle_{number:03d}",
                    "round_status": "no_promotion",
                    "quorum_reached": True,
                    "selected_candidate_id": "",
                    "branches": [
                        {
                            "branch_role": "flow",
                            "decision": "REPAIR_COMPILE",
                            "expected_benchmark_count": 2,
                            "cec_pass_count": 0,
                            "cec_total_count": 0,
                            "correctness_backed_rows": 0,
                        },
                        {
                            "branch_role": "logic",
                            "decision": "REPAIR_QOR",
                            "expected_benchmark_count": 2,
                            "cec_pass_count": 2,
                            "cec_total_count": 2,
                            "correctness_backed_rows": 2,
                        },
                    ],
                }, indent=2) + "\n",
                encoding="utf-8",
            )
        self.assertEqual(
            portfolio_module._consecutive_portfolio_no_winner(
                self.repo, "cycle_004"
            ),
            4,
        )
        plan = self._plan()
        assignments = {
            branch.branch_role: json.loads(
                branch.assignment_path.read_text(encoding="utf-8")
            )
            for branch in plan.branches
        }
        portfolio_module._apply_campaign_recovery_state(
            assignments,
            consecutive_no_winner=4,
        )
        self.assertEqual(
            assignments["flow"]["campaign_state"]["evolution_phase"],
            "structural",
        )
        self.assertTrue(assignments["flow"]["planner_should_skip_llm"])
        self.assertEqual(assignments["flow"]["target_command"], "")
        self.assertEqual(
            assignments["logic"]["campaign_state"]["consecutive_no_winner"],
            4,
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

    def test_only_coding_infrastructure_statuses_fail_execution(self) -> None:
        fatal_statuses = (
            "agent_provider_transient_failed",
            "agent_provider_permanent_failed",
            "agent_provider_configuration_failed",
            "agent_model_response_failed",
            "agent_preparation_failed",
        )
        settled_statuses = (
            "agent_deferred",
            "agent_needs_planner_approval",
            "agent_response_validation_failed",
        )

        def outcome(build_status: str) -> BranchOutcome:
            return BranchOutcome(
                branch_role="flow",
                agent_name="flow_agent",
                candidate_id="flow_candidate_001",
                status="reviewed",
                return_code=1,
                decision=(
                    "CODING_INFRASTRUCTURE_FAILURE"
                    if build_status.startswith("agent_provider_")
                    else "REPAIR_VALIDATION"
                ),
                eligible_for_promotion=False,
                artifact_root="experiments/cycle_001/candidates/flow_candidate_001",
                review_path="experiments/cycle_001/review_decision.json",
                elapsed_seconds=0.0,
                error="pipeline return code 1",
                build_status=build_status,
            )

        for build_status in fatal_statuses:
            with self.subTest(build_status=build_status):
                self.assertTrue(_branch_execution_failed(outcome(build_status)))
        for build_status in settled_statuses:
            with self.subTest(build_status=build_status):
                self.assertFalse(_branch_execution_failed(outcome(build_status)))

    def test_campaign_persists_fan_in_then_stops_on_coding_infrastructure(self) -> None:
        plan = self._plan()

        def reviewed_outcome(branch_role: str, build_status: str) -> BranchOutcome:
            branch = next(
                item for item in plan.branches if item.branch_role == branch_role
            )
            return BranchOutcome(
                branch_role=branch.branch_role,
                agent_name=branch.agent_name,
                candidate_id=branch.candidate_id,
                status="reviewed",
                return_code=1,
                decision="REPAIR_VALIDATION",
                eligible_for_promotion=False,
                artifact_root=(
                    f"experiments/cycle_001/candidates/{branch.candidate_id}"
                ),
                review_path=(
                    "experiments/cycle_001/candidates/"
                    f"{branch.candidate_id}/review_decision.json"
                ),
                elapsed_seconds=0.0,
                error="pipeline return code 1",
                expected_benchmark_count=2,
                build_status=build_status,
                review_reason="synthetic coding-agent result",
                next_action="repair",
            )

        outcomes = (
            reviewed_outcome("flow", "agent_provider_transient_failed"),
            reviewed_outcome("logic", "agent_deferred"),
        )
        args = parse_args(
            (
                "--repo-root",
                str(self.repo),
                "--new-cycle-budget",
                "2",
                "--planner-mode",
                "deterministic",
            )
        )
        output = StringIO()
        with patch(
            "scripts.agents.self_evolved_abc.workflow.dual_agent_loop."
            "_load_or_create_initial_plan",
            return_value=plan,
        ):
            with patch(
                "scripts.agents.self_evolved_abc.workflow.dual_agent_loop."
                "execute_portfolio_plan",
                return_value=outcomes,
            ):
                with patch(
                    "scripts.agents.self_evolved_abc.workflow.dual_agent_loop."
                    "_print_plan",
                ), patch.object(
                    PlanningAgent,
                    "create_next_parallel_coding_dispatch",
                ) as next_planning:
                    with redirect_stdout(output):
                        return_code = _run_campaign(self.repo, args)

        self.assertEqual(return_code, 1)
        next_planning.assert_not_called()
        self.assertTrue(
            (
                self.repo
                / "experiments"
                / "cycle_001"
                / "planning"
                / "portfolio_review.json"
            ).is_file()
        )
        portfolio = json.loads(
            (
                self.repo
                / "experiments/cycle_001/planning/portfolio_review.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(portfolio["round_status"], "infrastructure_failed")
        self.assertFalse(portfolio["quorum_reached"])
        self.assertEqual(portfolio["reviewed_count"], 2)
        self.assertEqual(portfolio["failed_count"], 1)
        rendered = output.getvalue()
        self.assertIn("coding-agent infrastructure failure", rendered)
        self.assertIn("flow=agent_provider_transient_failed", rendered)
        self.assertNotIn("exhausted --new-cycle-budget", rendered)

    def test_paired_planner_batch_is_idempotent_and_evidence_only(self) -> None:
        plan = self._plan()
        flow_branch = plan.branches[0]
        _install_flow_batch_sources(self.repo)
        before = json.loads(flow_branch.assignment_path.read_text(encoding="utf-8"))
        baseline_keys = (
            "baseline_ref",
            "baseline_kind",
            "base_source_root",
            "baseline_abc_bin",
            "champion_cycle_id",
            "champion_candidate_id",
            "champion_source_root",
            "champion_abc_bin",
        )
        baseline_before = {key: before.get(key) for key in baseline_keys}
        before["_planning_meta"]["target_command"] = "fx"
        before["target_command"] = "fx"
        context_payload = {
            "schema_version": 1,
            "variant_set": "flow_wide",
            "target_command": before["_planning_meta"]["target_command"],
        }
        before["planner_batch_lineage_context"] = context_payload
        flow_branch.assignment_path.write_text(
            json.dumps(before, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        context = CycleContext(self.repo, before)
        variants = build_variants(
            context,
            "flow_wide",
            target_command=str(context_payload["target_command"]),
        )
        lineage = build_batch_lineage(
            before,
            variant_set="flow_wide",
            target_command=str(context_payload["target_command"]),
            variant_space=describe_variant_space(context, variants),
        )
        lineage_hash = hash_batch_lineage(lineage)
        batch_id = f"cycle_001_planner_flow_wide_{lineage_hash[:12]}"
        manifest = generate_batch(
            context=context,
            base_assignment_path=flow_branch.assignment_path,
            start_cycle="probe_001",
            batch_id=batch_id,
            variant_set="flow_wide",
            include_variants=set(),
            target_command=str(context_payload["target_command"]),
            force=False,
        )
        winner_path = self.repo / str(manifest["manifest_path"])
        winner_path = winner_path.parent / "winner.json"
        for index, item in enumerate(manifest["items"]):
            probe_assignment_path = self.repo / item["assignment_path"]
            _write_review(
                self.repo,
                probe_assignment_path,
                decision=(
                    "ACCEPT_FOR_NEXT_CYCLE" if index == 0 else "REPAIR_QOR"
                ),
            )
            probe_context = CycleContext.from_assignment_file(
                self.repo,
                probe_assignment_path,
            )
            probe_qor = (
                impl_compare_root(probe_context)
                / "comparison"
                / "qor_delta.csv"
            )
            probe_qor.write_text(
                "benchmark,baseline_and,candidate_and,delta\n"
                "a,100,99,-1\n",
                encoding="utf-8",
            )
        summarize_batch(repo_root=self.repo, manifest=manifest)

        with patch(
            "scripts.agents.self_evolved_abc.flow.planner_batch.subprocess.run"
        ) as provider:
            result = run_and_integrate_planner_batch(
                repo_root=self.repo,
                assignment_path=flow_branch.assignment_path,
                build_candidate_binary=True,
                build_jobs=2,
                build_timeout_seconds=10.0,
                timeout_seconds=10.0,
                update_baseline=False,
            )
            repeated = run_and_integrate_planner_batch(
                repo_root=self.repo,
                assignment_path=flow_branch.assignment_path,
                build_candidate_binary=True,
                build_jobs=2,
                build_timeout_seconds=10.0,
                timeout_seconds=10.0,
                update_baseline=False,
            )
            provider.assert_not_called()

            first_probe_path = self.repo / manifest["items"][0]["assignment_path"]
            original_probe = first_probe_path.read_text(encoding="utf-8")
            tampered_probe = json.loads(original_probe)
            tampered_probe["baseline_ref"] = {"kind": "tampered"}
            first_probe_path.write_text(json.dumps(tampered_probe), encoding="utf-8")
            tampered_assignment_result = run_and_integrate_planner_batch(
                repo_root=self.repo,
                assignment_path=flow_branch.assignment_path,
                build_candidate_binary=True,
                build_jobs=2,
                build_timeout_seconds=10.0,
                timeout_seconds=10.0,
                update_baseline=False,
            )
            first_probe_path.write_text(original_probe, encoding="utf-8")
            provider.assert_not_called()

            probe_context = CycleContext.from_assignment_file(
                self.repo,
                first_probe_path,
            )
            probe_review_path = (
                impl_compare_root(probe_context)
                / "comparison"
                / "review_decision.json"
            )
            original_review = probe_review_path.read_text(encoding="utf-8")
            tampered_review = json.loads(original_review)
            tampered_review["average_and_improve_pct"] = 999.0
            probe_review_path.write_text(json.dumps(tampered_review), encoding="utf-8")
            tampered_review_result = run_and_integrate_planner_batch(
                repo_root=self.repo,
                assignment_path=flow_branch.assignment_path,
                build_candidate_binary=True,
                build_jobs=2,
                build_timeout_seconds=10.0,
                timeout_seconds=10.0,
                update_baseline=False,
            )
            probe_review_path.write_text(original_review, encoding="utf-8")
            provider.assert_not_called()

            changed_source = self.repo / str(manifest["items"][0]["target_file"])
            changed_source.write_text(
                changed_source.read_text(encoding="utf-8")
                + "\n/* stale-lineage regression sentinel */\n",
                encoding="utf-8",
            )
            provider.return_value.returncode = 2
            stale_result = run_and_integrate_planner_batch(
                repo_root=self.repo,
                assignment_path=flow_branch.assignment_path,
                build_candidate_binary=True,
                build_jobs=2,
                build_timeout_seconds=10.0,
                timeout_seconds=10.0,
                update_baseline=False,
            )

        self.assertEqual(result, "")
        self.assertEqual(repeated, "")
        self.assertIsNone(tampered_assignment_result)
        self.assertIsNone(tampered_review_result)
        self.assertIsNone(stale_result)
        provider.assert_called_once()
        invoked = provider.call_args.args[0]
        self.assertNotIn(batch_id, invoked)
        after = json.loads(flow_branch.assignment_path.read_text(encoding="utf-8"))
        self.assertEqual(
            {key: after.get(key) for key in baseline_keys},
            baseline_before,
        )
        self.assertFalse(after["_planning_meta"]["should_skip_llm"])
        self.assertTrue(after["batch_search_evidence"])
        self.assertEqual(after["batch_search_evidence"]["lineage_hash"], lineage_hash)
        load_portfolio_plan(self.repo, plan.cycle_id)

    def test_promoted_probe_uses_candidate_scoped_workspace_and_qor_paths(self) -> None:
        plan = self._plan()
        base = json.loads(
            plan.branches[0].assignment_path.read_text(encoding="utf-8")
        )
        probe_assignment = (
            self.repo
            / "experiments"
            / "probe_011"
            / "agents"
            / "assignments"
            / "candidate_001.json"
        )
        probe_assignment.parent.mkdir(parents=True, exist_ok=True)
        probe_payload = dict(base)
        probe_payload.update(
            {
                "cycle_id": "probe_011",
                "candidate_id": "candidate_001",
                "artifact_layout": CANDIDATE_SCOPED_LAYOUT,
            }
        )
        probe_assignment.write_text(json.dumps(probe_payload), encoding="utf-8")
        pending_assignment = (
            self.repo
            / "experiments"
            / "cycle_006"
            / "agents"
            / "assignments"
            / "candidate_001.json"
        )
        pending_assignment.parent.mkdir(parents=True, exist_ok=True)
        pending_payload = dict(base)
        pending_payload.update(
            {"cycle_id": "cycle_006", "candidate_id": "candidate_001"}
        )
        pending_assignment.write_text(json.dumps(pending_payload), encoding="utf-8")

        integrated = integrate_batch_winner(
            assignment_path=pending_assignment,
            batch_id="cycle_006_planner_flow_wide",
            winner_payload={
                "promotion_found": True,
                "winner": {
                    "cycle_id": "probe_011",
                    "variant_id": "rewrite_last_equal_gain",
                    "decision": "ACCEPT_FOR_NEXT_CYCLE",
                    "promotion_allowed": True,
                    "average_and_improve_pct": 1.0,
                    "total_and_delta_candidate_minus_baseline": -10,
                    "improved_benchmark_count": 3,
                    "regressed_benchmark_count": 0,
                },
            },
        )
        self.assertEqual(integrated, "probe_011")
        updated = json.loads(pending_assignment.read_text(encoding="utf-8"))
        expected_root = (
            "experiments/probe_011/candidates/candidate_001/impl_compare/"
            "candidate_modified/workspace/third_party/FlowTune/src"
        )
        self.assertEqual(updated["base_source_root"], expected_root)
        self.assertIn(
            "experiments/probe_011/candidates/candidate_001/impl_compare/"
            "comparison/qor_delta.csv",
            updated["recent_evidence"],
        )

    def test_flow_batch_families_respect_paired_source_ownership(self) -> None:
        checked_in = (
            PROJECT_ROOT
            / "experiments"
            / "cycle_001"
            / "agents"
            / "assignments"
            / "candidate_001.json"
        )
        payload = json.loads(checked_in.read_text(encoding="utf-8"))
        payload["source_patch_allowed_roots"] = [
            "third_party/FlowTune/src/src/opt"
        ]
        context = CycleContext(PROJECT_ROOT, payload)
        for command in ("rewrite", "resub", "dc2"):
            with self.subTest(command=command):
                variants = build_variants(
                    context,
                    "flow_wide",
                    target_command=command,
                )
                self.assertTrue(variants)
                self.assertTrue(
                    all(
                        item.target_file.startswith(
                            "third_party/FlowTune/src/src/opt/"
                        )
                        for item in variants
                    )
                )
                self.assertTrue(
                    all(
                        variant_command(item.variant_id) == command
                        for item in variants
                    )
                )
                for item in variants:
                    checked = subprocess.run(
                        ("git", "apply", "--check", "--recount", "-"),
                        cwd=PROJECT_ROOT,
                        input=item.patch_text,
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(
                        checked.returncode,
                        0,
                        msg=(
                            f"{item.variant_id} did not strictly apply: "
                            f"{checked.stderr}"
                        ),
                    )

    def test_planner_batch_failure_blocks_both_coding_branches(self) -> None:
        plan = self._plan()
        flow_assignment = plan.branches[0].assignment_path
        payload = json.loads(flow_assignment.read_text(encoding="utf-8"))
        meta = dict(payload.get("_planning_meta", {}))
        meta.update({"should_skip_llm": True, "target_command": "rewrite"})
        payload["_planning_meta"] = meta
        payload["planner_should_skip_llm"] = True
        flow_assignment.write_text(json.dumps(payload), encoding="utf-8")
        args = parse_args(
            (
                "--repo-root",
                str(self.repo),
                "--new-cycle-budget",
                "1",
                "--build-candidate-binary",
            )
        )
        output = StringIO()
        with patch(
            "scripts.agents.self_evolved_abc.workflow.dual_agent_loop."
            "_load_or_create_initial_plan",
            return_value=plan,
        ):
            with patch(
                "scripts.agents.self_evolved_abc.workflow.dual_agent_loop."
                "run_and_integrate_planner_batch",
                return_value=None,
            ) as planner_batch:
                with patch(
                    "scripts.agents.self_evolved_abc.workflow.dual_agent_loop."
                    "execute_portfolio_plan",
                ) as execute:
                    with patch(
                        "scripts.agents.self_evolved_abc.workflow.dual_agent_loop."
                        "_print_plan",
                    ):
                        with redirect_stdout(output):
                            return_code = _run_campaign(self.repo, args)

        self.assertEqual(return_code, 1)
        self.assertFalse(planner_batch.call_args.kwargs["update_baseline"])
        lineage_context = planner_batch.call_args.kwargs["lineage_context"]
        self.assertEqual(lineage_context["planner_dispatch_id"], plan.planner_dispatch_id)
        self.assertEqual(lineage_context["parent_plan_hash"], plan.parent_plan_hash)
        self.assertEqual(lineage_context["parent_review_hash"], plan.parent_review_hash)
        self.assertEqual(len(lineage_context["portfolio_plan_sha256"]), 64)
        execute.assert_not_called()
        self.assertIn("no coding branch was started", output.getvalue())

    def test_pre_control_flow_manifest_cannot_bypass_planner_batch(self) -> None:
        plan = self._plan()
        flow_assignment = plan.branches[0].assignment_path
        payload = json.loads(flow_assignment.read_text(encoding="utf-8"))
        meta = dict(payload.get("_planning_meta", {}))
        meta["should_skip_llm"] = True
        payload["_planning_meta"] = meta
        payload["planner_should_skip_llm"] = True
        flow_assignment.write_text(json.dumps(payload), encoding="utf-8")

        def runner(command, cwd):
            assignment = _assignment_from_command(cwd, command)
            _write_review(cwd, assignment, decision="REPAIR_QOR")
            return 1

        execute_portfolio_plan(
            repo_root=self.repo,
            plan=plan,
            command_runner=runner,
        )

        # Both legacy Coding manifests may be complete, but the coordinator
        # control is still pending. They must not make startup fast-forward this
        # cycle or serve as proof that the model-free batch already ran.
        self.assertIsNone(_load_completed_portfolio_review(self.repo, plan))

        def integrate_batch(**kwargs):
            assignment_path = kwargs["assignment_path"]
            integrated = json.loads(assignment_path.read_text(encoding="utf-8"))
            integrated_meta = dict(integrated.get("_planning_meta", {}))
            integrated_meta["should_skip_llm"] = False
            integrated["_planning_meta"] = integrated_meta
            integrated["planner_should_skip_llm"] = False
            integrated["planner_hypothesis"] = (
                "Use the lineage-bound model-free sensitivity evidence."
            )
            integrated["batch_search_evidence"] = {
                "batch_id": "cycle_001_planner_flow_wide",
                "requires_replanning": True,
                "planning_consumed": False,
            }
            assignment_path.write_text(
                json.dumps(integrated, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return ""

        with patch(
            "scripts.agents.self_evolved_abc.workflow.dual_agent_loop."
            "run_and_integrate_planner_batch",
            side_effect=integrate_batch,
        ) as planner_batch:
            allowed = _honor_flow_planner_control(
                repo_root=self.repo.resolve(),
                plan=plan,
                build_candidate_binary=True,
                build_jobs=2,
                build_timeout_seconds=10.0,
                timeout_seconds=10.0,
                planner_mode="deterministic",
            )
        self.assertIsNotNone(allowed)
        assert allowed is not None
        planner_batch.assert_called_once()
        self.assertNotEqual(allowed.planner_advice_hash, plan.planner_advice_hash)
        refreshed = json.loads(flow_assignment.read_text(encoding="utf-8"))
        self.assertTrue(refreshed["batch_search_evidence"]["planning_consumed"])

        # The manifest was valid only for the pre-control assignment. The
        # refreshed assignment/advice lineage makes it non-resumable, so Coding
        # will rerun the stale lane instead of trusting its old review.
        self.assertIsNone(_load_completed_portfolio_review(self.repo, allowed))

    def test_new_cycle_budget_fast_forwards_history_and_prepares_frontier(self) -> None:
        plan_1 = self._plan()

        def settle(plan):
            # These fixtures represent history whose coordinator pre-control
            # already settled.  A Coding manifest alone is intentionally not
            # enough to establish that fact.
            flow_assignment = plan.branches[0].assignment_path
            flow_payload = json.loads(flow_assignment.read_text(encoding="utf-8"))
            flow_meta = dict(flow_payload.get("_planning_meta", {}))
            flow_meta["should_skip_llm"] = False
            flow_payload["_planning_meta"] = flow_meta
            flow_payload["planner_should_skip_llm"] = False
            batch_evidence = flow_payload.get("batch_search_evidence")
            if isinstance(batch_evidence, dict):
                batch_evidence["planning_consumed"] = True
                flow_payload["batch_search_evidence"] = batch_evidence
            flow_assignment.write_text(
                json.dumps(flow_payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            def runner(command, cwd):
                assignment = _assignment_from_command(cwd, command)
                _write_review(cwd, assignment, decision="REPAIR_QOR")
                return 1

            outcomes = execute_portfolio_plan(
                repo_root=self.repo,
                plan=plan,
                command_runner=runner,
            )
            return write_portfolio_review(
                repo_root=self.repo,
                plan=plan,
                outcomes=outcomes,
            )

        review_1 = settle(plan_1)
        plan_2 = create_next_portfolio_plan(
            repo_root=self.repo,
            current_plan=plan_1,
            portfolio_review=review_1,
            next_cycle_id="cycle_002",
        )
        review_2 = settle(plan_2)
        plan_3 = create_next_portfolio_plan(
            repo_root=self.repo,
            current_plan=plan_2,
            portfolio_review=review_2,
            next_cycle_id="cycle_003",
        )

        execution_events: list[str] = []

        def integrate_planner_batch(**kwargs):
            execution_events.append("batch")
            self.assertFalse(kwargs["update_baseline"])
            assignment_path = kwargs["assignment_path"]
            payload = json.loads(assignment_path.read_text(encoding="utf-8"))
            meta = dict(payload.get("_planning_meta", {}))
            meta["should_skip_llm"] = False
            payload["_planning_meta"] = meta
            payload["planner_should_skip_llm"] = False
            payload["planner_hypothesis"] = (
                "Use the integrated probe_001 sensitivity vector."
            )
            payload["batch_search_evidence"] = {
                "winner_cycle_id": "probe_001",
                "requires_replanning": True,
                "planning_consumed": False,
            }
            assignment_path.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return ""

        def execute_frontier(**kwargs):
            plan = kwargs["plan"]
            execution_events.append(f"execute:{plan.cycle_id}")

            def runner(command, cwd):
                assignment = _assignment_from_command(cwd, command)
                _write_review(cwd, assignment, decision="REPAIR_QOR")
                return 1

            return execute_portfolio_plan(command_runner=runner, **kwargs)

        args = parse_args(
            (
                "--repo-root",
                str(self.repo),
                "--new-cycle-budget",
                "1",
                "--planner-mode",
                "deterministic",
            )
        )
        output = StringIO()
        with patch(
            "scripts.agents.self_evolved_abc.workflow.dual_agent_loop."
            "_load_or_create_initial_plan",
            return_value=plan_1,
        ):
            with patch(
                "scripts.agents.self_evolved_abc.workflow.dual_agent_loop."
                "execute_portfolio_plan",
                side_effect=execute_frontier,
            ):
                with patch(
                    "scripts.agents.self_evolved_abc.workflow.dual_agent_loop."
                    "run_and_integrate_planner_batch",
                    side_effect=integrate_planner_batch,
                ):
                    with patch(
                        "scripts.agents.self_evolved_abc.workflow.dual_agent_loop."
                        "_print_plan",
                    ):
                        with redirect_stdout(output):
                            return_code = _run_campaign(self.repo, args)

        self.assertEqual(return_code, 0)
        self.assertEqual(execution_events, ["batch", "execute:cycle_003"])
        refreshed_plan_3 = load_portfolio_plan(self.repo, "cycle_003")
        self.assertNotEqual(
            refreshed_plan_3.planner_advice_hash,
            plan_3.planner_advice_hash,
        )
        refreshed_assignments = [
            json.loads(branch.assignment_path.read_text(encoding="utf-8"))
            for branch in refreshed_plan_3.branches
        ]
        self.assertTrue(
            refreshed_assignments[0]["batch_search_evidence"][
                "planning_consumed"
            ]
        )
        self.assertEqual(
            [item["planner_advice_hash"] for item in refreshed_assignments],
            [refreshed_plan_3.planner_advice_hash] * 2,
        )
        plan_4 = load_portfolio_plan(self.repo, "cycle_004")
        self.assertEqual(plan_4.previous_cycle_id, "cycle_003")
        rendered = output.getvalue()
        self.assertIn("fast_forwarded=2", rendered)
        self.assertIn("new_cycle_budget_unchanged=true", rendered)
        self.assertIn("exhausted --new-cycle-budget", rendered)
        self.assertIn("feedback from cycle_003 was consumed", rendered)
        self.assertIn("prepared dispatch cycle_004", rendered)

    def test_run_sh_exposes_resume_stable_cycle_limits(self) -> None:
        args = parse_args(())
        self.assertEqual(args.new_cycle_budget, 10)
        self.assertEqual(args.target_cycle, 10)
        script = (PROJECT_ROOT / "run.sh").read_text(encoding="utf-8")
        self.assertIn("EDA_AGENT_NEW_CYCLE_BUDGET", script)
        self.assertIn("EDA_AGENT_TARGET_CYCLE", script)
        self.assertIn("--new-cycle-budget", script)
        self.assertIn("--target-cycle", script)
        self.assertNotIn("--max-cycles", script)

    def test_absolute_target_stops_after_fan_in_without_unused_dispatch(self) -> None:
        plan = self._plan()

        def negative_outcome(branch_role: str) -> BranchOutcome:
            branch = next(
                item for item in plan.branches if item.branch_role == branch_role
            )
            return BranchOutcome(
                branch_role=branch.branch_role,
                agent_name=branch.agent_name,
                candidate_id=branch.candidate_id,
                status="reviewed",
                return_code=1,
                decision="REPAIR_QOR",
                eligible_for_promotion=False,
                artifact_root=(
                    f"experiments/cycle_001/candidates/{branch.candidate_id}"
                ),
                review_path=(
                    "experiments/cycle_001/candidates/"
                    f"{branch.candidate_id}/impl_compare/comparison/"
                    "review_decision.json"
                ),
                elapsed_seconds=0.0,
                error="pipeline return code 1",
                expected_benchmark_count=2,
                build_status="candidate_binary_build_passed",
                review_reason="synthetic zero-delta candidate",
                next_action="change strategy",
            )

        outcomes = (negative_outcome("flow"), negative_outcome("logic"))
        args = parse_args(
            (
                "--repo-root",
                str(self.repo),
                "--new-cycle-budget",
                "10",
                "--target-cycle",
                "1",
                "--planner-mode",
                "deterministic",
            )
        )
        output = StringIO()
        with patch(
            "scripts.agents.self_evolved_abc.workflow.dual_agent_loop."
            "_load_or_create_initial_plan",
            return_value=plan,
        ), patch(
            "scripts.agents.self_evolved_abc.workflow.dual_agent_loop."
            "_honor_flow_planner_control",
            return_value=plan,
        ), patch(
            "scripts.agents.self_evolved_abc.workflow.dual_agent_loop."
            "execute_portfolio_plan",
            return_value=outcomes,
        ), patch(
            "scripts.agents.self_evolved_abc.workflow.dual_agent_loop."
            "_load_or_create_next_plan",
        ) as next_plan, patch(
            "scripts.agents.self_evolved_abc.workflow.dual_agent_loop."
            "_print_plan",
        ):
            with redirect_stdout(output):
                return_code = _run_campaign(self.repo, args)

        self.assertEqual(return_code, 1)
        next_plan.assert_not_called()
        self.assertFalse(
            (self.repo / "experiments/cycle_002/planning/portfolio_plan.json").exists()
        )
        self.assertIn("reached absolute target cycle_001", output.getvalue())
        self.assertIn("no unexecuted next dispatch was created", output.getvalue())
        self.assertIn("campaign objective unmet", output.getvalue())
        terminal = json.loads(
            (
                self.repo
                / "experiments/cycle_001/planning/final_champion.json"
            ).read_text(encoding="utf-8")
        )
        self.assertFalse(terminal["objective_achieved"])
        self.assertEqual(terminal["status"], "no_champion")

    def test_target_winner_is_finalized_without_creating_next_dispatch(self) -> None:
        plan = self._plan()
        flow = next(item for item in plan.branches if item.branch_role == "flow")
        logic = next(item for item in plan.branches if item.branch_role == "logic")
        winner_workspace = (
            self.repo
            / "experiments/cycle_001/candidates"
            / flow.candidate_id
            / "impl_compare/candidate_modified/workspace/third_party/FlowTune/src"
        )
        winner_workspace.mkdir(parents=True)
        (winner_workspace / "abc").write_text("test binary", encoding="utf-8")

        outcomes = (
            BranchOutcome(
                branch_role="flow",
                agent_name=flow.agent_name,
                candidate_id=flow.candidate_id,
                status="reviewed",
                return_code=0,
                decision="ACCEPT_FOR_NEXT_CYCLE",
                eligible_for_promotion=True,
                artifact_root=(
                    f"experiments/cycle_001/candidates/{flow.candidate_id}/impl_compare"
                ),
                review_path="flow_review.json",
                elapsed_seconds=0.0,
                error="",
                scalar_and_reward=10,
                improved_benchmark_count=2,
                average_and_improve_pct=1.0,
                cec_pass_count=2,
                cec_total_count=2,
                correctness_backed_rows=2,
                expected_benchmark_count=2,
                build_status="candidate_binary_build_passed",
                total_depth_delta=0,
                structural_proxy_reward_pct=1.0,
            ),
            BranchOutcome(
                branch_role="logic",
                agent_name=logic.agent_name,
                candidate_id=logic.candidate_id,
                status="reviewed",
                return_code=1,
                decision="REPAIR_QOR",
                eligible_for_promotion=False,
                artifact_root=(
                    f"experiments/cycle_001/candidates/{logic.candidate_id}/impl_compare"
                ),
                review_path="logic_review.json",
                elapsed_seconds=0.0,
                error="pipeline return code 1",
                expected_benchmark_count=2,
                build_status="candidate_binary_build_passed",
            ),
        )
        args = parse_args(
            (
                "--repo-root",
                str(self.repo),
                "--new-cycle-budget",
                "10",
                "--target-cycle",
                "1",
                "--planner-mode",
                "deterministic",
            )
        )
        with patch(
            "scripts.agents.self_evolved_abc.workflow.dual_agent_loop."
            "_load_or_create_initial_plan",
            return_value=plan,
        ), patch(
            "scripts.agents.self_evolved_abc.workflow.dual_agent_loop."
            "_honor_flow_planner_control",
            return_value=plan,
        ), patch(
            "scripts.agents.self_evolved_abc.workflow.dual_agent_loop."
            "execute_portfolio_plan",
            return_value=outcomes,
        ), patch(
            "scripts.agents.self_evolved_abc.workflow.dual_agent_loop."
            "_load_or_create_next_plan",
        ) as next_plan, patch(
            "scripts.agents.self_evolved_abc.workflow.dual_agent_loop._print_plan",
        ):
            return_code = _run_campaign(self.repo, args)

        self.assertEqual(return_code, 0)
        next_plan.assert_not_called()
        terminal = json.loads(
            (
                self.repo
                / "experiments/cycle_001/planning/final_champion.json"
            ).read_text(encoding="utf-8")
        )
        self.assertTrue(terminal["objective_achieved"])
        self.assertEqual(terminal["selected_candidate_id"], flow.candidate_id)
        self.assertEqual(terminal["final_baseline_ref"]["kind"], "champion")

    def test_resume_at_completed_target_does_not_materialize_next_cycle(self) -> None:
        plan = self._plan()

        def runner(command, cwd):
            assignment = _assignment_from_command(cwd, command)
            _write_review(cwd, assignment, decision="REPAIR_QOR")
            return 1

        outcomes = execute_portfolio_plan(
            repo_root=self.repo,
            plan=plan,
            command_runner=runner,
        )
        self.assertEqual([item.status for item in outcomes], ["reviewed", "reviewed"])
        args = parse_args(
            (
                "--repo-root",
                str(self.repo),
                "--new-cycle-budget",
                "10",
                "--target-cycle",
                "1",
                "--planner-mode",
                "deterministic",
            )
        )
        output = StringIO()
        with patch(
            "scripts.agents.self_evolved_abc.workflow.dual_agent_loop."
            "_load_or_create_initial_plan",
            return_value=plan,
        ), patch(
            "scripts.agents.self_evolved_abc.workflow.dual_agent_loop."
            "execute_portfolio_plan",
        ) as execute_again, patch(
            "scripts.agents.self_evolved_abc.workflow.dual_agent_loop."
            "_load_or_create_next_plan",
        ) as next_plan, patch(
            "scripts.agents.self_evolved_abc.workflow.dual_agent_loop."
            "_print_plan",
        ):
            with redirect_stdout(output):
                return_code = _run_campaign(self.repo, args)

        self.assertEqual(return_code, 1)
        execute_again.assert_not_called()
        next_plan.assert_not_called()
        self.assertFalse(
            (self.repo / "experiments/cycle_002/planning/portfolio_plan.json").exists()
        )
        self.assertIn("target cycle already complete", output.getvalue())
        self.assertIn("no unexecuted next dispatch was created", output.getvalue())

    def test_equal_promotion_metrics_have_stable_candidate_id_tiebreak(self) -> None:
        common = {
            "agent_name": "agent",
            "status": "reviewed",
            "return_code": 0,
            "decision": "ACCEPT_FOR_NEXT_CYCLE",
            "eligible_for_promotion": True,
            "artifact_root": "artifact",
            "review_path": "review.json",
            "elapsed_seconds": 0.0,
            "error": "",
            "scalar_and_reward": 12,
            "improved_benchmark_count": 2,
            "average_and_improve_pct": 0.5,
            "total_depth_delta": -1,
            "structural_proxy_reward_pct": 0.8,
        }
        logic = BranchOutcome(
            branch_role="logic",
            candidate_id="logic_candidate_001",
            **common,
        )
        flow = BranchOutcome(
            branch_role="flow",
            candidate_id="flow_candidate_001",
            **common,
        )

        winner = _select_winner((logic, flow))

        self.assertIsNotNone(winner)
        assert winner is not None
        self.assertEqual(winner.candidate_id, "flow_candidate_001")

    def test_invalid_coding_reply_materializes_a_settled_negative_review(self) -> None:
        plan = self._plan()
        logic_branch = plan.branches[1]
        with patch(
            "scripts.agents.self_evolved_abc.workflow.candidate_pipeline."
            "_run_agent_with_retry",
            return_value=AgentRunResult(
                succeeded=False,
                decision="NEEDS_HUMAN_REVIEW",
                failure_kind="response_validation",
                attempts=3,
                detail="synthetic response validation failure",
            ),
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
        self.assertEqual(outcome.build_status, "agent_response_validation_failed")
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

    def test_resume_retries_legacy_missing_coding_failure(self) -> None:
        plan = self._plan()
        first_calls: list[str] = []

        def first_runner(command, cwd):
            assignment = _assignment_from_command(cwd, command)
            role = str(
                json.loads(assignment.read_text(encoding="utf-8"))["branch_role"]
            )
            first_calls.append(role)
            if role == "flow":
                _write_review(
                    cwd,
                    assignment,
                    decision="REPAIR_VALIDATION",
                    build_status="missing",
                )
                return 1
            _write_review(cwd, assignment)
            return 0

        execute_portfolio_plan(
            repo_root=self.repo,
            plan=plan,
            command_runner=first_runner,
        )
        self.assertCountEqual(first_calls, ["flow", "logic"])

        resumed_calls: list[str] = []

        def resumed_runner(command, cwd):
            assignment = _assignment_from_command(cwd, command)
            role = str(
                json.loads(assignment.read_text(encoding="utf-8"))["branch_role"]
            )
            resumed_calls.append(role)
            _write_review(cwd, assignment)
            return 0

        outcomes = execute_portfolio_plan(
            repo_root=self.repo,
            plan=plan,
            command_runner=resumed_runner,
        )

        self.assertEqual(resumed_calls, ["flow"])
        self.assertEqual([item.status for item in outcomes], ["reviewed", "reviewed"])
        self.assertEqual(outcomes[0].build_status, "candidate_binary_build_passed")

    def test_changed_review_regenerates_stale_downstream_plan(self) -> None:
        plan = self._plan()

        def runner(command, cwd):
            assignment = _assignment_from_command(cwd, command)
            _write_review(
                cwd,
                assignment,
                decision="REPAIR_QOR",
                build_status="candidate_binary_build_passed",
            )
            return 1

        outcomes = execute_portfolio_plan(
            repo_root=self.repo,
            plan=plan,
            command_runner=runner,
        )
        review = write_portfolio_review(
            repo_root=self.repo,
            plan=plan,
            outcomes=outcomes,
        )
        stale = create_next_portfolio_plan(
            repo_root=self.repo,
            current_plan=plan,
            portfolio_review=review,
            next_cycle_id="cycle_002",
        )

        review["selection_reason"] = "updated after re-running legacy failures"
        review_path = (
            self.repo / "experiments/cycle_001/planning/portfolio_review.json"
        )
        review_path.write_text(
            json.dumps(review, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        output = StringIO()
        with redirect_stdout(output):
            refreshed = _load_or_create_next_plan(
                repo_root=self.repo,
                current_plan=plan,
                portfolio_review=review,
                next_cycle_id="cycle_002",
                timeout_seconds=300.0,
                build_timeout_seconds=900.0,
                planner_mode="deterministic",
            )

        self.assertNotEqual(refreshed.parent_review_hash, stale.parent_review_hash)
        self.assertIn("regenerating stale downstream", output.getvalue())

    def test_started_downstream_plan_is_not_overwritten_on_parent_hash_drift(
        self,
    ) -> None:
        plan = self._plan()

        def runner(command, cwd):
            assignment = _assignment_from_command(cwd, command)
            _write_review(cwd, assignment, decision="REPAIR_QOR")
            return 1

        outcomes = execute_portfolio_plan(
            repo_root=self.repo,
            plan=plan,
            command_runner=runner,
        )
        review = write_portfolio_review(
            repo_root=self.repo,
            plan=plan,
            outcomes=outcomes,
        )
        downstream = create_next_portfolio_plan(
            repo_root=self.repo,
            current_plan=plan,
            portfolio_review=review,
            next_cycle_id="cycle_002",
        )
        started_context = CycleContext.from_assignment_file(
            self.repo, downstream.branches[0].assignment_path
        )
        started_review = review_decision_path(started_context)
        started_review.parent.mkdir(parents=True, exist_ok=True)
        started_review.write_text("{}\n", encoding="utf-8")
        downstream_path = portfolio_plan_path(self.repo, "cycle_002")
        frozen_bytes = downstream_path.read_bytes()

        review["selection_reason"] = "parent review normalized after dispatch"
        (
            self.repo / "experiments/cycle_001/planning/portfolio_review.json"
        ).write_text(
            json.dumps(review, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            ValueError, "refusing to overwrite frozen branch work"
        ):
            _load_or_create_next_plan(
                repo_root=self.repo,
                current_plan=plan,
                portfolio_review=review,
                next_cycle_id="cycle_002",
                timeout_seconds=300.0,
                build_timeout_seconds=900.0,
                planner_mode="deterministic",
            )
        self.assertEqual(downstream_path.read_bytes(), frozen_bytes)

    def test_completed_historical_review_bytes_are_preserved_on_fast_forward(
        self,
    ) -> None:
        plan = self._plan()

        def runner(command, cwd):
            assignment = _assignment_from_command(cwd, command)
            _write_review(cwd, assignment, decision="REPAIR_QOR")
            return 1

        outcomes = execute_portfolio_plan(
            repo_root=self.repo,
            plan=plan,
            command_runner=runner,
        )
        review = write_portfolio_review(
            repo_root=self.repo,
            plan=plan,
            outcomes=outcomes,
        )
        review["selection_reason"] = "historical schema wording"
        review_path = (
            self.repo / "experiments/cycle_001/planning/portfolio_review.json"
        )
        review_path.write_text(
            json.dumps(review, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        historical_bytes = review_path.read_bytes()

        loaded = _load_completed_portfolio_review(self.repo, plan)

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["selection_reason"], "historical schema wording")
        self.assertEqual(review_path.read_bytes(), historical_bytes)

    def test_fast_forward_rebuilds_checkpoint_that_forges_a_winner(self) -> None:
        plan = self._plan()

        def runner(command, cwd):
            assignment = _assignment_from_command(cwd, command)
            _write_review(cwd, assignment, decision="REPAIR_QOR")
            return 1

        outcomes = execute_portfolio_plan(
            repo_root=self.repo,
            plan=plan,
            command_runner=runner,
        )
        review = write_portfolio_review(
            repo_root=self.repo,
            plan=plan,
            outcomes=outcomes,
        )
        flow = next(
            item for item in review["branches"] if item["branch_role"] == "flow"
        )
        flow["eligible_for_promotion"] = True
        review.update(
            {
                "round_status": "promotion_selected",
                "eligible_count": 1,
                "selected_candidate_id": flow["candidate_id"],
                "selected_agent_name": flow["agent_name"],
            }
        )
        review_path = (
            self.repo / "experiments/cycle_001/planning/portfolio_review.json"
        )
        review_path.write_text(
            json.dumps(review, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        forged_bytes = review_path.read_bytes()

        loaded = _load_completed_portfolio_review(self.repo, plan)

        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded["round_status"], "no_promotion")
        self.assertEqual(loaded["eligible_count"], 0)
        self.assertEqual(loaded["selected_candidate_id"], "")
        self.assertFalse(
            any(item["eligible_for_promotion"] for item in loaded["branches"])
        )
        self.assertNotEqual(review_path.read_bytes(), forged_bytes)

    def test_terminal_rejects_inconsistent_branch_promotion_claim(self) -> None:
        plan = self._plan()

        def runner(command, cwd):
            assignment = _assignment_from_command(cwd, command)
            _write_review(cwd, assignment, decision="REPAIR_QOR")
            return 1

        outcomes = execute_portfolio_plan(
            repo_root=self.repo,
            plan=plan,
            command_runner=runner,
        )
        review = write_portfolio_review(
            repo_root=self.repo,
            plan=plan,
            outcomes=outcomes,
        )
        flow = next(
            item for item in review["branches"] if item["branch_role"] == "flow"
        )
        flow["eligible_for_promotion"] = True
        review["selected_candidate_id"] = flow["candidate_id"]

        with self.assertRaisesRegex(ValueError, "self-consistent promotion claim"):
            portfolio_module.finalize_portfolio_champion(
                repo_root=self.repo,
                current_plan=plan,
                portfolio_review=review,
            )

    def test_fast_forward_rebuilds_checkpoint_that_suppresses_real_winner(
        self,
    ) -> None:
        plan = self._plan()

        def runner(command, cwd):
            assignment = _assignment_from_command(cwd, command)
            branch_role = json.loads(
                assignment.read_text(encoding="utf-8")
            )["branch_role"]
            decision = (
                "ACCEPT_FOR_NEXT_CYCLE" if branch_role == "flow" else "REPAIR_QOR"
            )
            _write_review(cwd, assignment, decision=decision)
            return 0 if branch_role == "flow" else 1

        outcomes = execute_portfolio_plan(
            repo_root=self.repo,
            plan=plan,
            command_runner=runner,
        )
        review = write_portfolio_review(
            repo_root=self.repo,
            plan=plan,
            outcomes=outcomes,
        )
        flow = next(
            item for item in review["branches"] if item["branch_role"] == "flow"
        )
        self.assertTrue(flow["eligible_for_promotion"])
        flow["eligible_for_promotion"] = False
        review.update(
            {
                "round_status": "no_promotion",
                "eligible_count": 0,
                "selected_candidate_id": "",
                "selected_agent_name": "",
            }
        )
        review_path = (
            self.repo / "experiments/cycle_001/planning/portfolio_review.json"
        )
        review_path.write_text(
            json.dumps(review, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        loaded = _load_completed_portfolio_review(self.repo, plan)

        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded["round_status"], "promotion_selected")
        self.assertEqual(loaded["eligible_count"], 1)
        self.assertEqual(loaded["selected_candidate_id"], flow["candidate_id"])
        rebuilt_flow = next(
            item for item in loaded["branches"] if item["branch_role"] == "flow"
        )
        self.assertTrue(rebuilt_flow["eligible_for_promotion"])

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
