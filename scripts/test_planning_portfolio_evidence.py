#!/usr/bin/env python3
"""Focused tests for Planning's paired-round evidence handoff."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from scripts.agents.self_evolved_abc.cycle_context import CycleContext
from scripts.agents.self_evolved_abc.flow.next_cycle import (
    _previous_evidence_paths,
)
from scripts.agents.self_evolved_abc.planning_agent import PlanningAgent


class PlanningPortfolioEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _planning_agent(self, **assignment_updates: object) -> PlanningAgent:
        assignment: dict[str, object] = {
            "cycle_id": "cycle_006",
            "previous_cycle_id": "cycle_005",
            "candidate_id": "planner_dispatch",
            "agent_name": "planning_agent",
            "paper_role": "Planning Agent",
            "artifact_layout": "candidate_scoped_v2",
        }
        assignment.update(assignment_updates)
        context = CycleContext(repo_root=self.repo, assignment=assignment)
        return PlanningAgent(context=context, model_client=None)  # type: ignore[arg-type]

    def test_no_champion_reports_both_portfolio_branches_in_role_order(self) -> None:
        path = (
            self.repo
            / "experiments/cycle_005/planning/portfolio_review.json"
        )
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "cycle_id": "cycle_005",
                    "round_status": "no_promotion",
                    "selected_candidate_id": "",
                    # Persisted order is intentionally reversed.  Planning's
                    # prompt must remain deterministic: Flow, then Logic.
                    "branches": [
                        {
                            "branch_role": "logic",
                            "candidate_id": "logic_candidate_001",
                            "decision": "REPAIR_VALIDATION",
                            "build_status": "missing",
                            "review_reason": "Logic response was invalid.",
                            "next_action": "Repair Logic JSON fields.",
                        },
                        {
                            "branch_role": "flow",
                            "candidate_id": "flow_candidate_001",
                            "decision": "REPAIR_QOR",
                            "build_status": "candidate_binary_build_passed",
                            "review_reason": "QoR regressed on two benchmarks.",
                            "next_action": "Narrow the Flow patch.",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

        rendered = self._planning_agent()._rejected_candidates_text("cycle_005")

        self.assertIn("round_status=no_promotion", rendered)
        self.assertIn("selected_candidate_id=none", rendered)
        self.assertLess(rendered.index("Flow branch"), rendered.index("Logic branch"))
        self.assertIn(
            "decision=REPAIR_QOR; "
            "build_status=candidate_binary_build_passed; "
            "reason=QoR regressed on two benchmarks.; "
            "next_action=Narrow the Flow patch.",
            rendered,
        )
        self.assertIn(
            "decision=REPAIR_VALIDATION; build_status=missing; "
            "reason=Logic response was invalid.; "
            "next_action=Repair Logic JSON fields.",
            rendered,
        )
        self.assertNotIn("planner_dispatch", rendered)

    def test_no_portfolio_and_no_champion_never_queries_planner_dispatch(self) -> None:
        forged = (
            self.repo
            / "experiments/cycle_005/candidates/planner_dispatch"
            / "impl_compare/comparison/review_decision.json"
        )
        forged.parent.mkdir(parents=True)
        forged.write_text(
            json.dumps(
                {
                    "decision": "REPAIR_VALIDATION",
                    "reason": "forged planner dispatch review",
                    "next_action": "wrong path",
                }
            ),
            encoding="utf-8",
        )

        rendered = self._planning_agent()._rejected_candidates_text("cycle_005")

        self.assertIn("No previous portfolio review", rendered)
        self.assertNotIn("forged planner dispatch review", rendered)

    def test_next_assignment_evidence_includes_portfolio_review(self) -> None:
        context = CycleContext(
            repo_root=self.repo,
            assignment={
                "cycle_id": "cycle_005",
                "candidate_id": "flow_candidate_001",
                "agent_name": "flow_agent",
                "paper_role": "Flow Agent",
                "artifact_layout": "candidate_scoped_v2",
            },
        )

        evidence = _previous_evidence_paths(context)

        self.assertEqual(
            evidence[0],
            "experiments/cycle_005/planning/portfolio_review.json",
        )
        self.assertEqual(len(evidence), len(set(evidence)))


if __name__ == "__main__":
    unittest.main()
