"""One-cycle Flow Agent autonomous feedback loop driver."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Sequence


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the Flow Agent source-patch feedback loop for one cycle."
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--assignment", type=Path, required=True)
    parser.add_argument(
        "--abc-bin",
        default=None,
        help="Compatibility shortcut: use the same ABC binary for baseline and candidate.",
    )
    parser.add_argument(
        "--baseline-abc-bin",
        default=None,
        help="Explicit baseline ABC binary for S5/F7. Defaults to S4 manifest.",
    )
    parser.add_argument(
        "--candidate-abc-bin",
        default=None,
        help="Explicit candidate ABC binary for S5/F7. Defaults to S4 manifest.",
    )
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--build-timeout-seconds", type=float, default=900.0)
    parser.add_argument("--build-jobs", type=int, default=4)
    parser.add_argument("--next-cycle", default=None)
    parser.add_argument("--force-next-assignment", action="store_true")
    parser.add_argument(
        "--skip-agent",
        action="store_true",
        help="Use already materialized candidate artifacts instead of calling the model.",
    )
    parser.add_argument(
        "--skip-patch-apply",
        action="store_true",
        help="Skip S4d patch application; useful for abc_flow-only cycles.",
    )
    parser.add_argument(
        "--build-candidate-binary",
        action="store_true",
        help="Build candidate ABC inside the isolated workspace before S5/F7.",
    )
    return parser.parse_args(argv)


def _run_agent_with_retry(
    *,
    repo_root: Path,
    assignment: Path,
    max_retries: int,
) -> None:
    """Run cycle_driver, retrying on NEEDS_HUMAN_REVIEW with validation feedback."""
    import json

    agent_cmd = (
        sys.executable,
        "-B",
        "-m",
        "scripts.agents.self_evolved_abc.cycle_driver",
        "--repo-root",
        str(repo_root),
        "--assignment",
        str(assignment),
        "--agent",
        "flow_agent",
    )

    for attempt in range(max_retries + 1):
        print(f"running: {' '.join(agent_cmd)}  (attempt {attempt + 1}/{max_retries + 1})")
        subprocess.run(agent_cmd, cwd=repo_root, check=False)

        # Derive cycle id from assignment path
        cycle_id = assignment.parent.parent.parent.name
        candidate = assignment.stem
        feedback_path = (
            repo_root
            / "experiments"
            / cycle_id
            / "agents"
            / "feedback"
            / f"{candidate}.md"
        )
        candidate_path = (
            repo_root
            / "experiments"
            / cycle_id
            / "agents"
            / "candidate_changes"
            / f"{candidate}.md"
        )

        if not candidate_path.is_file():
            return  # model call crashed, don't retry

        decision_text = candidate_path.read_text(encoding="utf-8", errors="replace")
        if "NEEDS_HUMAN_REVIEW" not in decision_text:
            return  # accepted or deferred — don't retry

        if attempt >= max_retries:
            print(
                f"iteration_loop: NEEDS_HUMAN_REVIEW after "
                f"{max_retries + 1} attempts — giving up"
            )
            return

        # Gather validation errors for the retry
        feedback_text = ""
        if feedback_path.is_file():
            feedback_text = feedback_path.read_text(encoding="utf-8", errors="replace")

        # Patch assignment with validation feedback as a repair hint
        try:
            payload = json.loads(assignment.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return

        hint = (
            "PREVIOUS ATTEMPT FAILED VALIDATION. Fix the following issues "
            "in your JSON response and try again:\n\n"
            f"{feedback_text[:3000]}"
        )
        original = str(payload.get("planner_hypothesis", ""))
        payload["planner_hypothesis"] = hint + "\n---\n" + original
        assignment.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print("iteration_loop: retrying with validation feedback in planner_hypothesis")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    repo_root = args.repo_root.resolve()
    assignment = args.assignment
    commands: list[tuple[tuple[str, ...], bool]] = []
    if not args.skip_agent:
        _run_agent_with_retry(
            repo_root=repo_root,
            assignment=assignment,
            max_retries=2,
        )
    if not args.skip_patch_apply:
        source_patch_command = [
            sys.executable,
            "-B",
            "-m",
            "scripts.agents.self_evolved_abc.flow.source_patch_runner",
            "--repo-root",
            str(repo_root),
            "--assignment",
            str(assignment),
            "--apply-candidate-patch",
            "--record-build-gate",
        ]
    else:
        source_patch_command = [
            sys.executable,
            "-B",
            "-m",
            "scripts.agents.self_evolved_abc.flow.source_patch_runner",
            "--repo-root",
            str(repo_root),
            "--assignment",
            str(assignment),
            "--record-build-gate",
        ]
    if args.build_candidate_binary:
        source_patch_command.extend(
            (
                "--build-candidate-binary",
                "--build-jobs",
                str(max(1, args.build_jobs)),
                "--build-timeout-seconds",
                f"{args.build_timeout_seconds:g}",
            )
        )
    commands.append((tuple(source_patch_command), True))

    baseline_abc_bin = args.baseline_abc_bin or args.abc_bin
    candidate_abc_bin = args.candidate_abc_bin or args.abc_bin
    compare_command = [
        sys.executable,
        "-B",
        "-m",
        "scripts.agents.self_evolved_abc.flow.implementation_compare",
        "--repo-root",
        str(repo_root),
        "--assignment",
        str(assignment),
        "--timeout-seconds",
        f"{args.timeout_seconds:g}",
    ]
    if baseline_abc_bin:
        compare_command.extend(("--baseline-abc-bin", baseline_abc_bin))
    if candidate_abc_bin:
        compare_command.extend(("--candidate-abc-bin", candidate_abc_bin))
    commands.extend(
        (
            (tuple(compare_command), True),
            ((
                sys.executable,
                "-B",
                "-m",
                "scripts.agents.self_evolved_abc.flow.review",
                "--repo-root",
                str(repo_root),
                "--assignment",
                str(assignment),
            ), True),
        )
    )
    next_command = [
        sys.executable,
        "-B",
        "-m",
        "scripts.agents.self_evolved_abc.flow.next_cycle",
        "--repo-root",
        str(repo_root),
        "--assignment",
        str(assignment),
    ]
    if args.next_cycle:
        next_command.extend(("--next-cycle", args.next_cycle))
    if args.force_next_assignment:
        next_command.append("--force")
    commands.append((tuple(next_command), False))

    final_return_code = 0
    for command, continue_on_failure in commands:
        print(f"running: {' '.join(command)}")
        completed = subprocess.run(command, cwd=repo_root, check=False)
        if completed.returncode != 0:
            final_return_code = completed.returncode
        if completed.returncode != 0 and not continue_on_failure:
            print(f"stopped: return_code={completed.returncode}")
            return completed.returncode
    return final_return_code


if __name__ == "__main__":
    raise SystemExit(main())
