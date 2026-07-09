"""Shared command log rendering and parsing helpers."""

from __future__ import annotations


def render_command_log(
    *,
    command: str,
    return_code: int | None,
    runtime_seconds: float,
    output: str,
) -> str:
    return (
        f"command: {command}\n"
        f"return_code: {return_code}\n"
        f"runtime_seconds: {runtime_seconds:.6f}\n"
        "\n"
        "----- output -----\n"
        f"{output}"
    )
