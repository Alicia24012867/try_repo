"""Shared ABC log parsing helpers for Flow evaluation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
PS_RE = re.compile(
    r"(?P<network>\S+)\s*:\s*"
    r"i/o\s*=\s*(?P<inputs>\d+)\s*/\s*(?P<outputs>\d+)\s+"
    r"lat\s*=\s*(?P<lat>\d+)\s+"
    r"and\s*=\s*(?P<ands>\d+)\s+"
    r"lev\s*=\s*(?P<lev>\d+)"
)


@dataclass(frozen=True)
class PsMetrics:
    """Metrics parsed from one ABC `ps` line."""

    ands: int
    lev: int
    inputs: int
    outputs: int
    lat: int


def strip_ansi(text: str) -> str:
    """Remove ANSI color/control sequences from ABC output."""

    return ANSI_RE.sub("", text)


def parse_last_ps_metrics_text(text: str) -> PsMetrics | None:
    """Return metrics from the last parseable ABC `ps` line in text."""

    last_match: re.Match[str] | None = None
    for match in PS_RE.finditer(strip_ansi(text)):
        last_match = match

    if last_match is None:
        return None
    return PsMetrics(
        ands=int(last_match.group("ands")),
        lev=int(last_match.group("lev")),
        inputs=int(last_match.group("inputs")),
        outputs=int(last_match.group("outputs")),
        lat=int(last_match.group("lat")),
    )


def parse_last_ps_metrics_file(path: Path) -> PsMetrics | None:
    """Return metrics from the last parseable ABC `ps` line in a file."""

    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    return parse_last_ps_metrics_text(text)


def parse_log_header_value(text: str, field: str) -> str | None:
    """Read `field: value` from the header written by local runners."""

    prefix = f"{field}:"
    for line in text.splitlines():
        if line == "----- output -----":
            break
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return None


def parse_log_header_int(text: str, field: str) -> int | None:
    """Read an integer `field: value` from a local runner log header."""

    value = parse_log_header_value(text, field)
    if value in (None, "None", ""):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse_log_header_float(text: str, field: str) -> float | None:
    """Read a float `field: value` from a local runner log header."""

    value = parse_log_header_value(text, field)
    if value in (None, "None", ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None
