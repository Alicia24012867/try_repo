"""Paper-faithful Logic Minimization Agent."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from scripts.agents.self_evolved_abc.coding_agents.flow_agent import (
    KEY_SOURCE_CHAR_LIMIT,
    KEY_SOURCE_LIMIT,
    SOURCE_CONTEXT_WINDOW_LINES,
    FlowAgent,
)
from scripts.agents.self_evolved_abc.flow.artifacts import (
    render_flow_validation_failure_artifacts,
)
from scripts.agents.self_evolved_abc.flow.materialization import (
    materialize_validated_flow_response,
)
from scripts.agents.self_evolved_abc.logic.contracts import (
    LOGIC_ABCI_ROOT,
    LOGIC_AGENT_NAME,
    LOGIC_EVALUATION_FLOW_COMMANDS,
    LOGIC_PAPER_ROLE,
    LOGIC_SOURCE_PATCH_MODE,
    LOGIC_SOURCE_TOUCHPOINTS,
)
from scripts.agents.self_evolved_abc.logic.validation import (
    logic_response_json_schema,
    validate_logic_agent_response,
)
from scripts.agents.self_evolved_abc.model_client import ModelInvocation, ModelReply
from scripts.agents.self_evolved_abc.prompt_rendering import compact_text_block
from scripts.agents.self_evolved_abc.repository_context import (
    RepositoryContextBundle,
    build_repository_context,
)
from scripts.agents.self_evolved_abc.schemas import AgentArtifacts


LOGIC_SOURCE_CONTEXT_KEY_PATHS = {
    command: tuple(paths) for command, paths in LOGIC_SOURCE_TOUCHPOINTS.items()
}
LOGIC_SOURCE_CONTEXT_PATTERNS = {
    "rewrite": ("Abc_CommandRewrite", "Abc_NtkRewrite", "Abc_NtkCheck"),
    "resub": (
        "Abc_CommandResubstitute",
        "Abc_NtkResubstitute",
        "Abc_NtkCheck",
    ),
    "refactor": (
        "Abc_CommandRefactor",
        "Abc_NtkRefactor",
        "Abc_NtkCheck",
    ),
    "orchestrate": (
        "Cmd_CommandAdd",
        "Abc_CommandRewrite",
        "Abc_CommandRefactor",
        "Abc_CommandResubstitute",
    ),
}


class LogicMinimizationAgent(FlowAgent):
    """Technology-independent ABCI optimizer with formal gate contracts."""

    agent_name = LOGIC_AGENT_NAME
    paper_role = LOGIC_PAPER_ROLE
    prompt_template = "configs/agents/prompts/coding_agent_prompt.md"
    allowed_subsystems = (LOGIC_ABCI_ROOT,)
    candidate_kind = LOGIC_SOURCE_PATCH_MODE

    def response_schema(self) -> Mapping[str, Any]:
        return logic_response_json_schema()

    def build_model_invocation(self, evidence: Mapping[str, str]) -> ModelInvocation:
        base = super().build_model_invocation(evidence)
        system_prompt = (
            "You are the paper's Logic Minimization Agent (AIG Syn), not the "
            "Flow Agent. Either propose at most one technology-independent, "
            "combinational optimization hypothesis as a source_patch_diff over "
            f"existing files strictly under {LOGIC_ABCI_ROOT}, or return a "
            "structured diagnostic-only DEFER/approval decision when evidence "
            "or scope is insufficient. Preserve Boolean semantics; never alter "
            "retiming, latches, registers, initial state, "
            "benchmarks, build metadata, or evaluation code. Use related "
            "open-source excerpts only as read-only design precedents and verify "
            "every symbol against the local FlowTune fork. The candidate remains "
            "provisional until isolated compilation and all-design CEC pass; QoR "
            "is evaluated only afterward. Return exactly one JSON object without "
            "Markdown prose. Ignore Flow-Agent-only operating instructions in the "
            "shared template whenever they conflict with this role contract."
        )
        return ModelInvocation(
            system_prompt=system_prompt,
            user_prompt=base.user_prompt,
            response_schema=base.response_schema,
            model=base.model,
            temperature=0.0,
        )

    def materialize_reply(
        self, reply: ModelReply, evidence: Mapping[str, str]
    ) -> AgentArtifacts:
        validation = validate_logic_agent_response(reply.parsed_json, self.context)
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

    def source_patch_boundary(self) -> tuple[str, ...]:
        """Return the immutable paper-owned Logic source boundary."""

        return self.allowed_subsystems

    def repository_context_bundle(self) -> RepositoryContextBundle:
        """Return pinned, query-relevant, read-only repository precedents."""

        return build_repository_context(
            self.context.repo_root,
            self.context.assignment,
            role=self.agent_name,
        )

    def _prompt_values(self, evidence: Mapping[str, str]) -> dict[str, object]:
        values = super()._prompt_values(evidence)
        assignment = self.context.assignment
        repository_context = self.repository_context_bundle()
        values.update(
            {
                "PLANNER_TASK": self._logic_planner_task(assignment),
                "PROGRAMMING_GUIDANCE": (
                    str(values["PROGRAMMING_GUIDANCE"]).rstrip()
                    + "\n\n"
                    + self._logic_programming_contract()
                ),
                "RULEBASE": (
                    str(values["RULEBASE"]).rstrip()
                    + "\n\n"
                    + self._logic_gate_contract()
                ),
                "EXTERNAL_REPOSITORY_CONTEXT": repository_context.text,
                "FLOW_TOUCHPOINTS": self._format_flow_touchpoints(assignment),
                "FLOW_SOURCE_TOUCHPOINTS": assignment.get(
                    "logic_source_touchpoints", dict(LOGIC_SOURCE_TOUCHPOINTS)
                ),
                "FLOW_SCOPE": (
                    "For PROPOSE_CANDIDATE, use source_patch_diff only: edit at "
                    f"most three existing .c/.h files under {LOGIC_ABCI_ROOT}; "
                    "files_to_write must equal the diff targets. Do not "
                    "add/delete/rename files or edit module.make. Use one "
                    "deterministic combinational rewrite, resub, refactor, or "
                    "orchestration hypothesis. Apply the diff solely in the "
                    "isolated candidate workspace. Compile must pass before "
                    "all-design CEC; QoR is meaningful only after CEC. For a "
                    "non-proposal decision, use diagnostic_only with no patch."
                ),
                "RUNTIME_BUDGET": (
                    "bounded pass behavior over the frozen evaluation scope; record "
                    "runtime for every design and reject hidden skips"
                ),
                "PRIMARY_METRIC": assignment.get("target_metric", "and_count"),
                "SECONDARY_METRICS": assignment.get(
                    "secondary_metrics", ["depth", "runtime", "stability"]
                ),
                "SMOKE_COMMAND": self._smoke_command(),
                "QOR_COMMAND": self._qor_command(),
                "COMPILE_PASS_CONDITION": (
                    "The diff applies only in the isolated workspace and the local "
                    "FlowTune make build produces the candidate ABC binary with exit 0."
                ),
                "CEC_PASS_CONDITION": (
                    "Every design in the frozen evaluation scope passes cec/dsat; "
                    "any mismatch or counterexample rejects the candidate immediately."
                ),
                "QOR_PASS_CONDITION": (
                    "Only correctness-backed rows are compared; record AIG/AND node "
                    "count, depth, runtime, failures, and skipped designs for the full scope."
                ),
            }
        )
        return values

    def _format_flow_touchpoints(self, assignment: Mapping[str, Any]) -> str:
        touchpoints = assignment.get(
            "logic_source_touchpoints", LOGIC_SOURCE_TOUCHPOINTS
        )
        if not isinstance(touchpoints, Mapping) or not touchpoints:
            return "No Logic source touchpoints in assignment."
        lines = [
            "Paper-role Logic target -> existing local ABCI entry points:",
            "",
        ]
        for command, raw_paths in sorted(touchpoints.items()):
            if isinstance(raw_paths, str):
                paths = (raw_paths,)
            else:
                paths = tuple(str(path) for path in raw_paths)
            lines.append(f"- `{command}` -> {', '.join(paths)}")
        lines.extend(
            (
                "",
                "The local FlowTune files are the API source of truth. Related "
                "repositories below are read-only precedents, never patch targets.",
            )
        )
        return "\n".join(lines)

    def _select_key_source_files(
        self,
        all_files: list[tuple[str, int]],
    ) -> list[tuple[str, int]]:
        by_path = {path: (path, size) for path, size in all_files}
        selected: list[tuple[str, int]] = []
        seen: set[str] = set()

        def add(path: str) -> None:
            if path in seen or path not in by_path:
                return
            seen.add(path)
            selected.append(by_path[path])

        target = self._planned_target_command()
        for path in LOGIC_SOURCE_CONTEXT_KEY_PATHS.get(target, ()):
            add(path)
        for path in (
            f"{LOGIC_ABCI_ROOT}/abc.c",
            f"{LOGIC_ABCI_ROOT}/abcRewrite.c",
            f"{LOGIC_ABCI_ROOT}/abcResub.c",
            f"{LOGIC_ABCI_ROOT}/abcRefactor.c",
            f"{LOGIC_ABCI_ROOT}/abcBalance.c",
        ):
            add(path)
        return selected[:KEY_SOURCE_LIMIT]

    def _source_excerpt(self, rel: str, content: str) -> str:
        patterns = LOGIC_SOURCE_CONTEXT_PATTERNS.get(
            self._planned_target_command(), ()
        )
        if not patterns:
            return super()._source_excerpt(rel, content)
        lines = content.splitlines()
        windows: list[tuple[int, int]] = []
        for index, line in enumerate(lines):
            if not any(pattern in line for pattern in patterns):
                continue
            half = SOURCE_CONTEXT_WINDOW_LINES // 2
            windows.append((max(0, index - half), min(len(lines), index + half)))
            if len(windows) >= 4:
                break
        if not windows:
            return super()._source_excerpt(rel, content)
        merged: list[tuple[int, int]] = []
        for start, end in windows:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        snippets = [
            f"/* local lines {start + 1}-{end} */\n" + "\n".join(lines[start:end])
            for start, end in merged
        ]
        excerpt = "\n\n...\n\n".join(snippets)
        if len(excerpt) > KEY_SOURCE_CHAR_LIMIT:
            return excerpt[:KEY_SOURCE_CHAR_LIMIT].rstrip() + "\n... [truncated]"
        return excerpt

    def _runtime_context(self, evidence: Mapping[str, str]) -> str:
        lines = [
            "Logic source-patch cycle: preserve combinational semantics and "
            "use strict isolated compile -> all-design CEC -> QoR ordering.",
            "Any compiler failure returns to repair; any CEC mismatch or "
            "counterexample rejects the iteration before QoR.",
            "evidence files loaded:",
            *(f"- {path}" for path in evidence),
        ]
        return compact_text_block("logic_compile_or_runtime_context", "\n".join(lines), 4000)

    def _smoke_command(self) -> str:
        benchmark = self._smoke_benchmark()
        recipe = "; ".join(
            str(command)
            for command in self.context.assignment.get(
                "evaluation_flow_commands", LOGIC_EVALUATION_FLOW_COMMANDS
            )
        )
        return (
            'candidate_abc -c "source third_party/FlowTune/abc.rc; '
            f"read {benchmark}; {recipe}; print_stats\""
        )

    def _qor_command(self) -> str:
        commands = " -> ".join(
            str(command)
            for command in self.context.assignment.get(
                "evaluation_flow_commands", LOGIC_EVALUATION_FLOW_COMMANDS
            )
        )
        benchmarks = ", ".join(self.context.evaluation_benchmark_scope)
        return (
            "After candidate build and all-design CEC pass, run baseline and "
            f"candidate ABC binaries on every benchmark ({benchmarks}) with the "
            f"same frozen recipe ({commands}); record AIG/AND nodes, depth, "
            "runtime, exit status, and every failure/skip reason."
        )

    def _validate_rendered_prompt(self, prompt: str) -> None:
        super()._validate_rendered_prompt(prompt)
        required = (self.paper_role, LOGIC_ABCI_ROOT, "compile", "CEC", "QoR")
        missing = tuple(token for token in required if token not in prompt)
        if missing:
            raise ValueError(
                "rendered Logic Agent prompt is missing contract markers: "
                + ", ".join(missing)
            )

    def _logic_planner_task(self, assignment: Mapping[str, Any]) -> str:
        hypothesis = str(assignment.get("planner_hypothesis", "")).strip()
        target = self._planned_target_command() or "rewrite"
        contract = (
            f"Target family: {target}. Trace its registered command/wrapper to "
            "one reached local decision and test exactly one hypothesis. For a "
            "proposal, return source_patch_diff for at most three existing files "
            f"under {LOGIC_ABCI_ROOT}; otherwise return diagnostic_only with no "
            "patch. "
            "State combinational equivalence plus explicit no-retiming and "
            "no-sequential-state invariants. Validation must be ordered as "
            "isolated compile, CEC/dsat over every design with immediate rejection "
            "on mismatch/counterexample, then full-scope AIG-node/depth/runtime QoR."
        )
        return f"{hypothesis}\n\nNON-NEGOTIABLE LOGIC CONTRACT:\n{contract}".strip()

    @staticmethod
    def _logic_programming_contract() -> str:
        return (
            "## Logic Agent local-source contract\n\n"
            "The bundled FlowTune ABCI implementation is authoritative. Verify "
            "each type, symbol, option, allocation/free path, network-form check, "
            "and command call chain locally. External snippets supply patterns, "
            "not compatible APIs. Do not add dependencies, file I/O, randomness, "
            "time/environment decisions, benchmark branches, or source files."
        )

    @staticmethod
    def _logic_gate_contract() -> str:
        return (
            "## Enforced Logic proposal rules\n\n"
            "- PROPOSE_CANDIDATE uses source_patch_diff and the exact ABCI target scope; non-proposals use diagnostic_only with no source_patch.\n"
            "- Touch at most three existing .c/.h files, and make files_to_write exactly equal the diff targets.\n"
            "- No build metadata, add/delete/rename, or binary patches.\n"
            "- No retiming, latch, register, initial-state, or other sequential behavior changes.\n"
            "- Compile precedes CEC for every design; CEC precedes QoR.\n"
            "- A mismatch or counterexample rejects the iteration; QoR records AIG/AND nodes and depth.\n"
        )
