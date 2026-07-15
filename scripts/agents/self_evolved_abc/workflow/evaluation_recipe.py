"""Materialise candidate-specific ABC evaluation recipes."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Sequence

from scripts.agents.self_evolved_abc.flow.contracts import (
    DEFAULT_EVAL_FLOW_COMMANDS,
    LEGACY_EVAL_FLOW_COMMANDS,
)


def ensure_evaluation_recipe(repo_root: Path, assignment_path: Path) -> Path:
    """Write the exact ordered recipe frozen in a coding assignment."""

    payload = json.loads(assignment_path.read_text(encoding="utf-8"))
    cycle_id = str(payload.get("cycle_id") or assignment_path.parent.parent.parent.name)
    candidate_id = str(payload.get("candidate_id") or assignment_path.stem)
    requested = tuple(
        str(command).strip()
        for command in payload.get("evaluation_flow_commands", ())
        if str(command).strip()
    )
    flow_dir = repo_root / "configs" / "flows"
    flow_path = flow_dir / f"{cycle_id}_{candidate_id}.abc"
    if requested and payload.get("source_patch_mode") != "abc_flow":
        desired = render_flow_recipe(requested)
        flow_dir.mkdir(parents=True, exist_ok=True)
        if not flow_path.is_file() or flow_path.read_text(
            encoding="utf-8", errors="replace"
        ) != desired:
            flow_path.write_text(desired, encoding="utf-8")
            print(f"workflow: wrote assignment evaluation recipe {flow_path.name}")
        return flow_path
    if flow_path.is_file():
        if flow_text_matches_commands(
            flow_path.read_text(encoding="utf-8", errors="replace"),
            LEGACY_EVAL_FLOW_COMMANDS,
        ):
            flow_path.write_text(
                render_flow_recipe(DEFAULT_EVAL_FLOW_COMMANDS),
                encoding="utf-8",
            )
            print(f"workflow: refreshed legacy flow recipe {flow_path.name}")
        return flow_path

    template = next(
        iter(sorted(flow_dir.glob("cycle_*_candidate_001.abc"))),
        None,
    )
    flow_dir.mkdir(parents=True, exist_ok=True)
    if template is not None:
        shutil.copy2(template, flow_path)
        if flow_text_matches_commands(
            flow_path.read_text(encoding="utf-8", errors="replace"),
            LEGACY_EVAL_FLOW_COMMANDS,
        ):
            flow_path.write_text(
                render_flow_recipe(DEFAULT_EVAL_FLOW_COMMANDS),
                encoding="utf-8",
            )
    else:
        flow_path.write_text(
            render_flow_recipe(DEFAULT_EVAL_FLOW_COMMANDS),
            encoding="utf-8",
        )
    return flow_path


def render_flow_recipe(commands: Sequence[str]) -> str:
    return "".join(f"{command.strip().rstrip(';')};\n" for command in commands)


def flow_text_matches_commands(text: str, commands: Sequence[str]) -> bool:
    normalized_lines = tuple(
        line.strip().rstrip(";")
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    expected = tuple(command.strip().rstrip(";") for command in commands)
    return normalized_lines == expected
