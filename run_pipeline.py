"""
Run the four operational modules in sequence.

Usage:
    python run_pipeline.py
    python run_pipeline.py --fixture
    python run_pipeline.py --skip-scrape
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from meridian.brand import banner, error, info, success

ROOT = Path(__file__).resolve().parent
PYTHON = sys.executable


def run_step(script: str, extra: list[str] | None = None) -> None:
    command = [PYTHON, str(ROOT / script), *(extra or [])]
    info("pipeline", f"$ {' '.join(command)}")
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode != 0:
        error("pipeline", f"{script} exited {result.returncode}")
        sys.exit(result.returncode)
    success("pipeline", f"{script} complete")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Execute the Meridian Flow Network pipeline.")
    parser.add_argument("--fixture", action="store_true", help="Seed buyers from fixtures instead of Apify.")
    parser.add_argument("--skip-scrape", action="store_true", help="Skip the scraper module.")
    parser.add_argument("--skip-setup", action="store_true", help="Skip database verification/seed.")
    parser.add_argument("--mock", action="store_true", help="Enrich and draft outreach without LLM credits.")
    parser.add_argument("--force-outreach", action="store_true", help="Overwrite existing outreach_draft text.")
    return parser.parse_args()


def main() -> None:
    banner("full matching pipeline")
    args = parse_args()
    llm_flags = ["--mock"] if args.mock else []
    if not args.skip_setup:
        run_step("setup_schema.py")
        run_step("setup_database.py", ["--verify-only", "--seed"])
    if not args.skip_scrape:
        scrape_flags = ["--fixture"] if args.fixture else []
        run_step("run_scraper.py", scrape_flags)
    run_step("enrich_and_match.py", llm_flags)
    outreach_flags = [*llm_flags]
    if args.force_outreach:
        outreach_flags.append("--force")
    run_step("generate_outreach.py", outreach_flags)
    success("pipeline", "Meridian Flow Network cycle finished")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
