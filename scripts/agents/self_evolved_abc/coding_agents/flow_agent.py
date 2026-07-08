"""Flow Agent scaffold."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from scripts.agents.self_evolved_abc.coding_agents.base_coding_agent import CodingAgent
from scripts.agents.self_evolved_abc.flow.artifacts import (
    render_flow_validation_failure_artifacts,
)
from scripts.agents.self_evolved_abc.flow.materialization import (
    materialize_validated_flow_response,
)
from scripts.agents.self_evolved_abc.model_client import ModelInvocation, ModelReply
from scripts.agents.self_evolved_abc.prompt_rendering import (
    compact_text_block,
    find_unresolved_placeholders,
    load_template,
    render_template,
    summarize_csv,
    summarize_flow_scripts,
)
from scripts.agents.self_evolved_abc.flow.validation import (
    flow_response_json_schema,
    validate_flow_agent_response,
)
from scripts.agents.self_evolved_abc.schemas import AgentArtifacts


class FlowAgent(CodingAgent):
    """Flow Agent for flow scheduling and FlowTune-related candidates."""

    agent_name = "flow_agent"
    paper_role = "Flow Agent"
    prompt_template = "configs/agents/prompts/coding_agent_prompt.md"
    allowed_subsystems = ("configs/flows", "third_party/FlowTune/src/opt/flowtune")
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

        return {
            "REPO_ROOT": repo_root,
            "CYCLE_ID": self.context.cycle_id,
            "CANDIDATE_ID": self.context.candidate_id,
            "AGENT_NAME": self.context.agent_name,
            "PAPER_ROLE": self.context.paper_role,
            "SUBSYSTEM": "FlowTune / ABC flow scheduling",
            "DRY_RUN": str(assignment.get("dry_run", False)).lower(),
            "PLANNER_TASK": assignment.get("planner_hypothesis", ""),
            "ALLOWED_FILES": assignment.get("allowed_to_edit", ()),
            "PROGRAMMING_GUIDANCE": load_template(
                repo_root, "configs/agents/shared/programming_guidance.md"
            ),
            "RULEBASE": load_template(repo_root, "configs/agents/shared/rulebase.md"),
            "COMPILE_OR_RUNTIME_LOGS": self._runtime_context(evidence),
            "CEC_LOGS": (
                "CEC is not wired into cycle_000/cycle_001 yet. Treat all QoR "
                "as provisional process evidence until equivalence checks pass."
            ),
            "QOR_DELTAS": "\n\n".join(
                (
                    summarize_csv(summary_path, max_rows=20, max_chars=10000),
                    summarize_csv(skipped_path, max_rows=20, max_chars=4000),
                    self._read_optional_block("run_notes", run_notes_path, 6000),
                )
            ),
            "PREVIOUS_CANDIDATES": self._previous_flow_context(previous_cycle),
            "PRIMARY_METRIC": assignment.get(
                "target_metric", "AIG node count / depth provisional"
            ),
            "SECONDARY_METRICS": assignment.get(
                "secondary_metrics", ["depth", "runtime", "stability"]
            ),
            "REGRESSION_THRESHOLD": (
                "No hidden skipped designs; depth/runtime regressions must be "
                "reported per benchmark."
            ),
            "RUNTIME_BUDGET": "small EPFL subset; keep flow length conservative",
            "BENCHMARK_SCOPE": assignment.get("benchmark_scope", ()),
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

    def _previous_flow_context(self, previous_cycle: str) -> str:
        outputs = self.context.repo_root / "experiments" / previous_cycle / "outputs"
        preferred = ("epfl_adder", "epfl_bar", "epfl_sqrt")
        paths = tuple(outputs / f"{name}.flowtune.script" for name in preferred)
        return summarize_flow_scripts(paths, max_files=3, max_chars=6000)

    def _runtime_context(self, evidence: Mapping[str, str]) -> str:
        lines = [
            "Flow Agent source-patch cycle: source edits must pass isolated patch application, build/smoke, CEC, and QoR review.",
            "evidence files loaded:",
            *(f"- {path}" for path in evidence),
        ]
        return compact_text_block("compile_or_runtime_context", "\n".join(lines), 4000)

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
        return (
            'abc -c "source third_party/FlowTune/abc.rc; '
            "read benchmarks/epfl/epfl_adder.blif; "
            f"source {flow_path.relative_to(self.context.repo_root)}; "
            'strash; ps"'
        )

    def _qor_command(self) -> str:
        flow_path = self.candidate_flow_path().relative_to(self.context.repo_root)
        benchmarks = self.context.assignment.get("benchmark_scope", ())
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

        forbidden = (".env", "API_KEY", "EDA_AGENT_MODEL_API_KEY")
        leaked = [item for item in forbidden if item in prompt]
        if leaked:
            raise ValueError("rendered prompt contains forbidden secret markers.")

        if "TODO_CODING_PROMPT_RENDER" in prompt:
            raise ValueError("Flow Agent prompt still contains scaffold TODO.")
