"""Central configuration. Every value can be overridden with an environment variable
(or a `.env` file placed in the `backend/` directory)."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv is optional
    pass

BASE_DIR = Path(__file__).resolve().parent.parent          # backend/
FRONTEND_DIR = BASE_DIR.parent / "frontend"

# Where finished downloads are kept until they expire. Only sub-folders created by
# this app (32-character hex names) are ever deleted.
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", Path(tempfile.gettempdir()) / "clipdock"))
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_CONCURRENT_DOWNLOADS = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "3"))
MAX_QUEUED_JOBS = int(os.getenv("MAX_QUEUED_JOBS", "20"))
MAX_DURATION_SECONDS = int(os.getenv("MAX_DURATION_SECONDS", str(2 * 60 * 60)))
JOB_TTL_SECONDS = int(os.getenv("JOB_TTL_SECONDS", str(30 * 60)))
SEARCH_LIMIT = int(os.getenv("SEARCH_LIMIT", "12"))

# Optional: a Netscape-format cookies.txt. Helps with age-gated / logged-in-only
# content, Bilibili's higher resolutions, and Rednote.
COOKIES_FILE = os.getenv("COOKIES_FILE") or None
PROXY = os.getenv("PROXY") or None

# Only needed if you serve the frontend from a different origin during development.
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]

# Videos shown on the home page before the user searches. Comma-separated keyword
# searches (run on YouTube and Bilibili). Change these to whatever you like to watch.
FEED_QUERIES = [q.strip() for q in os.getenv(
    "FEED_QUERIES", "Myanmar music,Myanmar movie recap,AI tutorial"
).split(",") if q.strip()]
FEED_PER_QUERY = int(os.getenv("FEED_PER_QUERY", "6"))
FEED_TTL_SECONDS = int(os.getenv("FEED_TTL_SECONDS", "900"))

# ---- v2: paging, pinned sources ------------------------------------------------------
DATA_DIR = BASE_DIR / "data"                       # pinned links are stored here
DATA_DIR.mkdir(parents=True, exist_ok=True)
PAGE_SIZE = int(os.getenv("PAGE_SIZE", "20"))      # results per page for searches / pasted profiles
SOURCE_PAGE_SIZE = int(os.getenv("SOURCE_PAGE_SIZE", "12"))   # videos per pinned source per page
# Extra links always shown in the home feed (videos, TikTok profiles/hashtags, Rednote notes...).
FEED_SOURCES = [u.strip() for u in os.getenv("FEED_SOURCES", "").split(",") if u.strip()]
