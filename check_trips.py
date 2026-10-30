#!/usr/bin/env python3
"""
check_trips.py - DVC Upcoming & Past Trips Checker

Navigates to your Disney Vacation Club dashboard headlessly using `auth_state.json`
to retrieve:
1. Upcoming Vacations (Resort, Dates, Confirmation #, Room Category, Points)
2. Past Reservations from the last 12 months
"""

import sys
import os
import json
import argparse
import re
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()

AUTH_STATE_FILE = Path(__file__).parent / "auth_state.json"
DVC_HOME_URL = "https://disneyvacationclub.disney.go.com/home/"

RESORT_NAMES = {
    "VWL": "Boulder Ridge Villas at Disney's Wilderness Lodge",
    "CCV": "Copper Creek Villas & Cabins at Disney's Wilderness Lodge",
    "BLT": "Bay Lake Tower at Disney's Contemporary Resort",
    "AKV": "Disney's Animal Kingdom Villas",
    "BCV": "Disney's Beach Club Villas",
    "BWV": "Disney's BoardWalk Villas",
    "OKW": "Disney's Old Key West Resort",
    "SSR": "Disney's Saratoga Springs Resort & Spa",
    "PVB": "Disney's Polynesian Villas & Bungalows",
    "RIV": "Disney's Riviera Resort",
    "VGF": "The Villas at Disney's Grand Floridian Resort & Spa",
    "VGC": "The Villas at Disney's Grand Californian Hotel & Spa",
    "VDH": "The Villas at Disneyland Hotel",
    "HHI": "Disney's Hilton Head Island Resort",
    "VB": "Disney's Vero Beach Resort",
    "AUL": "Aulani, Disney Vacation Club Villas",
    "CFW": "The Cabins at Disney's Fort Wilderness Resort"
}

def parse_args():
    parser = argparse.ArgumentParser(description="Check upcoming and past DVC reservations.")
    parser.add_argument("--headed", action="store_true", help="Run browser in visible mode for debugging.")
    parser.add_argument("--json", action="store_true", help="Output raw trip data as JSON.")
    parser.add_argument("--debug", action="store_true", help="Save debug screenshot and page HTML.")
    return parser.parse_args()

def parse_reservation_item(res):
    """Normalize reservation dictionary from API or DOM."""
    if not isinstance(res, dict):
        return None

    resort_code = res.get("resortCode", "")
    resort_name = res.get("resortName") or RESORT_NAMES.get(resort_code, resort_code or "Unknown Resort")
    
    check_in = res.get("checkInDate") or res.get("arrivalDate") or res.get("startDate", "N/A")
    check_out = res.get("checkOutDate") or res.get("departureDate") or res.get("endDate", "N/A")
    conf_num = res.get("confirmationNumber") or res.get("reservationId") or res.get("confNumber", "N/A")
    room_type = res.get("roomType") or res.get("roomDescription") or res.get("villaType", "Standard")
    points = res.get("points") or res.get("vacationPoints") or res.get("pointsUsed", "N/A")
    status = res.get("status", "Confirmed")

    return {
        "resort": resort_name,
        "check_in": check_in,
        "check_out": check_out,
        "confirmation_number": conf_num,
        "room_type": room_type,
        "points": points,
        "status": status,
        "raw": res
    }

def main():
    args = parse_args()

    if not AUTH_STATE_FILE.exists():
        console.print(Panel.fit(
            "[bold red]Session Not Found![/bold red]\n"
            "No [bold]auth_state.json[/bold] found.\n"
            "Please first run the interactive login helper:\n\n"
            "  [bold cyan]python login.py[/bold cyan]\n",
            border_style="red"
        ))
        sys.exit(1)

    if not args.json:
        console.print(Panel.fit(
            "[bold cyan]Disney Vacation Club - Trip & Reservation Checker[/bold cyan]\n"
            f"Mode: {'[yellow]Headed (Debug)[/yellow]' if args.headed else '[green]Headless[/green]'}\n"
            f"Session: [dim]{AUTH_STATE_FILE.name}[/dim]",
            border_style="cyan"
        ))

    api_reservations = []
    past_reservations_data = []

    with sync_playwright() as p:
        if not args.json:
            console.print("[dim]Launching browser...[/dim]")

        browser = p.chromium.launch(
            headless=not args.headed,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox"
            ]
        )

        context = browser.new_context(
            storage_state=str(AUTH_STATE_FILE),
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900}
        )

        page = context.new_page()

        # Intercept reservation-list and booking APIs
        def handle_response(response):
            try:
                ct = response.headers.get("content-type", "")
                if "application/json" in ct:
                    url = response.url.lower()
                    if "reservation-list" in url or ("booking" in url and "list" in url):
                        data = response.json()
                        res_list = data.get("reservations", [])
                        if isinstance(res_list, list):
                            api_reservations.extend(res_list)
                    elif "past-reservations" in url or "past-bookings" in url:
                        data = response.json()
                        if isinstance(data, list):
                            past_reservations_data.extend(data)
                        elif isinstance(data, dict):
                            past_reservations_data.extend(data.get("reservations", []))
            except Exception:
                pass

        page.on("response", handle_response)

        if not args.json:
            console.print("[yellow]Fetching DVC Member Dashboard...[/yellow]")

        try:
            page.goto(DVC_HOME_URL, wait_until="networkidle", timeout=45000)
        except PlaywrightTimeoutError:
            if not args.json:
                console.print("[dim]Network idle wait timed out, continuing...[/dim]")
        except Exception as e:
            if not args.json:
                console.print(f"[red]Error navigating to dashboard: {e}[/red]")

        # Check if session expired
        current_url = page.url.lower()
        if "sign-in" in current_url or "login" in current_url:
            console.print(Panel.fit(
                "[bold red]Session Expired![/bold red]\n"
                "Disney redirected to the sign-in page.\n"
                "Please run [bold cyan]python login.py[/bold cyan] to refresh your session.",
                border_style="red"
            ))
            browser.close()
            sys.exit(1)

        # Allow time for initial widgets to mount
        page.wait_for_timeout(3000)

        # Look for "View Past Bookings" button to expand past reservations
        past_bookings_button = page.get_by_text("View Past Bookings")
        has_past_btn = False
        try:
            if past_bookings_button.count() > 0:
                has_past_btn = True
                if not args.json:
                    console.print("[dim]Loading past bookings from the last year...[/dim]")
                past_bookings_button.first.click()
                page.wait_for_timeout(3000)
        except Exception:
            pass

        # Extract DOM text for vacation plans
        plans_section_text = ""
        try:
            section = page.locator("section, div").filter(has_text="My Vacation Plans").first
            if section.count() > 0:
                plans_section_text = section.inner_text()
        except Exception:
            pass

        if args.debug:
            screenshot_path = Path(__file__).parent / "screenshot_trips_debug.png"
            html_path = Path(__file__).parent / "debug_trips.html"
            page.screenshot(path=str(screenshot_path))
            html_path.write_text(page.content())
            if not args.json:
                console.print(f"[dim]Saved debug screenshot to {screenshot_path.name} and HTML to {html_path.name}[/dim]")

        browser.close()

    # Process and display trips
    if args.json:
        output_obj = {
            "api_reservations": api_reservations,
            "past_reservations_api": past_reservations_data,
            "dashboard_plans_text": plans_section_text
        }
        print(json.dumps(output_obj, indent=2))
        return

    # Check for upcoming trips
    upcoming_trips = [parse_reservation_item(r) for r in api_reservations if parse_reservation_item(r)]
    has_no_upcoming_text = "no upcoming vacations" in plans_section_text.lower()

    console.print("\n[bold cyan]═══ UPCOMING VACATIONS ═══[/bold cyan]")
    if upcoming_trips:
        table_upcoming = Table(border_style="cyan", header_style="bold cyan")
        table_upcoming.add_column("Resort", style="bold white")
        table_upcoming.add_column("Dates (Check-in - Check-out)", style="yellow")
        table_upcoming.add_column("Room Type", style="white")
        table_upcoming.add_column("Confirmation #", style="dim")
        table_upcoming.add_column("Points", justify="right", style="bold green")

        for t in upcoming_trips:
            dates = f"{t['check_in']} – {t['check_out']}"
            table_upcoming.add_row(
                t["resort"],
                dates,
                str(t["room_type"]),
                str(t["confirmation_number"]),
                str(t["points"])
            )
        console.print(table_upcoming)
    else:
        status_msg = "No upcoming vacations currently booked." if has_no_upcoming_text else "No upcoming reservations detected on your account."
        console.print(Panel(f"[dim yellow]{status_msg}[/dim yellow]", border_style="dim"))

    # Check for past trips (last 12 months)
    console.print("\n[bold cyan]═══ PAST RESERVATIONS (LAST 12 MONTHS) ═══[/bold cyan]")
    past_trips = [parse_reservation_item(r) for r in past_reservations_data if parse_reservation_item(r)]

    if past_trips:
        table_past = Table(border_style="cyan", header_style="bold cyan")
        table_past.add_column("Resort", style="bold white")
        table_past.add_column("Dates", style="yellow")
        table_past.add_column("Room Type", style="white")
        table_past.add_column("Confirmation #", style="dim")
        table_past.add_column("Points", justify="right", style="green")

        for t in past_trips:
            dates = f"{t['check_in']} – {t['check_out']}"
            table_past.add_row(
                t["resort"],
                dates,
                str(t["room_type"]),
                str(t["confirmation_number"]),
                str(t["points"])
            )
        console.print(table_past)
    else:
        console.print(Panel(
            "[dim yellow]No completed reservations found in the last 12 months.[/dim yellow]\n"
            "[dim](Completed bookings only display for reservations taken within the last year).[/dim]",
            border_style="dim"
        ))

    console.print("\n[dim green]✓ Trip query completed successfully![/dim green]\n")

if __name__ == "__main__":
    main()
