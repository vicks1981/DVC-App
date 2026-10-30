#!/usr/bin/env python3
"""
dvc_service.py - Unified Data Service for Disney Vacation Club

Handles fetching, caching, and calculating points, reservations, and booking windows.
"""

import os
import json
import calendar
from datetime import datetime, date, time
from pathlib import Path
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

DIR = Path(__file__).parent
AUTH_STATE_FILE = DIR / "auth_state.json"
CACHE_FILE = DIR / "data_cache.json"
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
                return json.load(f)
        except Exception:
            pass
    return None

def save_cache_data(data: dict):
    """Save data to cache file."""
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f, indent=2)

def fetch_live_data():
    """Run headless Playwright to fetch fresh Points and Trips data."""
    if not AUTH_STATE_FILE.exists():
        raise FileNotFoundError("auth_state.json not found. Run python login.py first.")

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
                if "application/json" in ct:
                    url = response.url.lower()
                    if "memberships/details" in url:
                        membership_details = response.json()
                    elif "profile" in url and "dvc-lightcycle-api" in url:
                        profile_details = response.json()
                    elif "reservation-list" in url:
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

        # 1. Fetch Points Page
        try:
            page.goto(DVC_POINTS_URL, wait_until="networkidle", timeout=35000)
        except Exception:
            pass
        page.wait_for_timeout(3000)

        # 2. Fetch Dashboard for Trips
        try:
            page.goto(DVC_HOME_URL, wait_until="networkidle", timeout=35000)
            page.wait_for_timeout(2000)
            past_btn = page.get_by_text("View Past Bookings")
            if past_btn.count() > 0:
                past_btn.first.click()
                page.wait_for_timeout(2500)
            section = page.locator("section, div").filter(has_text="My Vacation Plans").first
            if section.count() > 0:
                dashboard_plans_text = section.inner_text()
        except Exception:
            pass

        try:
            context.storage_state(path=str(AUTH_STATE_FILE))
        except Exception:
            pass

        browser.close()

    # Parse and structure the collected data
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

    # Normalized reservations
    upcoming_trips = []
    for r in api_reservations:
        if isinstance(r, dict):
            upcoming_trips.append({
                "resort": r.get("resortName") or RESORT_NAMES.get(r.get("resortCode", ""), "Unknown Resort"),
                "check_in": r.get("checkInDate") or r.get("arrivalDate", "N/A"),
                "check_out": r.get("checkOutDate") or r.get("departureDate", "N/A"),
                "confirmation_number": r.get("confirmationNumber", "N/A"),
                "room_type": r.get("roomType", "Standard"),
                "points": r.get("points", 0)
            })

    past_trips = []
    for r in past_reservations_data:
        if isinstance(r, dict):
            past_trips.append({
                "resort": r.get("resortName") or RESORT_NAMES.get(r.get("resortCode", ""), "Unknown Resort"),
                "check_in": r.get("checkInDate") or r.get("arrivalDate", "N/A"),
                "check_out": r.get("checkOutDate") or r.get("departureDate", "N/A"),
                "confirmation_number": r.get("confirmationNumber", "N/A"),
                "room_type": r.get("roomType", "Standard"),
                "points": r.get("points", 0)
            })

    result_data = {
        "last_updated": datetime.now(ET).strftime("%B %d, %Y at %I:%M %p %Z"),
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

    save_cache_data(result_data)
    return result_data
