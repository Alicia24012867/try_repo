#!/usr/bin/env python3
"""End-to-end tests for exact, role-tagged validation feedback handoff."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from scripts.agents.self_evolved_abc.coding_agents.logic_minimization_agent import (
    LogicMinimizationAgent,
)
from scripts.agents.self_evolved_abc.cycle_context import CycleContext
from scripts.agents.self_evolved_abc.flow.paths import impl_compare_root
from scripts.agents.self_evolved_abc.planning.portfolio import (
    create_next_portfolio_plan,
    create_portfolio_plan,
)
from scripts.agents.self_evolved_abc.planning_agent import PlanningAgent
from scripts.agents.self_evolved_abc.prompt_rendering import compact_text_block
from scripts.agents.self_evolved_abc.repository_context import (
    RepositoryContextBundle,
)
from scripts.agents.self_evolved_abc.workflow.portfolio_review import (
    collect_branch_outcome,
    write_portfolio_review,
)
from scripts.agents.self_evolved_abc.workflow.failure_evidence import (
    validation_feedback_payload,
)


FLOW_SENTINEL = "FLOW_EXACT_VALIDATION_SENTINEL"
LOGIC_SENTINEL = "LOGIC_EXACT_VALIDATION_SENTINEL"


def _ready_prior() -> RepositoryContextBundle:
    names = tuple(f"prior-{index}" for index in range(10))
    return RepositoryContextBundle(
        text="# Pinned prior\n\nReady test context.",
        configured_count=10,
        available_count=10,
        available_names=names,
        missing_names=(),
        revision_mismatches=(),
        incomplete_names=(),
        dirty_names=(),
    )


class RoleTaggedFailureEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name)
        self._install_prompt_assets()
        source = (
            self.repo
            / "third_party/FlowTune/src/src/base/abci/abcRewrite.c"
        )
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(
            "int Abc_CommandRewrite(void) { return 0; }\n",
            encoding="utf-8",
        )
        self.current_plan = create_portfolio_plan(
            repo_root=self.repo,
            cycle_id="cycle_005",
            previous_cycle_id="cycle_000",
            benchmarks=("benchmarks/a.blif",),
        )
        self._write_branch_failure("flow", FLOW_SENTINEL)
        self._write_branch_failure("logic", LOGIC_SENTINEL)
        outcomes = tuple(
            collect_branch_outcome(
                repo_root=self.repo.resolve(),
                branch=branch,
                return_code=1,
                elapsed_seconds=0.0,
            )
            for branch in self.current_plan.branches
        )
        self.portfolio_review = write_portfolio_review(
            repo_root=self.repo,
            plan=self.current_plan,
            outcomes=outcomes,
        )
        self.next_plan = create_next_portfolio_plan(
            repo_root=self.repo,
            current_plan=self.current_plan,
            portfolio_review=self.portfolio_review,
            next_cycle_id="cycle_006",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _install_prompt_assets(self) -> None:
        for relative in (
            "configs/agents/prompts/planner_prompt.md",
            "configs/agents/prompts/coding_agent_prompt.md",
            "configs/agents/shared/rulebase.md",
            "configs/agents/shared/programming_guidance.md",
        ):
            destination = self.repo / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(PROJECT_ROOT / relative, destination)

    @staticmethod
    def _long_issue(sentinel: str) -> str:
        return (
            "- `error` `source_patch.diff`: "
            + "left-context-"
            + ("L" * 4500)
            + sentinel
            + ("R" * 4500)
            + "-right-context"
        )

    def _write_branch_failure(self, role: str, sentinel: str) -> None:
        branch = next(
            item for item in self.current_plan.branches if item.branch_role == role
        )
        context = CycleContext.from_assignment_file(
            self.repo,
            branch.assignment_path,
        )
        feedback = context.artifact_paths().feedback
        feedback.parent.mkdir(parents=True, exist_ok=True)
        issue = self._long_issue(sentinel)
        feedback.write_text(
            f"# {context.paper_role} Feedback -- {context.candidate_id}\n\n"
            "## Validation Issues\n\n"
            f"{issue}\n\n"
            "## Raw Model Text Preview\n\nignored raw output\n",
            encoding="utf-8",
        )
        review = impl_compare_root(context) / "comparison/review_decision.json"
        review.parent.mkdir(parents=True, exist_ok=True)
        review.write_text(
            json.dumps(
                {
                    "cycle_id": context.cycle_id,
                    "candidate_id": context.candidate_id,
                    "decision": "REPAIR_VALIDATION",
                    "champion_update": False,
                    "promotion_allowed": False,
                    "build_status": "agent_response_validation_failed",
                    "cec_pass_count": 0,
                    "cec_total_count": 0,
                    "correctness_backed_rows": 0,
                    "average_and_improve_pct": None,
                    "total_and_delta_candidate_minus_baseline": None,
                    "scalar_and_reward": None,
                    "improved_benchmark_count": 0,
                    "regressed_benchmark_count": 0,
                    "unchanged_benchmark_count": 0,
                    "min_average_and_improve_pct": 3.0,
                    "min_total_and_reduction": 10,
                    "min_improved_benchmarks": 1,
                    "reason": f"{role} response violated its local schema.",
                    "next_action": f"Repair the exact {role} validation fields.",
                    "validation_evidence_type": "local_response_validation",
                    "validation_issues_markdown": issue,
                    "validation_evidence_source": feedback.resolve().relative_to(
                        self.repo.resolve()
                    ).as_posix(),
                    "validation_evidence_sha256": hashlib.sha256(
                        issue.encode("utf-8")
                    ).hexdigest(),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def _next_payload(self, role: str) -> dict[str, object]:
        branch = next(
            item for item in self.next_plan.branches if item.branch_role == role
        )
        return json.loads(branch.assignment_path.read_text(encoding="utf-8"))

    def test_portfolio_and_next_assignments_keep_exact_role_issues(self) -> None:
        by_role = {
            str(item["branch_role"]): item
            for item in self.portfolio_review["branches"]
        }
        self.assertIn(
            FLOW_SENTINEL,
            by_role["flow"]["validation_issues_markdown"],
        )
        self.assertIn(
            LOGIC_SENTINEL,
            by_role["logic"]["validation_issues_markdown"],
        )

        flow = self._next_payload("flow")
        logic = self._next_payload("logic")
        flow_feedback = flow["previous_role_validation_feedback"]
        logic_feedback = logic["previous_role_validation_feedback"]
        self.assertEqual(flow_feedback["branch_role"], "flow")
        self.assertEqual(logic_feedback["branch_role"], "logic")
        self.assertEqual(
            logic_feedback["evidence_type"],
            "local_response_validation",
        )
        self.assertIn(FLOW_SENTINEL, flow_feedback["issues_markdown"])
        self.assertNotIn(LOGIC_SENTINEL, flow_feedback["issues_markdown"])
        self.assertIn(LOGIC_SENTINEL, logic_feedback["issues_markdown"])
        self.assertNotIn(FLOW_SENTINEL, logic_feedback["issues_markdown"])
        self.assertTrue(
            any(
                path.endswith("feedback/logic_candidate_001.md")
                for path in logic["recent_evidence"]
            )
        )

    def test_review_recovers_exact_issues_after_shared_feedback_is_lost(self) -> None:
        branch = next(
            item for item in self.current_plan.branches
            if item.branch_role == "logic"
        )
        context = CycleContext.from_assignment_file(self.repo, branch.assignment_path)
        context.artifact_paths().feedback.unlink()

        payload = validation_feedback_payload(context)

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertIn(LOGIC_SENTINEL, payload["issues_markdown"])
        self.assertEqual(
            payload["evidence_type"], "local_response_validation"
        )

    def test_planning_prompt_has_untruncated_role_tagged_issues(self) -> None:
        payloads = {
            role: self._next_payload(role) for role in ("flow", "logic")
        }
        locked = {
            "repo_root": str(self.repo),
            "cycle_id": "cycle_006",
            "previous_cycle_id": "cycle_005",
            "branches": payloads,
        }
        context = PlanningAgent._planner_context(locked)
        agent = PlanningAgent(
            context=context,
            model_client=None,  # type: ignore[arg-type]
        )
        evidence = dict(context.read_evidence_text())
        combined = "\n\n".join(
            f"## {name}\n{text}" for name, text in evidence.items()
        )
        # This demonstrates the old generic channel loses a middle sentinel.
        self.assertNotIn(
            LOGIC_SENTINEL,
            compact_text_block("compile", combined, max_chars=4000),
        )
        evidence.update(
            {
                "compile_or_build": combined,
                "cec_or_correctness": combined,
                "qor_or_metrics": combined,
            }
        )
        with patch(
            "scripts.agents.self_evolved_abc.planning_agent."
            "build_repository_context",
            return_value=_ready_prior(),
        ):
            prompt = agent.build_model_invocation(evidence).user_prompt

        exact = prompt.split(
            "Exact role-tagged local validation issues", 1
        )[1].split("Compile and smoke feedback", 1)[0]
        self.assertIn("FLOW branch exact validation issues", exact)
        self.assertIn("LOGIC branch exact validation issues", exact)
        self.assertIn("evidence_type: local_response_validation", exact)
        self.assertIn(FLOW_SENTINEL, exact)
        self.assertIn(LOGIC_SENTINEL, exact)
        self.assertLess(exact.index("FLOW branch"), exact.index("LOGIC branch"))

    def test_next_logic_coding_prompt_receives_only_logic_exact_issues(self) -> None:
        branch = next(
            item for item in self.next_plan.branches if item.branch_role == "logic"
        )
        context = CycleContext.from_assignment_file(self.repo, branch.assignment_path)
        agent = LogicMinimizationAgent(
            context=context,
            model_client=None,  # type: ignore[arg-type]
        )
        prior = _ready_prior()
        with patch(
            "scripts.agents.self_evolved_abc.coding_agents.flow_agent."
            "build_repository_context",
            return_value=prior,
        ), patch.object(
            LogicMinimizationAgent,
            "repository_context_bundle",
            return_value=prior,
        ):
            prompt = agent.build_model_invocation(
                context.read_evidence_text()
            ).user_prompt

        exact = prompt.split(
            "Exact validation issues from this same role's previous cycle", 1
        )[1].split("## Target Metrics", 1)[0]
        self.assertIn("LOGIC branch exact validation issues", exact)
        self.assertIn(LOGIC_SENTINEL, exact)
        self.assertNotIn(FLOW_SENTINEL, exact)
        self.assertIn("logic_minimization_agent", exact)
        self.assertIn("logic_candidate_001", exact)


if __name__ == "__main__":
    unittest.main()
