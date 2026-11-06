#!/usr/bin/env python3
"""
app.py - Disney Vacation Club Local Web Dashboard

Starts a local web server to display your DVC points, trips, and booking windows in your browser.
"""

import sys
import webbrowser
import threading
from pathlib import Path
from flask import Flask, render_template, jsonify, request
from rich.console import Console
from rich.panel import Panel

import dvc_service

console = Console()
app = Flask(__name__, template_folder="templates")

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/data")
def get_data():
    data = dvc_service.load_cached_data()
    if not data:
        try:
            data = dvc_service.fetch_live_data()
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    data["watchers"] = dvc_service.get_active_watchers()
    return jsonify(data)

@app.route("/api/refresh", methods=["POST"])
def refresh_data():
    try:
        data = dvc_service.fetch_live_data()
        data["watchers"] = dvc_service.get_active_watchers()
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/watchers", methods=["GET"])
def list_watchers():
    return jsonify({"watchers": dvc_service.get_active_watchers()})

@app.route("/api/watchers", methods=["POST"])
def create_watcher():
    payload = request.get_json(silent=True) or {}
    try:
        result = dvc_service.start_watcher(
            resorts=payload.get("resorts", "VWL"),
            room_type=payload.get("room_type", "studio"),
            check_in=payload.get("check_in", ""),
            check_out=payload.get("check_out", ""),
            interval=int(payload.get("interval", 15))
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/watchers/<watcher_id>", methods=["DELETE"])
def delete_watcher(watcher_id):
    try:
        dvc_service.stop_watcher(watcher_id)
        return jsonify({"status": "stopped", "id": watcher_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/windows")
def get_windows():
    date_param = request.args.get("date")
    try:
        result = dvc_service.calculate_windows(date_param)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

def open_browser(port):
    webbrowser.open(f"http://localhost:{port}")

def main():
    port = 5050
    url = f"http://localhost:{port}"

    console.print(Panel.fit(
        f"[bold cyan]Disney Vacation Club - Local Web Dashboard[/bold cyan]\n"
        f"Server running at: [bold green]{url}[/bold green]\n"
        f"Press [bold yellow]Ctrl+C[/bold yellow] in this terminal to stop the server.",
        border_style="cyan"
    ))

    # Auto-open browser after 1 second
    threading.Timer(1.2, open_browser, args=[port]).start()

    # Run Flask server
    app.run(host="127.0.0.1", port=port, debug=False)

if __name__ == "__main__":
    main()
