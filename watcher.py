#!/usr/bin/env python3
"""
watcher.py - Disney Vacation Club Room Availability Watcher & Alert Daemon

Monitors specified DVC resorts, room categories, and date ranges at regular intervals.
Sends instant macOS desktop notifications (and optional mobile push) the moment 
a room becomes available to book.
"""

import sys
import os
import json
import time
import random
import signal
import fcntl
import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

import availability
import notifications

console = Console()
ET = ZoneInfo("America/New_York")
DIR = Path(__file__).parent
WATCHER_STATE_FILE = DIR / "watcher_state.json"


def _update_watcher_state(watcher_id: str, entry: dict = None):
    """Safely update or remove a watcher entry in watcher_state.json."""
    lock_path = DIR / ".watcher_state.lock"
    try:
        with open(lock_path, "w") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            state = {}
            if WATCHER_STATE_FILE.exists():
                try:
                    with open(WATCHER_STATE_FILE, "r") as f:
                        state = json.load(f)
                        if not isinstance(state, dict):
                            state = {}
                except Exception:
                    state = {}

            if entry is None:
                state.pop(watcher_id, None)
            else:
                state[watcher_id] = entry

            tmp_file = WATCHER_STATE_FILE.with_suffix(".tmp")
            with open(tmp_file, "w") as f:
                json.dump(state, f, indent=2)
            tmp_file.replace(WATCHER_STATE_FILE)
            fcntl.flock(lock_file, fcntl.LOCK_UN)
    except Exception:
        pass


def run_watcher(
    resorts: list[str],
    room_type: str,
    check_in: str,
    check_out: str,
    interval_minutes: int = 15,
    run_once: bool = False
):
    """
    Main watcher loop. Checks availability periodically and fires notifications.
    """
    # Track which rooms were already alerted to avoid notification spam
    previously_available = set()
    session_alert_sent = False

    pid = os.getpid()
    watcher_id = f"watcher_{pid}"
    normalized_resorts = [availability.resolve_resort_code(r) for r in resorts]
    resort_names = [availability.RESORT_MAP.get(c, c) for c in normalized_resorts]
    friendly_room = availability.ROOM_TYPES.get(room_type.lower(), room_type.title())
    started_at = datetime.now(ET).strftime("%b %d, %Y %I:%M %p %Z")

    state_entry = {
        "id": watcher_id,
        "pid": pid,
        "resorts": normalized_resorts,
        "resort_names": resort_names,
        "room_type": friendly_room,
        "room_arg": room_type,
        "check_in": check_in,
        "check_out": check_out,
        "interval_minutes": interval_minutes,
        "started_at": started_at,
        "status": "checking",
        "status_text": "Starting initial DVC availability check...",
        "check_count": 0,
        "last_checked": None,
        "next_check": None,
        "latest_results": [],
        "open_rooms_count": 0
    }

    def handle_stop(signum, frame):
        _update_watcher_state(watcher_id, None)
        console.print("\n[yellow]Watcher stopped.[/yellow]")
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)

    banner = (
        f"[bold cyan]Disney Vacation Club - Availability Watcher[/bold cyan]\n"
        f"Watching: [bold green]{', '.join(normalized_resorts)}[/bold green]\n"
        f"Room Type: [bold yellow]{friendly_room}[/bold yellow]\n"
        f"Dates: [bold white]{check_in} -> {check_out}[/bold white]\n"
        f"Check Interval: [bold cyan]Every ~{interval_minutes} minutes[/bold cyan]\n"
        f"Alert Channel: [bold magenta]macOS Native Notifications[/bold magenta] (Glass chime)"
    )
    console.print(Panel.fit(banner, border_style="cyan"))

    check_count = 0

    try:
        while True:
            check_count += 1
            now_str = datetime.now(ET).strftime("%I:%M:%S %p %Z")
            console.print(f"\n[cyan][#{check_count} @ {now_str}][/cyan] Checking DVC availability...")

            state_entry["check_count"] = check_count
            state_entry["status"] = "checking"
            state_entry["status_text"] = f"Checking DVC availability (Check #{check_count})..."
            _update_watcher_state(watcher_id, state_entry)

            try:
                data = availability.check_dvc_availability(
                    resorts=normalized_resorts,
                    check_in=check_in,
                    check_out=check_out,
                    room_type=room_type,
                    headed=False
                )
                session_alert_sent = False  # Reset session alert if successful

                results = data.get("results", [])
                open_rooms = [r for r in results if r.get("is_available")]
                checked_str = datetime.now(ET).strftime("%I:%M:%S %p %Z")
                state_entry["last_checked"] = checked_str
                state_entry["latest_results"] = results
                state_entry["open_rooms_count"] = len(open_rooms)

                if open_rooms:
                    console.print(f"[bold green]🎉 FOUND {len(open_rooms)} AVAILABLE ROOM(S)![/bold green]")
                    state_entry["status"] = "available"
                    state_entry["status_text"] = f"Found {len(open_rooms)} available room(s)!"
                    for r in open_rooms:
                        room_key = f"{r['resort_code']}_{r['room_type']}_{check_in}_{check_out}"
                        console.print(
                            f"  👉 [bold green]{r['resort_name']}[/bold green] - "
                            f"{r['room_type']} ({r['points_total']} pts)"
                        )

                        # Only notify if not already notified during this availability window
                        if room_key not in previously_available:
                            title = f"DVC Room Available: {r['resort_code']}!"
                            msg = (
                                f"{r['resort_name']} ({r['room_type']}) is OPEN for "
                                f"{check_in} to {check_out} ({r['points_total']} pts)! Click to book."
                            )
                            notifications.notify(
                                title=title,
                                message=msg,
                                url="https://disneyvacationclub.disney.go.com/booking/"
                            )
                            previously_available.add(room_key)
                else:
                    console.print("[dim]No openings found yet (all waitlist/unavailable).[/dim]")
                    state_entry["status"] = "sleeping"
                    state_entry["status_text"] = "No openings found yet (Waitlist Only)"
                    # If a room was previously available but is now gone, reset tracking
                    previously_available.clear()

            except PermissionError as e:
                console.print(f"[bold red]Session Error:[/bold red] {e}")
                state_entry["last_checked"] = datetime.now(ET).strftime("%I:%M:%S %p %Z")
                state_entry["status"] = "error"
                state_entry["status_text"] = "Disney session expired. Run 'python dvc.py login'."
                if not session_alert_sent:
                    notifications.notify(
                        title="DVC Watcher Alert",
                        message="Your Disney session has expired. Please run 'python dvc.py login' to resume tracking."
                    )
                    session_alert_sent = True

            except Exception as e:
                console.print(f"[red]Check encountered an error: {e}[/red]")
                state_entry["last_checked"] = datetime.now(ET).strftime("%I:%M:%S %p %Z")
                state_entry["status"] = "error"
                state_entry["status_text"] = str(e)

            if run_once:
                break

            # Calculate sleep with random jitter (e.g., ±90 seconds)
            jitter = random.uniform(-90, 90)
            sleep_secs = max(180, int((interval_minutes * 60) + jitter))
            next_check = datetime.now(ET).timestamp() + sleep_secs
            next_str = datetime.fromtimestamp(next_check, ET).strftime("%I:%M:%S %p %Z")
            state_entry["next_check"] = next_str
            _update_watcher_state(watcher_id, state_entry)

            console.print(f"[dim]Sleeping for {sleep_secs // 60}m {sleep_secs % 60}s... Next check at {next_str}. Press Ctrl+C to stop.[/dim]")

            try:
                time.sleep(sleep_secs)
            except KeyboardInterrupt:
                console.print("\n[yellow]Watcher stopped by user.[/yellow]")
                break
    finally:
        _update_watcher_state(watcher_id, None)

def main():
    parser = argparse.ArgumentParser(description="Watch DVC room availability and send alerts")
    parser.add_argument("--resort", default="VWL", help="Resort code(s), comma-separated (e.g., VWL,RIV,BLT)")
    parser.add_argument("--room", default="studio", help="Room type: studio, 1-bed, 2-bed, 3-bed")
    parser.add_argument("--start", required=True, help="Check-in date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="Check-out date (YYYY-MM-DD)")
    parser.add_argument("--interval", type=int, default=15, help="Check interval in minutes (default: 15)")
    parser.add_argument("--once", action="store_true", help="Run once and exit without looping")

    args = parser.parse_args()
    resorts = [r.strip() for r in args.resort.split(",")]

    run_watcher(
        resorts=resorts,
        room_type=args.room,
        check_in=args.start,
        check_out=args.end,
        interval_minutes=args.interval,
        run_once=args.once
    )

if __name__ == "__main__":
    main()
