#!/usr/bin/env python3
"""Focused contracts and adversarial tests for LogicMinimizationAgent."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.agents.self_evolved_abc.cycle_context import CycleContext
from scripts.agents.self_evolved_abc.logic.assignment import (
    normalize_logic_assignment_scope,
)
from scripts.agents.self_evolved_abc.logic.contracts import (
    LOGIC_ABCI_ROOT,
    LOGIC_AGENT_NAME,
    LOGIC_EVALUATION_FLOW_COMMANDS,
    LOGIC_PAPER_ROLE,
    logic_touchpoints_for,
    normalize_logic_target_command,
)
from scripts.agents.self_evolved_abc.logic.validation import (
    extract_logic_diff_sections,
    logic_response_json_schema,
    validate_logic_agent_response,
    validate_logic_assignment_contract,
)


TARGET = f"{LOGIC_ABCI_ROOT}/abcRewrite.c"


def _assignment(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "agent_name": LOGIC_AGENT_NAME,
        "paper_role": LOGIC_PAPER_ROLE,
        "cycle_id": "cycle_001",
        "previous_cycle_id": "cycle_000",
        "candidate_id": "logic_candidate_001",
        "artifact_layout": "candidate_scoped_v2",
        "target_command": "rewrite",
        "benchmark_scope": ["benchmarks/a.blif", "benchmarks/b.blif"],
        "evaluation_benchmark_scope": [
            "benchmarks/a.blif",
            "benchmarks/b.blif",
        ],
        "evaluation_flow_commands": list(LOGIC_EVALUATION_FLOW_COMMANDS),
    }
    payload.update(updates)
    return normalize_logic_assignment_scope(payload)


def _diff(target: str = TARGET, changed: str = "return fUseZeros ? 1 : 0;") -> str:
    return (
        f"diff --git a/{target} b/{target}\n"
        f"--- a/{target}\n"
        f"+++ b/{target}\n"
        "@@ -1,4 +1,4 @@\n"
        " int Abc_CommandRewrite( int fUseZeros )\n"
        " {\n"
        "-    return 0;\n"
        f"+    {changed}\n"
        " }\n"
    )


def _reply() -> dict[str, object]:
    return {
        "rationale": "Trace Abc_CommandRewrite and test one bounded rewrite decision.",
        "candidate_kind": "source_patch_diff",
        "candidate_steps": [
            "Trace the rewrite wrapper to one reached local decision.",
            "Change one deterministic tie-break and preserve the command interface.",
        ],
        "source_design": "One conservative ABCI rewrite decision.",
        "expected_effect": "Reduce AIG nodes without increasing depth.",
        "entry_points": [f"Abc_CommandRewrite in {TARGET}"],
        "invariants": [
            "Preserve functional equivalence of the combinational Boolean function.",
            "No retiming is allowed.",
            "No sequential latch or register state changes are allowed.",
        ],
        "risk_hotspots": ["The rewrite gain tie-break may be behavior-neutral."],
        "files_to_write": [TARGET],
        "compatibility_notes": {
            "command_interface": "unchanged",
            "build_system": "unchanged",
        },
        "source_patch": {
            "patch_format": "unified_diff",
            "target_scope": LOGIC_ABCI_ROOT,
            "apply_strategy": "isolated_workspace",
            "diff": _diff(),
        },
        "validation_plan": [
            "Compile the candidate binary with make and require build success.",
            "Run CEC formal equivalence for every benchmark design; reject on mismatch or counterexample.",
            "After CEC, record QoR AIG node count and depth for the full scope.",
        ],
        "risks": ["A small local decision may not change final QoR."],
        "rollback_plan": "Discard the isolated workspace and keep the baseline.",
        "rule_updates": ["Keep Logic edits within existing ABCI sources."],
        "decision": "PROPOSE_CANDIDATE",
    }


class LogicMinimizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        source = self.repo / TARGET
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(
            "int Abc_CommandRewrite( int fUseZeros )\n{\n    return 0;\n}\n",
            encoding="utf-8",
        )
        self.context = CycleContext(self.repo, _assignment())

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _issues(self, payload: dict[str, object]) -> str:
        result = validate_logic_agent_response(payload, self.context)
        return "\n".join(f"{issue.field}: {issue.message}" for issue in result.issues)

    def test_01_role_identity(self) -> None:
        self.assertEqual(self.context.agent_name, LOGIC_AGENT_NAME)
        self.assertEqual(self.context.paper_role, LOGIC_PAPER_ROLE)

    def test_02_empty_target_defaults_to_rewrite(self) -> None:
        self.assertEqual(normalize_logic_target_command(""), "rewrite")

    def test_03_target_aliases_are_normalized(self) -> None:
        self.assertEqual(normalize_logic_target_command("refactoring -z"), "refactor")
        self.assertEqual(normalize_logic_target_command("resubstitution"), "resub")

    def test_04_unknown_target_fails_to_safe_rewrite(self) -> None:
        self.assertEqual(normalize_logic_target_command("mapper"), "rewrite")

    def test_05_touchpoints_stay_in_abci(self) -> None:
        self.assertTrue(logic_touchpoints_for("rewrite"))
        self.assertTrue(
            all(path.startswith(LOGIC_ABCI_ROOT + "/") for path in logic_touchpoints_for("rewrite"))
        )

    def test_06_assignment_owns_exactly_abci(self) -> None:
        self.assertEqual(
            self.context.assignment["source_patch_allowed_roots"],
            [LOGIC_ABCI_ROOT],
        )

    def test_07_assignment_forbids_new_files_and_build_metadata(self) -> None:
        self.assertFalse(self.context.assignment["planner_approved_new_source_files"])
        self.assertFalse(self.context.assignment["planner_approved_build_metadata"])

    def test_08_candidate_scoped_artifacts_are_writable(self) -> None:
        allowed = self.context.assignment["allowed_to_edit"]
        self.assertTrue(any("logic_candidate_001" in path for path in allowed))
        self.assertNotIn("experiments/cycle_001/impl_compare", allowed)

    def test_09_frozen_evaluation_contract_wins(self) -> None:
        commands = ["strash", "rewrite -z", "print_stats", "rewrite -z"]
        assignment = _assignment(
            evaluation_contract={"flow_commands": commands},
            evaluation_flow_commands=["balance"],
        )
        self.assertEqual(assignment["evaluation_flow_commands"], commands)

    def test_10_schema_is_logic_specific(self) -> None:
        schema = logic_response_json_schema()
        kinds = schema["properties"]["candidate_kind"]["enum"]
        self.assertEqual(kinds, ["source_patch_diff", "diagnostic_only"])

    def test_11_valid_candidate_passes(self) -> None:
        result = validate_logic_agent_response(_reply(), self.context)
        self.assertTrue(result.ok, self._issues(_reply()))

    def test_12_assignment_role_drift_is_rejected(self) -> None:
        payload = dict(self.context.assignment)
        payload["agent_name"] = "flow_agent"
        issues = validate_logic_assignment_contract(CycleContext(self.repo, payload))
        self.assertTrue(any(issue.field == "agent_name" for issue in issues))

    def test_13_assignment_cross_domain_root_is_rejected(self) -> None:
        payload = dict(self.context.assignment)
        payload["source_patch_allowed_roots"] = [
            LOGIC_ABCI_ROOT,
            "third_party/FlowTune/src/src/opt",
        ]
        issues = validate_logic_assignment_contract(CycleContext(self.repo, payload))
        self.assertTrue(any(issue.field == "source_patch_allowed_roots" for issue in issues))

    def test_14_unreachable_target_is_rejected(self) -> None:
        payload = dict(self.context.assignment)
        payload["target_command"] = "resub"
        payload["evaluation_flow_commands"] = ["strash", "rewrite", "print_stats"]
        issues = validate_logic_assignment_contract(CycleContext(self.repo, payload))
        self.assertTrue(any("does not reach" in issue.message for issue in issues))

    def test_15_path_escape_is_rejected(self) -> None:
        payload = _reply()
        payload["source_patch"]["diff"] = _diff("../escape.c")  # type: ignore[index]
        self.assertIn("escapes repository", self._issues(payload))

    def test_16_non_abci_target_is_rejected(self) -> None:
        payload = _reply()
        target = "third_party/FlowTune/src/src/opt/rwr/rwrEva.c"
        payload["files_to_write"] = [target]
        payload["source_patch"]["diff"] = _diff(target)  # type: ignore[index]
        self.assertIn("outside", self._issues(payload))

    def test_17_new_file_diff_is_rejected(self) -> None:
        payload = _reply()
        payload["source_patch"]["diff"] = (  # type: ignore[index]
            f"diff --git a/{TARGET} b/{TARGET}\n--- /dev/null\n+++ b/{TARGET}\n"
            "@@ -0,0 +1 @@\n+int x;\n"
        )
        self.assertIn("create or delete", self._issues(payload))

    def test_18_header_source_only(self) -> None:
        payload = _reply()
        target = f"{LOGIC_ABCI_ROOT}/README.md"
        (self.repo / target).write_text("old\n", encoding="utf-8")
        payload["files_to_write"] = [target]
        payload["source_patch"]["diff"] = _diff(target)  # type: ignore[index]
        self.assertIn(".c/.h", self._issues(payload))

    def test_19_declared_files_must_match_diff(self) -> None:
        payload = _reply()
        payload["files_to_write"] = [f"{LOGIC_ABCI_ROOT}/abcResub.c"]
        self.assertIn("missing from files_to_write", self._issues(payload))

    def test_20_equivalence_invariant_is_required(self) -> None:
        payload = _reply()
        payload["invariants"] = ["No retiming", "No sequential latch changes"]
        self.assertIn("equivalence", self._issues(payload))

    def test_21_no_retiming_invariant_is_required(self) -> None:
        payload = _reply()
        payload["invariants"] = [
            "Preserve functional equivalence in the combinational network",
            "No sequential latch changes",
        ]
        self.assertIn("retiming", self._issues(payload))

    def test_22_compile_cec_qor_order_is_required(self) -> None:
        payload = _reply()
        payload["validation_plan"] = list(reversed(payload["validation_plan"]))  # type: ignore[arg-type]
        self.assertIn("ordered compile", self._issues(payload))

    def test_23_cec_must_cover_every_design(self) -> None:
        payload = _reply()
        payload["validation_plan"][1] = "Run CEC and reject on mismatch."  # type: ignore[index]
        self.assertIn("every benchmark", self._issues(payload))

    def test_24_qor_must_record_nodes_and_depth(self) -> None:
        payload = _reply()
        payload["validation_plan"][2] = "After CEC, record QoR runtime."  # type: ignore[index]
        self.assertIn("node count and depth", self._issues(payload))

    def test_25_sequential_changes_are_rejected(self) -> None:
        payload = _reply()
        payload["source_patch"]["diff"] = _diff(changed="return Abc_NtkRetime( pNtk );")  # type: ignore[index]
        self.assertIn("sequential", self._issues(payload))

    def test_26_nondeterministic_changes_are_rejected(self) -> None:
        payload = _reply()
        payload["source_patch"]["diff"] = _diff(changed="return rand() & 1;")  # type: ignore[index]
        self.assertIn("nondeterministic", self._issues(payload))

    def test_27_benchmark_hardcoding_is_rejected(self) -> None:
        payload = _reply()
        payload["source_patch"]["diff"] = _diff(changed='return !strcmp( pName, "a" );')  # type: ignore[index]
        self.assertIn("benchmark", self._issues(payload))

    def test_28_diff_parser_requires_git_header(self) -> None:
        sections, issues = extract_logic_diff_sections(
            f"--- a/{TARGET}\n+++ b/{TARGET}\n@@ -1 +1 @@\n-x\n+y\n"
        )
        self.assertFalse(sections)
        self.assertTrue(issues)

    def test_29_source_patch_payload_has_no_extra_fields(self) -> None:
        payload = _reply()
        payload["source_patch"]["benchmark_override"] = "a"  # type: ignore[index]
        self.assertIn("unexpected", self._issues(payload))


if __name__ == "__main__":
    unittest.main(verbosity=2)
