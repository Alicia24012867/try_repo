"""Evaluation planning helpers for materialized Flow Agent candidates."""

from __future__ import annotations

import argparse
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from scripts.agents.self_evolved_abc.cycle_context import CycleContext
from scripts.agents.self_evolved_abc.flow.materialization import (
    candidate_flow_relative_path,
)


ABC_RC_PATH = Path("third_party") / "FlowTune" / "abc.rc"
BASELINE_LABEL = "vanilla_strash"
CANDIDATE_LABEL = "candidate_flow"
CORRECTNESS_PROVISIONAL = "provisional_no_cec"
EXPECTED_METRICS = (
    "abc_exit_code",
    "aig_nodes",
    "aig_depth",
    "runtime_seconds",
    "skipped_reason",
    "correctness_status",
)
RESULT_CSV_FIELDS = (
    "benchmark",
    "flow_label",
    "abc_exit_code",
    "aig_nodes",
    "aig_depth",
    "runtime_seconds",
    "skipped_reason",
    "correctness_status",
)


@dataclass(frozen=True)
class FlowEvaluationCase:
    """One benchmark/candidate-flow pair to evaluate."""

    benchmark: Path
    candidate_flow: Path
    baseline_label: str
    candidate_label: str
    cycle_id: str


@dataclass(frozen=True)
class FlowEvaluationCommand:
    """One executable ABC command and the metrics expected from its log."""

    benchmark: Path
    abc_script: str
    command: str
    expected_metrics: tuple[str, ...]


@dataclass(frozen=True)
class FlowResultRow:
    """Stable CSV result row shape for flow evaluation outputs."""

    benchmark: str
    flow_label: str
    abc_exit_code: int | None
    aig_nodes: int | None
    aig_depth: int | None
    runtime_seconds: float | None
    skipped_reason: str
    correctness_status: str


def build_flow_evaluation_cases(
    *,
    context: CycleContext,
    candidate_flow: Path,
) -> tuple[FlowEvaluationCase, ...]:
    """Build one evaluation case per benchmark in assignment scope."""

    candidate_flow_relative = _repo_relative_existing_path(context, candidate_flow)
    cases: list[FlowEvaluationCase] = []
    for benchmark_text in context.benchmark_scope:
        benchmark_relative = _repo_relative_existing_path(context, Path(benchmark_text))
        cases.append(
            FlowEvaluationCase(
                benchmark=benchmark_relative,
                candidate_flow=candidate_flow_relative,
                baseline_label=BASELINE_LABEL,
                candidate_label=CANDIDATE_LABEL,
                cycle_id=context.cycle_id,
            )
        )
    return tuple(cases)


def render_abc_qor_command(case: FlowEvaluationCase) -> FlowEvaluationCommand:
    """Render the ABC command used to collect provisional QoR."""

    abc_commands = (
        f"source {ABC_RC_PATH}",
        f"read {case.benchmark}",
        f"source {case.candidate_flow}",
        "strash",
        f"write_aiger {_output_path(case, case.candidate_label)}",
        "ps",
    )
    abc_script = render_abc_script(abc_commands)
    return FlowEvaluationCommand(
        benchmark=case.benchmark,
        abc_script=abc_script,
        command=render_abc_shell_command("abc", abc_script),
        expected_metrics=EXPECTED_METRICS,
    )


def render_baseline_qor_command(case: FlowEvaluationCase) -> FlowEvaluationCommand:
    """Render the baseline ABC command for the same benchmark."""

    abc_commands = (
        f"source {ABC_RC_PATH}",
        f"read {case.benchmark}",
        "strash",
        f"write_aiger {_output_path(case, case.baseline_label)}",
        "ps",
    )
    abc_script = render_abc_script(abc_commands)
    return FlowEvaluationCommand(
        benchmark=case.benchmark,
        abc_script=abc_script,
        command=render_abc_shell_command("abc", abc_script),
        expected_metrics=EXPECTED_METRICS,
    )


def render_abc_script(commands: Sequence[str]) -> str:
    """Render the command payload passed to `abc -c`."""

    return "; ".join(commands)


def render_abc_shell_command(abc_bin: str, abc_script: str) -> str:
    """Render a pasteable shell command for logs and manual execution."""

    return shlex.join((abc_bin, "-c", abc_script))


def render_abc_argv(abc_bin: str, abc_script: str) -> tuple[str, str, str]:
    """Render argv for subprocess execution without a shell layer."""

    return (abc_bin, "-c", abc_script)


def render_manual_cec_note(case: FlowEvaluationCase) -> str:
    """Describe the CEC gate until automated CEC is wired."""

    baseline_output = _output_path(case, case.baseline_label)
    candidate_output = _output_path(case, case.candidate_label)
    return (
        "CEC is not automated in F5. The commands above write "
        f"`{baseline_output}` and `{candidate_output}`. Check equivalence with "
        f"`abc -c \"cec {baseline_output} {candidate_output}\"`. Until then, "
        f"set correctness_status to `{CORRECTNESS_PROVISIONAL}`."
    )


def write_flow_evaluation_plan(
    *,
    context: CycleContext,
    candidate_flow: Path,
    output_path: Path,
) -> Path:
    """Write a markdown evaluation plan for the candidate flow."""

    cases = build_flow_evaluation_cases(
        context=context,
        candidate_flow=candidate_flow,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        render_flow_evaluation_plan_markdown(context=context, cases=cases),
        encoding="utf-8",
    )
    return output_path


def write_results_readme(*, context: CycleContext, output_path: Path) -> Path:
    """Write the result directory contract consumed by the review stage."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_results_readme(context), encoding="utf-8")
    return output_path


def result_csv_header() -> tuple[str, ...]:
    """Return the stable result CSV schema."""

    return RESULT_CSV_FIELDS


def render_flow_evaluation_plan_markdown(
    *,
    context: CycleContext,
    cases: tuple[FlowEvaluationCase, ...],
) -> str:
    """Render a reviewable plan with executable ABC commands."""

    lines = [
        f"# Flow Evaluation Plan -- {context.cycle_id} {context.candidate_id}",
        "",
        "## Scope",
        "",
        f"- Agent: {context.agent_name}",
        f"- Candidate flow: `{cases[0].candidate_flow if cases else candidate_flow_relative_path(context)}`",
        f"- Benchmark count: {len(cases)}",
        "- ABC command root: run from the repository root on the execution host.",
        "- Correctness status before CEC: `provisional_no_cec`.",
        "",
        "## Result CSV Schema",
        "",
        "```text",
        ",".join(result_csv_header()),
        "```",
        "",
        "## Commands",
        "",
    ]

    for case in cases:
        baseline = render_baseline_qor_command(case)
        candidate = render_abc_qor_command(case)
        baseline_log = _log_path(case, case.baseline_label)
        candidate_log = _log_path(case, case.candidate_label)
        lines.extend(
            (
                f"### {case.benchmark}",
                "",
                f"- Baseline label: `{case.baseline_label}`",
                f"- Candidate label: `{case.candidate_label}`",
                f"- Baseline log: `{baseline_log}`",
                f"- Candidate log: `{candidate_log}`",
                "",
                "Baseline:",
                "",
                "```bash",
                baseline.command,
                "```",
                "",
                "Candidate:",
                "",
                "```bash",
                candidate.command,
                "```",
                "",
                "CEC note:",
                "",
                f"- {render_manual_cec_note(case)}",
                "",
            )
        )

    lines.extend(
        (
            "## Runner Responsibilities",
            "",
            f"- Capture stdout and stderr for every command under `experiments/{context.cycle_id}/logs/`.",
            "- Record wall-clock runtime outside ABC and store it as `runtime_seconds`.",
            "- Parse `ps` output into `aig_nodes` and `aig_depth`.",
            f"- Preserve AIG outputs under `experiments/{context.cycle_id}/outputs/` for later CEC.",
            "- Write one row per benchmark and flow label using the CSV schema above.",
            "- Do not promote QoR while `correctness_status` is `provisional_no_cec`.",
            "",
        )
    )
    return "\n".join(lines)


def render_results_readme(context: CycleContext) -> str:
    """Render the cycle result directory contract."""

    header = ",".join(result_csv_header())
    return "\n".join(
        (
            f"# {context.cycle_id} Flow Results",
            "",
            "This directory receives synchronized results from the execution host.",
            "F5 defines the contract; F6 or later scripts may populate the files.",
            "",
            "## Required Files",
            "",
            "- `flow_summary.csv`: one row per benchmark and flow label.",
            "- `run_notes.md`: short human-readable summary of skips, failures, and caveats.",
            "- Optional raw logs are stored under `../logs/`.",
            "- AIG outputs are stored under `../outputs/` for later CEC.",
            "",
            "## CSV Header",
            "",
            "```text",
            header,
            "```",
            "",
            "## Status Rules",
            "",
            "- Use `provisional_no_cec` until automated or manual CEC has passed.",
            "- Use `not_run` when a command did not execute.",
            "- Fill `skipped_reason` for every skipped, timed-out, crashed, or unparseable case.",
            "- Do not use provisional QoR rows as champion-promotion evidence.",
            "",
        )
    )


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the F5 Flow Agent evaluation plan."
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--assignment",
        type=Path,
        required=True,
        help="Cycle assignment JSON.",
    )
    parser.add_argument(
        "--candidate-flow",
        type=Path,
        default=None,
        help="Candidate ABC flow path. Defaults to configs/flows/<cycle>_<candidate>.abc.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("configs/evaluation/flow_cycle_001.md"),
        help="Markdown evaluation plan path.",
    )
    parser.add_argument(
        "--results-readme",
        type=Path,
        default=None,
        help="Result directory README path. Defaults to experiments/<cycle>/results/README.md.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    context = CycleContext.from_assignment_file(args.repo_root, args.assignment)
    candidate_flow = args.candidate_flow or candidate_flow_relative_path(context)
    output_path = (
        args.output if args.output.is_absolute() else context.repo_root / args.output
    )
    results_readme = (
        args.results_readme
        if args.results_readme is not None and args.results_readme.is_absolute()
        else context.repo_root
        / (
            args.results_readme
            if args.results_readme is not None
            else Path("experiments") / context.cycle_id / "results" / "README.md"
        )
    )

    plan_path = write_flow_evaluation_plan(
        context=context,
        candidate_flow=candidate_flow,
        output_path=output_path,
    )
    readme_path = write_results_readme(context=context, output_path=results_readme)

    cases = build_flow_evaluation_cases(context=context, candidate_flow=candidate_flow)
    print(f"evaluation_plan: {plan_path}")
    print(f"results_readme: {readme_path}")
    print(f"case_count: {len(cases)}")
    print("result_csv_header: " + ",".join(result_csv_header()))
    return 0


def _repo_relative_existing_path(context: CycleContext, path: Path) -> Path:
    resolved = context.resolve_repo_path(str(path))
    if not resolved.exists():
        raise FileNotFoundError(f"required evaluation input is missing: {path}")
    return resolved.relative_to(context.repo_root)


def _log_path(case: FlowEvaluationCase, flow_label: str) -> Path:
    return (
        Path("experiments")
        / case.cycle_id
        / "logs"
        / f"{case.benchmark.stem}.{flow_label}.log"
    )


def _output_path(case: FlowEvaluationCase, flow_label: str) -> Path:
    return (
        Path("experiments")
        / case.cycle_id
        / "outputs"
        / f"{case.benchmark.stem}.{flow_label}.aig"
    )


if __name__ == "__main__":
    raise SystemExit(main())
