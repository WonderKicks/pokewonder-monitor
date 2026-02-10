import os
import json
import hashlib
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

URLS = {
    "PCUK Home": "https://www.pokemoncenter.com/en-gb",
    # Optional:
    # "PCUK TCG": "https://www.pokemoncenter.com/en-gb/category/trading-card-game",
}

QUEUE_FINGERPRINTS = [
    "queue-it",
    "virtual queue",
    "virtual waiting room",
    "you are in line",
    "you are now in line",
    "please wait",
    "do not refresh",
    "queue number",
    "position",
]

UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"

def tg_send(text: str):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": text},
        timeout=20
    )

def stable_hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()[:12]

def host(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""

def looks_like_queue_redirect(final_url: str) -> bool:
    h = host(final_url)
    if "queue-it" in h:
        return True
    if h and "pokemoncenter.com" not in h and ("queue" in final_url.lower() or "waiting" in final_url.lower()):
        return True
    return False

def html_has_queue_fingerprint(text: str) -> bool:
    t = text.lower()
    return any(fp in t for fp in QUEUE_FINGERPRINTS)

def load_state():
    try:
        with open("state.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_state(state):
    with open("state.json", "w", encoding="utf-8") as f:
        json.dump(state, f)

def check_url(name: str, url: str):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=25, allow_redirects=True)
    final = r.url

    if looks_like_queue_redirect(final):
        return ("QUEUE", f"🚨 PCUK QUEUE DETECTED (redirect)\n{name}\nFrom: {url}\nTo:   {final}")

    soup = BeautifulSoup(r.text, "html.parser")
    text = soup.get_text(" ", strip=True)

    if html_has_queue_fingerprint(text):
        return ("QUEUE", f"🚨 PCUK QUEUE DETECTED (HTML)\n{name}\n{final}")

    return ("OK", None)

def main():
    state = load_state()

    for name, url in URLS.items():
        status, alert_msg = check_url(name, url)
        key = stable_hash(name + "|" + url)

        if status == "QUEUE" and state.get(key) != "QUEUE":
            tg_send(alert_msg)

        state[key] = status

    save_state(state)

if __name__ == "__main__":
    main()
