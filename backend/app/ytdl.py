"""Small helpers shared by the extractor and the downloader."""
from __future__ import annotations

import random
import re
from typing import Any

from . import config

# Random User-Agents List (Bot ဟု မမိစေရန် Rotating အသုံးပြုရန်)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


def base_opts(**extra: Any) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 15,
        "retries": 3,
        "extractor_retries": 3,
        # Random User-Agent ရွေးချယ်ခြင်း
        "user_agent": random.choice(USER_AGENTS),
        "http_headers": {
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Fetch-Mode": "navigate",
        },
    }
    
    # Cookies Integration
    if config.COOKIES_FILE:
        opts["cookiefile"] = config.COOKIES_FILE
    elif config.COOKIES_FROM_BROWSER:
        opts["cookiesfrombrowser"] = tuple(p for p in config.COOKIES_FROM_BROWSER.split(":", 1) if p)
        
    # Proxy Support
    if config.PROXY:
        opts["proxy"] = config.PROXY
        
    opts.update(extra)
    return opts


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def clean_error(exc: BaseException) -> str:
    """Turn a yt-dlp exception into a short message that is safe to show to users."""
    msg = _ANSI.sub("", str(exc))
    msg = re.sub(r"^ERROR:\s*", "", msg.strip())
    low = msg.lower()
    if "not a bot" in low or ("sign in" in low and "cookies" in low):
        return (
            "YouTube is asking for a sign-in (bot check). Add a cookies.txt file, set COOKIES_FILE "
            "in backend/.env, then restart the server. See the README."
        )
    return msg[:300] or "Something went wrong."