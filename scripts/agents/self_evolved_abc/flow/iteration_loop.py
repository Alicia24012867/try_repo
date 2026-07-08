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


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    repo_root = args.repo_root.resolve()
    assignment = args.assignment
    commands: list[tuple[tuple[str, ...], bool]] = []
    if not args.skip_agent:
        commands.append((
            (
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
            ),
            False,
        ))
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
