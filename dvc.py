#!/usr/bin/env python3
"""
dvc.py - Disney Vacation Club Unified CLI

Manage points, upcoming trips, and past reservations in one place.

Usage:
  python dvc.py points    # Check vacation points across Use Years
  python dvc.py trips     # Check upcoming trips & past 12 months reservations
  python dvc.py all       # Check both points and trips
  python dvc.py login     # Interactive session login
"""

import sys
import argparse
import subprocess
from pathlib import Path
from rich.console import Console

console = Console()
DIR = Path(__file__).parent

def run_script(script_name, extra_args):
    script_path = DIR / script_name
    python_bin = sys.executable
    cmd = [python_bin, str(script_path)] + extra_args
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        sys.exit(e.returncode)

def main():
    parser = argparse.ArgumentParser(
        description="Disney Vacation Club Automation CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  points   Check points balance breakdown by Use Year
  trips    Check upcoming vacations & past reservations (last year)
  windows  Calculate 11-month and 7-month booking windows
  search   One-off room availability check for resort(s) and dates
  watch    Start live room availability tracker and alert daemon
  web      Launch local web dashboard in your browser (http://localhost:5050)
  all      Check both points and trips
  login    Run interactive login to refresh auth_state.json
        """
    )
    parser.add_argument(
        "command",
        choices=["points", "trips", "windows", "search", "watch", "web", "all", "login"],
        help="Action to perform"
    )
    parser.add_argument("--headed", action="store_true", help="Run browser visibly")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    parser.add_argument("--debug", action="store_true", help="Save debug artifacts")

    args, remaining = parser.parse_known_args()

    forward_args = []
    if args.headed:
        forward_args.append("--headed")
    if args.json:
        forward_args.append("--json")
    if args.debug:
        forward_args.append("--debug")
    forward_args.extend(remaining)

    if args.command == "login":
        run_script("login.py", forward_args)
    elif args.command == "points":
        run_script("check_points.py", forward_args)
    elif args.command == "trips":
        run_script("check_trips.py", forward_args)
    elif args.command == "windows":
        run_script("booking_windows.py", forward_args)
    elif args.command == "search":
        run_script("availability.py", forward_args)
    elif args.command == "watch":
        run_script("watcher.py", forward_args)
    elif args.command == "web":
        run_script("app.py", forward_args)
    elif args.command == "all":
        run_script("check_points.py", forward_args)
        run_script("check_trips.py", forward_args)

if __name__ == "__main__":
    main()
