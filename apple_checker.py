#!/usr/bin/env python3
"""
Apple Malaysia MacBook Pro M5 Pro Availability Checker
Monitors the Apple MY edu store and sends Telegram alerts when stock status changes.
Includes a keep-alive HTTP ping server for UptimeRobot.
"""

import os
import time
import logging
import hashlib
import requests
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from bs4 import BeautifulSoup
from datetime import datetime

# ── Configuration ──────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID",   "YOUR_CHAT_ID_HERE")

TARGET_URL = (
    "https://www.apple.com/my-edu/shop/buy-mac/macbook-pro/"
    "14-inch-space-black-standard-display-apple-m5-pro-chip-"
    "15-core-cpu-16-core-gpu-24gb-memory-1tb-storage"
)

# How often to check (seconds). 300 = every 5 minutes.
CHECK_INTERVAL = 300

# Exact text found on the Apple MY page (normalised to lowercase for comparison).
UNAVAILABLE_PHRASE = "check back later for availability: new models."

STATE_FILE = "last_status.txt"   # persists status across restarts

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("apple_checker.log"),
    ],
)
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-MY,en;q=0.9",
}


# ── Keep-alive ping server (for UptimeRobot) ──────────────────────────────────
class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK - Apple checker is alive!")

    def log_message(self, format, *args):
        pass  # Suppress noisy HTTP access logs


def start_ping_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), PingHandler)
    log.info(f"Ping server listening on port {port}")
    server.serve_forever()


# ── Telegram helpers ───────────────────────────────────────────────────────────
def send_telegram(message: str) -> bool:
    """Send a message via Telegram Bot API. Returns True on success."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        log.info("Telegram notification sent.")
        return True
    except Exception as e:
        log.error(f"Telegram send failed: {e}")
        return False


# ── Page scraping ──────────────────────────────────────────────────────────────
def fetch_availability() -> str | None:
    """
    Fetch the Apple page and extract the availability / add-to-bag text.
    Returns a normalised string, or None on error.
    """
    try:
        r = requests.get(TARGET_URL, headers=HEADERS, timeout=20)
        r.raise_for_status()
    except Exception as e:
        log.error(f"HTTP error: {e}")
        return None

    soup = BeautifulSoup(r.text, "html.parser")

    # 1️⃣  Look for the exact "Check back later for availability: New models." notice
    for tag in soup.find_all(string=True):
        if "check back later for availability" in tag.lower():
            return tag.strip()

    # 2️⃣  Look for the Add to Bag / Buy button
    for selector in [
        "button.add-to-cart",
        "button[data-autom='add-to-cart']",
        "button[data-autom='buy-button']",
        ".purchaseButtons button",
        "button.button-cta",
    ]:
        btn = soup.select_one(selector)
        if btn:
            return btn.get_text(strip=True)

    # 3️⃣  Fallback: return a stable hash of the whole page body so any change
    #     is still detected even if the selectors miss.
    body = soup.get_text(separator=" ", strip=True)
    digest = hashlib.md5(body.encode()).hexdigest()
    log.warning("Could not find a specific availability element; using page hash.")
    return f"PAGE_HASH:{digest}"


# ── State persistence ──────────────────────────────────────────────────────────
def load_last_status() -> str | None:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return f.read().strip() or None
    return None


def save_status(status: str) -> None:
    with open(STATE_FILE, "w") as f:
        f.write(status)


# ── Main loop ──────────────────────────────────────────────────────────────────
def main():
    # Start ping server in background thread so UptimeRobot can keep us alive
    t = threading.Thread(target=start_ping_server, daemon=True)
    t.start()

    log.info("🍎 Apple MY MacBook Pro M5 Pro availability checker starting…")
    send_telegram(
        "🤖 <b>Apple Checker started</b>\n"
        "Monitoring MacBook Pro 14\" M5 Pro (MY Edu Store) every "
        f"{CHECK_INTERVAL // 60} min."
    )

    last_status = load_last_status()
    log.info(f"Last known status: {last_status!r}")

    while True:
        current = fetch_availability()
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if current is None:
            log.warning("Could not fetch page — will retry.")
        else:
            log.info(f"Current status: {current!r}")

            if last_status is None:
                # First run
                log.info("First run — saving baseline status.")
                save_status(current)
                last_status = current
                send_telegram(
                    f"📋 <b>Baseline recorded</b>\n"
                    f"Status: <i>{current}</i>\n"
                    f"Time: {ts}"
                )

            elif current.lower() != last_status.lower():
                log.info(f"⚡ Status changed!  {last_status!r} → {current!r}")

                was_unavailable = UNAVAILABLE_PHRASE in last_status.lower()
                now_available   = UNAVAILABLE_PHRASE not in current.lower()

                if was_unavailable and now_available:
                    msg = (
                        "🚨 <b>MacBook Pro M5 Pro is NOW AVAILABLE in Malaysia!</b> 🇲🇾\n\n"
                        f"Previous: <s>{last_status}</s>\n"
                        f"Now: <b>{current}</b>\n\n"
                        f"🛒 <a href='{TARGET_URL}'>Buy now</a>\n"
                        f"⏰ {ts}"
                    )
                else:
                    msg = (
                        "🔔 <b>Apple Store status changed</b>\n\n"
                        f"Previous: <i>{last_status}</i>\n"
                        f"Now: <b>{current}</b>\n\n"
                        f"🔗 <a href='{TARGET_URL}'>Check page</a>\n"
                        f"⏰ {ts}"
                    )

                send_telegram(msg)
                save_status(current)
                last_status = current

        log.info(f"Sleeping {CHECK_INTERVAL}s…\n")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
