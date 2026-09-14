"""Pitch-black / cyan brand surface for every CLI surface and log line."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

CYAN = "#00FFFF"
BLACK = "#000000"

THEME = Theme(
    {
        "m.info": f"bold {CYAN}",
        "m.warn": "bold yellow",
        "m.error": "bold red",
        "m.muted": f"dim {CYAN}",
        "m.label": f"bold {CYAN}",
    }
)

console = Console(
    theme=THEME,
    force_terminal=True,
    color_system="truecolor",
    legacy_windows=False,
    stderr=False,
)


def banner(subtitle: str) -> None:
    title = Text()
    title.append("MERIDIAN FLOW NETWORK", style=f"bold {CYAN}")
    title.append("\n")
    title.append(subtitle.upper(), style=f"dim {CYAN}")
    console.print(
        Panel(
            title,
            border_style=CYAN,
            style=f"{CYAN} on {BLACK}",
            padding=(1, 4),
        )
    )


def log(module: str, message: str, *, level: str = "info") -> None:
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    style = {
        "info": "m.info",
        "warn": "m.warn",
        "error": "m.error",
        "success": "m.info",
    }.get(level, "m.info")
    prefix = Text.assemble(
        (f"  {stamp}  ", f"dim {CYAN}"),
        (f"{module.upper():<12}", "m.label"),
        (message, style),
    )
    console.print(prefix)


def info(module: str, message: str) -> None:
    log(module, message, level="info")


def warn(module: str, message: str) -> None:
    log(module, message, level="warn")


def error(module: str, message: str) -> None:
    log(module, message, level="error")


def success(module: str, message: str) -> None:
    log(module, message, level="success")


def kv_table(title: str, rows: list[tuple[str, str]]) -> None:
    table = Table(
        title=title,
        title_style=f"bold {CYAN}",
        border_style=CYAN,
        header_style=f"bold {CYAN}",
        style=f"{CYAN} on {BLACK}",
        show_lines=False,
        pad_edge=True,
    )
    table.add_column("FIELD", style=f"dim {CYAN}")
    table.add_column("VALUE", style=CYAN)
    for key, value in rows:
        table.add_row(key, value)
    console.print(table)


def fail(module: str, message: str, code: int = 1) -> None:
    error(module, message)
    sys.exit(code)
