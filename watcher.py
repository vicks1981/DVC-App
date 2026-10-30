#!/usr/bin/env python3
"""
watcher.py - Disney Vacation Club Room Availability Watcher & Alert Daemon

Monitors specified DVC resorts, room categories, and date ranges at regular intervals.
Sends instant macOS desktop notifications (and optional mobile push) the moment 
a room becomes available to book.
"""

import sys
import os
import time
import random
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

    banner = (
        f"[bold cyan]Disney Vacation Club - Availability Watcher[/bold cyan]\n"
        f"Watching: [bold green]{', '.join(resorts)}[/bold green]\n"
        f"Room Type: [bold yellow]{room_type.title()}[/bold yellow]\n"
        f"Dates: [bold white]{check_in} -> {check_out}[/bold white]\n"
        f"Check Interval: [bold cyan]Every ~{interval_minutes} minutes[/bold cyan]\n"
        f"Alert Channel: [bold magenta]macOS Native Notifications[/bold magenta] (Glass chime)"
    )
    console.print(Panel.fit(banner, border_style="cyan"))

    check_count = 0

    while True:
        check_count += 1
        now_str = datetime.now(ET).strftime("%I:%M:%S %p %Z")
        console.print(f"\n[cyan][#{check_count} @ {now_str}][/cyan] Checking DVC availability...")

        try:
            data = availability.check_dvc_availability(
                resorts=resorts,
                check_in=check_in,
                check_out=check_out,
                room_type=room_type,
                headed=False
            )
            session_alert_sent = False  # Reset session alert if successful

            results = data.get("results", [])
            open_rooms = [r for r in results if r.get("is_available")]

            if open_rooms:
                console.print(f"[bold green]🎉 FOUND {len(open_rooms)} AVAILABLE ROOM(S)![/bold green]")
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
                # If a room was previously available but is now gone, reset tracking
                previously_available.clear()

        except PermissionError as e:
            console.print(f"[bold red]Session Error:[/bold red] {e}")
            if not session_alert_sent:
                notifications.notify(
                    title="DVC Watcher Alert",
                    message="Your Disney session has expired. Please run 'python dvc.py login' to resume tracking."
                )
                session_alert_sent = True

        except Exception as e:
            console.print(f"[red]Check encountered an error: {e}[/red]")

        if run_once:
            break

        # Calculate sleep with random jitter (e.g., ±90 seconds)
        jitter = random.uniform(-90, 90)
        sleep_secs = max(180, int((interval_minutes * 60) + jitter))
        next_check = datetime.now(ET).timestamp() + sleep_secs
        next_str = datetime.fromtimestamp(next_check, ET).strftime("%I:%M:%S %p %Z")
        console.print(f"[dim]Sleeping for {sleep_secs // 60}m {sleep_secs % 60}s... Next check at {next_str}. Press Ctrl+C to stop.[/dim]")

        try:
            time.sleep(sleep_secs)
        except KeyboardInterrupt:
            console.print("\n[yellow]Watcher stopped by user.[/yellow]")
            break

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
