# Disney Vacation Club (DVC) CLI & Automation Tool

A lightweight Python & Playwright automation toolkit to query your Disney Vacation Club vacation points and reservation history headlessly.

---

## Features

* **Points Tracking:** Intercepts points balances across all Use Years, contracts, and resorts (banked in/out, borrowed, and available).
* **Trip History:** Queries upcoming vacations as well as past completed reservations from the last 12 months.
* **Persistent Sessions:** Authenticate once interactively (with 2FA); future runs execute 100% headlessly in the background.
* **Unified CLI:** Single master interface (`dvc.py`) for points, trips, or all information.

---

## Quick Start

### 1. Activate Environment
```bash
cd ~/projects/dvc-points
source venv/bin/activate
```

---

### 2. Login & Save Session (First Time Only)
Run the interactive login script:
```bash
python dvc.py login
```
*(or `python login.py`)*

1. A browser will open to the DVC sign-in page.
2. Sign in with your Disney credentials and complete any 2FA/SMS verification prompt.
3. When you see your dashboard, return to the terminal and press `[Enter]`.
4. Session cookies are securely stored in `auth_state.json`.

---

### 3. Usage & Commands

You can run individual modules or use the unified **`dvc.py`** CLI:

#### Check Vacation Points
```bash
python dvc.py points
```
*(or `python check_points.py`)*

#### Check Upcoming & Past Trips
```bash
python dvc.py trips
```
*(or `python check_trips.py`)*

#### Calculate 11-Month & 7-Month Booking Windows
```bash
# Check farthest check-in dates you can book today
python dvc.py windows

# Calculate opening dates and live countdown for a future vacation
python dvc.py windows --date 2027-10-15
```

#### Search Live Room Availability
```bash
# Check availability for specific resorts, room types, and dates
python dvc.py search --resort VWL,RIV --room studio --start 2027-10-15 --end 2027-10-20
```

#### Live Availability Tracker & Alert Watcher
```bash
# Monitor resorts every 15 minutes and fire native macOS alerts when rooms open
python dvc.py watch --resort VWL --room studio --start 2027-10-15 --end 2027-10-20 --interval 15
```

#### Launch Local Web Dashboard
```bash
python dvc.py web
```
*(or `python app.py`)*
Opens an interactive dashboard in your web browser at `http://localhost:5050`.

#### Check Everything at Once
```bash
python dvc.py all
```

---

## Optional Flags

* `--headed`: Watch the browser run in a visible window (helpful for troubleshooting):
  ```bash
  python dvc.py points --headed
  ```
* `--json`: Output raw JSON data (for alerts, integrations, or piping to `jq`):
  ```bash
  python dvc.py trips --json
  ```
* `--debug`: Save screenshot and rendered HTML for inspection:
  ```bash
  python dvc.py all --debug
  ```

---

## Security & Privacy Note
* Your password is **never** stored in plaintext.
* Session tokens in `auth_state.json` remain local on your computer and are excluded from git via `.gitignore`.
* When Disney invalidates your session (typically after several weeks), the scripts will clearly prompt you to run `python dvc.py login` again.
