"""
Cenacolo Vinciano (Last Supper) — April 30 ticket availability checker.

Periodically opens the Vivaticket calendar page, inspects the DOM for the
April 30 cell, and reports whether seats are available.  When availability is
detected it plays an audible alert and can optionally send a macOS notification.

Usage:
    python check_availability.py                  # single check
    python check_availability.py --loop           # poll every 5 minutes
    python check_availability.py --loop --interval 120  # poll every 2 minutes
"""

import argparse
import asyncio
import os
import platform
import subprocess
import sys
import time
from datetime import datetime

from playwright.async_api import async_playwright

URL = (
    "https://cenacolovinciano.vivaticket.it/en/event/cenacolo-vinciano/151991"
    "?idt=2547"
)

TARGET_DAY = 21
MONTH_NAME = "APRIL 2026"
CAL_CELL_SELECTOR = f"li.day.cal4{TARGET_DAY}"
CALENDAR_SELECTOR = "#calendar_151991"
NEXT_MONTH_SELECTOR = "#mese_next_151991 a"
MONTH_LABEL_SELECTOR = "#mese_anno_151991"


ALERT_REPEATS = 5
ALERT_VOLUME = 5.0  # 1.0 = normal, 2.0 = double loudness (max useful ~10)
ALERT_SOUND = "/System/Library/Sounds/Glass.aiff"

# ntfy.sh push notifications — install the ntfy app on your phone,
# subscribe to this topic, and you'll get instant alerts.
NTFY_TOPIC = "anushik-last-supper"


def send_push(title: str, message: str) -> None:
    """Send a push notification via ntfy.sh (free, no account needed)."""
    if not NTFY_TOPIC:
        return
    try:
        result = subprocess.run(
            [
                "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                "-H", f"Title: {title}",
                "-H", "Priority: urgent",
                "-H", "Tags: rotating_light",
                "-d", message,
                f"https://ntfy.sh/{NTFY_TOPIC}",
            ],
            capture_output=True, text=True, timeout=15,
        )
        if result.stdout.strip() == "200":
            print(f"  Push notification sent to ntfy.sh/{NTFY_TOPIC}")
        else:
            print(f"  Push notification failed (HTTP {result.stdout.strip()})")
    except Exception as exc:
        print(f"  Failed to send push notification: {exc}")


def notify(title: str, message: str) -> None:
    """Send push notification; on macOS also show desktop alert + loud sound."""
    send_push(title, message)
    if platform.system() != "Darwin":
        return
    try:
        subprocess.run([
            "osascript", "-e",
            f'display notification "{message}" with title "{title}" sound name "Hero"',
        ])
    except FileNotFoundError:
        pass
    for _ in range(ALERT_REPEATS):
        try:
            subprocess.run([
                "afplay", "--volume", str(ALERT_VOLUME), ALERT_SOUND,
            ])
        except FileNotFoundError:
            break


async def check_availability(headless: bool = True) -> bool | None:
    """
    Return True if April 30 has availability, False if sold-out / inactive,
    or None if the date cell wasn't found at all.
    """
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        try:
            max_queue_retries = 3
            for attempt in range(max_queue_retries):
                await page.goto(URL, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_timeout(3_000)

                # Case 1: Redirected to queue-it.net waiting room
                queue_wait = 0
                while "queue-it.net" in page.url:
                    if queue_wait == 0:
                        print("  In Queue-it waiting room, waiting…")
                    await page.wait_for_timeout(10_000)
                    queue_wait += 10
                    if queue_wait >= 300:
                        print("  Queue timeout after 5 min, retrying…")
                        break
                    if queue_wait % 60 == 0:
                        print(f"  Still in queue ({queue_wait}s)…")

                # Case 2: "Your place in line is no longer valid" error page
                # URL contains /queue/queueerrorpage.php
                if "/queue/" in page.url or "queueerror" in page.url:
                    print("  Queue session expired ('Your place in line is no longer valid').")
                    new_place_btn = page.locator('a.btn:has-text("Take a new place in line")')
                    if await new_place_btn.count() > 0:
                        print("  Clicking 'Take a new place in line'…")
                        await new_place_btn.click()
                        await page.wait_for_timeout(5_000)
                    continue

                # Case 3: Queue-it overlay/link on the actual page
                queue_link = page.locator('a[href*="queue-it.net"]')
                if await queue_link.count() > 0:
                    print("  Queue-it overlay detected, retrying…")
                    continue

                # No queue issues — we're on the real page
                break
            else:
                print(f"  Could not get past queue after {max_queue_retries} attempts.")
                return None

            await page.wait_for_selector(CALENDAR_SELECTOR, timeout=15_000)

            # Make sure we're looking at the right month.  The page might open
            # on a different month, so navigate forward/back if needed.
            month_text = await page.text_content(MONTH_LABEL_SELECTOR)
            if month_text and MONTH_NAME not in month_text.upper():
                print(f"  Calendar shows '{month_text}', navigating to {MONTH_NAME}…")
                next_btn = page.locator(NEXT_MONTH_SELECTOR)
                for _ in range(12):
                    await next_btn.click()
                    await page.wait_for_timeout(800)
                    month_text = await page.text_content(MONTH_LABEL_SELECTOR)
                    if month_text and MONTH_NAME in month_text.upper():
                        break

            cell = page.locator(CAL_CELL_SELECTOR)
            if await cell.count() == 0:
                print(f"  Could not find calendar cell for day {TARGET_DAY}.")
                return None

            classes = await cell.get_attribute("class") or ""
            title = await cell.get_attribute("title") or ""
            inner = await cell.inner_html()
            has_link = "<a " in inner.lower()

            print(f"  Day {TARGET_DAY} — class: '{classes}' | title: '{title}' | has link: {has_link}")

            if "inactive" in classes:
                return False
            if "no-event" in classes:
                return False
            if has_link:
                return True
            return False

        finally:
            await browser.close()


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check Cenacolo Vinciano April 30 ticket availability"
    )
    parser.add_argument(
        "--loop", action="store_true", help="Keep polling until availability is found"
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=300,
        help="Seconds between checks when --loop is set (default: 300)",
    )
    parser.add_argument(
        "--headed", action="store_true", help="Show the browser window"
    )
    args = parser.parse_args()

    while True:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n[{now}] Checking availability for April {TARGET_DAY}…")

        try:
            result = await check_availability(headless=not args.headed)
        except Exception as exc:
            print(f"  ERROR: {exc}")
            result = None

        if result is True:
            msg = f"SEATS AVAILABLE for April {TARGET_DAY}! Go book now!"
            print(f"\n  *** {msg} ***\n")
            notify("Last Supper Tickets", msg)
            sys.exit(0)
        elif result is False:
            print("  No availability yet.")
        else:
            print("  Could not determine availability (page may have changed).")

        if not args.loop:
            break

        print(f"  Next check in {args.interval} seconds…")
        await asyncio.sleep(args.interval)


if __name__ == "__main__":
    asyncio.run(main())
