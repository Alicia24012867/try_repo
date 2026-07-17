#!/usr/bin/env python3
"""Focused regression checks for Verilog frontend and multi-flow aggregation."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from scripts.agents.self_evolved_abc.benchmarks import (
    BENCHMARK_FRONTEND_YOSYS_VERILOG,
    benchmark_frontend_kind,
    expand_benchmark_suite,
    with_abc_native_evaluation_scope,
)
from scripts.agents.self_evolved_abc.flow.multi_flow import (
    aggregate_flow_comparison_rows,
    default_evaluation_flows,
    default_flow_aggregation,
    normalize_evaluation_flows,
)
from scripts.agents.self_evolved_abc.flow.verilog_frontend import (
    benchmark_key,
    prepare_benchmark_frontend,
    render_yosys_verilog_to_blif_script,
)
from scripts.agents.self_evolved_abc.cycle_context import CycleContext


def _comparison_row(*, benchmark: str, flow_id: str, and_delta: int) -> dict[str, object]:
    return {
        "benchmark": benchmark,
        "flow_id": flow_id,
        "frontend_kind": "yosys_verilog",
        "frontend_status": "yosys_pass",
        "cec_status": "cec_pass",
        "correctness_backed": True,
        "baseline_aig_nodes": "100",
        "candidate_aig_nodes": str(100 + and_delta),
        "and_delta_candidate_minus_baseline": str(and_delta),
        "and_improve_pct": f"{-and_delta:.1f}",
        "baseline_aig_depth": "10",
        "candidate_aig_depth": "9" if and_delta <= 0 else "11",
        "depth_delta_candidate_minus_baseline": "-1" if and_delta <= 0 else "1",
        "baseline_runtime_seconds": "1.0",
        "candidate_runtime_seconds": "1.1",
        "runtime_delta_seconds": "0.1",
        "skipped_reason": "",
    }


class VerilogFrontendAndMultiFlowTests(unittest.TestCase):
    def test_large_70_is_fully_frontend_enabled(self) -> None:
        scope = expand_benchmark_suite(PROJECT_ROOT, "large_70")
        payload = with_abc_native_evaluation_scope({"benchmark_scope": scope})
        self.assertEqual(len(scope), 70)
        self.assertEqual(payload["benchmark_frontend"], BENCHMARK_FRONTEND_YOSYS_VERILOG)
        self.assertEqual(len(payload["evaluation_benchmark_scope"]), 70)
        self.assertEqual(payload["unsupported_benchmark_scope"], [])
        self.assertEqual(benchmark_frontend_kind("benchmarks/itc99/b11.v"), "yosys_verilog")
        self.assertEqual(benchmark_frontend_kind("benchmarks/epfl/epfl_adder.blif"), "abc_native")

    def test_yosys_frontend_is_deterministic_and_path_safe(self) -> None:
        script = render_yosys_verilog_to_blif_script(
            source_path=Path("benchmarks/itc99/b11.v"),
            output_path=Path("experiments/cycle_001/frontend/b11.blif"),
        )
        self.assertIn("read_verilog benchmarks/itc99/b11.v", script)
        self.assertIn("hierarchy -auto-top", script)
        self.assertIn("proc", script)
        self.assertIn("write_blif experiments/cycle_001/frontend/b11.blif", script)
        self.assertNotEqual(
            benchmark_key(Path("benchmarks/itc99/b11.v")),
            benchmark_key(Path("benchmarks/iscas99/b11.v")),
        )

    def test_frontend_runs_once_and_reuses_logged_blif(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            benchmark = root / "benchmarks" / "tiny.v"
            benchmark.parent.mkdir(parents=True)
            benchmark.write_text("module tiny(input a, output y); assign y = a; endmodule\n")
            fake_yosys = root / "fake_yosys.sh"
            fake_yosys.write_text(
                "#!/bin/sh\n"
                "script=\"$3\"\n"
                "output=$(printf '%s' \"$script\" | sed -n 's/.*write_blif \\([^;]*\\).*/\\1/p')\n"
                "mkdir -p \"$(dirname \"$output\")\"\n"
                "printf '.model tiny\\n.inputs a\\n.outputs y\\n.names a y\\n1 1\\n.end\\n' > \"$output\"\n",
                encoding="utf-8",
            )
            fake_yosys.chmod(0o755)
            context = CycleContext(
                repo_root=root,
                assignment={"cycle_id": "cycle_001", "candidate_id": "flow_candidate_001"},
            )
            output_root = root / "experiments" / "cycle_001" / "candidates" / "flow_candidate_001" / "impl_compare"
            result = prepare_benchmark_frontend(
                context=context,
                output_root=output_root,
                benchmark=Path("benchmarks/tiny.v"),
                yosys_bin=str(fake_yosys),
                timeout_seconds=5.0,
                from_existing_logs=False,
            )
            self.assertTrue(result.ready)
            self.assertEqual(result.frontend_status, "yosys_pass")
            self.assertIsNotNone(result.input_path)
            reused = prepare_benchmark_frontend(
                context=context,
                output_root=output_root,
                benchmark=Path("benchmarks/tiny.v"),
                yosys_bin=str(fake_yosys),
                timeout_seconds=5.0,
                from_existing_logs=True,
            )
            self.assertTrue(reused.ready)
            self.assertEqual(reused.input_path, result.input_path)

    def test_multi_flow_median_vote_and_guard(self) -> None:
        flows = default_evaluation_flows()
        flow_ids = [str(flow["flow_id"]) for flow in flows]
        self.assertEqual(
            flow_ids,
            [
                "resyn",
                "resyn2",
                "resyn2a",
                "resyn3",
                "compress",
                "compress2",
                "resyn2rs",
                "compress2rs",
            ],
        )
        normalize_evaluation_flows(flows)
        benchmark = "benchmarks/itc99/b11.v"
        rows = [
            _comparison_row(benchmark=benchmark, flow_id=flow_ids[0], and_delta=-8),
            _comparison_row(benchmark=benchmark, flow_id=flow_ids[1], and_delta=-6),
            _comparison_row(benchmark=benchmark, flow_id=flow_ids[2], and_delta=-4),
            _comparison_row(benchmark=benchmark, flow_id=flow_ids[3], and_delta=-2),
            _comparison_row(benchmark=benchmark, flow_id=flow_ids[4], and_delta=-1),
            _comparison_row(benchmark=benchmark, flow_id=flow_ids[5], and_delta=0),
            _comparison_row(benchmark=benchmark, flow_id=flow_ids[6], and_delta=0),
            _comparison_row(benchmark=benchmark, flow_id=flow_ids[7], and_delta=3),
        ]
        for row in rows[5:7]:
            row["candidate_aig_depth"] = "10"
            row["depth_delta_candidate_minus_baseline"] = "0"
        aggregate, votes, summary = aggregate_flow_comparison_rows(
            rows,
            flow_ids=flow_ids,
            aggregation=default_flow_aggregation(),
        )
        self.assertEqual(aggregate[0]["flow_vote_outcome"], "candidate_wins")
        self.assertEqual(aggregate[0]["and_delta_candidate_minus_baseline"], -2)
        self.assertFalse(aggregate[0]["all_flows_nonregressing"])
        self.assertFalse(aggregate[0]["safe_for_promotion"])
        self.assertEqual(votes[0]["candidate_vote_count"], 5)
        self.assertEqual(votes[0]["vote_quorum"], 5)
        self.assertEqual(summary["candidate_vote_wins"], 1)


if __name__ == "__main__":
    unittest.main()
