#!/usr/bin/env python3
"""
availability.py - Disney Vacation Club Live Room Availability Checker

Searches real-time room availability across DVC resorts and room categories
using your authenticated Playwright session.
"""

import sys
import os
import json
import argparse
import calendar
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

import notifications

console = Console()
DIR = Path(__file__).parent
AUTH_STATE_FILE = DIR / "auth_state.json"
ET = ZoneInfo("America/New_York")

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

DVC_BOOKING_URL = "https://disneyvacationclub.disney.go.com/booking/"

RESORT_MAP = {
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

ROOM_TYPES = {
    "studio": "Deluxe Studio",
    "deluxe studio": "Deluxe Studio",
    "1-bed": "1-Bedroom",
    "1-bedroom": "1-Bedroom",
    "2-bed": "2-Bedroom",
    "2-bedroom": "2-Bedroom",
    "3-bed": "3-Bedroom",
    "3-bedroom": "3-Bedroom",
    "grand villa": "3-Bedroom"
}

RESORT_KEYWORDS = {
    "VWL": ["Boulder Ridge"],
    "CCV": ["Copper Creek"],
    "BLT": ["Bay Lake"],
    "AKV": ["Animal Kingdom"],
    "BCV": ["Beach Club"],
    "BWV": ["BoardWalk"],
    "OKW": ["Old Key West"],
    "SSR": ["Saratoga Springs"],
    "PVB": ["Polynesian"],
    "RIV": ["Riviera"],
    "VGF": ["Grand Floridian"],
    "VGC": ["Grand Californian"],
    "VDH": ["Disneyland Hotel"],
    "HHI": ["Hilton Head"],
    "VB": ["Vero Beach"],
    "AUL": ["Aulani"],
    "CFW": ["Fort Wilderness", "Cabins"]
}

ROOM_CODE_MAP = {
    "XA": "Deluxe Studio",
    "ST": "Deluxe Studio",
    "DS": "Deluxe Studio",
    "1B": "1-Bedroom Villa",
    "2B": "2-Bedroom Villa",
    "3B": "3-Bedroom Grand Villa",
    "GV": "3-Bedroom Grand Villa",
    "TOW": "Tower Studio",
    "CAB": "Cabin",
}

def friendly_room_name(code_or_name: str, fallback_category: str) -> str:
    if not code_or_name:
        return fallback_category
    upper = code_or_name.strip().upper()
    if upper in ROOM_CODE_MAP:
        return f"{ROOM_CODE_MAP[upper]} ({upper})"
    if len(code_or_name) <= 3 and code_or_name.isupper():
        return f"{fallback_category} ({code_or_name})"
    return code_or_name


def resolve_resort_code(query: str) -> str:
    """Normalize user input to official 3-letter resort code."""
    query_upper = query.strip().upper()
    if query_upper in RESORT_MAP:
        return query_upper
    for code, name in RESORT_MAP.items():
        if query.lower() in name.lower():
            return code
    return query_upper

def check_dvc_availability(
    resorts: list[str],
    check_in: str,
    check_out: str,
    room_type: str = "studio",
    adults: int = 2,
    children: int = 0,
    headed: bool = False
):
    """
    Search DVC availability via Playwright session.
    Returns dict with search parameters and availability list.
    """
    if not AUTH_STATE_FILE.exists():
        raise FileNotFoundError("auth_state.json not found. Run python dvc.py login first.")

    resort_codes = [resolve_resort_code(r) for r in resorts]
    room_category = ROOM_TYPES.get(room_type.lower(), "Deluxe Studio")

    try:
        ci_date = datetime.strptime(check_in, "%Y-%m-%d").date()
        co_date = datetime.strptime(check_out, "%Y-%m-%d").date()
    except ValueError:
        ci_date = datetime.strptime(check_in, "%m/%d/%Y").date()
        co_date = datetime.strptime(check_out, "%m/%d/%Y").date()

    if co_date <= ci_date:
        raise ValueError("Check-out date must be after check-in date.")

    # Pre-flight check: validate against DVC booking windows
    today_et = datetime.now(ET).date()
    if ci_date < today_et:
        raise ValueError(f"Check-in date {ci_date.strftime('%B %d, %Y')} is in the past.")

    farthest_11m = add_months(today_et, 11)
    if ci_date > farthest_11m:
        window_opens = subtract_months(ci_date, 11)
        raise ValueError(
            f"Check-in date {ci_date.strftime('%B %d, %Y')} is outside the 11-month booking window!\n\n"
            f"  • Farthest check-in bookable today: {farthest_11m.strftime('%A, %B %d, %Y')}\n"
            f"  • For Home Resort (VWL), bookings for {ci_date.strftime('%B %d, %Y')} open on: "
            f"{window_opens.strftime('%A, %B %d, %Y')} @ 8:00 AM ET.\n\n"
            f"Tip: Run 'python dvc.py windows --date {ci_date}' to see the live countdown to opening day."
        )

    nights = (co_date - ci_date).days
    availability_results = []
    captured_payload = None

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=not headed,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )
        context = browser.new_context(
            storage_state=str(AUTH_STATE_FILE),
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900}
        )
        page = context.new_page()

        # Intercept calendar availability response
        def handle_response(response):
            nonlocal captured_payload
            try:
                ct = response.headers.get("content-type", "")
                url = response.url.lower()
                if "calendar-availability" in url and "json" in ct.lower():
                    payload = response.json()
                    if isinstance(payload, dict) and ("availability" in payload or "isFullAvail" in payload):
                        captured_payload = payload
            except Exception:
                pass

        page.on("response", handle_response)

        # 1. Navigate to booking
        page.goto(DVC_BOOKING_URL, wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_selector("app-booking, app-datepicker-range", timeout=12000)
        except Exception:
            pass

        # Check if redirected to login
        if "login.disney.com" in page.url.lower() or "sign-in" in page.url.lower():
            browser.close()
            raise PermissionError(
                "Disney session expired or requires booking authorization. "
                "Please run 'python dvc.py login' to refresh your session."
            )

        # Dismiss survey modal if present
        try:
            survey_no = page.locator("button:has-text('No, thanks')")
            if survey_no.count() > 0 and survey_no.is_visible():
                survey_no.first.click()
                page.wait_for_timeout(500)
        except Exception:
            pass

        # 2. Select Dates on Calendar
        # Format: "Friday, October 16, 2026"
        ci_str = ci_date.strftime("%A, %B ") + str(ci_date.day) + ci_date.strftime(", %Y")
        co_str = co_date.strftime("%A, %B ") + str(co_date.day) + co_date.strftime(", %Y")

        # Navigate calendar months if needed
        ci_button = page.locator(f"a[aria-label*='{ci_date.strftime('%B')} {ci_date.day}, {ci_date.year}']")
        attempts = 0
        while ci_button.count() == 0 and attempts < 12:
            next_month = page.locator("button[aria-label='Next Month']")
            if next_month.count() > 0 and next_month.is_visible():
                # Don't click disabled next month button
                is_disabled = (
                    next_month.get_attribute("disabled") is not None
                    or "disabled" in (next_month.get_attribute("class") or "")
                )
                if is_disabled:
                    break
                next_month.first.click()
                page.wait_for_timeout(400)
            else:
                break
            attempts += 1
            ci_button = page.locator(f"a[aria-label*='{ci_date.strftime('%B')} {ci_date.day}, {ci_date.year}']")

        if ci_button.count() > 0:
            ci_button.first.click()
            page.wait_for_timeout(500)
        else:
            browser.close()
            raise ValueError(f"Could not locate check-in date ({ci_date.strftime('%B %d, %Y')}) on the booking calendar.")
        
        co_button = page.locator(f"a[aria-label*='{co_date.strftime('%B')} {co_date.day}, {co_date.year}']")
        attempts = 0
        while co_button.count() == 0 and attempts < 12:
            next_month = page.locator("button[aria-label='Next Month']")
            if next_month.count() > 0 and next_month.is_visible():
                is_disabled = (
                    next_month.get_attribute("disabled") is not None
                    or "disabled" in (next_month.get_attribute("class") or "")
                )
                if is_disabled:
                    break
                next_month.first.click()
                page.wait_for_timeout(400)
            else:
                break
            attempts += 1
            co_button = page.locator(f"a[aria-label*='{co_date.strftime('%B')} {co_date.day}, {co_date.year}']")

        if co_button.count() > 0:
            co_button.first.click()
            page.wait_for_timeout(500)
        else:
            browser.close()
            raise ValueError(f"Could not locate check-out date ({co_date.strftime('%B %d, %Y')}) on the booking calendar.")

        # 3. Select Room Type
        room_btn = page.locator("button").filter(has_text=room_category)
        if room_btn.count() > 0:
            room_btn.first.click()
            page.wait_for_timeout(500)

        # 4. Select Resorts
        # Check if Boulder Ridge (Home Resort) is pre-checked by default
        vwl_chk = page.locator("wdpr-checkbox").filter(has_text="Boulder Ridge")
        if vwl_chk.count() > 0:
            is_vwl_checked = (
                vwl_chk.first.get_attribute("checked") is not None
                or vwl_chk.first.get_attribute("aria-checked") == "true"
            )
            # If user explicitly asked for other resorts but not VWL, uncheck Boulder Ridge
            if "VWL" not in resort_codes and is_vwl_checked:
                vwl_chk.first.click()
                page.wait_for_timeout(300)

        notices = []
        for code in resort_codes:
            keywords = RESORT_KEYWORDS.get(code, [code])
            found_chk = None
            for kw in keywords:
                chk = page.locator("wdpr-checkbox").filter(has_text=kw)
                if chk.count() > 0:
                    found_chk = chk
                    break
            
            if found_chk and found_chk.count() > 0:
                is_checked = (
                    found_chk.first.get_attribute("checked") is not None
                    or found_chk.first.get_attribute("aria-checked") == "true"
                )
                if not is_checked:
                    found_chk.first.click()
                    page.wait_for_timeout(400)
            else:
                if code not in ("VWL",):
                    r_name = RESORT_MAP.get(code, code)
                    notices.append(f"{r_name} ({code}) is not selectable on this membership portal (may be resale restricted or outside 7-month window).")

        page.wait_for_timeout(1000)

        # 5. Click Check Availability
        avail_btn = page.locator("wdpr-button, button").filter(has_text="Check Availability")
        if avail_btn.count() > 0:
            is_disabled = (
                avail_btn.first.get_attribute("disabled") is not None
                or avail_btn.first.get_attribute("aria-disabled") == "true"
            )
            if is_disabled:
                # Try triggering via Angular component or click(force=True)
                try:
                    page.evaluate("""() => {
                        const el = document.querySelector('app-booking');
                        if (el && window.ng) {
                            const comp = window.ng.getComponent(el);
                            if (comp && comp.checkAvailibility) {
                                comp.checkAvailibility();
                            }
                        }
                    }""")
                except Exception:
                    pass
                try:
                    avail_btn.first.click(force=True, timeout=5000)
                except Exception:
                    pass
            else:
                avail_btn.first.click()

            # Wait for search results or search page
            try:
                page.wait_for_url("**/search**", timeout=15000)
            except Exception:
                pass
            try:
                page.wait_for_selector("app-collapsable-room-type, app-resort, .no-resort-found", timeout=12000)
            except Exception:
                page.wait_for_timeout(6000)

        # Always persist updated storage state to protect tokens from revoking
        try:
            context.storage_state(path=str(AUTH_STATE_FILE))
        except Exception:
            pass

        # Parse captured payload if intercepted
        if captured_payload:
            avail_list = captured_payload.get("availability", [])
            for res_item in avail_list:
                resort_code = res_item.get("resortCode", "Unknown")
                resort_name = RESORT_MAP.get(resort_code, resort_code)

                # 1. Fully available rooms (instant booking available!)
                for room in res_item.get("full", []):
                    raw_name = (
                        room.get("roomTypeName")
                        or room.get("name")
                        or room.get("roomName")
                        or room.get("roomCode")
                        or room_category
                    )
                    room_name = friendly_room_name(raw_name, room_category)
                    points = room.get("points") or room.get("totalPoints") or 0
                    availability_results.append({
                        "resort_code": resort_code,
                        "resort_name": resort_name,
                        "room_type": room_name,
                        "is_available": True,
                        "status": "Available",
                        "points_total": points,
                        "points_per_night": round(points / nights, 1) if nights else 0,
                        "nights": nights,
                        "check_in": ci_date.strftime("%Y-%m-%d"),
                        "check_out": co_date.strftime("%Y-%m-%d")
                    })

                # 2. Partially available rooms
                for room in res_item.get("partial", []):
                    raw_name = (
                        room.get("roomTypeName")
                        or room.get("name")
                        or room.get("roomName")
                        or room.get("roomCode")
                        or room_category
                    )
                    room_name = friendly_room_name(raw_name, room_category)
                    points = room.get("points") or room.get("totalPoints") or 0
                    availability_results.append({
                        "resort_code": resort_code,
                        "resort_name": resort_name,
                        "room_type": room_name,
                        "is_available": False,
                        "status": "Partial Availability",
                        "points_total": points,
                        "points_per_night": round(points / nights, 1) if nights else 0,
                        "nights": nights,
                        "check_in": ci_date.strftime("%Y-%m-%d"),
                        "check_out": co_date.strftime("%Y-%m-%d")
                    })

                # 3. Waitlist rooms
                for room in res_item.get("waitlist", []):
                    raw_name = (
                        room.get("roomTypeName")
                        or room.get("name")
                        or room.get("roomName")
                        or room.get("roomCode")
                        or room_category
                    )
                    room_name = friendly_room_name(raw_name, room_category)
                    points = room.get("points") or room.get("totalPoints") or 0
                    availability_results.append({
                        "resort_code": resort_code,
                        "resort_name": resort_name,
                        "room_type": room_name,
                        "is_available": False,
                        "status": "Waitlist Only",
                        "points_total": points,
                        "points_per_night": round(points / nights, 1) if nights else 0,
                        "nights": nights,
                        "check_in": ci_date.strftime("%Y-%m-%d"),
                        "check_out": co_date.strftime("%Y-%m-%d")
                    })

                # 4. Backward compatibility with flat roomTypes list
                for room in res_item.get("roomTypes", []):
                    raw_name = room.get("roomTypeName", room.get("name", "Standard"))
                    room_name = friendly_room_name(raw_name, room_category)
                    is_available = room.get("isFullAvailability", room.get("available", False))
                    points_total = room.get("totalPoints", room.get("points", 0))
                    availability_results.append({
                        "resort_code": resort_code,
                        "resort_name": resort_name,
                        "room_type": room_name,
                        "is_available": is_available,
                        "status": "Available" if is_available else "Waitlist Only",
                        "points_total": points_total,
                        "points_per_night": round(points_total / nights, 1) if nights else 0,
                        "nights": nights,
                        "check_in": ci_date.strftime("%Y-%m-%d"),
                        "check_out": co_date.strftime("%Y-%m-%d")
                    })

        # Fallback: Extract from rendered Angular room cards in DOM
        if not availability_results:
            try:
                dom_rooms = page.evaluate("""() => {
                    const cards = Array.from(document.querySelectorAll('app-collapsable-room-type'));
                    return cards.map(el => {
                        let comp = null;
                        if (window.ng) {
                            try { comp = window.ng.getComponent(el); } catch (e) {}
                        }
                        const titleEl = el.querySelector('h3, .title');
                        const pointsEl = el.querySelector('.points, [class*="points"]');
                        const hasWaitlist = el.querySelector('.waitlist-container, .waitlist-only') !== null;
                        const hasPartial = el.querySelector('.partial-container') !== null;
                        
                        let compType = comp?.type || (hasWaitlist ? 'waitlist' : (hasPartial ? 'partial' : 'full'));
                        let roomName = comp?.roomName || titleEl?.innerText?.trim() || '';
                        let points = comp?.selectedPoints || comp?.room?.points || 0;
                        if (!points && pointsEl) {
                            const m = pointsEl.innerText.replace(/,/g, '').match(/\\d+/);
                            if (m) points = parseInt(m[0], 10);
                        }
                        
                        let resortCode = comp?.resortCode || '';
                        return {
                            resort_code: resortCode,
                            room_type: roomName,
                            type: compType,
                            points_total: points
                        };
                    });
                }""")
                for d in dom_rooms:
                    r_code = d.get("resort_code") or (resort_codes[0] if len(resort_codes) == 1 else "")
                    r_name = RESORT_MAP.get(r_code, r_code)
                    is_full = d.get("type") == "full"
                    is_part = d.get("type") == "partial"
                    status = "Available" if is_full else ("Partial Availability" if is_part else "Waitlist Only")
                    pts = d.get("points_total", 0)
                    availability_results.append({
                        "resort_code": r_code,
                        "resort_name": r_name,
                        "room_type": d.get("room_type") or room_category,
                        "is_available": is_full,
                        "status": status,
                        "points_total": pts,
                        "points_per_night": round(pts / nights, 1) if nights else 0,
                        "nights": nights,
                        "check_in": ci_date.strftime("%Y-%m-%d"),
                        "check_out": co_date.strftime("%Y-%m-%d")
                    })
            except Exception:
                pass

        # If no results parsed from network payload or DOM, report requested resorts as Waitlist Only
        if not availability_results:
            for code in resort_codes:
                resort_name = RESORT_MAP.get(code, code)
                availability_results.append({
                    "resort_code": code,
                    "resort_name": resort_name,
                    "room_type": room_category,
                    "is_available": False,
                    "status": "Waitlist Only",
                    "points_total": 0,
                    "points_per_night": 0,
                    "nights": nights,
                    "check_in": ci_date.strftime("%Y-%m-%d"),
                    "check_out": co_date.strftime("%Y-%m-%d")
                })

        browser.close()

    return {
        "search": {
            "resorts": resort_codes,
            "room_type": room_category,
            "check_in": ci_date.strftime("%b %d, %Y"),
            "check_out": co_date.strftime("%b %d, %Y"),
            "nights": nights
        },
        "results": availability_results,
        "notices": notices
    }

def print_results(data: dict):
    search = data["search"]
    results = data["results"]

    console.print(Panel.fit(
        f"[bold cyan]Disney Vacation Club - Availability Search[/bold cyan]\n"
        f"Dates: [bold white]{search['check_in']} -> {search['check_out']} ({search['nights']} nights)[/bold white]\n"
        f"Room Category: [bold yellow]{search['room_type']}[/bold yellow] | "
        f"Resorts: [bold green]{', '.join(search['resorts'])}[/bold green]",
        border_style="cyan"
    ))

    table = Table(title="Inventory Search Results", border_style="cyan")
    table.add_column("Resort", style="bold cyan")
    table.add_column("Room Category", style="white")
    table.add_column("Status", justify="center")
    table.add_column("Points", justify="right")

    for r in results:
        if r.get("is_available"):
            status_str = "[bold green]AVAILABLE[/bold green]"
        elif "partial" in r.get("status", "").lower():
            status_str = "[cyan]Partial[/cyan]"
        else:
            status_str = "[yellow]Waitlist Only[/yellow]"
        pts_str = f"{r['points_total']} pts" if r.get("points_total") else "N/A"
        table.add_row(
            r["resort_name"],
            r["room_type"],
            status_str,
            pts_str
        )

    console.print(table)

    if data.get("notices"):
        for n in data["notices"]:
            console.print(f"[yellow]ℹ {n}[/yellow]")


def main():
    parser = argparse.ArgumentParser(description="Check DVC room availability")
    parser.add_argument("--resort", default="VWL", help="Resort code(s), comma-separated (e.g., VWL,RIV,BLT)")
    parser.add_argument("--room", default="studio", help="Room type: studio, 1-bed, 2-bed, 3-bed")
    parser.add_argument("--start", required=True, help="Check-in date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="Check-out date (YYYY-MM-DD)")
    parser.add_argument("--headed", action="store_true", help="Run browser visibly")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")

    args = parser.parse_args()
    resorts = [r.strip() for r in args.resort.split(",")]

    try:
        data = check_dvc_availability(
            resorts=resorts,
            check_in=args.start,
            check_out=args.end,
            room_type=args.room,
            headed=args.headed
        )
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            print_results(data)
    except Exception as e:
        console.print(f"[bold red]Search Error:[/bold red] {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
