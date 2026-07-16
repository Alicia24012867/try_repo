#!/usr/bin/env python3
"""Regression tests for prompt/runner frozen-source alignment."""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from scripts.agents.self_evolved_abc.coding_agents.logic_minimization_agent import (
    LogicMinimizationAgent,
)
from scripts.agents.self_evolved_abc.coding_agents.flow_agent import (
    KEY_SOURCE_LIMIT,
    FlowAgent,
)
from scripts.agents.self_evolved_abc.cycle_context import CycleContext
from scripts.agents.self_evolved_abc.flow.lineage import (
    resolve_base_source_root,
    source_context_path,
)
from scripts.agents.self_evolved_abc.flow.paths import impl_candidate_dir
from scripts.agents.self_evolved_abc.flow.source_patch_runner import (
    reset_patch_workspace,
)
from scripts.agents.self_evolved_abc.logic.contracts import LOGIC_ABCI_ROOT


LOGICAL_SOURCE = Path(LOGIC_ABCI_ROOT) / "abc.c"


class FrozenBaselineSourceContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name)
        self.live_source_root = self.repo / "third_party/FlowTune/src"
        self.frozen_source_relative = Path(
            "experiments/cycle_004/candidates/logic_candidate_001/"
            "impl_compare/candidate/workspace/third_party/FlowTune/src"
        )
        self.frozen_source_root = self.repo / self.frozen_source_relative
        self._write_source(
            self.live_source_root,
            "int live_repository_marker = 1;\n",
        )
        self._write_source(
            self.frozen_source_root,
            "int frozen_baseline_marker = 2;\n",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _write_source(root: Path, content: str) -> None:
        FrozenBaselineSourceContextTests._write_relative_source(
            root,
            "src/base/abci/abc.c",
            content,
        )

    @staticmethod
    def _write_relative_source(root: Path, relative: str, content: str) -> None:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _context(
        self,
        *,
        frozen_source: Path | None = None,
        base_source: Path | None = None,
        requested_files: tuple[str, ...] = (),
    ) -> CycleContext:
        frozen = frozen_source or self.frozen_source_relative
        base = base_source or frozen
        frozen_text = frozen.as_posix()
        return CycleContext(
            repo_root=self.repo,
            assignment={
                "cycle_id": "cycle_005",
                "candidate_id": "logic_candidate_001",
                "agent_name": "logic_minimization_agent",
                "paper_role": "Logic Minimization Agent",
                "artifact_layout": "candidate_scoped_v2",
                "source_patch_allowed_roots": [LOGIC_ABCI_ROOT],
                "target_command": "rewrite",
                "baseline_ref": {
                    "kind": "champion",
                    "cycle_id": "cycle_004",
                    "candidate_id": "logic_candidate_001",
                    "source_root": frozen_text,
                    "abc_bin": f"{frozen_text}/abc",
                },
                "base_source_root": base.as_posix(),
                "champion_source_root": frozen_text,
                "source_context_requested_files": list(requested_files),
            },
        )

    def test_logic_prompt_and_runner_use_the_same_frozen_source(self) -> None:
        context = self._context()
        agent = LogicMinimizationAgent(
            context=context,
            model_client=None,  # type: ignore[arg-type]
        )
        prompt_input_path = source_context_path(context, LOGICAL_SOURCE)
        prompt_input_bytes = prompt_input_path.read_bytes()

        prompt_source = agent._source_file_context()

        self.assertIn(prompt_input_bytes.decode("utf-8"), prompt_source)
        self.assertIn("frozen_baseline_marker", prompt_source)
        self.assertNotIn("live_repository_marker", prompt_source)
        self.assertIn(LOGICAL_SOURCE.as_posix(), prompt_source)
        self.assertNotIn(self.frozen_source_relative.as_posix(), prompt_source)
        self.assertNotIn(str(self.repo), prompt_source)

        workspace = impl_candidate_dir(context) / "workspace"
        reset_patch_workspace(
            context,
            workspace,
            (LOGICAL_SOURCE.as_posix(),),
            base_source_root=resolve_base_source_root(context),
        )
        copied = workspace / LOGICAL_SOURCE
        self.assertEqual(copied.read_bytes(), prompt_input_bytes)
        self.assertEqual(
            hashlib.sha256(copied.read_bytes()).hexdigest(),
            hashlib.sha256(prompt_input_bytes).hexdigest(),
        )

    def test_missing_frozen_source_never_falls_back_to_live_repository(self) -> None:
        missing = Path("experiments/cycle_004/missing/source")
        context = self._context(frozen_source=missing, base_source=missing)
        agent = LogicMinimizationAgent(
            context=context,
            model_client=None,  # type: ignore[arg-type]
        )

        with self.assertRaisesRegex(
            FileNotFoundError,
            "frozen baseline source root is missing",
        ):
            agent._source_file_context()

    def test_baseline_alias_drift_is_rejected(self) -> None:
        context = self._context(base_source=Path("third_party/FlowTune/src"))

        with self.assertRaisesRegex(
            ValueError,
            "diverges from frozen baseline_ref at base_source_root",
        ):
            resolve_base_source_root(context)

    def test_source_context_rejects_flowtune_path_traversal(self) -> None:
        context = self._context()

        with self.assertRaisesRegex(ValueError, "escapes the FlowTune source root"):
            source_context_path(
                context,
                Path("third_party/FlowTune/src/../../outside.c"),
            )

    def test_logic_retry_requested_file_is_prioritized_within_whitelist(self) -> None:
        requested = f"{LOGIC_ABCI_ROOT}/abcRequested.c"
        forbidden = "third_party/FlowTune/src/src/opt/forbidden.c"
        self._write_relative_source(
            self.frozen_source_root,
            "src/base/abci/abcRequested.c",
            "int requested_logic_context_marker = 3;\n",
        )
        self._write_relative_source(
            self.frozen_source_root,
            "src/opt/forbidden.c",
            "int forbidden_context_marker = 4;\n",
        )
        context = self._context(requested_files=(requested, forbidden))
        agent = LogicMinimizationAgent(
            context=context,
            model_client=None,  # type: ignore[arg-type]
        )

        prompt_source = agent._source_file_context()

        self.assertIn("requested_logic_context_marker", prompt_source)
        self.assertNotIn("forbidden_context_marker", prompt_source)
        self.assertLess(
            prompt_source.index(f"#### {requested}"),
            prompt_source.index(f"#### {LOGICAL_SOURCE.as_posix()}"),
        )

    def test_flow_requested_files_are_whitelisted_deduplicated_and_bounded(self) -> None:
        requested = "third_party/FlowTune/src/src/opt/custom.c"
        outside_scope = f"{LOGIC_ABCI_ROOT}/abc.c"
        context = self._context(requested_files=(outside_scope, requested, requested))
        agent = FlowAgent(
            context=context,
            model_client=None,  # type: ignore[arg-type]
        )
        all_files = [
            (requested, 10),
            *(
                (f"third_party/FlowTune/src/src/opt/file_{index}.c", index)
                for index in range(KEY_SOURCE_LIMIT + 3)
            ),
        ]

        selected = agent._select_key_source_files(all_files)

        self.assertEqual(selected[0], (requested, 10))
        self.assertNotIn(outside_scope, {path for path, _size in selected})
        self.assertEqual(
            sum(path == requested for path, _size in selected),
            1,
        )
        self.assertLessEqual(len(selected), KEY_SOURCE_LIMIT)


if __name__ == "__main__":
    unittest.main()
