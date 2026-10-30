#!/usr/bin/env python3
"""
check_points.py - Headless DVC Points Balance Checker

Uses the saved session from `auth_state.json` to navigate to your Disney Vacation
Club account headlessly, intercept the DVC membership details API, and output a clean
breakdown of your vacation points for each year and contract.
"""

import sys
import os
import json
import argparse
import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()

AUTH_STATE_FILE = Path(__file__).parent / "auth_state.json"
DVC_POINTS_URL = "https://disneyvacationclub.disney.go.com/home/points/"

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

MONTH_NAMES = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December"
}

def parse_args():
    parser = argparse.ArgumentParser(description="Check DVC Points headlessly using saved session.")
    parser.add_argument("--headed", action="store_true", help="Run browser in visible mode for debugging.")
    parser.add_argument("--json", action="store_true", help="Output raw points data as JSON.")
    parser.add_argument("--debug", action="store_true", help="Save debug screenshot and page HTML.")
    return parser.parse_args()

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
            "[bold cyan]Disney Vacation Club - Points Checker[/bold cyan]\n"
            f"Mode: {'[yellow]Headed (Debug)[/yellow]' if args.headed else '[green]Headless[/green]'}\n"
            f"Session: [dim]{AUTH_STATE_FILE.name}[/dim]",
            border_style="cyan"
        ))

    membership_details = None
    profile_details = None

    with sync_playwright() as p:
        if not args.json:
            console.print("[dim]Launching browser...[/dim]")

        browser = p.chromium.launch(
            headless=not args.headed,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
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

        # Intercept background API responses to capture DVC points and membership data
        def handle_response(response):
            nonlocal membership_details, profile_details
            try:
                ct = response.headers.get("content-type", "")
                if "application/json" in ct:
                    url = response.url.lower()
                    if "memberships/details" in url:
                        membership_details = response.json()
                    elif "profile" in url and "dvc-lightcycle-api" in url:
                        profile_details = response.json()
            except Exception:
                pass

        page.on("response", handle_response)

        if not args.json:
            console.print("[yellow]Fetching DVC Vacation Points Overview...[/yellow]")

        try:
            page.goto(DVC_POINTS_URL, wait_until="networkidle", timeout=45000)
        except PlaywrightTimeoutError:
            if not args.json:
                console.print("[dim]Network idle wait timed out, continuing...[/dim]")
        except Exception as e:
            if not args.json:
                console.print(f"[red]Error loading page: {e}[/red]")

        # Check if session expired / redirected to login
        current_url = page.url.lower()
        if "sign-in" in current_url or "login" in current_url:
            console.print(Panel.fit(
                "[bold red]Session Expired![/bold red]\n"
                "Disney redirected to the sign-in page.\n"
                "Your stored session has expired or was revoked.\n\n"
                "Please run [bold cyan]python login.py[/bold cyan] to refresh your session.",
                border_style="red"
            ))
            browser.close()
            sys.exit(1)

        # Allow extra seconds for background API responses to settle
        page.wait_for_timeout(4000)

        if args.debug:
            screenshot_path = Path(__file__).parent / "screenshot_debug.png"
            html_path = Path(__file__).parent / "debug_page.html"
            page.screenshot(path=str(screenshot_path))
            html_path.write_text(page.content())
            if not args.json:
                console.print(f"[dim]Saved debug screenshot to {screenshot_path.name} and HTML to {html_path.name}[/dim]")

        browser.close()

    # If --json was requested, output the raw data
    if args.json:
        output_obj = {
            "membership_details": membership_details,
            "profile_details": profile_details
        }
        print(json.dumps(output_obj, indent=2))
        return

    if not membership_details:
        console.print(Panel.fit(
            "[bold yellow]Could not intercept DVC Points API payload.[/bold yellow]\n"
            "Try running with [bold]python check_points.py --headed --debug[/bold] to see the page in action.",
            border_style="yellow"
        ))
        sys.exit(1)

    # Extract member information
    member_name = "DVC Member"
    use_year_month_name = ""
    try:
        member_data = profile_details.get("memberData", [{}])[0].get("memberDetail", {}).get("getMemberDetailReturn", {})
        first = member_data.get("firstName", "")
        last = member_data.get("lastName", "")
        if first or last:
            member_name = f"{first.title()} {last.title()}".strip()
        contracts_meta = member_data.get("memberships", {}).get("membership", [{}])[0].get("memberContracts", {}).get("contract", [])
        if contracts_meta:
            m_code = contracts_meta[0].get("useYearMonth")
            if m_code and int(m_code) in MONTH_NAMES:
                use_year_month_name = MONTH_NAMES[int(m_code)]
    except Exception:
        pass

    membership_id = membership_details.get("membershipId", "N/A")
    avail_to_book = membership_details.get("vacationPointsAvailableToBook", 0)
    banking_deadline = membership_details.get("currentUseYearBankingWindowCloses", "N/A")

    # Display Member Banner
    banner_text = f"[bold white]Member:[/bold white] {member_name}   |   [bold white]Membership ID:[/bold white] {membership_id}"
    if use_year_month_name:
        banner_text += f"   |   [bold white]Use Year Month:[/bold white] {use_year_month_name}"
    console.print(Panel(banner_text, border_style="blue"))

    # Display Current Booking Summary
    console.print(f"\n[bold green]Vacation Points Currently Available to Book:[/bold green] [bold white]{avail_to_book}[/bold white]")
    if banking_deadline != "N/A":
        console.print(f"[yellow]Banking Window Closes:[/yellow] {banking_deadline}\n")

    # Parse pointsInContractsPerUseDate
    points_per_use_date = membership_details.get("pointsInContractsPerUseDate", [])

    if not points_per_use_date:
        console.print("[yellow]No contract point breakdown found in API response.[/yellow]")
        return

    # Build Rich Table
    table = Table(
        title="DVC Vacation Points Breakdown by Year",
        header_style="bold cyan",
        border_style="cyan"
    )
    table.add_column("Use Year", justify="center", style="bold yellow")
    table.add_column("Resort / Contract", style="white")
    table.add_column("Annual Allotment", justify="right", style="dim")
    table.add_column("Banked In", justify="right", style="blue")
    table.add_column("Banked Out", justify="right", style="magenta")
    table.add_column("Borrowed In", justify="right", style="cyan")
    table.add_column("Points Balance", justify="right", style="bold green")

    for entry in points_per_use_date:
        use_year = str(entry.get("useDate", "N/A"))
        contracts = entry.get("contracts", [])
        for c in contracts:
            contract_id = str(c.get("contractId", c.get("addOnId", "N/A")))
            resort_code = c.get("resortCode", "N/A")
            resort_name = RESORT_NAMES.get(resort_code, resort_code)
            resort_display = f"{resort_name} (#{contract_id})"

            annual = str(c.get("annualPointsAllotment", 0))
            balance = str(c.get("pointsBalance", 0))

            banked_in_val = c.get("pointsBankedIn", {}).get("value", 0)
            banked_in_yr = c.get("pointsBankedIn", {}).get("useYear", "")
            banked_in_str = f"+{banked_in_val} (from {banked_in_yr})" if banked_in_val else "0"

            banked_out_val = c.get("pointsBankedOut", {}).get("value", 0)
            banked_out_yr = c.get("pointsBankedOut", {}).get("useYear", "")
            banked_out_str = f"-{banked_out_val} (to {banked_out_yr})" if banked_out_val else "0"

            borrowed_in_val = c.get("pointsBorrowedIn", {}).get("value", 0)
            borrowed_in_str = f"+{borrowed_in_val}" if borrowed_in_val else "0"

            table.add_row(
                use_year,
                resort_display,
                annual,
                banked_in_str,
                banked_out_str,
                borrowed_in_str,
                balance
            )

    console.print(table)
    console.print("\n[dim green]✓ Points successfully retrieved![/dim green]\n")

if __name__ == "__main__":
    main()
