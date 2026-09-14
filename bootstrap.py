"""
Stdlib-only runtime bootstrap.

Finds a real Python interpreter (skips the Windows Store stub), creates
.venv if needed, installs requirements.txt, and re-execs the caller inside
that environment.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REQUIREMENTS = ROOT / "requirements.txt"
SETUP_MODULES = ("dotenv", "psycopg", "supabase", "rich")
BOOTSTRAP_FLAG = "MERIDIAN_BOOTSTRAPPED"

KNOWN_WINDOWS_PYTHONS = (
    Path.home() / "AppData" / "Local" / "Python" / "bin" / "python.exe",
    Path.home() / "AppData" / "Local" / "Programs" / "Python" / "Python314" / "python.exe",
    Path.home() / "AppData" / "Local" / "Programs" / "Python" / "Python313" / "python.exe",
    Path.home() / "AppData" / "Local" / "Programs" / "Python" / "Python312" / "python.exe",
    Path.home() / "AppData" / "Local" / "Programs" / "Python" / "Python311" / "python.exe",
)


def venv_python() -> Path:
    if os.name == "nt":
        return ROOT / ".venv" / "Scripts" / "python.exe"
    return ROOT / ".venv" / "bin" / "python"


def is_windows_store_stub(executable: str | Path) -> bool:
    text = str(executable).replace("/", "\\").lower()
    return "windowsapps" in text


def modules_missing(names: tuple[str, ...] = SETUP_MODULES) -> list[str]:
    missing: list[str] = []
    for name in names:
        try:
            __import__(name)
        except ImportError:
            missing.append(name)
    return missing


def _usable(executable: Path) -> bool:
    if not executable.is_file() or is_windows_store_stub(executable):
        return False
    try:
        result = subprocess.run(
            [str(executable), "-c", "import sys; print(sys.version_info[:2])"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and "(" in result.stdout


def discover_python() -> Path:
    venv = venv_python()
    if _usable(venv):
        return venv

    candidates: list[Path] = []
    if not is_windows_store_stub(sys.executable):
        candidates.append(Path(sys.executable))
    candidates.extend(KNOWN_WINDOWS_PYTHONS)
    for name in ("python", "python3", "py"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))

    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve() if candidate.exists() else candidate
        if resolved in seen:
            continue
        seen.add(resolved)
        if _usable(candidate):
            return candidate

    raise SystemExit(
        "No usable Python interpreter found.\n"
        "Install Python 3.10+ (not the Microsoft Store stub) and re-run.\n"
        f"On this machine a known good path is:\n  {KNOWN_WINDOWS_PYTHONS[0]}"
    )


def _run(command: list[str], *, label: str) -> None:
    print(f"[bootstrap] {label}")
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode != 0:
        raise SystemExit(f"[bootstrap] failed: {label} (exit {result.returncode})")


def ensure_runtime(*, reexec_script: str | None = None) -> Path:
    """
    Return the interpreter that should run project scripts.
    Re-execs the caller into .venv once dependencies are in place.
    """
    python = venv_python()
    already_in_venv = Path(sys.executable).resolve() == python.resolve()
    missing = modules_missing()

    if already_in_venv and not missing:
        os.environ[BOOTSTRAP_FLAG] = "1"
        return python

    if os.environ.get(BOOTSTRAP_FLAG) == "1" and missing:
        raise SystemExit(
            "[bootstrap] Still missing packages after install: "
            + ", ".join(missing)
        )

    creator = discover_python()
    if not python.exists():
        _run([str(creator), "-m", "venv", str(ROOT / ".venv")], label=f"create venv with {creator}")

    if missing or not already_in_venv:
        _run(
            [str(python), "-m", "pip", "install", "--upgrade", "pip"],
            label="upgrade pip in .venv",
        )
        if REQUIREMENTS.exists():
            _run(
                [str(python), "-m", "pip", "install", "-r", str(REQUIREMENTS)],
                label=f"install {REQUIREMENTS.name}",
            )

    if already_in_venv:
        os.environ[BOOTSTRAP_FLAG] = "1"
        return python

    script = reexec_script or str(Path(sys.argv[0]).resolve())
    os.environ[BOOTSTRAP_FLAG] = "1"
    # subprocess keeps paths with spaces intact; os.execv does not on Windows.
    completed = subprocess.run([str(python), script, *sys.argv[1:]], cwd=ROOT)
    raise SystemExit(completed.returncode)
