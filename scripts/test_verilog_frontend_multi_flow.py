#!/usr/bin/env python3
"""Focused regression checks for Verilog frontend and multi-flow aggregation."""

from __future__ import annotations

import json
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
from scripts.agents.self_evolved_abc.flow.implementation_compare import (
    collect_impl_result,
    render_ftune_mab_command,
)
from scripts.agents.self_evolved_abc.flow.asap7_qor import (
    build_asap7_qor_rows,
    collect_asap7_qor_result,
    default_asap7_qor_config,
    render_asap7_sta_script,
    write_asap7_qor_summary,
)
from scripts.agents.self_evolved_abc.flow.verilog_frontend import (
    FrontendResult,
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
                "ftune_mab_aig_nodes",
            ],
        )
        normalized = normalize_evaluation_flows(flows)
        self.assertEqual(normalized[-1].kind, "ftune_mab")
        self.assertEqual(normalized[-1].ftune_option("iterations"), 1)
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
            _comparison_row(benchmark=benchmark, flow_id=flow_ids[8], and_delta=0),
        ]
        for row in rows[5:7] + rows[8:]:
            row["candidate_aig_depth"] = "10"
            row["depth_delta_candidate_minus_baseline"] = "0"
        aggregate, votes, summary = aggregate_flow_comparison_rows(
            rows,
            flow_ids=flow_ids,
            aggregation=default_flow_aggregation(),
        )
        self.assertEqual(aggregate[0]["flow_vote_outcome"], "candidate_wins")
        self.assertEqual(aggregate[0]["and_delta_candidate_minus_baseline"], -1)
        self.assertFalse(aggregate[0]["all_flows_nonregressing"])
        self.assertFalse(aggregate[0]["safe_for_promotion"])
        self.assertEqual(votes[0]["candidate_vote_count"], 5)
        self.assertEqual(votes[0]["vote_quorum"], 5)
        self.assertEqual(summary["candidate_vote_wins"], 1)

    def test_ftune_mab_replays_the_selected_flow_in_an_isolated_sandbox(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            input_path = root / "benchmarks" / "tiny.blif"
            input_path.parent.mkdir(parents=True)
            input_path.write_text(
                ".model tiny\n.inputs a\n.outputs y\n.names a y\n1 1\n.end\n",
                encoding="utf-8",
            )
            fake_abc = root / "fake_abc.sh"
            fake_abc.write_text(
                "#!/bin/sh\n"
                "script=\"$2\"\n"
                "case \"$script\" in\n"
                "  *\"ftune -d \"*)\n"
                "    design=$(printf '%s' \"$script\" | sed -n 's/.*ftune -d \\([^ ]*\\).*/\\1/p')\n"
                "    printf 'read %s;strash;rewrite;ifraig;dch -f;strash;print_stats\\n' \"$design\" > \"$design.script\"\n"
                "    printf 'ftune completed\\n'\n"
                "    ;;\n"
                "  *\"write_aiger \"*)\n"
                "    output=$(printf '%s' \"$script\" | sed -n 's/.*write_aiger \\([^;]*\\).*/\\1/p')\n"
                "    mkdir -p \"$(dirname \"$output\")\"\n"
                "    printf 'aig' > \"$output\"\n"
                "    printf 'tiny : i/o = 1/1 lat = 0 and = 42 lev = 7\\n'\n"
                "    ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            fake_abc.chmod(0o755)
            context = CycleContext(
                repo_root=root,
                assignment={"cycle_id": "cycle_001", "candidate_id": "flow_candidate_001"},
            )
            frontend = FrontendResult(
                benchmark=Path("benchmarks/tiny.blif"),
                input_path=input_path,
                frontend_kind="abc_native",
                frontend_status="abc_native",
                command="",
                log_path=None,
                frontend_exit_code=0,
                runtime_seconds=0.0,
                skipped_reason="",
            )
            flow = normalize_evaluation_flows(default_evaluation_flows())[-1]
            self.assertEqual(
                render_ftune_mab_command(flow, "ftune_input.blif"),
                "ftune -d ftune_input.blif -r 1 -t 0 -p 1 -i 1 -s 1",
            )
            result = collect_impl_result(
                context=context,
                output_root=root / "experiments/cycle_001/candidates/flow_candidate_001/impl_compare",
                benchmark=Path("benchmarks/tiny.blif"),
                frontend=frontend,
                flow=flow,
                candidate_flow=root / "unused.abc",
                implementation_label="baseline_unmodified",
                abc_bin=str(fake_abc),
                timeout_seconds=5.0,
                from_existing_logs=False,
            )
            self.assertEqual(result.aig_nodes, 42)
            self.assertEqual(result.aig_depth, 7)
            self.assertTrue(result.aig_path.is_file())
            shim = (
                root
                / "experiments/cycle_001/candidates/flow_candidate_001/impl_compare"
                / "baseline_unmodified/ftune/ftune_mab_aig_nodes"
                / benchmark_key(Path("benchmarks/tiny.blif"))
                / "bin/abc"
            )
            self.assertIn(str(fake_abc), shim.read_text(encoding="utf-8"))

    def test_asap7_mapping_collects_post_sizing_area_and_sta_delay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source_aig = root / "outputs/tiny.aig"
            source_aig.parent.mkdir(parents=True)
            source_aig.write_text("aig", encoding="utf-8")
            library = root / "libraries/asap7.lib"
            library.parent.mkdir(parents=True)
            library.write_text("library (ASAP7) {}\n", encoding="utf-8")
            fake_abc = root / "fake_abc.sh"
            fake_abc.write_text(
                "#!/bin/sh\n"
                "printf 'Area = 123.5  (100%%)  Delay = 45.25 ps\\n'\n",
                encoding="utf-8",
            )
            fake_abc.chmod(0o755)
            context = CycleContext(
                repo_root=root,
                assignment={"cycle_id": "cycle_001", "candidate_id": "flow_candidate_001"},
            )
            config = default_asap7_qor_config()
            self.assertEqual(config["clock_period_ps"], 1000.0)
            self.assertEqual(config["clock_period_source"], "project_reference_1ghz")
            config.update(
                {
                    "library_path": "libraries/asap7.lib",
                    "clock_period_ps": 100.0,
                    "clock_period_source": "test_fixture",
                }
            )
            script = render_asap7_sta_script(
                aig_path=Path("outputs/tiny.aig"),
                library_path=Path("libraries/asap7.lib"),
            )
            self.assertIn("read_lib libraries/asap7.lib", script)
            self.assertIn("map; topo; upsize; dnsize; topo; stime", script)
            baseline = collect_asap7_qor_result(
                context=context,
                output_root=root / "impl_compare",
                benchmark=Path("benchmarks/tiny.blif"),
                flow_id="resyn",
                implementation_label="baseline_unmodified",
                source_aig=source_aig,
                abc_bin=str(fake_abc),
                timeout_seconds=5.0,
                config=config,
                from_existing_logs=False,
            )
            candidate = collect_asap7_qor_result(
                context=context,
                output_root=root / "impl_compare",
                benchmark=Path("benchmarks/tiny.blif"),
                flow_id="resyn",
                implementation_label="candidate_modified",
                source_aig=source_aig,
                abc_bin=str(fake_abc),
                timeout_seconds=5.0,
                config=config,
                from_existing_logs=False,
            )
            self.assertEqual(baseline.status, "asap7_pass")
            self.assertEqual(baseline.area, 123.5)
            self.assertEqual(baseline.sta_delay_ps, 45.25)
            self.assertEqual(baseline.worst_slack_ps, 54.75)
            rows = build_asap7_qor_rows(
                [baseline], [candidate], ["cec_pass"], repo_root=root
            )
            self.assertTrue(rows[0]["correctness_backed"])
            self.assertEqual(rows[0]["adp_improve_pct"], "0")
            summary_path = write_asap7_qor_summary(
                root / "impl_compare",
                rows,
                config=config,
                static_flow_ids=["resyn"],
            )
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertTrue(summary["table_comparable"])
            self.assertEqual(summary["timing_metric"], "worst_slack_ps")
            self.assertEqual(summary["clock_period_source"], "test_fixture")


if __name__ == "__main__":
    unittest.main()
