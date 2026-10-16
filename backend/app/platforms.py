"""Link parsing, platform detection and URL allow-listing.

Only hosts belonging to the four supported platforms are ever passed to yt-dlp.
That keeps the server from being used as an open proxy / SSRF gadget.
"""
from __future__ import annotations

import re
import urllib.request
from urllib.parse import urlparse

PLATFORM_HOSTS: dict[str, tuple[str, ...]] = {
    "youtube": ("youtube.com", "youtu.be", "youtube-nocookie.com"),
    "tiktok": ("tiktok.com",),
    "bilibili": ("bilibili.com", "b23.tv"),
    "rednote": ("xiaohongshu.com", "xhslink.com"),
}

# Short-link hosts that yt-dlp does not always follow itself.
SHORT_LINK_HOSTS = ("xhslink.com",)

# ASCII-only on purpose: Rednote/TikTok "share text" often glues Chinese punctuation
# or words straight after the link.
_URL_RE = re.compile(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&()*+,;=%]+", re.I)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class UnsupportedURL(ValueError):
    """Raised when a link is malformed or not from a supported platform."""


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def detect_platform(url: str) -> str | None:
    host = (urlparse(url).hostname or "").lower()
    for platform, domains in PLATFORM_HOSTS.items():
        if any(_host_matches(host, d) for d in domains):
            return platform
    return None


def extract_url(text: str) -> str | None:
    """Pull a URL out of whatever the user pasted (plain link or full share text)."""
    text = text.strip()
    match = _URL_RE.search(text)
    if match:
        return match.group(0).rstrip(".,;:!?)")
    # "youtube.com/watch?v=..." typed without a scheme
    if text and not re.search(r"\s", text):
        lowered = text.lower()
        if any(d in lowered for domains in PLATFORM_HOSTS.values() for d in domains):
            return "https://" + text
    return None


def _validate(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise UnsupportedURL("That doesn't look like a valid link.")
    platform = detect_platform(url)
    if not platform:
        raise UnsupportedURL(
            "Unsupported site. Use a link from YouTube, TikTok, Bilibili or Rednote."
        )
    return platform


def _resolve_redirects(url: str, timeout: float = 8.0) -> str:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.geturl()
    except Exception:
        return url


def normalize_url(text: str) -> tuple[str, str]:
    """Return (clean_url, platform) or raise UnsupportedURL."""
    url = extract_url(text)
    if not url:
        raise UnsupportedURL("No link found.")
    platform = _validate(url)

    host = (urlparse(url).hostname or "").lower()
    if any(_host_matches(host, h) for h in SHORT_LINK_HOSTS):
        url = _resolve_redirects(url)
        platform = _validate(url)  # re-check: the redirect target must be allow-listed too
    return url, platform
