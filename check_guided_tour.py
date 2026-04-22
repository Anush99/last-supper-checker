"""
Cenacolo Vinciano (Last Supper) — Guided Tour (English) ticket checker.
Checks April 29 & 30 availability on the guided tour page.
"""

import argparse
import asyncio
import platform
import subprocess
import sys
from datetime import datetime

from playwright.async_api import async_playwright

URL = (
    "https://cenacolovinciano.vivaticket.it/en/event/"
    "cenacolo-visite-guidate-a-orario-fisso-in-inglese/238363?idt=2547"
)
EVENT_NAME = "Guided Tour (English)"
EVENT_ID = "238363"
TARGET_DAYS = [29, 30]
MONTH_NAME = "APRIL 2026"

ALERT_REPEATS = 10
ALERT_VOLUME = 5.0
ALERT_SOUND = "/System/Library/Sounds/Sosumi.aiff"
NTFY_TOPIC = "anushik-last-supper"


def send_push(title: str, message: str) -> None:
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
            print(f"  Push sent to ntfy.sh/{NTFY_TOPIC}")
        else:
            print(f"  Push failed (HTTP {result.stdout.strip()})")
    except Exception as exc:
        print(f"  Push error: {exc}")


def notify(title: str, message: str) -> None:
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
            subprocess.run(["afplay", "--volume", str(ALERT_VOLUME), ALERT_SOUND])
        except FileNotFoundError:
            break


async def check_availability(headless: bool = True) -> dict[int, bool | None]:
    results: dict[int, bool | None] = {}
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
            for attempt in range(3):
                await page.goto(URL, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_timeout(3_000)

                queue_wait = 0
                while "queue-it.net" in page.url:
                    if queue_wait == 0:
                        print("  In Queue-it waiting room…")
                    await page.wait_for_timeout(10_000)
                    queue_wait += 10
                    if queue_wait >= 300:
                        break
                    if queue_wait % 60 == 0:
                        print(f"  Still in queue ({queue_wait}s)…")

                if "/queue/" in page.url or "queueerror" in page.url:
                    print("  Queue session expired, retrying…")
                    btn = page.locator('a.btn:has-text("Take a new place in line")')
                    if await btn.count() > 0:
                        await btn.click()
                        await page.wait_for_timeout(5_000)
                    continue

                if await page.locator('a[href*="queue-it.net"]').count() > 0:
                    print("  Queue-it overlay, retrying…")
                    continue
                break
            else:
                print("  Could not get past queue.")
                return {day: None for day in TARGET_DAYS}

            await page.wait_for_selector(f"#calendar_{EVENT_ID}", timeout=15_000)

            month_text = await page.text_content(f"#mese_anno_{EVENT_ID}")
            if month_text and MONTH_NAME not in month_text.upper():
                print(f"  Navigating to {MONTH_NAME}…")
                next_btn = page.locator(f"#mese_next_{EVENT_ID} a")
                for _ in range(12):
                    await next_btn.click()
                    await page.wait_for_timeout(800)
                    month_text = await page.text_content(f"#mese_anno_{EVENT_ID}")
                    if month_text and MONTH_NAME in month_text.upper():
                        break

            for day in TARGET_DAYS:
                cell = page.locator(f"li.day.cal4{day}")
                if await cell.count() == 0:
                    print(f"  Day {day} — not found.")
                    results[day] = None
                    continue
                classes = await cell.get_attribute("class") or ""
                title = await cell.get_attribute("title") or ""
                has_link = "<a " in (await cell.inner_html()).lower()
                print(f"  Day {day} — class: '{classes}' | title: '{title}' | link: {has_link}")
                if "inactive" in classes or "no-event" in classes:
                    results[day] = False
                elif has_link:
                    results[day] = True
                else:
                    results[day] = False
        finally:
            await browser.close()
    return results


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int, default=300)
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    days_label = ", ".join(str(d) for d in TARGET_DAYS)
    while True:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n[{now}] {EVENT_NAME} — checking April {days_label}…")
        try:
            results = await check_availability(headless=not args.headed)
        except Exception as exc:
            print(f"  ERROR: {exc}")
            results = {day: None for day in TARGET_DAYS}

        available = [d for d, v in results.items() if v is True]
        if available:
            days_str = ", ".join(str(d) for d in available)
            msg = f"April {days_str} AVAILABLE! {URL}"
            print(f"\n  *** {msg} ***\n")
            notify(f"{EVENT_NAME} Tickets", msg)
            sys.exit(0)
        else:
            print("  No availability yet.")

        if not args.loop:
            break
        print(f"  Next check in {args.interval}s…")
        await asyncio.sleep(args.interval)


if __name__ == "__main__":
    asyncio.run(main())
