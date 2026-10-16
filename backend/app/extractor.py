"""Metadata extraction and search, built on yt-dlp (no downloading happens here)."""
from __future__ import annotations

import hashlib
import html
import json
import re
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from itertools import zip_longest
from typing import Any

import yt_dlp

from . import config, sources
from .platforms import detect_platform
from .ytdl import base_opts, clean_error

# yt-dlp has native keyword search for these two. TikTok and Rednote have none.
SEARCH_PREFIXES = {"youtube": "ytsearch", "bilibili": "bilisearch"}
NO_SEARCH_NOTICE = (
    "TikTok and Rednote can't be searched by keyword. Paste a link, then choose Add to home to keep it in your feed."
)

# ----------------------------------------------------------------------------- cache
_CACHE_TTL = 600
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_cache_lock = threading.Lock()


def _cache_get(key: str) -> dict[str, Any] | None:
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < _CACHE_TTL:
            return hit[1]
        _cache.pop(key, None)
    return None


def _cache_put(key: str, value: dict[str, Any]) -> None:
    with _cache_lock:
        if len(_cache) > 500:
            _cache.clear()
        _cache[key] = (time.time(), value)


# ------------------------------------------------------------------------- helpers
def _https(url: str | None) -> str | None:
    if not url:
        return None
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://"):
        return "https://" + url[len("http://"):]
    return url


def _best_thumbnail(entry: dict[str, Any]) -> str | None:
    if entry.get("thumbnail"):
        return entry["thumbnail"]
    thumbs = [t for t in entry.get("thumbnails") or [] if t.get("url")]
    return thumbs[-1]["url"] if thumbs else None


def _card(entry: dict[str, Any], fallback_platform: str | None = None) -> dict[str, Any] | None:
    """Normalise a yt-dlp entry into the card shape the frontend renders."""
    url = entry.get("webpage_url") or entry.get("original_url") or entry.get("url")
    if not url or not str(url).startswith("http"):
        ie_key = (entry.get("ie_key") or "").lower()
        if entry.get("id") and "youtube" in ie_key:
            url = f"https://www.youtube.com/watch?v={entry['id']}"
        else:
            return None
    platform = detect_platform(url) or fallback_platform
    if not platform:
        return None
    thumbnail = _https(_best_thumbnail(entry))
    return {
        "id": str(entry.get("id") or url),
        "url": url,
        "title": entry.get("title") or "Untitled",
        "thumbnail": thumbnail,
        "duration": entry.get("duration"),
        "author": entry.get("uploader") or entry.get("channel") or entry.get("uploader_id"),
        "views": entry.get("view_count"),
        "platform": platform,
        "source": None,
        "_incomplete": not entry.get("title") or not thumbnail,
    }


def _qualities(info: dict[str, Any]) -> list[int]:
    heights = {
        int(f["height"])
        for f in info.get("formats") or []
        if f.get("height") and f.get("vcodec") != "none"
    }
    return sorted(heights, reverse=True)


def _remember(requested_url: str, info: dict[str, Any], fallback_platform: str | None) -> dict[str, Any]:
    card = _card(info, fallback_platform)
    if not card:
        raise ValueError("Couldn't read this video's details.")
    data = {"card": card, "qualities": _qualities(info)}
    _cache_put(requested_url, data)
    _cache_put(card["url"], data)
    return data


# --------------------------------------------------------------- Bilibili search (API)
# yt-dlp's `bilisearch` is frequently answered with HTTP 412 (Bilibili's anti-bot check)
# when no cookies are sent. The site's own search API works if we first obtain the
# anonymous device cookies (buvid3/buvid4) and sign the request the way the web client does.
_BILI_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_WBI_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49, 33, 9, 42, 19,
    29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]
_bili: dict[str, Any] = {"at": 0.0, "cookie": "", "mixin": ""}
_bili_lock = threading.Lock()


def _bili_get_json(url: str, cookie: str = "") -> dict[str, Any]:
    headers = {"User-Agent": _BILI_UA, "Referer": "https://www.bilibili.com/", "Accept": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    handlers = [urllib.request.ProxyHandler({"http": config.PROXY, "https": config.PROXY})] if config.PROXY else []
    opener = urllib.request.build_opener(*handlers)
    with opener.open(urllib.request.Request(url, headers=headers), timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _wbi_mixin_key(img_key: str, sub_key: str) -> str:
    raw = img_key + sub_key
    return "".join(raw[i] for i in _WBI_TAB)[:32]


def _wbi_sign(params: dict[str, Any], mixin_key: str, wts: int | None = None) -> dict[str, Any]:
    signed = dict(params)
    signed["wts"] = int(time.time()) if wts is None else wts
    cleaned = {k: re.sub(r"[!'()*]", "", str(v)) for k, v in sorted(signed.items())}
    query = urllib.parse.urlencode(cleaned, quote_via=urllib.parse.quote)
    cleaned["w_rid"] = hashlib.md5((query + mixin_key).encode()).hexdigest()
    return cleaned


def _bili_session() -> tuple[str, str]:
    with _bili_lock:
        if _bili["cookie"] and time.time() - _bili["at"] < 3600:
            return _bili["cookie"], _bili["mixin"]
    spi = _bili_get_json("https://api.bilibili.com/x/frontend/finger/spi")["data"]
    cookie = f"buvid3={spi['b_3']}; buvid4={spi['b_4']}"
    # `nav` reports "not logged in" but still returns the signing keys we need.
    wbi = _bili_get_json("https://api.bilibili.com/x/web-interface/nav", cookie)["data"]["wbi_img"]
    key = lambda u: u.rsplit("/", 1)[-1].split(".")[0]  # noqa: E731
    mixin = _wbi_mixin_key(key(wbi["img_url"]), key(wbi["sub_url"]))
    with _bili_lock:
        _bili.update(at=time.time(), cookie=cookie, mixin=mixin)
    return cookie, mixin


def _to_seconds(text: Any) -> int | None:
    try:
        parts = [int(p) for p in str(text).split(":")]
    except ValueError:
        return None
    total = 0
    for part in parts:
        total = total * 60 + part
    return total


def _bili_card(v: dict[str, Any]) -> dict[str, Any] | None:
    bvid = v.get("bvid")
    url = f"https://www.bilibili.com/video/{bvid}" if bvid else _https(v.get("arcurl"))
    if not url:
        return None
    title = html.unescape(re.sub(r"<[^>]+>", "", v.get("title") or "")).strip() or "Untitled"
    play = v.get("play")
    return {
        "id": str(bvid or v.get("aid") or url),
        "url": url,
        "title": title,
        "thumbnail": _https(v.get("pic")),
        "duration": _to_seconds(v.get("duration")),
        "author": v.get("author"),
        "views": play if isinstance(play, int) else None,
        "platform": "bilibili",
        "source": None,
    }


def _bilibili_api_search(query: str, page: int, size: int) -> list[dict[str, Any]]:
    cookie, mixin = _bili_session()
    params = _wbi_sign({"search_type": "video", "keyword": query, "page": page, "page_size": size}, mixin)
    url = "https://api.bilibili.com/x/web-interface/wbi/search/type?" + urllib.parse.urlencode(
        params, quote_via=urllib.parse.quote
    )
    data = _bili_get_json(url, cookie)
    if data.get("code") != 0:
        with _bili_lock:  # force a fresh session next time
            _bili["at"] = 0.0
        raise RuntimeError(f"Bilibili API returned code {data.get('code')}")
    cards = [c for v in (data.get("data") or {}).get("result") or [] if (c := _bili_card(v))]
    return cards[:size]


def _friendly_error(platform: str, exc: BaseException) -> str:
    msg = clean_error(exc)
    if platform == "bilibili" and any(code in msg for code in ("412", "352", "403")):
        return (
            "Bilibili is blocking keyword search from this connection. "
            "Paste a Bilibili video link instead, or set COOKIES_FILE (see README)."
        )
    return f"{platform.capitalize()} search failed: {msg}"


# ---------------------------------------------------------------------------- public
def get_video(url: str) -> dict[str, Any]:
    """Full metadata + available resolutions for one video (cached for 10 minutes)."""
    cached = _cache_get(url)
    if cached:
        return cached
    with yt_dlp.YoutubeDL(base_opts(skip_download=True)) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info or info.get("_type") == "playlist":
        raise ValueError("That link points to a playlist or profile, not a single video.")
    return _remember(url, info, detect_platform(url))


def from_url(url: str, platform: str, page: int = 1, size: int = 20) -> tuple[list[dict[str, Any]], bool]:
    """Resolve a pasted/pinned link. Single video -> one card; profile/playlist/hashtag -> one
    page of videos. Returns (cards, has_more)."""
    start = (page - 1) * size + 1
    opts = base_opts(extract_flat="in_playlist", playlist_items=f"{start}-{start + size - 1}")
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        return [], False

    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        cards = [c for e in entries if (c := _card(e, platform))]
        _fill_missing(cards)
        return _finalize(cards), len(entries) >= size

    if page > 1:  # a single video has no second page
        return [], False
    data = _remember(url, info, platform)
    return _finalize([data["card"]]), False


def _search_one(platform: str, query: str, page: int, size: int) -> list[dict[str, Any]]:
    if platform == "bilibili":
        try:
            cards = _bilibili_api_search(query, page, size)
            if cards:
                return cards
        except Exception:
            pass  # fall back to yt-dlp below
    # yt-dlp can only ask for "the first N", so request page*size and keep the last page.
    opts = base_opts(extract_flat=True, noplaylist=False, playlistend=page * size)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"{SEARCH_PREFIXES[platform]}{page * size}:{query}", download=False)
    entries = [e for e in ((info or {}).get("entries") or []) if e][(page - 1) * size:]
    return [c for e in entries if (c := _card(e, platform))]


def keyword_search(
    query: str, page: int = 1, size: int = 20, platform: str = "all", shorts: bool = False
) -> tuple[list[dict[str, Any]], list[str], bool]:
    """Search YouTube and Bilibili in parallel. Returns (cards, notices, has_more)."""
    if platform in ("tiktok", "rednote"):
        return [], [NO_SEARCH_NOTICE], False

    targets = [p for p in SEARCH_PREFIXES if platform in ("all", p)]
    if shorts:  # only YouTube has a dependable shorts tag
        targets = [p for p in targets if p == "youtube"]
    notices = [NO_SEARCH_NOTICE] if platform == "all" else []
    per_platform: dict[str, list[dict[str, Any]]] = {}

    def run(p: str) -> list[dict[str, Any]]:
        return _search_one(p, f"{query} #shorts" if shorts else query, page, size)

    with ThreadPoolExecutor(max_workers=max(1, len(targets))) as pool:
        futures = {pool.submit(run, p): p for p in targets}
        for future, p in futures.items():
            try:
                per_platform[p] = future.result()
            except Exception as exc:  # one platform failing shouldn't sink the other
                per_platform[p] = []
                notices.append(_friendly_error(p, exc))

    # Interleave so "All" mixes platforms instead of listing one after the other.
    merged = [c for group in zip_longest(*per_platform.values()) for c in group if c]
    has_more = any(per_platform.values())
    _fill_missing(merged)
    return _finalize(merged), notices, has_more


# ---------------------------------------------------------------------------- feed
_feed_cache: dict[tuple, tuple[float, tuple]] = {}
_feed_lock = threading.Lock()


def clear_feed_cache() -> None:
    with _feed_lock:
        _feed_cache.clear()


def feed(page: int = 1, kind: str = "all", platform: str = "all") -> tuple[list[dict[str, Any]], list[str], bool]:
    """Home-page videos: pinned/configured sources (this is how TikTok and Rednote get in)
    mixed with keyword searches from config.FEED_QUERIES. Paged, so the feed never runs out."""
    key = (page, kind, platform)
    with _feed_lock:
        hit = _feed_cache.get(key)
        if hit and time.time() - hit[0] < config.FEED_TTL_SECONDS:
            return hit[1]

    shorts = kind == "shorts"
    notices: list[str] = []
    groups: list[list[dict[str, Any]]] = []
    has_more = False

    with ThreadPoolExecutor(max_workers=8) as pool:
        src_jobs = []
        for src in sources.all_sources():
            sp = detect_platform(src)
            if platform in ("all", sp):
                src_jobs.append((src, pool.submit(from_url, src, sp, page, config.SOURCE_PAGE_SIZE)))
        query_jobs = []
        if platform in ("all", "youtube", "bilibili"):
            for q in config.FEED_QUERIES:
                query_jobs.append(pool.submit(keyword_search, q, page, config.FEED_PER_QUERY, platform, shorts))

        for src, fut in src_jobs:
            try:
                cards, more = fut.result()
            except Exception as exc:
                notices.append(f"Couldn't load {src}: {clean_error(exc)}")
                continue
            for c in cards:
                c["source"] = src
            if shorts:
                cards = [c for c in cards if c.get("short")]
            groups.append(cards)
            has_more = has_more or more
        for fut in query_jobs:
            try:
                cards, n, more = fut.result()
            except Exception as exc:
                notices.append(clean_error(exc))
                continue
            groups.append(cards)
            has_more = has_more or more
            notices.extend(x for x in n if x not in notices)

    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for row in zip_longest(*groups):
        for card in row:
            if card and card["url"] not in seen:
                seen.add(card["url"])
                merged.append(card)

    result = (merged, notices, has_more)
    if merged:  # never cache an empty / failed page
        with _feed_lock:
            _feed_cache[key] = (time.time(), result)
    return result


# -------------------------------------------------------------------------- streaming
_stream_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_stream_lock = threading.Lock()


def get_stream(url: str) -> dict[str, Any]:
    """Resolve a direct, single-file media URL (plus the headers its CDN expects) so the
    browser can play the video inline. Cached briefly because these URLs expire."""
    with _stream_lock:
        hit = _stream_cache.get(url)
        if hit and time.time() - hit[0] < 300:
            return hit[1]

    opts = base_opts(skip_download=True, format="best[acodec!=none][vcodec!=none]/best")
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if not info or info.get("_type") == "playlist":
            raise ValueError("That link points to a playlist or profile, not a single video.")
        if info.get("requested_formats"):
            raise ValueError("This video is only offered as separate streams. Use Download instead.")
        media = info.get("url")
        if not media or str(info.get("protocol", "")).startswith("m3u8"):
            raise ValueError("This video can't be played inline. Use Download instead.")
        headers = dict(info.get("http_headers") or {})
        try:
            cookie = ydl.cookiejar.get_cookie_header(media)
            if cookie:
                headers["Cookie"] = cookie
        except Exception:
            pass

    data = {"url": media, "headers": headers}
    with _stream_lock:
        if len(_stream_cache) > 200:
            _stream_cache.clear()
        _stream_cache[url] = (time.time(), data)
    return data


# ---------------------------------------------------------------------------- utils
def _fill_missing(cards: list[dict[str, Any]]) -> None:
    """Flat results from some platforms (notably Bilibili) lack titles/thumbnails.
    Fetch full metadata for just those entries, in parallel."""
    incomplete = [c for c in cards if c.get("_incomplete")]
    if not incomplete:
        return

    def enrich(card: dict[str, Any]) -> None:
        try:
            full = get_video(card["url"])["card"]
            for key in ("title", "thumbnail", "duration", "author", "views"):
                if full.get(key):
                    card[key] = full[key]
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(enrich, incomplete))


def _is_short(card: dict[str, Any]) -> bool:
    """Short-form = YouTube Shorts, anything on TikTok, and brief clips elsewhere."""
    platform, duration = card.get("platform"), card.get("duration")
    if platform == "tiktok":
        return True
    if platform == "youtube":
        return "/shorts/" in card["url"] or (duration is not None and duration <= 60)
    if platform == "rednote":
        return duration is None or duration <= 180
    return duration is not None and duration <= 60


def _finalize(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for card in cards:
        card.pop("_incomplete", None)
        card["short"] = _is_short(card)
    return cards
