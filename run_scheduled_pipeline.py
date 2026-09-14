"""
Unattended scrape -> enrich -> outreach cycle.

Designed for Windows Task Scheduler, cron, or a cloud cron/worker.
A lockfile prevents overlapping runs. Logs append to logs/pipeline.log.

Usage:
    python run_scheduled_pipeline.py
    python run_scheduled_pipeline.py --mock --skip-setup
    python run_scheduled_pipeline.py --interval-hours 12

Environment (optional):
    PIPELINE_MOCK=1              # no LLM credits
    PIPELINE_SKIP_SCRAPE=1
    PIPELINE_SKIP_SETUP=1        # default on scheduled runs
    PIPELINE_FIXTURE=1
    PIPELINE_FORCE_OUTREACH=1
    PIPELINE_INTERVAL_HOURS=12   # loop forever (worker mode)
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from meridian.brand import error, info, success, warn

LOG_DIR = ROOT / "logs"
LOCK_PATH = LOG_DIR / "pipeline.lock"
LOG_PATH = LOG_DIR / "pipeline.log"
STALE_LOCK_SECONDS = 3 * 60 * 60


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _append_log(line: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line.rstrip() + "\n")


def acquire_lock() -> bool:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if LOCK_PATH.exists():
        age = time.time() - LOCK_PATH.stat().st_mtime
        if age < STALE_LOCK_SECONDS:
            warn("schedule", f"Pipeline already running (lock {age:.0f}s old) — skipping")
            return False
        warn("schedule", "Stale lock found — taking over")
    LOCK_PATH.write_text(f"{os.getpid()}\n{datetime.now(timezone.utc).isoformat()}\n", encoding="utf-8")
    return True


def release_lock() -> None:
    try:
        LOCK_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Meridian pipeline on a schedule.")
    parser.add_argument("--mock", action="store_true", help="Enrich and draft without LLM credits.")
    parser.add_argument("--fixture", action="store_true", help="Use local fixture buyers instead of Apify.")
    parser.add_argument("--skip-scrape", action="store_true", help="Skip the scraper step.")
    parser.add_argument("--skip-setup", action="store_true", help="Skip schema verify/seed.")
    parser.add_argument("--force-outreach", action="store_true", help="Overwrite existing outreach drafts.")
    parser.add_argument(
        "--interval-hours",
        type=float,
        default=0,
        help="If greater than 0, sleep and repeat forever (worker mode).",
    )
    return parser.parse_args()


def resolve_flags(args: argparse.Namespace) -> list[str]:
    flags: list[str] = []
    # Scheduled cycles skip schema/seed unless PIPELINE_RUN_SETUP=1.
    run_setup = _truthy("PIPELINE_RUN_SETUP") and not args.skip_setup
    if not run_setup:
        flags.append("--skip-setup")
    if args.mock or _truthy("PIPELINE_MOCK"):
        flags.append("--mock")
    if args.fixture or _truthy("PIPELINE_FIXTURE"):
        flags.append("--fixture")
    if args.skip_scrape or _truthy("PIPELINE_SKIP_SCRAPE"):
        flags.append("--skip-scrape")
    if args.force_outreach or _truthy("PIPELINE_FORCE_OUTREACH"):
        flags.append("--force-outreach")
    return flags


def run_once(flags: list[str]) -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    command = [sys.executable, str(ROOT / "run_pipeline.py"), *flags]
    info("schedule", f"{stamp} $ {' '.join(command)}")
    _append_log(f"{stamp} START {' '.join(command)}")
    if not acquire_lock():
        _append_log(f"{stamp} SKIP locked")
        return 0
    try:
        result = subprocess.run(command, cwd=ROOT)
        _append_log(f"{stamp} EXIT {result.returncode}")
        if result.returncode == 0:
            success("schedule", "Scheduled cycle finished")
        else:
            error("schedule", f"Scheduled cycle exited {result.returncode}")
        return result.returncode
    finally:
        release_lock()


def main() -> None:
    load_dotenv()
    args = parse_args()
    flags = resolve_flags(args)
    interval = args.interval_hours or float(os.getenv("PIPELINE_INTERVAL_HOURS", "0") or 0)
    if interval <= 0:
        sys.exit(run_once(flags))
    info("schedule", f"Worker mode — repeating every {interval:g} hours")
    while True:
        run_once(flags)
        time.sleep(max(60.0, interval * 3600))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        release_lock()
        sys.exit(130)
