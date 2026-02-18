import os
import json
import time
import hashlib
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

# Monitor BOTH the root domain and /en-gb.
# Your screenshot showed the root domain queue page (www.pokemoncenter.com),
# while earlier we were only checking /en-gb — that can cause missed alerts.
URLS = {
    "PC Root": "https://www.pokemoncenter.com",
    "PC UK (/en-gb)": "https://www.pokemoncenter.com/en-gb",
}

# Queue providers / patterns (redirect detection is the most reliable)
QUEUE_HOST_HINTS = [
    "queue-it",
    "queue",
    "waitingroom",
    "virtualqueue",
    "line",
    "wfp",  # sometimes appears in queue paths/params; harmless heuristic
]

# Queue page text fingerprints (covers the exact screen you shared)
QUEUE_TEXT_HINTS = [
    "virtual queue",
    "virtual waiting room",
    "hi, trainer",
    "you're in the virtual queue",
    "estimated wait time",
    "keep this window open",
    "do not refresh",
    "you will lose your place",
    "you are in line",
    "please wait",
    "high volume of requests",
]

UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

STATE_FILE = "state.json"
# If you're STILL in queue after a long time, you may want a repeat ping occasionally.
# This will allow a re-alert if queue remains for > 6 hours (optional safety).
RE_ALERT_SECONDS = 6 * 60 * 60


def tg_send(text: str) -> None:
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": text},
        timeout=20,
    )


def stable_hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()[:12]


def safe_host(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def looks_like_queue_url(url: str) -> bool:
    """Heuristic: queue provider often lives off pokemoncenter.com or includes queue-ish host/path."""
    u = (url or "").lower()
    h = safe_host(url)
    if not h:
        return False

    # If it redirected off pokemoncenter.com, that is very often the waiting room.
    if "pokemoncenter.com" not in h:
        # Many queue providers have queue-ish hostnames; check hint list.
        return any(hint in h or hint in u for hint in QUEUE_HOST_HINTS)

    # Even on pokemoncenter.com, some queue pages include strong indicators in path/params
    return any(hint in u for hint in ["virtual-queue", "waiting", "queue"])


def load_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)


def extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text(" ", strip=True)


def check_url(name: str, url: str) -> dict:
    """
    Returns a dict with:
      - status: OK / QUEUE / ERROR
      - final_url
      - history_urls
      - reason
    """
    try:
        r = requests.get(
            url,
            headers={"User-Agent": UA},
            timeout=30,
            allow_redirects=True,
        )

        final_url = r.url
        history_urls = [h.url for h in (r.history or [])]

        # 1) Redirect-chain detection (strong)
        # If any hop looks like queue, call it.
        for hop in history_urls + [final_url]:
            if looks_like_queue_url(hop):
                return {
                    "status": "QUEUE",
                    "final_url": final_url,
                    "history_urls": history_urls,
                    "reason": f"redirect/URL matched queue pattern: {hop}",
                }

        # 2) HTML text detection (covers cases where it stays on same host but shows queue page)
        text = extract_text(r.text).lower()
        if any(hint in text for hint in QUEUE_TEXT_HINTS):
            return {
                "status": "QUEUE",
                "final_url": final_url,
                "history_urls": history_urls,
                "reason": "HTML text matched queue fingerprints",
            }

        return {
            "status": "OK",
            "final_url": final_url,
            "history_urls": history_urls,
            "reason": "no queue detected",
        }

    except Exception as e:
        return {
            "status": "ERROR",
            "final_url": None,
            "history_urls": [],
            "reason": f"exception: {type(e).__name__}: {e}",
        }


def should_alert_queue(prev: dict, key: str, now_ts: int) -> bool:
    """
    Alert rules:
    - If we just transitioned into QUEUE: alert
    - If we are still QUEUE but last alert was > RE_ALERT_SECONDS ago: alert
    """
    last_status = prev.get(key, {}).get("status")
    last_alert_ts = prev.get(key, {}).get("last_alert_ts", 0)

    if last_status != "QUEUE":
        return True

    if (now_ts - last_alert_ts) > RE_ALERT_SECONDS:
        return True

    return False


def main():
    now_ts = int(time.time())
    state = load_state()

    for name, url in URLS.items():
        result = check_url(name, url)

        key = stable_hash(name + "|" + url)
        prev_entry = state.get(key, {})

        # Debug line for Actions logs (so you can see exactly what it saw)
        print(
            json.dumps(
                {
                    "name": name,
                    "url": url,
                    "status": result["status"],
                    "final_url": result["final_url"],
                    "history_urls": result["history_urls"],
                    "reason": result["reason"],
                },
                ensure_ascii=False,
            )
        )

        if result["status"] == "QUEUE":
            if should_alert_queue(state, key, now_ts):
                msg = (
                    "🚨 POKÉMON CENTER QUEUE DETECTED\n\n"
                    f"Source: {name}\n"
                    f"Checked: {url}\n"
                    f"Final: {result['final_url']}\n"
                    f"Reason: {result['reason']}\n"
                )
                tg_send(msg)
                # Update last alert time
                result_entry = {
                    "status": "QUEUE",
                    "last_alert_ts": now_ts,
                    "final_url": result["final_url"],
                    "reason": result["reason"],
                }
                state[key] = result_entry
            else:
                # Still queue, but within cooldown window
                state[key] = {
                    "status": "QUEUE",
                    "last_alert_ts": prev_entry.get("last_alert_ts", now_ts),
                    "final_url": result["final_url"],
                    "reason": result["reason"],
                }

        elif result["status"] == "OK":
            state[key] = {
                "status": "OK",
                "last_alert_ts": prev_entry.get("last_alert_ts", 0),
                "final_url": result["final_url"],
                "reason": result["reason"],
            }

        else:  # ERROR
            # Optional: you can alert on errors later, but for now just record it
            state[key] = {
                "status": "ERROR",
                "last_alert_ts": prev_entry.get("last_alert_ts", 0),
                "final_url": result["final_url"],
                "reason": result["reason"],
            }

    save_state(state)


if __name__ == "__main__":
    main()