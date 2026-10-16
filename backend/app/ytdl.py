"""Small helpers shared by the extractor and the downloader."""
from __future__ import annotations

import re
from typing import Any

from . import config


def base_opts(**extra: Any) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 15,
        "retries": 2,
        "extractor_retries": 2,
    }
    if config.COOKIES_FILE:
        opts["cookiefile"] = config.COOKIES_FILE
    if config.PROXY:
        opts["proxy"] = config.PROXY
    opts.update(extra)
    return opts


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def clean_error(exc: BaseException) -> str:
    """Turn a yt-dlp exception into a short message that is safe to show to users."""
    msg = _ANSI.sub("", str(exc))
    msg = re.sub(r"^ERROR:\s*", "", msg.strip())
    return msg[:300] or "Something went wrong."
