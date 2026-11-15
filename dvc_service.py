#!/usr/bin/env python3
"""
dvc_service.py - Unified Data Service for Disney Vacation Club

Handles fetching, caching, and calculating points, reservations, and booking windows.
"""

import os
import sys
import json
import signal
import fcntl
import shlex
import subprocess
import calendar
from datetime import datetime, date, time
from pathlib import Path
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

DIR = Path(__file__).parent
AUTH_STATE_FILE = DIR / "auth_state.json"
CACHE_FILE = DIR / "data_cache.json"
WATCHER_STATE_FILE = DIR / "watcher_state.json"
ET = ZoneInfo("America/New_York")

DVC_POINTS_URL = "https://disneyvacationclub.disney.go.com/home/points/"
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

MONTH_NAMES = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December"
}

def add_months(source_date: date, months: int) -> date:
    month = source_date.month - 1 + months
    year = source_date.year + month // 12
    month = month % 12 + 1
    max_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(source_date.day, max_day))

def subtract_months(source_date: date, months: int) -> date:
    month = source_date.month - 1 - months
    year = source_date.year + month // 12
    month = month % 12 + 1
    max_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(source_date.day, max_day))

def calculate_windows(target_date_str: str = None):
    now_et = datetime.now(ET)
    today_et = now_et.date()

    if target_date_str:
        # User specified a trip date
        try:
            target_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()
        except ValueError:
            target_date = datetime.strptime(target_date_str, "%m/%d/%Y").date()

        home_date = subtract_months(target_date, 11)
        other_date = subtract_months(target_date, 7)

        home_dt = datetime.combine(home_date, time(8, 0), tzinfo=ET)
        other_dt = datetime.combine(other_date, time(8, 0), tzinfo=ET)

        return {
            "mode": "trip",
            "target_date": target_date.strftime("%B %d, %Y"),
            "target_date_iso": target_date.isoformat(),
            "now_et": now_et.strftime("%b %d, %Y %I:%M %p %Z"),
            "home_resort_11m": {
                "opens_at": home_dt.strftime("%A, %B %d, %Y @ 8:00 AM ET"),
                "opens_at_iso": home_dt.isoformat(),
                "is_open": now_et >= home_dt,
                "days_until": (home_dt.date() - today_et).days
            },
            "other_resorts_7m": {
                "opens_at": other_dt.strftime("%A, %B %d, %Y @ 8:00 AM ET"),
                "opens_at_iso": other_dt.isoformat(),
                "is_open": now_et >= other_dt,
                "days_until": (other_dt.date() - today_et).days
            }
        }
    else:
        # Today's horizons
        effective_date = today_et if now_et.hour >= 8 else subtract_months(today_et, 0)
        farthest_11m = add_months(effective_date, 11)
        farthest_7m = add_months(effective_date, 7)

        return {
            "mode": "today",
            "today": today_et.strftime("%A, %B %d, %Y"),
            "now_et": now_et.strftime("%b %d, %Y %I:%M %p %Z"),
            "farthest_11m": farthest_11m.strftime("%A, %B %d, %Y"),
            "farthest_11m_iso": farthest_11m.isoformat(),
            "farthest_7m": farthest_7m.strftime("%A, %B %d, %Y"),
            "farthest_7m_iso": farthest_7m.isoformat()
        }

def load_cached_data():
    """Load cached data if available, else returns None."""
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    if not data.get("points_by_year"):
                        data["session_expired"] = True
                    return data
        except Exception:
            pass
    return None

def save_cache_data(data: dict):
    """Save data to cache file."""
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f, indent=2)

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

def _format_trip_date(date_str: str) -> str:
    """Format YYYY-MM-DD into a friendly 'Jul 14, 2027' date string if applicable."""
    if not date_str or date_str == "N/A":
        return "N/A"
    try:
        dt = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
        return dt.strftime("%b %d, %Y")
    except Exception:
        return str(date_str)

def _normalize_reservation(r: dict) -> dict:
    """Normalize a reservation dictionary from DVC API responses (including nested stayDetails)."""
    stay = r.get("stayDetails") if isinstance(r.get("stayDetails"), dict) else {}

    resort_code = stay.get("resortCode") or r.get("resortCode") or ""
    resort_name = (
        stay.get("destinationName")
        or stay.get("resortName")
        or r.get("destinationName")
        or r.get("resortName")
        or r.get("resort")
        or RESORT_NAMES.get(resort_code, resort_code or "Unknown Resort")
    )
    raw_ci = (
        stay.get("checkInDate")
        or r.get("checkInDate")
        or r.get("arrivalDate")
        or r.get("startDate")
        or r.get("check_in", "N/A")
    )
    raw_co = (
        stay.get("checkOutDate")
        or r.get("checkOutDate")
        or r.get("departureDate")
        or r.get("endDate")
        or r.get("check_out", "N/A")
    )
    conf_num = (
        r.get("reservationNumber")
        or r.get("confirmationNumber")
        or r.get("travelPlanSegmentId")
        or r.get("reservationId")
        or r.get("confNumber")
        or stay.get("confirmationNumber")
        or r.get("confirmation_number", "N/A")
    )
    raw_room = (
        stay.get("roomType")
        or stay.get("roomDescription")
        or r.get("roomType")
        or r.get("roomDescription")
        or r.get("villaType")
        or r.get("roomName")
        or r.get("room_type", "Standard")
    )
    room_upper = str(raw_room).strip().upper()
    room_type = ROOM_CODE_MAP.get(room_upper, raw_room)

    points = (
        stay.get("totalPoints")
        or stay.get("points")
        or r.get("totalPoints")
        or r.get("points")
        or r.get("vacationPoints")
        or r.get("pointsUsed", 0)
    )
    nights = stay.get("totalNights") or r.get("totalNights")

    return {
        "resort": resort_name,
        "resort_code": resort_code,
        "check_in": _format_trip_date(raw_ci),
        "check_out": _format_trip_date(raw_co),
        "check_in_iso": str(raw_ci)[:10] if raw_ci else "",
        "check_out_iso": str(raw_co)[:10] if raw_co else "",
        "confirmation_number": str(conf_num),
        "room_type": room_type,
        "points": points,
        "nights": nights
    }


def _extract_dom_reservations(page) -> list[dict]:
    """Fallback: extract upcoming reservations from rendered DOM cards on /home/."""
    try:
        items = page.evaluate("""() => {
            const results = [];
            const cards = document.querySelectorAll(
                'app-reservation-card, app-vacation-plan, [class*="reservation-card"], [class*="booking-card"]'
            );
            cards.forEach(card => {
                const text = card.innerText || '';
                const confMatch = text.match(/(?:Confirmation|Conf)[\\s#:]*([A-Z0-9]{6,16})/i);
                const ptsMatch = text.match(/(\\d+)\\s*(?:Vacation\\s*)?Points/i);
                const dateMatch = text.match(/([A-Z][a-z]{2,8}\\s+\\d{1,2},?\\s+\\d{4})\\s*(?:[-–—]|to)\\s*([A-Z][a-z]{2,8}\\s+\\d{1,2},?\\s+\\d{4})/i);
                const heading = card.querySelector('h2, h3, h4, .resort-name, [class*="resort"]');
                if (confMatch || dateMatch) {
                    results.push({
                        resort: heading ? heading.innerText.trim() : 'DVC Resort',
                        check_in: dateMatch ? dateMatch[1] : 'N/A',
                        check_out: dateMatch ? dateMatch[2] : 'N/A',
                        confirmation_number: confMatch ? confMatch[1] : 'N/A',
                        room_type: 'Villa',
                        points: ptsMatch ? parseInt(ptsMatch[1], 10) : 0
                    });
                }
            });
            return results;
        }""")
        return items if isinstance(items, list) else []
    except Exception:
        return []


def _build_result_data(
    membership_details: dict,
    profile_details: dict,
    api_reservations: list,
    past_reservations_data: list,
    dashboard_plans_text: str,
    existing_cache: dict = None
) -> dict:
    """Build normalized dashboard payload from captured DVC API responses."""
    member_name = "Daniel Vickers"
    use_year_month_name = "March"
    home_resort_name = "Boulder Ridge Villas at Disney's Wilderness Lodge"

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

    membership_id = membership_details.get("membershipId", "506080513403") if membership_details else "506080513403"
    avail_to_book = membership_details.get("vacationPointsAvailableToBook", 0) if membership_details else 0
    banking_deadline = membership_details.get("currentUseYearBankingWindowCloses", "2026-10-31") if membership_details else "2026-10-31"

    points_by_year = []
    points_per_use_date = membership_details.get("pointsInContractsPerUseDate", []) if membership_details else []

    for entry in points_per_use_date:
        use_year = str(entry.get("useDate", ""))
        for c in entry.get("contracts", []):
            contract_id = str(c.get("contractId", c.get("addOnId", "")))
            resort_code = c.get("resortCode", "VWL")
            r_name = RESORT_NAMES.get(resort_code, resort_code)
            home_resort_name = r_name

            annual = c.get("annualPointsAllotment", 0)
            balance = c.get("pointsBalance", 0)

            banked_in_val = c.get("pointsBankedIn", {}).get("value", 0)
            banked_in_yr = c.get("pointsBankedIn", {}).get("useYear", "")

            banked_out_val = c.get("pointsBankedOut", {}).get("value", 0)
            banked_out_yr = c.get("pointsBankedOut", {}).get("useYear", "")

            borrowed_in_val = c.get("pointsBorrowedIn", {}).get("value", 0)

            points_by_year.append({
                "use_year": use_year,
                "contract_id": contract_id,
                "resort_code": resort_code,
                "resort_name": r_name,
                "annual_allotment": annual,
                "points_balance": balance,
                "banked_in": banked_in_val,
                "banked_in_from_year": banked_in_yr,
                "banked_out": banked_out_val,
                "banked_out_to_year": banked_out_yr,
                "borrowed_in": borrowed_in_val,
                "in_holding": c.get("pointsInHolding", 0)
            })

    # If points_by_year is empty but existing_cache had points, preserve existing points
    if not points_by_year and existing_cache and existing_cache.get("points_by_year"):
        points_by_year = existing_cache["points_by_year"]

    # Normalized & deduplicated upcoming reservations
    upcoming_trips = []
    seen_up = set()
    for r in api_reservations:
        if isinstance(r, dict):
            norm = _normalize_reservation(r)
            key = f"{norm['confirmation_number']}_{norm['check_in']}_{norm['resort']}"
            if key not in seen_up:
                seen_up.add(key)
                upcoming_trips.append(norm)

    # Normalized & deduplicated past reservations
    past_trips = []
    seen_past = set()
    for r in past_reservations_data:
        if isinstance(r, dict):
            norm = _normalize_reservation(r)
            key = f"{norm['confirmation_number']}_{norm['check_in']}_{norm['resort']}"
            if key not in seen_past:
                seen_past.add(key)
                past_trips.append(norm)

    return {
        "last_updated": datetime.now(ET).strftime("%B %d, %Y at %I:%M %p %Z"),
        "session_expired": False,
        "member": {
            "name": member_name,
            "membership_id": membership_id,
            "use_year_month": use_year_month_name,
            "home_resort": home_resort_name,
            "available_to_book": avail_to_book,
            "banking_deadline": banking_deadline
        },
        "points_by_year": points_by_year,
        "upcoming_trips": upcoming_trips,
        "past_trips": past_trips,
        "has_no_upcoming_text": "no upcoming vacations" in dashboard_plans_text.lower(),
        "today_windows": calculate_windows()
    }


def fetch_live_data():
    """Run headless Playwright to fetch fresh Points and Trips data."""
    if not AUTH_STATE_FILE.exists():
        raise PermissionError("auth_state.json not found. Please sign in to Disney first.")

    existing_cache = load_cached_data()
    membership_details = None
    profile_details = None
    api_reservations = []
    past_reservations_data = []
    dashboard_plans_text = ""

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
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

        def handle_response(response):
            nonlocal membership_details, profile_details
            try:
                ct = response.headers.get("content-type", "")
                if "application/json" in ct.lower():
                    url = response.url.lower()
                    if "memberships/details" in url:
                        membership_details = response.json()
                    elif "profile" in url and "dvc-lightcycle-api" in url:
                        profile_details = response.json()
                    elif "past-reservations" in url or "past-bookings" in url:
                        data = response.json()
                        if isinstance(data, list):
                            past_reservations_data.extend(data)
                        elif isinstance(data, dict):
                            past_reservations_data.extend(data.get("reservations", data.get("bookings", [])))
                    elif "reservation-list" in url or ("booking" in url and "list" in url) or "reservations" in url:
                        data = response.json()
                        if isinstance(data, dict):
                            res_list = data.get("reservations", data.get("upcomingReservations", []))
                            if isinstance(res_list, list):
                                api_reservations.extend(res_list)
                        elif isinstance(data, list) and "past" not in url:
                            api_reservations.extend(data)
            except Exception:
                pass

        page.on("response", handle_response)

        # 1. Fetch Points Page
        try:
            page.goto(DVC_POINTS_URL, wait_until="networkidle", timeout=35000)
        except Exception:
            pass

        # Check if Disney redirected to Sign-In
        current_url = page.url.lower()
        if "sign-in" in current_url or "login" in current_url:
            browser.close()
            raise PermissionError(
                "Your Disney session has expired. Click 'Sign In to Disney' (or run 'python dvc.py login') to refresh your session."
            )

        page.wait_for_timeout(3500)

        # 2. Fetch Dashboard for Trips
        try:
            page.goto(DVC_HOME_URL, wait_until="networkidle", timeout=35000)
            page.wait_for_timeout(2500)
            past_btn = page.get_by_text("View Past Bookings")
            if past_btn.count() > 0:
                past_btn.first.click()
                page.wait_for_timeout(2500)
            section = page.locator("section, div").filter(has_text="My Vacation Plans").first
            if section.count() > 0:
                dashboard_plans_text = section.inner_text()
            if not api_reservations:
                api_reservations.extend(_extract_dom_reservations(page))
        except Exception:
            pass

        if not membership_details:
            browser.close()
            raise PermissionError(
                "Could not retrieve DVC Points payload (your Disney session may have expired). Click 'Sign In to Disney' to re-authenticate."
            )

        try:
            context.storage_state(path=str(AUTH_STATE_FILE))
        except Exception:
            pass

        browser.close()

    result_data = _build_result_data(
        membership_details=membership_details,
        profile_details=profile_details,
        api_reservations=api_reservations,
        past_reservations_data=past_reservations_data,
        dashboard_plans_text=dashboard_plans_text,
        existing_cache=existing_cache
    )
    save_cache_data(result_data)
    return result_data


def interactive_login_and_sync() -> dict:
    """
    Launch a visible browser window for the user to log into Disney Vacation Club.
    Automatically detects when login completes and captures points & reservations.
    """
    existing_cache = load_cached_data()
    membership_details = None
    profile_details = None
    api_reservations = []
    past_reservations_data = []
    dashboard_plans_text = ""

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--start-maximized"]
        )
        context = browser.new_context(
            viewport=None,
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()

        def handle_response(response):
            nonlocal membership_details, profile_details
            try:
                ct = response.headers.get("content-type", "")
                if "application/json" in ct.lower():
                    url = response.url.lower()
                    if "memberships/details" in url:
                        membership_details = response.json()
                    elif "profile" in url and "dvc-lightcycle-api" in url:
                        profile_details = response.json()
                    elif "past-reservations" in url or "past-bookings" in url:
                        data = response.json()
                        if isinstance(data, list):
                            past_reservations_data.extend(data)
                        elif isinstance(data, dict):
                            past_reservations_data.extend(data.get("reservations", data.get("bookings", [])))
                    elif "reservation-list" in url or ("booking" in url and "list" in url) or "reservations" in url:
                        data = response.json()
                        if isinstance(data, dict):
                            res_list = data.get("reservations", data.get("upcomingReservations", []))
                            if isinstance(res_list, list):
                                api_reservations.extend(res_list)
                        elif isinstance(data, list) and "past" not in url:
                            api_reservations.extend(data)
            except Exception:
                pass

        page.on("response", handle_response)

        try:
            page.goto(DVC_POINTS_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            pass

        # Wait up to 3 minutes for the user to complete login + 2FA and land on DVC Points
        max_wait_ms = 180000
        elapsed = 0
        while elapsed < max_wait_ms:
            if membership_details is not None:
                break
            page.wait_for_timeout(1000)
            elapsed += 1000
            # If user logged in and landed on /home/ instead of /home/points/, navigate to /home/points/
            cur = page.url.lower()
            if (
                membership_details is None
                and "disneyvacationclub.disney.go.com" in cur
                and "sign-in" not in cur
                and "login" not in cur
                and "/home/points" not in cur
                and elapsed > 5000
                and elapsed % 8000 == 0
            ):
                try:
                    page.goto(DVC_POINTS_URL, wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    pass

        if not membership_details:
            browser.close()
            raise PermissionError("Login timed out or was closed before DVC Points loaded.")

        page.wait_for_timeout(2000)

        # Now visit DVC Home to capture Upcoming & Past Reservations
        try:
            page.goto(DVC_HOME_URL, wait_until="networkidle", timeout=35000)
            page.wait_for_timeout(2500)
            past_btn = page.get_by_text("View Past Bookings")
            if past_btn.count() > 0:
                past_btn.first.click()
                page.wait_for_timeout(2500)
            section = page.locator("section, div").filter(has_text="My Vacation Plans").first
            if section.count() > 0:
                dashboard_plans_text = section.inner_text()
            if not api_reservations:
                api_reservations.extend(_extract_dom_reservations(page))
        except Exception:
            pass

        # Save fresh authenticated session state
        try:
            context.storage_state(path=str(AUTH_STATE_FILE))
        except Exception:
            pass

        browser.close()

    result_data = _build_result_data(
        membership_details=membership_details,
        profile_details=profile_details,
        api_reservations=api_reservations,
        past_reservations_data=past_reservations_data,
        dashboard_plans_text=dashboard_plans_text,
        existing_cache=existing_cache
    )
    save_cache_data(result_data)
    return result_data


def _is_pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is currently running."""
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def _discover_unregistered_watchers(known_pids: set[int]) -> list[dict]:
    """Fallback: scan running processes for watcher.py instances not yet in watcher_state.json."""
    discovered = []
    try:
        out = subprocess.check_output(["ps", "-eo", "pid,command"], text=True, stderr=subprocess.DEVNULL)
        for line in out.splitlines():
            line = line.strip()
            if "watcher.py" not in line or "grep" in line:
                continue
            parts = line.split(None, 1)
            if len(parts) < 2:
                continue
            try:
                pid = int(parts[0])
            except ValueError:
                continue
            if pid in known_pids or pid == os.getpid():
                continue

            cmd_str = parts[1]
            tokens = shlex.split(cmd_str)
            # Ensure this is actually invoking watcher.py (not dvc.py wrapper which spawns watcher.py)
            if not any(t.endswith("watcher.py") for t in tokens):
                continue

            resorts = ["VWL"]
            room_arg = "studio"
            check_in = "N/A"
            check_out = "N/A"
            interval = 15

            for i, tok in enumerate(tokens):
                if tok == "--resort" and i + 1 < len(tokens):
                    resorts = [r.strip().upper() for r in tokens[i + 1].split(",")]
                elif tok == "--room" and i + 1 < len(tokens):
                    room_arg = tokens[i + 1]
                elif tok == "--start" and i + 1 < len(tokens):
                    check_in = tokens[i + 1]
                elif tok == "--end" and i + 1 < len(tokens):
                    check_out = tokens[i + 1]
                elif tok == "--interval" and i + 1 < len(tokens):
                    try:
                        interval = int(tokens[i + 1])
                    except ValueError:
                        pass

            room_map = {
                "studio": "Deluxe Studio",
                "1-bed": "1-Bedroom",
                "2-bed": "2-Bedroom",
                "3-bed": "3-Bedroom"
            }
            discovered.append({
                "id": f"watcher_{pid}",
                "pid": pid,
                "resorts": resorts,
                "resort_names": [RESORT_NAMES.get(c, c) for c in resorts],
                "room_type": room_map.get(room_arg.lower(), room_arg.title()),
                "room_arg": room_arg,
                "check_in": check_in,
                "check_out": check_out,
                "interval_minutes": interval,
                "started_at": "Running in background",
                "status": "checking",
                "status_text": "Active background process",
                "check_count": 1,
                "last_checked": None,
                "next_check": None,
                "latest_results": [],
                "open_rooms_count": 0
            })
    except Exception:
        pass
    return discovered


def get_active_watchers() -> list[dict]:
    """Return list of currently running availability watchers, pruning any dead PIDs."""
    lock_path = DIR / ".watcher_state.lock"
    active_list = []
    known_pids = set()

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

            cleaned_state = {}
            dirty = False
            for wid, entry in state.items():
                pid = entry.get("pid")
                if _is_pid_alive(pid):
                    cleaned_state[wid] = entry
                    active_list.append(entry)
                    known_pids.add(pid)
                else:
                    dirty = True

            if dirty:
                tmp_file = WATCHER_STATE_FILE.with_suffix(".tmp")
                with open(tmp_file, "w") as f:
                    json.dump(cleaned_state, f, indent=2)
                tmp_file.replace(WATCHER_STATE_FILE)

            fcntl.flock(lock_file, fcntl.LOCK_UN)
    except Exception:
        pass

    # Also include any running watcher.py processes discovered via ps
    extra = _discover_unregistered_watchers(known_pids)
    active_list.extend(extra)
    return active_list


def start_watcher(resorts: str, room_type: str, check_in: str, check_out: str, interval: int = 15) -> dict:
    """Spawn a new background availability watcher process."""
    if not check_in or not check_out:
        raise ValueError("Check-in and check-out dates are required.")

    ci_date = datetime.strptime(check_in, "%Y-%m-%d").date()
    co_date = datetime.strptime(check_out, "%Y-%m-%d").date()
    if co_date <= ci_date:
        raise ValueError("Check-out date must be after check-in date.")

    today_et = datetime.now(ET).date()
    if ci_date < today_et:
        raise ValueError("Check-in date cannot be in the past.")

    farthest_11m = add_months(today_et, 11)
    if ci_date > farthest_11m:
        raise ValueError(
            f"Check-in date ({check_in}) is beyond the 11-month booking horizon ({farthest_11m.isoformat()})."
        )

    watcher_script = DIR / "watcher.py"
    cmd = [
        sys.executable,
        str(watcher_script),
        "--resort", resorts,
        "--room", room_type,
        "--start", check_in,
        "--end", check_out,
        "--interval", str(max(3, int(interval)))
    ]

    proc = subprocess.Popen(
        cmd,
        cwd=str(DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True
    )

    return {
        "pid": proc.pid,
        "id": f"watcher_{proc.pid}",
        "status": "started"
    }


def stop_watcher(watcher_id: str) -> bool:
    """Terminate a running watcher by ID (e.g., 'watcher_12345') or PID."""
    pid = None
    if watcher_id.startswith("watcher_"):
        try:
            pid = int(watcher_id.split("_", 1)[1])
        except ValueError:
            pass
    else:
        try:
            pid = int(watcher_id)
            watcher_id = f"watcher_{pid}"
        except ValueError:
            pass

    if pid and _is_pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass

    # Clean up from state file immediately
    lock_path = DIR / ".watcher_state.lock"
    try:
        with open(lock_path, "w") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            if WATCHER_STATE_FILE.exists():
                with open(WATCHER_STATE_FILE, "r") as f:
                    state = json.load(f)
                if isinstance(state, dict) and watcher_id in state:
                    state.pop(watcher_id, None)
                    tmp_file = WATCHER_STATE_FILE.with_suffix(".tmp")
                    with open(tmp_file, "w") as f:
                        json.dump(state, f, indent=2)
                    tmp_file.replace(WATCHER_STATE_FILE)
            fcntl.flock(lock_file, fcntl.LOCK_UN)
    except Exception:
        pass

    return True
