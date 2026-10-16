"""Links the user pinned to the home feed (stored in backend/data/sources.json).

TikTok and Rednote can't be searched by keyword, so this is how their videos get into the
feed: paste a video/profile link, choose "Add to home", and it is loaded with the feed.
"""
from __future__ import annotations

import json
import threading

from . import config
from .platforms import detect_platform

_PATH = config.DATA_DIR / "sources.json"
_lock = threading.Lock()


def _load() -> list[str]:
    try:
        data = json.loads(_PATH.read_text("utf-8"))
    except (OSError, ValueError):
        return []
    return [u for u in data if isinstance(u, str)]


def _save(items: list[str]) -> None:
    _PATH.write_text(json.dumps(items, ensure_ascii=False, indent=2), "utf-8")


def all_sources() -> list[str]:
    with _lock:
        merged = _load() + config.FEED_SOURCES
    seen: set[str] = set()
    out = []
    for url in merged:
        if url not in seen and detect_platform(url):
            seen.add(url)
            out.append(url)
    return out


def add(url: str) -> None:
    with _lock:
        items = _load()
        if url not in items:
            items.insert(0, url)
            _save(items)


def remove(url: str) -> None:
    with _lock:
        _save([u for u in _load() if u != url])
