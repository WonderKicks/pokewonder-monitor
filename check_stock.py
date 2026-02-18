import os
import json
import time
import hashlib

from playwright.sync_api import sync_playwright

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

URLS = {
    "PC Root": "https://www.pokemoncenter.com",
    "PC UK": "https://www.pokemoncenter.com/en-gb",
}

STATE_FILE = "state.json"
RE_ALERT_SECONDS = 6 * 60 * 60  # re-alert if queue persists > 6 hours

QUEUE_TEXT_HINTS = [
    "virtual queue",
    "you're in the virtual queue",
    "estimated wait time",
    "keep this window open",
    "do not refresh",
    "you will lose your place",
    "high volume of requests",
    "you are in line",
    "please wait",
]

QUEUE_URL_HINTS = [
    "queue-it",
    "queue",
    "waiting",
    "virtual-queue",
]


def tg_send(text: str) -> None:
    # Use simple fetch via Playwright request? No—Telegram is fine with plain HTTP.
    import requests
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": text},
        timeout=20,
    )


def stable_hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()[:12]


def load_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)


def should_alert(prev: dict, key: str, now_ts: int) -> bool:
    last_status = prev.get(key, {}).get("status")
    last_alert_ts = prev.get(key, {}).get("last_alert_ts", 0)

    if last_status != "QUEUE":
        return True
    return (now_ts - last_alert_ts) > RE_ALERT_SECONDS


def is_queue(url: str, text: str) -> tuple[bool, str]:
    u = (url or "").lower()
    t = (text or "").lower()

    if any(h in u for h in QUEUE_URL_HINTS):
        return True, "URL matched queue hints"
    if any(h in t for h in QUEUE_TEXT_HINTS):
        return True, "Page text matched queue hints"
    return False, "No queue hints"


def check_with_browser(target_url: str) -> dict:
    """
    Loads the page with a real browser (JS enabled) and returns:
      - final_url
      - text_snippet
      - queue_detected, reason
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
            )
        )
        page = context.new_page()

        # Go and wait for network to settle (best for JS-driven queue pages)
        page.goto(target_url, wait_until="networkidle", timeout=60000)

        final_url = page.url
        body_text = page.inner_text("body") if page.locator("body").count() else ""

        # Keep a small snippet for logs only
        snippet = " ".join(body_text.split())[:400]

        q, reason = is_queue(final_url, body_text)

        context.close()
        browser.close()

        return {
            "final_url": final_url,
            "snippet": snippet,
            "queue": q,
            "reason": reason,
        }


def main():
    now_ts = int(time.time())
    state = load_state()

    for name, url in URLS.items():
        key = stable_hash(name + "|" + url)

        result = check_with_browser(url)

        # Print debug info into Actions logs
        print(json.dumps({
            "name": name,
            "checked": url,
            "final_url": result["final_url"],
            "queue": result["queue"],
            "reason": result["reason"],
            "snippet": result["snippet"],
        }, ensure_ascii=False))

        if result["queue"]:
            if should_alert(state, key, now_ts):
                tg_send(
                    "🚨 POKÉMON CENTER QUEUE DETECTED\n\n"
                    f"Source: {name}\n"
                    f"Link: {result['final_url']}\n"
                    f"Reason: {result['reason']}\n"
                )
                state[key] = {"status": "QUEUE", "last_alert_ts": now_ts, "final_url": result["final_url"]}
            else:
                state[key] = {**state.get(key, {}), "status": "QUEUE", "final_url": result["final_url"]}
        else:
            state[key] = {**state.get(key, {}), "status": "OK", "final_url": result["final_url"]}

    save_state(state)


if __name__ == "__main__":
    main()