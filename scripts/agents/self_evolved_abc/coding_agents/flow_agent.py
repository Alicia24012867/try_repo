"""Flow Agent scaffold."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from scripts.agents.self_evolved_abc.coding_agents.base_coding_agent import CodingAgent
from scripts.agents.self_evolved_abc.flow.artifacts import (
    render_flow_validation_failure_artifacts,
)
from scripts.agents.self_evolved_abc.flow.contracts import (
    DEFAULT_EVAL_FLOW_COMMANDS,
    FLOW_SOURCE_TOUCHPOINTS,
    IMPL_CANDIDATE_LABEL,
)
from scripts.agents.self_evolved_abc.flow.lineage import source_context_path
from scripts.agents.self_evolved_abc.flow.materialization import (
    materialize_validated_flow_response,
)
from scripts.agents.self_evolved_abc.flow.promotion import (
    normalize_promotion_thresholds,
    threshold_prompt_text,
)
from scripts.agents.self_evolved_abc.model_client import ModelInvocation, ModelReply
from scripts.agents.self_evolved_abc.prompt_rendering import (
    compact_text_block,
    find_forbidden_secret_markers,
    find_unresolved_placeholders,
    load_template,
    render_template,
    summarize_csv,
    summarize_flow_scripts,
)
from scripts.agents.self_evolved_abc.repository_context import (
    build_repository_context,
)
from scripts.agents.self_evolved_abc.flow.validation import (
    flow_response_json_schema,
    validate_flow_agent_response,
)
from scripts.agents.self_evolved_abc.schemas import AgentArtifacts
from scripts.agents.self_evolved_abc.workflow.artifacts import (
    LEGACY_CYCLE_LAYOUT,
    implementation_root_for,
)


FLOW_SOURCE_CONTEXT_COMMON_KEY_SUFFIXES = (
    "third_party/FlowTune/src/src/base/abci/abcFxu.c",
    "third_party/FlowTune/src/src/opt/csw/cswCore.c",
    "third_party/FlowTune/src/src/opt/fxu/fxuSelect.c",
    "third_party/FlowTune/src/src/opt/fxu/fxu.c",
    "third_party/FlowTune/src/src/opt/fxu/fxu.h",
    "third_party/FlowTune/src/src/opt/fxu/fxuCreate.c",
)
FLOW_SOURCE_CONTEXT_KEY_SUFFIXES_BY_COMMAND = {
    "fx": (
        "third_party/FlowTune/src/src/base/abci/abcFx.c",
        "third_party/FlowTune/src/src/base/abci/abcFxu.c",
        "third_party/FlowTune/src/src/opt/fxu/fxu.c",
        "third_party/FlowTune/src/src/opt/fxu/fxuSelect.c",
        "third_party/FlowTune/src/src/opt/fxu/fxuCreate.c",
    ),
    "rewrite": (
        "third_party/FlowTune/src/src/base/abci/abcRewrite.c",
        "third_party/FlowTune/src/src/opt/rwr/rwrEva.c",
        "third_party/FlowTune/src/src/opt/rwr/rwrMan.c",
        "third_party/FlowTune/src/src/base/abci/abc.c",
    ),
    "resub": (
        "third_party/FlowTune/src/src/base/abci/abcResub.c",
        "third_party/FlowTune/src/src/opt/res/resCore.c",
        "third_party/FlowTune/src/src/opt/res/resWin.c",
        "third_party/FlowTune/src/src/base/abci/abc.c",
    ),
    "dc2": (
        "third_party/FlowTune/src/src/base/abci/abcDar.c",
        "third_party/FlowTune/src/src/opt/dar/darCore.c",
        "third_party/FlowTune/src/src/opt/dar/darScript.c",
        "third_party/FlowTune/src/src/base/abci/abc.c",
    ),
    "csweep": (
        "third_party/FlowTune/src/src/opt/csw/cswCore.c",
        "third_party/FlowTune/src/src/base/abci/abcDar.c",
        "third_party/FlowTune/src/src/base/abci/abc.c",
    ),
    "refactor": (
        "third_party/FlowTune/src/src/base/abci/abcRefactor.c",
        "third_party/FlowTune/src/src/opt/dar/darRefact.c",
        "third_party/FlowTune/src/src/base/abci/abc.c",
    ),
}
FLOW_SOURCE_CONTEXT_PATTERNS_BY_COMMAND = {
    "fx": (
        "Abc_CommandFx",
        "Abc_CommandFxu",
        "Fxu_FastExtract",
        "Fxu_Select",
        "LitCountMax",
    ),
    "rewrite": ("Abc_CommandRewrite", "Abc_NtkRewrite", "Rwr_NodeRewrite"),
    "resub": ("Abc_CommandResubstitute", "Abc_NtkResubstitute", "Res_ManPerform"),
    "dc2": ("Abc_CommandDc2", "Abc_NtkDC2", "Dar_ManCompress2"),
    "csweep": ("Abc_CommandCSweep", "Abc_NtkCSweep", "Csw_Sweep"),
    "refactor": ("Abc_CommandRefactor", "Abc_NtkRefactor", "Dar_ManRefactor"),
}
SOURCE_INDEX_LIMIT = 120
KEY_SOURCE_LIMIT = 8
KEY_SOURCE_CHAR_LIMIT = 6000
SOURCE_CONTEXT_WINDOW_LINES = 80


class FlowAgent(CodingAgent):
    """Flow Agent for flow scheduling and FlowTune-related candidates."""

    agent_name = "flow_agent"
    paper_role = "Flow Agent"
    prompt_template = "configs/agents/prompts/coding_agent_prompt.md"
    allowed_subsystems = ("configs/flows", "third_party/FlowTune/src/src/opt")
    candidate_kind = "abc_flow"

    def response_schema(self) -> Mapping[str, Any]:
        return flow_response_json_schema()

    def build_model_invocation(self, evidence: Mapping[str, str]) -> ModelInvocation:
        template = load_template(self.context.repo_root, self.prompt_template)
        values = self._prompt_values(evidence)
        user_prompt = render_template(template, values)
        self._validate_rendered_prompt(user_prompt)

        system_prompt = (
            "You are the Flow Agent in a small reproduction of Multi-Agent "
            "Self-Evolved ABC. Propose one conservative FlowTune source patch "
            "or ABC flow candidate within the assignment scope. Return exactly "
            "one JSON object and do not include Markdown prose."
        )

        return ModelInvocation(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=self.response_schema(),
        )

    def materialize_reply(
        self, reply: ModelReply, evidence: Mapping[str, str]
    ) -> AgentArtifacts:
        validation = validate_flow_agent_response(reply.parsed_json, self.context)
        if not validation.ok or validation.response is None:
            return render_flow_validation_failure_artifacts(
                paper_role=self.paper_role,
                candidate_id=self.context.candidate_id,
                reply=reply,
                issues=validation.issues,
                evidence=evidence,
            )
        result = materialize_validated_flow_response(
            response=validation.response,
            context=self.context,
            evidence=evidence,
        )
        return result.artifacts

    def candidate_flow_path(self) -> Path:
        return (
            self.context.repo_root
            / "configs"
            / "flows"
            / f"{self.context.cycle_id}_{self.context.candidate_id}.abc"
        )

    def _prompt_values(self, evidence: Mapping[str, str]) -> dict[str, object]:
        assignment = self.context.assignment
        repo_root = self.context.repo_root
        previous_cycle = str(assignment.get("previous_cycle_id", "cycle_000"))

        summary_path = repo_root / "experiments" / previous_cycle / "results/summary.csv"
        skipped_path = repo_root / "experiments" / previous_cycle / "results/skipped.csv"
        run_notes_path = repo_root / "experiments" / previous_cycle / "results/run_notes.md"
        repository_context = build_repository_context(
            repo_root,
            assignment,
            role=self.agent_name,
        )
        minimum_context = int(assignment.get("repository_context_min_available", 0))
        if (
            bool(assignment.get("repository_context_enforce_minimum", False))
            and repository_context.available_count < minimum_context
        ):
            raise ValueError(
                "Flow Agent repository context is incomplete: "
                f"{repository_context.available_count}/{minimum_context} pinned "
                "repositories are ready; run scripts/bootstrap_agent_context.py"
            )

        return {
            "REPO_ROOT": repo_root,
            "CYCLE_ID": self.context.cycle_id,
            "CANDIDATE_ID": self.context.candidate_id,
            "AGENT_NAME": self.context.agent_name,
            "PAPER_ROLE": self.context.paper_role,
            "SUBSYSTEM": str(assignment.get("subsystem", "FlowTune / ABC flow scheduling")),
            "DRY_RUN": str(assignment.get("dry_run", False)).lower(),
            "SOURCE_PATCH_MODE": str(assignment.get("source_patch_mode", "abc_flow")),
            "SOURCE_PATCH_TARGET_SCOPE": self._source_patch_target_scope(
                assignment
            ),
            "PLANNER_TASK": assignment.get("planner_hypothesis", ""),
            "ALLOWED_FILES": assignment.get("allowed_to_edit", ()),
            "PROGRAMMING_GUIDANCE": load_template(
                repo_root, "configs/agents/shared/programming_guidance.md"
            ),
            "EXTERNAL_REPOSITORY_CONTEXT": repository_context.text,
            "RULEBASE": self._format_rulebase(assignment),
            "COMPILE_OR_RUNTIME_LOGS": self._runtime_context(evidence),
            "SOURCE_FILES": self._source_file_context(),
            "CEC_LOGS": self._cec_context(previous_cycle),
            "QOR_DELTAS": self._qor_context(
                previous_cycle,
                legacy_summary_path=summary_path,
                legacy_skipped_path=skipped_path,
                legacy_run_notes_path=run_notes_path,
            ),
            "FLOW_TOUCHPOINTS": self._format_flow_touchpoints(assignment),
            "EVALUATION_FLOW": self._format_evaluation_flow(assignment),
            "BASELINE_QOR": self._format_champion_qor(
                assignment,
                previous_cycle=previous_cycle,
                legacy_summary_path=summary_path,
            ),
            "PREVIOUS_CANDIDATES": self._previous_candidate_context(previous_cycle),
            "PRIMARY_METRIC": assignment.get(
                "target_metric", "AIG node count / depth provisional"
            ),
            "SECONDARY_METRICS": assignment.get(
                "secondary_metrics", ["depth", "runtime", "stability"]
            ),
            "REGRESSION_THRESHOLD": self._format_regression_threshold(assignment),
            "RUNTIME_BUDGET": "small EPFL subset; keep flow length conservative",
            "BENCHMARK_SCOPE": assignment.get("benchmark_scope", ()),
            "EVALUATION_FLOW_COMMANDS": assignment.get(
                "evaluation_flow_commands",
                list(DEFAULT_EVAL_FLOW_COMMANDS),
            ),
            "FLOW_SOURCE_TOUCHPOINTS": assignment.get(
                "flow_source_touchpoints",
                dict(FLOW_SOURCE_TOUCHPOINTS),
            ),
            "DISCOURAGED_PATCH_TARGETS": self._format_discouraged_targets(
                assignment
            ),
            "FLOW_SCOPE": (
                "For abc_flow: ABC commands only. Do not include shell commands, "
                "benchmark-name branches, redirection, pipes, or previous-cycle "
                "output writes. For source_patch_todo: proposal-only patch plan "
                "inside assignment allowed_to_edit. For source_patch_diff: provide "
                "a unified diff touching only approved source paths; it will be "
                "applied only in an isolated candidate workspace."
            ),
            "COMPILE_COMMAND": (
                "python3 -B -m scripts.agents.self_evolved_abc.flow.source_patch_runner "
                "--assignment <assignment.json> --apply-candidate-patch "
                "--record-build-gate --build-candidate-binary"
            ),
            "SMOKE_COMMAND": self._smoke_command(),
            "CEC_COMMAND": (
                "python3 -B -m scripts.agents.self_evolved_abc.flow.implementation_compare "
                "--assignment <assignment.json>"
            ),
            "QOR_COMMAND": self._qor_command(),
            "COMPILE_PASS_CONDITION": (
                "Candidate source patch applies in an isolated workspace and the "
                "build manifest records candidate_binary_build_passed."
            ),
            "SMOKE_PASS_CONDITION": "ABC exits 0 and prints parseable ps statistics.",
            "CEC_PASS_CONDITION": (
                "Every measured design has cec_status=cec_pass before QoR is trusted."
            ),
            "QOR_PASS_CONDITION": (
                "Provisional AND/depth/runtime deltas are recorded for every "
                "benchmark in scope, including failures and skipped designs."
            ),
        }

    @staticmethod
    def _source_patch_target_scope(assignment: Mapping[str, Any]) -> str:
        roots = assignment.get("source_patch_allowed_roots", ())
        if isinstance(roots, str):
            normalized = [roots.strip()] if roots.strip() else []
        else:
            normalized = [str(root).strip() for root in roots if str(root).strip()]
        if len(normalized) == 1:
            return normalized[0]
        if normalized:
            return ",".join(normalized)
        return "not_applicable"

    def _cec_context(self, previous_cycle: str) -> str:
        """Read actual CEC summary from the previous cycle's impl_compare."""
        comparison = self._previous_impl_root(previous_cycle) / "comparison"
        cec_path = comparison / "cec_summary.csv"
        review_path = comparison / "review_decision.json"
        parts: list[str] = []
        if cec_path.exists():
            parts.append(summarize_csv(cec_path, max_rows=20, max_chars=6000))
        else:
            parts.append(
                f"CEC summary not yet available for {previous_cycle}. "
                "Treat all QoR as provisional until equivalence checks pass."
            )
        if review_path.exists():
            parts.append(
                compact_text_block(
                    "review_decision",
                    review_path.read_text(encoding="utf-8", errors="replace"),
                    max_chars=3000,
                )
            )
        return "\n\n".join(parts)

    def _previous_flow_context(self, previous_cycle: str) -> str:
        outputs = self.context.repo_root / "experiments" / previous_cycle / "outputs"
        preferred = self._preferred_design_names()
        paths = tuple(outputs / f"{name}.flowtune.script" for name in preferred)
        return summarize_flow_scripts(paths, max_files=3, max_chars=6000)

    def _preferred_design_names(self) -> tuple[str, ...]:
        """Return up to 3 benchmark stem names from the assignment scope."""
        names: list[str] = []
        for benchmark in self.context.evaluation_benchmark_scope:
            stem = Path(str(benchmark)).stem
            if stem:
                names.append(stem)
        return tuple(names[:3])

    def _runtime_context(self, evidence: Mapping[str, str]) -> str:
        lines = [
            "Flow Agent source-patch cycle: source edits must pass isolated patch application, build/smoke, CEC, and QoR review.",
            "evidence files loaded:",
            *(f"- {path}" for path in evidence),
        ]
        return compact_text_block("compile_or_runtime_context", "\n".join(lines), 4000)

    def _format_rulebase(self, assignment: Mapping[str, Any]) -> str:
        static_rules = load_template(
            self.context.repo_root,
            "configs/agents/shared/rulebase.md",
        )
        learned = [
            str(item).strip()
            for item in assignment.get("evolved_rules", ())
            if str(item).strip()
        ]
        if not learned:
            return static_rules
        return "\n".join(
            (
                static_rules.rstrip(),
                "",
                "## Evaluated Cycle Rules",
                "",
                *(f"- {rule}" for rule in learned[-12:]),
            )
        )

    def _format_flow_touchpoints(self, assignment: Mapping[str, Any]) -> str:
        """Render the flow-command → source-directory mapping from the assignment."""
        touchpoints = assignment.get("flow_source_touchpoints", {})
        if not touchpoints:
            return "No flow_source_touchpoints in assignment."
        lines = [
            "Each ABC command in the evaluation flow maps to these source directories:",
            "",
        ]
        for command, paths in sorted(touchpoints.items()):
            lines.append(f"- `{command}` → {', '.join(paths)}")
        lines.append("")
        lines.append(
            "To change the behaviour of a specific command, target a file inside "
            "the corresponding directory.  A bounded index and selected key "
            "source snippets are provided under ## Source Files Available for "
            "Patching."
        )
        return "\n".join(lines)

    def _format_evaluation_flow(self, assignment: Mapping[str, Any]) -> str:
        """Render the evaluation flow recipe for context."""
        commands = assignment.get("evaluation_flow_commands", ())
        if not commands:
            return "No evaluation_flow_commands in assignment."
        return (
            "The candidate binary runs this command sequence for every benchmark: "
            + " → ".join(str(c) for c in commands)
        )

    def _format_baseline_qor(
        self, summary_path: Path, assignment: Mapping[str, Any]
    ) -> str:
        """Extract baseline QoR numbers that the model must try to beat."""
        if not summary_path.is_file():
            return "Baseline summary.csv not available."
        import csv

        benchmarks = set(str(b) for b in assignment.get("benchmark_scope", ()))
        # Build stem → full benchmark path lookup for efficient matching.
        stem_to_benchmark: dict[str, str] = {}
        for bm in benchmarks:
            stem = Path(str(bm)).stem
            if stem:
                stem_to_benchmark[stem] = bm
        lines = [
            (
                "BASELINE QoR — these are the numbers your patch must improve.  "
                "Target the `flowtune_and` column (post-FlowTune AIG nodes).  "
                "A change that leaves these unchanged is automatically rejected "
                "as REPAIR_QOR."
            ),
            "",
        ]
        try:
            with summary_path.open(
                "r", encoding="utf-8", newline=""
            ) as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    design = row.get("design", "")
                    if design in stem_to_benchmark:
                        lines.append(
                            f"- {design}:  vanilla={row.get('vanilla_and','?')}  "
                            f"flowtune={row.get('flowtune_and','?')}  "
                            f"(improvement={row.get('and_improve_pct','?')}%)  "
                            f"depth={row.get('flowtune_lev','?')}"
                        )
        except Exception:
            return "Failed to parse baseline summary.csv."
        if len(lines) == 2:
            lines.append("(no matching benchmarks found in summary.csv)")
        return "\n".join(lines)

    def _format_champion_qor(
        self,
        assignment: Mapping[str, Any],
        *,
        previous_cycle: str,
        legacy_summary_path: Path,
    ) -> str:
        """Render the incumbent from authoritative S5 comparison artifacts."""

        import csv

        evaluated = {
            str(item)
            for item in assignment.get(
                "evaluation_benchmark_scope",
                assignment.get("benchmark_scope", ()),
            )
        }
        champion_cycle = str(assignment.get("champion_cycle_id", "")).strip()
        sources: list[tuple[str, str, str]] = []
        if champion_cycle:
            sources.append(
                (
                    champion_cycle,
                    "candidate",
                    str(assignment.get("champion_candidate_id", self.context.candidate_id)),
                )
            )
        sources.append((previous_cycle, "baseline", self.context.candidate_id))

        for cycle_id, side, candidate_id in sources:
            path = self._previous_impl_root(
                cycle_id, candidate_id=candidate_id
            ) / "comparison" / "qor_delta.csv"
            if not path.is_file():
                continue
            try:
                with path.open("r", encoding="utf-8", newline="") as handle:
                    rows = list(csv.DictReader(handle))
            except Exception:
                continue

            and_key = f"{side}_aig_nodes"
            depth_key = f"{side}_aig_depth"
            selected = [
                row
                for row in rows
                if not evaluated or row.get("benchmark", "") in evaluated
            ]
            if not selected:
                continue

            lines = [
                "CURRENT CHAMPION QoR from correctness-backed S5 evidence.",
                f"source_cycle: {cycle_id}",
                f"source_side: {side}",
                "The next candidate is compared against these values.",
                "",
            ]
            total_and = 0
            parsed_rows = 0
            for row in selected[:40]:
                benchmark = row.get("benchmark", "?")
                and_value = row.get(and_key, "?")
                depth_value = row.get(depth_key, "?")
                lines.append(
                    f"- {benchmark}: champion_and={and_value}, "
                    f"champion_depth={depth_value}"
                )
                try:
                    total_and += int(float(and_value))
                    parsed_rows += 1
                except (TypeError, ValueError):
                    pass
            if parsed_rows:
                lines.extend(
                    (
                        "",
                        f"aggregate_champion_and={total_and} across "
                        f"{parsed_rows} rows",
                    )
                )
            return "\n".join(lines)

        return self._format_baseline_qor(legacy_summary_path, assignment)

    def _qor_context(
        self,
        previous_cycle: str,
        *,
        legacy_summary_path: Path,
        legacy_skipped_path: Path,
        legacy_run_notes_path: Path,
    ) -> str:
        comparison = self._previous_impl_root(previous_cycle) / "comparison"
        qor_delta = comparison / "qor_delta.csv"
        review = comparison / "review_decision.json"
        if qor_delta.is_file():
            return "\n\n".join(
                (
                    summarize_csv(qor_delta, max_rows=40, max_chars=18000),
                    self._read_optional_block("review_decision", review, 5000),
                )
            )
        return "\n\n".join(
            (
                summarize_csv(legacy_summary_path, max_rows=20, max_chars=10000),
                summarize_csv(legacy_skipped_path, max_rows=20, max_chars=4000),
                self._read_optional_block(
                    "run_notes", legacy_run_notes_path, 6000
                ),
            )
        )

    def _previous_candidate_context(self, previous_cycle: str) -> str:
        base = self.context.repo_root / "experiments" / previous_cycle
        impl_root = self._previous_impl_root(previous_cycle)
        candidate_id = self.context.candidate_id
        paths = (
            (
                "applied_patch",
                impl_root / IMPL_CANDIDATE_LABEL / "patch.diff",
                10000,
            ),
            (
                "agent_source_patch",
                base / "agents" / "source_patches" / f"{candidate_id}.diff",
                10000,
            ),
            (
                "agent_feedback",
                base / "agents" / "feedback" / f"{candidate_id}.md",
                7000,
            ),
        )
        blocks = [
            self._read_optional_block(label, path, limit)
            for label, path, limit in paths
            if path.is_file()
        ]
        if blocks:
            return "\n\n".join(blocks)
        return self._previous_flow_context(previous_cycle)

    def _previous_impl_root(
        self,
        cycle_id: str,
        *,
        candidate_id: str | None = None,
    ) -> Path:
        return implementation_root_for(
            repo_root=self.context.repo_root,
            cycle_id=cycle_id,
            candidate_id=candidate_id or self.context.candidate_id,
            layout=str(
                self.context.assignment.get(
                    "artifact_layout", LEGACY_CYCLE_LAYOUT
                )
            ),
        )

    def _format_regression_threshold(self, assignment: Mapping[str, Any]) -> str:
        thresholds = normalize_promotion_thresholds(
            assignment.get("promotion_thresholds")
        )
        return (
            "No hidden skipped designs; depth/runtime regressions must be "
            "reported per benchmark. "
            f"{threshold_prompt_text(thresholds)}"
        )

    def _format_discouraged_targets(self, assignment: Mapping[str, Any]) -> str:
        targets = [
            str(item).strip()
            for item in assignment.get("discouraged_patch_targets", ())
            if str(item).strip()
        ]
        if not targets:
            return "(none)"
        return "\n".join(f"- {target}" for target in targets)

    def _source_file_context(self) -> str:
        """Read source files from the assignment's source_patch_allowed_roots."""
        allowed_roots = self.context.assignment.get("source_patch_allowed_roots", ())
        if not allowed_roots:
            return "No source_patch_allowed_roots in assignment."

        chunks: list[str] = [
            "## Source Files Available for Patching",
            "",
            "### File Index (all source files under allowed scope)",
            "",
        ]
        all_files = self._collect_source_files(allowed_roots)

        for rel, size in all_files[:SOURCE_INDEX_LIMIT]:
            chunks.append(f"- {rel}  ({size} bytes)")
        if len(all_files) > SOURCE_INDEX_LIMIT:
            chunks.append(
                f"- ... {len(all_files) - SOURCE_INDEX_LIMIT} additional files omitted"
            )

        # Full content for key files most relevant to Flow Agent
        key_files = self._select_key_source_files(all_files)
        if key_files:
            chunks.append("")
            chunks.append("### Key Source Context")
            chunks.append("")
            for rel, size in key_files:
                path = self._source_context_file(Path(rel))
                try:
                    content = path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    chunks.append(f"#### {rel}\n[could not read]\n")
                    continue
                if len(content) > KEY_SOURCE_CHAR_LIMIT:
                    content = self._source_excerpt(rel, content)
                chunks.append(f"#### {rel} ({size} bytes)")
                chunks.append("```c")
                chunks.append(content)
                chunks.append("```")
                chunks.append("")

        return "\n".join(chunks)

    def _collect_source_files(
        self,
        allowed_roots: object,
    ) -> list[tuple[str, int]]:
        files: list[tuple[str, int]] = []
        for root in self._as_root_tuple(allowed_roots):
            root_rel = Path(str(root))
            root_path = self._source_context_file(root_rel)
            if not root_path.is_dir():
                continue
            for src_file in sorted(
                path
                for pattern in ("*.c", "*.h")
                for path in root_path.rglob(pattern)
            ):
                display_path = root_rel / src_file.relative_to(root_path)
                files.append(
                    (
                        str(display_path),
                        src_file.stat().st_size,
                    )
                )
        return files

    def _source_context_file(self, repo_relative: Path) -> Path:
        """Return source content path, using the champion source tree when present."""

        return source_context_path(self.context, repo_relative)

    def _as_root_tuple(self, value: object) -> tuple[object, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        try:
            return tuple(value)  # type: ignore[arg-type]
        except TypeError:
            return (value,)

    def _select_key_source_files(
        self,
        all_files: list[tuple[str, int]],
    ) -> list[tuple[str, int]]:
        by_path = {rel: (rel, size) for rel, size in all_files}
        selected: list[tuple[str, int]] = []
        seen: set[str] = set()

        def add(rel: str) -> None:
            item = by_path.get(rel)
            if item is None or rel in seen:
                return
            selected.append(item)
            seen.add(rel)

        target_command = self._planned_target_command()
        for suffix in FLOW_SOURCE_CONTEXT_KEY_SUFFIXES_BY_COMMAND.get(
            target_command, ()
        ):
            add(suffix)
        for suffix in FLOW_SOURCE_CONTEXT_COMMON_KEY_SUFFIXES:
            add(suffix)

        target_source_dir = self._planned_target_source_dir()
        if target_source_dir:
            prefix = target_source_dir.rstrip("/") + "/"
            for rel, _size in all_files:
                if rel.startswith(prefix):
                    add(rel)
                if len(selected) >= KEY_SOURCE_LIMIT:
                    break
        return selected[:KEY_SOURCE_LIMIT]

    def _planned_target_command(self) -> str:
        meta = self.context.assignment.get("_planning_meta")
        if isinstance(meta, Mapping):
            command = str(meta.get("target_command", "")).strip()
            if command:
                return command
        return str(self.context.assignment.get("target_command", "")).strip()

    def _planned_target_source_dir(self) -> str:
        meta = self.context.assignment.get("_planning_meta")
        if isinstance(meta, Mapping):
            source_dir = str(meta.get("target_source_dir", "")).strip()
            if source_dir:
                return source_dir
        return str(self.context.assignment.get("target_source_dir", "")).strip()

    def _source_excerpt(self, rel: str, content: str) -> str:
        patterns = FLOW_SOURCE_CONTEXT_PATTERNS_BY_COMMAND.get(
            self._planned_target_command(), ()
        )
        lines = content.splitlines()
        windows: list[tuple[int, int]] = []
        for index, line in enumerate(lines):
            if not any(pattern in line for pattern in patterns):
                continue
            start = max(0, index - SOURCE_CONTEXT_WINDOW_LINES // 2)
            end = min(len(lines), index + SOURCE_CONTEXT_WINDOW_LINES // 2)
            windows.append((start, end))
            if len(windows) >= 3:
                break

        if not windows:
            half = KEY_SOURCE_CHAR_LIMIT // 2
            return (
                content[:half].rstrip()
                + f"\n\n... [{len(content) - KEY_SOURCE_CHAR_LIMIT} chars omitted from {rel}] ...\n\n"
                + content[-half:].lstrip()
            )

        merged: list[tuple[int, int]] = []
        for start, end in windows:
            if merged and start <= merged[-1][1]:
                prev_start, prev_end = merged[-1]
                merged[-1] = (prev_start, max(prev_end, end))
            else:
                merged.append((start, end))

        snippets: list[str] = []
        previous_end = 0
        for start, end in merged:
            if start > previous_end:
                snippets.append(f"... [lines {previous_end + 1}-{start} omitted] ...")
            snippets.extend(lines[start:end])
            previous_end = end
        if previous_end < len(lines):
            snippets.append(f"... [lines {previous_end + 1}-{len(lines)} omitted] ...")
        excerpt = "\n".join(snippets)
        if len(excerpt) <= KEY_SOURCE_CHAR_LIMIT:
            return excerpt
        return excerpt[:KEY_SOURCE_CHAR_LIMIT].rstrip() + "\n... [excerpt truncated] ..."

    def _read_optional_block(self, label: str, path: Path, max_chars: int) -> str:
        if not path.exists():
            return f"{label}: missing"
        return compact_text_block(
            label,
            path.read_text(encoding="utf-8", errors="replace"),
            max_chars=max_chars,
        )

    def _smoke_command(self) -> str:
        flow_path = self.candidate_flow_path()
        smoke_benchmark = self._smoke_benchmark()
        return (
            'abc -c "source third_party/FlowTune/abc.rc; '
            f"read {smoke_benchmark}; "
            f"source {flow_path.relative_to(self.context.repo_root)}; "
            'strash; ps"'
        )

    def _smoke_benchmark(self) -> str:
        """Return the first benchmark in scope for the smoke test."""
        for bm in self.context.benchmark_scope:
            return str(bm)
        return "benchmarks/epfl/epfl_adder.blif"

    def _qor_command(self) -> str:
        flow_path = self.candidate_flow_path().relative_to(self.context.repo_root)
        benchmarks = self.context.evaluation_benchmark_scope
        joined = ", ".join(str(item) for item in benchmarks)
        return (
            "For each benchmark in scope "
            f"({joined}), run ABC with read <benchmark>; source {flow_path}; "
            "strash; ps; record AND count, depth, runtime, exit status, and skip reason."
        )

    def _validate_rendered_prompt(self, prompt: str) -> None:
        unresolved = find_unresolved_placeholders(prompt)
        if unresolved:
            raise ValueError(
                "unresolved Flow Agent prompt placeholders: "
                + ", ".join(unresolved)
            )

        leaked = find_forbidden_secret_markers(prompt)
        if leaked:
            raise ValueError("rendered prompt contains forbidden secret markers.")

        if "TODO_CODING_PROMPT_RENDER" in prompt:
            raise ValueError("Flow Agent prompt still contains scaffold TODO.")
