#!/usr/bin/env python3
"""
notifications.py - Multi-channel Notification System for DVC Tracker

Supports:
1. Native macOS Notifications (Default, zero setup, audio chime)
2. Discord Webhook (Simplest mobile push to iOS/Android via Discord app)
3. Telegram Bot (Free mobile push)
"""

import os
import sys
import json
import subprocess
import urllib.request
import urllib.parse
from pathlib import Path
from dotenv import load_dotenv

# Load optional .env file from project root
ENV_FILE = Path(__file__).parent / ".env"
if ENV_FILE.exists():
    load_dotenv(ENV_FILE)

DVC_BOOKING_URL = "https://disneyvacationclub.disney.go.com/booking/"

def send_macos_notification(title: str, message: str, sound: str = "Glass"):
    """Display native macOS banner notification with sound."""
    try:
        # Sanitize quotes
        safe_title = title.replace('"', '\\"')
        safe_msg = message.replace('"', '\\"')
        cmd = f'display notification "{safe_msg}" with title "{safe_title}" sound name "{sound}"'
        subprocess.run(["osascript", "-e", cmd], check=True)
        return True
    except Exception as e:
        print(f"[Notifications] macOS alert error: {e}", file=sys.stderr)
        return False

def send_discord_webhook(title: str, message: str, url: str = None, webhook_url: str = None):
    """Send push notification to Discord channel via webhook."""
    webhook = webhook_url or os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook:
        return False

    target_url = url or DVC_BOOKING_URL
    payload = {
        "embeds": [
            {
                "title": f"🏰 {title}",
                "description": message,
                "url": target_url,
                "color": 3447003,  # Disney Royal Blue
                "fields": [
                    {
                        "name": "Action",
                        "value": f"[Click here to Book on Disney Vacation Club]({target_url})",
                        "inline": False
                    }
                ],
                "footer": {
                    "text": "DVC Availability Tracker"
                }
            }
        ]
    }

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            webhook,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "DVC-Availability-Tracker/1.0"
            }
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status in (200, 204)
    except Exception as e:
        print(f"[Notifications] Discord webhook error: {e}", file=sys.stderr)
        return False

def send_telegram(title: str, message: str, url: str = None):
    """Send message via Telegram Bot if token and chat ID are configured."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        return False

    target_url = url or DVC_BOOKING_URL
    text = f"🏰 *{title}*\n\n{message}\n\n👉 [Book on DVC Website]({target_url})"
    api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    try:
        data = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }).encode("utf-8")
        req = urllib.request.Request(api_url, data=data)
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status == 200
    except Exception as e:
        print(f"[Notifications] Telegram error: {e}", file=sys.stderr)
        return False

def notify(title: str, message: str, url: str = None, sound: str = "Glass"):
    """
    Dispatch notification across all configured channels:
    - Native macOS banner (always)
    - Discord (if DISCORD_WEBHOOK_URL set)
    - Telegram (if TELEGRAM_BOT_TOKEN & TELEGRAM_CHAT_ID set)
    """
    results = {}
    
    # 1. Native macOS alert
    results["macos"] = send_macos_notification(title, message, sound=sound)
    
    # 2. Discord Webhook
    if os.getenv("DISCORD_WEBHOOK_URL"):
        results["discord"] = send_discord_webhook(title, message, url)
        
    # 3. Telegram Bot
    if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
        results["telegram"] = send_telegram(title, message, url)
        
    return results

if __name__ == "__main__":
    print("Testing DVC notification dispatcher...")
    res = notify(
        title="DVC Room Available!",
        message="Boulder Ridge Villas - Deluxe Studio is OPEN for Oct 12, 2027 to Oct 17, 2027 (82 pts total)."
    )
    print("Dispatch result:", res)
