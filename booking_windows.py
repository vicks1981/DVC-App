#!/usr/bin/env python3
"""
booking_windows.py - Disney Vacation Club 11-Month & 7-Month Booking Window Calculator

Calculates exact DVC booking windows based on Disney's official rules:
- 11-Month Window: Home Resort Priority (Opens at 8:00 AM Eastern Time)
- 7-Month Window: All Other DVC Resorts (Opens at 8:00 AM Eastern Time)

Supports two calculation modes:
1. Today's Booking Horizons (Farthest check-in dates bookable right now)
2. Target Trip Calculator (Exact opening dates & countdowns for a future vacation)
"""

import sys
import json
import argparse
import calendar
from datetime import datetime, date, time
from pathlib import Path
from zoneinfo import ZoneInfo
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()

AUTH_STATE_FILE = Path(__file__).parent / "auth_state.json"
ET = ZoneInfo("America/New_York")

def parse_args():
    parser = argparse.ArgumentParser(
        description="Calculate DVC 11-Month and 7-Month Booking Windows."
    )
    parser.add_argument(
        "--date", "-d",
        type=str,
        help="Target check-in date (e.g., 2027-10-15 or 10/15/2027). Defaults to today's active windows."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw window data as JSON."
    )
    return parser.parse_args()

def add_months(source_date: date, months: int) -> date:
    """Add N months to a date, clamping to the last valid day of the month."""
    month = source_date.month - 1 + months
    year = source_date.year + month // 12
    month = month % 12 + 1
    max_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(source_date.day, max_day))

def subtract_months(source_date: date, months: int) -> date:
    """Subtract N months from a date, clamping to the last valid day of the month."""
    month = source_date.month - 1 - months
    year = source_date.year + month // 12
    month = month % 12 + 1
    max_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(source_date.day, max_day))

def parse_user_date(date_str: str) -> date:
    """Parse flexible date strings into a date object."""
    formats = [
        "%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y",
        "%Y/%m/%d", "%B %d, %Y", "%b %d, %Y"
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Could not parse date '{date_str}'. Expected formats like YYYY-MM-DD or MM/DD/YYYY.")

def format_countdown(target_dt: datetime, now_dt: datetime) -> str:
    """Format human-readable countdown to target datetime."""
    if now_dt >= target_dt:
        return "[bold green]OPEN NOW[/bold green]"
    diff = target_dt - now_dt
    days = diff.days
    hours, remainder = divmod(diff.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    if days > 0:
        return f"[yellow]Opens in {days}d {hours}h {minutes}m[/yellow]"
    return f"[bold yellow]Opens today in {hours}h {minutes}m[/bold yellow]"

def main():
    args = parse_args()
    now_et = datetime.now(ET)
    today_et = now_et.date()

    # Home resort label
    home_resort_name = "Home Resort (e.g. Boulder Ridge)"

    if args.date:
        # Mode 2: Future Target Check-In Date
        try:
            target_checkin = parse_user_date(args.date)
        except ValueError as e:
            console.print(f"[bold red]Error:[/bold red] {e}")
            sys.exit(1)

        home_11m_date = subtract_months(target_checkin, 11)
        other_7m_date = subtract_months(target_checkin, 7)

        home_open_dt = datetime.combine(home_11m_date, time(8, 0), tzinfo=ET)
        other_open_dt = datetime.combine(other_7m_date, time(8, 0), tzinfo=ET)

        home_is_open = now_et >= home_open_dt
        other_is_open = now_et >= other_open_dt

        if args.json:
            print(json.dumps({
                "target_check_in": target_checkin.isoformat(),
                "calculation_time_et": now_et.isoformat(),
                "home_resort_11_month": {
                    "opens_at": home_open_dt.isoformat(),
                    "is_open": home_is_open
                },
                "other_dvc_resorts_7_month": {
                    "opens_at": other_open_dt.isoformat(),
                    "is_open": other_is_open
                }
            }, indent=2))
            return

        console.print(Panel.fit(
            f"[bold cyan]DVC Booking Window Calculator[/bold cyan]\n"
            f"Planned Check-in Date: [bold white]{target_checkin.strftime('%A, %B %d, %Y')}[/bold white]\n"
            f"Current Time (ET): [dim]{now_et.strftime('%b %d, %Y %I:%M %p %Z')}[/dim]",
            border_style="cyan"
        ))

        table = Table(border_style="cyan", header_style="bold cyan")
        table.add_column("Window", style="bold white")
        table.add_column("Resorts Covered", style="white")
        table.add_column("Window Opens (8:00 AM ET)", style="yellow")
        table.add_column("Status / Countdown", justify="right")

        table.add_row(
            "11-Month Priority",
            home_resort_name,
            home_open_dt.strftime("%a, %b %d, %Y @ 8:00 AM ET"),
            format_countdown(home_open_dt, now_et)
        )
        table.add_row(
            "7-Month General",
            "All Other DVC Resorts",
            other_open_dt.strftime("%a, %b %d, %Y @ 8:00 AM ET"),
            format_countdown(other_open_dt, now_et)
        )

        console.print(table)
        console.print("\n[dim]* Both booking windows open online at 8:00 AM Eastern Time on DisneyVacationClub.com[/dim]\n")

    else:
        # Mode 1: Today's Farthest Bookable Check-In Dates
        # If it is before 8:00 AM ET today, today's new window hasn't unlocked yet
        effective_date = today_et if now_et.hour >= 8 else subtract_months(today_et, 0)
        
        farthest_11m = add_months(effective_date, 11)
        farthest_7m = add_months(effective_date, 7)

        if args.json:
            print(json.dumps({
                "current_time_et": now_et.isoformat(),
                "farthest_bookable_home_resort_11m": farthest_11m.isoformat(),
                "farthest_bookable_other_resorts_7m": farthest_7m.isoformat()
            }, indent=2))
            return

        status_note = ""
        if now_et.hour < 8:
            status_note = " [dim yellow](Note: 8:00 AM ET window has not unlocked yet today)[/dim yellow]"

        console.print(Panel.fit(
            f"[bold cyan]DVC Active Booking Horizons (Today)[/bold cyan]\n"
            f"Today: [bold white]{today_et.strftime('%A, %B %d, %Y')}[/bold white]{status_note}\n"
            f"Current Time (ET): [dim]{now_et.strftime('%b %d, %Y %I:%M %p %Z')}[/dim]",
            border_style="cyan"
        ))

        table = Table(border_style="cyan", header_style="bold cyan")
        table.add_column("Window", style="bold white")
        table.add_column("Resorts Covered", style="white")
        table.add_column("Farthest Check-in Bookable Today", style="bold green")

        table.add_row(
            "11-Month Window",
            home_resort_name,
            farthest_11m.strftime("%A, %B %d, %Y")
        )
        table.add_row(
            "7-Month Window",
            "All Other DVC Resorts",
            farthest_7m.strftime("%A, %B %d, %Y")
        )

        console.print(table)
        console.print("\n[dim]Tip: To calculate opening dates for a future trip, run:[/dim]")
        console.print("[cyan]  python dvc.py windows --date YYYY-MM-DD[/cyan]\n")

if __name__ == "__main__":
    main()
