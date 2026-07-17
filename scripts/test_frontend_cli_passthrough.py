#!/usr/bin/env python3
"""Regression checks for explicit Yosys CLI forwarding."""

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
from scripts.agents.self_evolved_abc.flow import batch_search
from scripts.agents.self_evolved_abc.workflow import candidate_pipeline
from scripts.agents.self_evolved_abc.workflow.artifacts import review_decision_path


class FrontendCliPassthroughTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temporary.name).resolve()
        self.assignment = (
            self.repo_root
            / "experiments/cycle_001/agents/assignments/flow_candidate_001.json"
        )
        self.assignment.parent.mkdir(parents=True)
        self.assignment.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "cycle_id": "cycle_001",
                    "previous_cycle_id": "cycle_000",
                    "candidate_id": "flow_candidate_001",
                    "agent_name": "flow_agent",
                    "paper_role": "Flow Agent",
                    "artifact_layout": "candidate_scoped_v2",
                    "benchmark_scope": [],
                    "evaluation_benchmark_scope": [],
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_candidate_pipeline_forwards_explicit_frontend_options(self) -> None:
        context = CycleContext.from_assignment_file(self.repo_root, self.assignment)
        review_path = review_decision_path(context)
        seen: list[tuple[str, ...]] = []

        def fake_run(command: object, **_: object) -> SimpleNamespace:
            values = tuple(str(value) for value in command)  # type: ignore[arg-type]
            seen.append(values)
            if "scripts.agents.self_evolved_abc.flow.review" in values:
                review_path.parent.mkdir(parents=True, exist_ok=True)
                review_path.write_text("{}\n", encoding="utf-8")
            return SimpleNamespace(returncode=0)

        result = candidate_pipeline.AgentRunResult(
            succeeded=True,
            decision="PROPOSE_CANDIDATE",
            failure_kind="",
            attempts=1,
        )
        with patch.object(
            candidate_pipeline,
            "_run_agent_with_retry",
            return_value=result,
        ), patch.object(candidate_pipeline.subprocess, "run", side_effect=fake_run):
            return_code = candidate_pipeline.main(
                (
                    "--repo-root",
                    str(self.repo_root),
                    "--assignment",
                    str(self.assignment),
                    "--skip-next-cycle",
                    "--yosys-bin",
                    "/opt/yosys/bin/yosys",
                    "--frontend-timeout-seconds",
                    "600",
                )
            )

        self.assertEqual(return_code, 0)
        compare = next(
            command
            for command in seen
            if "scripts.agents.self_evolved_abc.flow.implementation_compare"
            in command
        )
        self.assertEqual(
            compare[compare.index("--yosys-bin") + 1], "/opt/yosys/bin/yosys"
        )
        self.assertEqual(
            compare[compare.index("--frontend-timeout-seconds") + 1], "600"
        )

    def test_batch_search_forwards_explicit_frontend_options(self) -> None:
        assignment = self.repo_root / "experiments/probe_001/agents/assignments/candidate_001.json"
        manifest = {
            "items": [
                {
                    "cycle_id": "probe_001",
                    "variant_id": "rewrite_no_level_update",
                    "assignment_path": str(assignment.relative_to(self.repo_root)),
                }
            ]
        }
        seen: list[tuple[str, ...]] = []

        with patch.object(batch_search, "write_flow_recipe_from_assignment"), patch.object(
            batch_search,
            "run_command",
            side_effect=lambda _root, command: seen.append(tuple(command)),
        ):
            batch_search.run_batch(
                repo_root=self.repo_root,
                manifest=manifest,
                build_candidate_binary=True,
                build_jobs=4,
                build_timeout_seconds=900.0,
                timeout_seconds=300.0,
                cec_timeout_seconds=300.0,
                yosys_bin="/opt/yosys/bin/yosys",
                frontend_timeout_seconds=600.0,
            )

        compare = next(
            command
            for command in seen
            if "scripts.agents.self_evolved_abc.flow.implementation_compare"
            in command
        )
        self.assertEqual(
            compare[compare.index("--yosys-bin") + 1], "/opt/yosys/bin/yosys"
        )
        self.assertEqual(
            compare[compare.index("--frontend-timeout-seconds") + 1], "600"
        )

    def test_omitted_options_leave_downstream_environment_fallback_intact(self) -> None:
        candidate = candidate_pipeline.parse_args(
            ("--assignment", "candidate.json")
        )
        batch = batch_search.parse_args(("--base-assignment", "candidate.json"))
        self.assertIsNone(candidate.yosys_bin)
        self.assertIsNone(candidate.frontend_timeout_seconds)
        self.assertIsNone(batch.yosys_bin)
        self.assertIsNone(batch.frontend_timeout_seconds)


if __name__ == "__main__":
    unittest.main()
