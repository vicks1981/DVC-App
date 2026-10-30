#!/usr/bin/env python3
"""
login.py - Interactive DVC Authentication Helper

Opens a visible browser window for you to log into your Disney Vacation Club account,
complete any 2FA/OTP verification prompts, and saves the authenticated session state 
to `auth_state.json` for headless use.
"""

import sys
import os
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
from rich.console import Console
from rich.panel import Panel

console = Console()

AUTH_STATE_FILE = Path(__file__).parent / "auth_state.json"
DVC_URL = "https://disneyvacationclub.disney.go.com/"
DVC_SIGNIN_URL = "https://disneyvacationclub.disney.go.com/sign-in/"

def main():
    console.print(Panel.fit(
        "[bold cyan]Disney Vacation Club (DVC) - Login & Session Setup[/bold cyan]\n"
        "This tool opens a browser window so you can safely log into MyDisney / DVC.\n"
        "Once logged in (including any 2FA/email/SMS codes), your session will be saved locally.",
        border_style="cyan"
    ))

    with sync_playwright() as p:
        console.print("\n[yellow]Launching browser...[/yellow]")
        browser = p.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-maximized"
            ]
        )

        context = browser.new_context(
            viewport=None,
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            )
        )

        page = context.new_page()

        console.print(f"[green]Navigating to Disney Vacation Club sign-in...[/green]")
        try:
            page.goto(DVC_SIGNIN_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            console.print(f"[yellow]Initial sign-in URL redirect note: {e}. Opening home page...[/yellow]")
            page.goto(DVC_URL, wait_until="domcontentloaded", timeout=60000)

        console.print("\n" + "=" * 65)
        console.print("[bold yellow]ACTION REQUIRED IN BROWSER:[/bold yellow]")
        console.print("1. Enter your Disney email and password.")
        console.print("2. Complete any 2-Step Verification code (email/SMS).")
        console.print("3. Wait until you see your DVC Member dashboard.")
        console.print("=" * 65 + "\n")

        try:
            input("👉 Step 1: Once you are logged in to the dashboard, press [ENTER] here to open the booking portal: ")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[red]Login aborted by user.[/red]")
            browser.close()
            sys.exit(1)

        console.print("\n[yellow]Navigating to DVC Booking Portal in your browser...[/yellow]")
        try:
            page.goto("https://disneyvacationclub.disney.go.com/booking/", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3000)
        except Exception as e:
            console.print(f"[yellow]Booking page navigation notice: {e}[/yellow]")

        console.print("\n" + "=" * 65)
        console.print("[bold yellow]STEP 2: BOOKING AUTHORIZATION[/bold yellow]")
        console.print("Look at the browser window. If Disney asks to confirm your email or prompts for sign-in again, complete it.")
        console.print("Once you see the 'Start Your Search' screen with the date calendar, you are fully authorized!")
        console.print("=" * 65 + "\n")

        try:
            input("👉 Step 2: Once you see the 'Start Your Search' page, press [ENTER] here to save session: ")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[red]Login aborted by user.[/red]")
            browser.close()
            sys.exit(1)

        console.print("[yellow]Capturing authentication state...[/yellow]")
        storage = context.storage_state(path=str(AUTH_STATE_FILE))
        
        # Verify that cookies were captured
        cookies = storage.get("cookies", [])
        disney_cookies = [c for c in cookies if "disney" in c.get("domain", "").lower() or "go.com" in c.get("domain", "").lower()]
        
        if disney_cookies:
            console.print(f"[bold green]✓ Successfully captured {len(disney_cookies)} Disney session cookies![/bold green]")
            console.print(f"[green]Saved session to: [bold]{AUTH_STATE_FILE.name}[/bold][/green]")
            console.print("\n[cyan]You can now run [bold]python check_points.py[/bold] in headless mode![/cyan]\n")
        else:
            console.print("[yellow]Warning: Saved session file, but few or no Disney cookies were detected.[/yellow]")
            console.print("If check_points.py fails, please re-run login.py and ensure you are completely signed in.")

        browser.close()

if __name__ == "__main__":
    main()
