"""Planning Agent — LLM-based planner.

Renders ``planner_prompt.md`` with real cycle evidence and calls the model
to produce a next-cycle plan.  Falls back to the deterministic engine when
the model is unavailable or too expensive.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from scripts.agents.self_evolved_abc.base_agent import PaperAgent
from scripts.agents.self_evolved_abc.flow.contracts import DEFAULT_EVAL_FLOW_COMMANDS
from scripts.agents.self_evolved_abc.model_client import ModelInvocation, ModelReply
from scripts.agents.self_evolved_abc.planning.engine import PlanningEngine
from scripts.agents.self_evolved_abc.prompt_rendering import (
    compact_text_block,
    find_unresolved_placeholders,
    load_template,
    render_template,
)
from scripts.agents.self_evolved_abc.schemas import AgentArtifacts, markdown_bullets


class PlanningAgent(PaperAgent):
    """Paper-style Planning Agent.

    Owns cycle objectives, subsystem selection, rollback policy, and global QoR
    interpretation.  Uses the deterministic engine as fallback when the LLM is
    not configured.
    """

    agent_name = "planning_agent"
    paper_role = "Planning Agent"
    prompt_template = "configs/agents/prompts/planner_prompt.md"

    # ------------------------------------------------------------------
    # Prompt rendering
    # ------------------------------------------------------------------

    def build_model_invocation(self, evidence: Mapping[str, str]) -> ModelInvocation:
        template = load_template(self.context.repo_root, self.prompt_template)
        values = self._prompt_values(evidence)
        user_prompt = render_template(template, values)
        self._validate_rendered_prompt(user_prompt)

        system_prompt = (
            "You are the Planning Agent for a small reproduction of "
            "Multi-Agent Self-Evolved ABC. Propose one conservative next-cycle "
            "objective and select which coding agent should act. Return exactly "
            "one JSON object and do not include Markdown prose."
        )

        return ModelInvocation(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=self.response_schema(),
        )

    def response_schema(self) -> Mapping[str, Any]:
        return {
            "type": "object",
            "required": [
                "cycle_objective",
                "selected_agent",
                "candidate_id",
                "benchmark_scope",
                "risk_controls",
            ],
            "additionalProperties": False,
            "properties": {
                "cycle_objective": {"type": "string"},
                "selected_agent": {
                    "type": "string",
                    "enum": [
                        "flow_agent",
                        "logic_minimization_agent",
                        "mapper_agent",
                    ],
                },
                "task_type": {
                    "type": "string",
                    "enum": [
                        "optimization",
                        "repair",
                        "rollback",
                        "instrumentation",
                        "evaluation_only",
                    ],
                },
                "candidate_id": {"type": "string"},
                "risk_level": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                },
                "source_patch_mode": {
                    "type": "string",
                    "enum": [
                        "source_patch_diff",
                        "abc_flow",
                        "source_patch_todo",
                    ],
                },
                "source_patch_allowed_roots": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "benchmark_scope": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "allowed_to_read": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "allowed_to_edit": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "evidence_summary": {
                    "type": "object",
                    "properties": {
                        "compile": {"type": "string"},
                        "cec": {"type": "string"},
                        "qor": {"type": "string"},
                        "runtime": {"type": "string"},
                    },
                },
                "hypothesis": {"type": "string"},
                "coding_agent_task": {"type": "string"},
                "acceptance_criteria": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "rollback_criteria": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "risk_controls": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "rulebase_notes": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        }

    def materialize_reply(
        self, reply: ModelReply, evidence: Mapping[str, str]
    ) -> AgentArtifacts:
        data = reply.parsed_json
        objective = str(data.get("cycle_objective", ""))
        selected_agent = str(data.get("selected_agent", "flow_agent"))
        task_type = str(data.get("task_type", "optimization"))
        hypothesis = str(data.get("hypothesis", ""))
        coding_task = str(data.get("coding_agent_task", ""))
        risk_controls = [
            str(item)
            for item in data.get("risk_controls", ())
            if str(item)
        ]
        rulebase_notes = [
            str(item)
            for item in data.get("rulebase_notes", ())
            if str(item)
        ]
        acceptance = [
            str(item)
            for item in data.get("acceptance_criteria", ())
            if str(item)
        ]
        rollback = [
            str(item)
            for item in data.get("rollback_criteria", ())
            if str(item)
        ]

        evidence_summary = data.get("evidence_summary", {}) or {}
        compile_status = str(evidence_summary.get("compile", "missing"))
        cec_status = str(evidence_summary.get("cec", "missing"))
        qor_status = str(evidence_summary.get("qor", "inconclusive"))

        return AgentArtifacts(
            plan_markdown=(
                f"# Planning Agent Plan -- {self.context.candidate_id}\n\n"
                f"## Objective\n\n{objective}\n\n"
                f"## Selected Coding Agent\n\n- {selected_agent}\n\n"
                f"## Task Type\n\n- {task_type}\n\n"
                f"## Evidence Summary\n\n"
                f"- compile: {compile_status}\n"
                f"- cec: {cec_status}\n"
                f"- qor: {qor_status}\n\n"
                f"## Hypothesis\n\n{hypothesis}\n\n"
                f"## Risk Controls\n\n{markdown_bullets(risk_controls)}"
                f"## Acceptance Criteria\n\n{markdown_bullets(acceptance)}"
                f"## Rollback Criteria\n\n{markdown_bullets(rollback)}"
            ),
            candidate_markdown=(
                "# Planner Candidate Dispatch\n\n"
                f"- Selected agent: {selected_agent}\n"
                f"- Task type: {task_type}\n"
                f"- Task for coding agent:\n\n{coding_task}\n\n"
                f"- Source patch mode: "
                f"{data.get('source_patch_mode', 'source_patch_diff')}\n"
                f"- Evidence files read: {', '.join(evidence.keys())}\n"
            ),
            feedback_markdown=(
                "# Planning Feedback\n\n"
                "Planning Agent produced a next-cycle plan. "
                "Feedback from the coding agent's validation and the "
                "review gate will be appended after execution.\n"
            ),
            rule_update_markdown=(
                "# Rulebase Update Proposal\n\n"
                f"{markdown_bullets(rulebase_notes)}"
            ),
            decision="PROPOSE_CANDIDATE",
        )

    # ------------------------------------------------------------------
    # Deterministic fallback
    # ------------------------------------------------------------------

    def plan_deterministic(self) -> AgentArtifacts:
        """Run the deterministic engine instead of calling the LLM.

        Useful when the model is not configured, token budget is exhausted,
        or a quick plan is needed before the remote run.
        """
        engine = PlanningEngine(self.context.repo_root)
        result = engine.plan(self.context.cycle_id)
        if result is None:
            return AgentArtifacts(
                plan_markdown=(
                    "# Planning Agent Plan — fallback\n\n"
                    "No previous cycle evidence found. Use the default "
                    "first-cycle Flow Agent assignment targeting csweep.\n"
                ),
                candidate_markdown=(
                    "# Planner Candidate Dispatch — fallback\n\n"
                    "- Selected agent: flow_agent\n"
                    "- Task type: optimization (first cycle)\n"
                    "- Target: csweep cut/leaf floors in Csw_Sweep\n"
                ),
                feedback_markdown=(
                    "# Planning Feedback\n\n"
                    "Deterministic engine fallback — no LLM call made.\n"
                ),
                rule_update_markdown=(
                    "# Rulebase Update Proposal\n\n- None.\n"
                ),
                decision="PROPOSE_CANDIDATE",
            )

        return AgentArtifacts(
            plan_markdown=(
                f"# Planning Agent Plan — deterministic\n\n"
                f"## Objective\n\n{result.hypothesis}\n\n"
                f"## Selected Coding Agent\n\n- flow_agent\n\n"
                f"## Task Type\n\n- {result.strategy.task_type}\n\n"
                f"## Thresholds\n\n"
                f"- avg >= {result.thresholds.min_average_and_improve_pct:.1f}%\n"
                f"- total reduction >= {result.thresholds.min_total_and_reduction}\n"
                f"- improved >= {result.thresholds.min_improved_benchmarks}\n"
            ),
            candidate_markdown=(
                "# Planner Candidate Dispatch — deterministic\n\n"
                f"- Selected agent: flow_agent\n"
                f"- Task type: {result.strategy.task_type}\n"
                f"- Target command: {result.strategy.target_command}\n"
                f"- Target source dir: {result.strategy.target_source_dir}\n"
                f"- Skip LLM: {result.strategy.should_skip_llm}\n\n"
                f"## Hypothesis\n\n{result.hypothesis}\n"
            ),
            feedback_markdown=(
                "# Planning Feedback\n\n"
                "Deterministic engine used — no LLM call made.\n"
                f"Strategy rationale: {result.strategy.rationale}\n"
            ),
            rule_update_markdown=(
                "# Rulebase Update Proposal\n\n"
                f"- Threshold adjustment: {result.thresholds.adjustment_reason}\n"
            ),
            decision="PROPOSE_CANDIDATE",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _prompt_values(self, evidence: Mapping[str, str]) -> dict[str, object]:
        assignment = self.context.assignment
        repo_root = self.context.repo_root
        previous_cycle = str(assignment.get("previous_cycle_id", "cycle_000"))

        compile_feedback = evidence.get(
            "compile_or_build", "No compile/build evidence provided."
        )
        cec_feedback = evidence.get(
            "cec_or_correctness", "No CEC evidence provided."
        )
        qor_feedback = evidence.get(
            "qor_or_metrics", "No QoR evidence provided."
        )

        champion_summary = self._champion_summary()

        return {
            "REPO_ROOT": str(repo_root),
            "CYCLE_ID": self.context.cycle_id,
            "MODE": "candidate_generation",
            "TIME_BUDGET": "2-3 hours per cycle (remote Linux host)",
            "COMPUTE_BUDGET": "single machine, sequential evaluation",
            "REMOTE_OR_LOCAL": "remote — ABC build, CEC, and QoR run on Linux host",
            "ABC_BINARY": str(
                assignment.get("baseline_abc_bin", "third_party/FlowTune/abc")
            ),
            "CURRENT_CHAMPION_SUMMARY": champion_summary,
            "COMPILE_FEEDBACK": compact_text_block(
                "compile", str(compile_feedback), max_chars=4000
            ),
            "CEC_FEEDBACK": compact_text_block(
                "cec", str(cec_feedback), max_chars=4000
            ),
            "QOR_FEEDBACK": compact_text_block(
                "qor", str(qor_feedback), max_chars=8000
            ),
            "RUNTIME_FEEDBACK": "Runtime data from remote Linux host.",
            "REJECTED_CANDIDATES": self._rejected_candidates_text(previous_cycle),
            "PRIMARY_METRIC": assignment.get(
                "target_metric", "AND node count"
            ),
            "BENCHMARK_SUITES": str(
                assignment.get("benchmark_scope", "EPFL + ISCAS85 + ISCAS89")
            ),
            "FLOW_CONFIGS": str(
                assignment.get(
                    "evaluation_flow_commands",
                    list(DEFAULT_EVAL_FLOW_COMMANDS),
                )
            ),
            "RULEBASE": load_template(
                repo_root, "configs/agents/shared/rulebase.md"
            ),
        }

    def _champion_summary(self) -> str:
        assignment = self.context.assignment
        champion_cycle = assignment.get("champion_cycle_id", "")
        if not champion_cycle or assignment.get("baseline_kind") == "vanilla":
            return (
                "No champion yet — using vanilla ABC/FlowTune binary as baseline. "
                "The first champion will be promoted when a candidate passes "
                "build, CEC, and QoR thresholds."
            )
        return (
            f"Champion from {champion_cycle} "
            f"(candidate {assignment.get('champion_candidate_id', '')}). "
            f"Source root: {assignment.get('champion_source_root', '')}. "
            f"Binary: {assignment.get('champion_abc_bin', '')}."
        )

    def _rejected_candidates_text(self, previous_cycle: str) -> str:
        review_path = (
            self.context.repo_root
            / "experiments"
            / previous_cycle
            / "impl_compare"
            / "comparison"
            / "review_decision.json"
        )
        if not review_path.is_file():
            return "No rejected candidates yet."

        import json

        try:
            payload = json.loads(review_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return "Could not read review decision."

        decision = str(payload.get("decision", "missing"))
        if decision == "ACCEPT_FOR_NEXT_CYCLE":
            return f"Previous candidate ({previous_cycle}) was ACCEPTED — no rejections."
        return (
            f"Previous candidate ({previous_cycle}) was {decision}. "
            f"Reason: {payload.get('reason', 'unknown')}. "
            f"Next action: {payload.get('next_action', 'unknown')}."
        )

    def _validate_rendered_prompt(self, prompt: str) -> None:
        unresolved = find_unresolved_placeholders(prompt)
        if unresolved:
            raise ValueError(
                "unresolved Planning Agent prompt placeholders: "
                + ", ".join(unresolved)
            )

        forbidden = (".env", "API_KEY", "EDA_AGENT_MODEL_API_KEY")
        leaked = [item for item in forbidden if item in prompt]
        if leaked:
            raise ValueError(
                "rendered prompt contains forbidden secret markers."
            )

        if "TODO_PLANNER_PROMPT_RENDER" in prompt:
            raise ValueError(
                "Planning Agent prompt still contains scaffold TODO."
            )

