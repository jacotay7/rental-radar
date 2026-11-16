"""Shared HTTP session with polite per-host rate limiting and simple retries."""
import time

import requests

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
# Identifies the tool to services whose usage policies ask for it (OpenStreetMap Nominatim).
# Never put personal details (names, email addresses) here.
TOOL_UA = "rental-radar/0.2 (self-hosted, low-volume personal rental search)"

_session = requests.Session()
_session.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
_last_call: dict[str, float] = {}


def get(url: str, *, params=None, headers=None, min_interval: float = 1.0, timeout: int = 30, retries: int = 2):
    """GET with a per-host minimum interval between requests. Backs off on 429/503."""
    host = url.split("/")[2]
    wait = _last_call.get(host, 0) + min_interval - time.time()
    if wait > 0:
        time.sleep(wait)
    for attempt in range(retries + 1):
        try:
            resp = _session.get(url, params=params, headers=headers, timeout=timeout)
            _last_call[host] = time.time()
            if resp.status_code in (429, 503) and attempt < retries:
                time.sleep(10 * (attempt + 1))
                continue
            return resp
        except requests.RequestException:
            _last_call[host] = time.time()
            if attempt == retries:
                raise
            time.sleep(5)
