"""FastAPI entrypoint. Serves the JSON API under /api and the static frontend at /."""
from __future__ import annotations

import threading
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from typing import Literal

import yt_dlp
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from yt_dlp.utils import DownloadError, ExtractorError

from . import config, downloader, extractor, sources
from .platforms import UnsupportedURL, extract_url, normalize_url
from .schemas import DownloadRequest
from .ytdl import clean_error

YTDLP_ERRORS = (DownloadError, ExtractorError, ValueError)
Kind = Literal["all", "shorts"]
PlatformFilter = Literal["all", "youtube", "tiktok", "rednote", "bilibili"]
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    stop = threading.Event()
    downloader.start_cleanup_thread(stop)
    yield
    stop.set()


app = FastAPI(title="M2 AI", version="2.0.0", lifespan=lifespan)

if config.CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.CORS_ORIGINS,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )


# NOTE: plain `def` handlers on purpose. yt-dlp is blocking, so FastAPI runs them in its
# worker thread pool instead of stalling the event loop.
@app.get("/api/health")
def health():
    return {"ok": True, "ffmpeg": downloader.HAS_FFMPEG, "yt_dlp": yt_dlp.version.__version__}


@app.get("/api/feed")
def home_feed(
    page: int = Query(1, ge=1, le=500),
    kind: Kind = "all",
    platform: PlatformFilter = "all",
):
    """Videos for the home page, page by page (the frontend keeps asking for more)."""
    try:
        results, notices, has_more = extractor.feed(page, kind, platform)
    except YTDLP_ERRORS as exc:
        raise HTTPException(422, clean_error(exc)) from exc
    return {"mode": "feed", "results": results, "notices": notices, "has_more": has_more, "page": page}


@app.get("/api/search")
def search(
    q: str = Query(..., min_length=1, max_length=500),
    page: int = Query(1, ge=1, le=500),
    size: int = Query(config.PAGE_SIZE, ge=1, le=50),
    kind: Kind = "all",
    platform: PlatformFilter = "all",
):
    """Paste a link -> that video (or a profile/playlist's videos). Otherwise keyword search."""
    text = q.strip()
    try:
        if extract_url(text):
            url, detected = normalize_url(text)
            results, has_more = extractor.from_url(url, detected, page, size)
            return {"mode": "url", "results": results, "notices": [], "has_more": has_more, "page": page}
        results, notices, has_more = extractor.keyword_search(text, page, size, platform, kind == "shorts")
        return {"mode": "search", "results": results, "notices": notices, "has_more": has_more, "page": page}
    except UnsupportedURL as exc:
        raise HTTPException(400, str(exc)) from exc
    except YTDLP_ERRORS as exc:
        raise HTTPException(422, clean_error(exc)) from exc


@app.get("/api/info")
def info(url: str = Query(..., max_length=2000)):
    """Metadata and available resolutions for the download options modal."""
    try:
        clean_url, _ = normalize_url(url)
        data = extractor.get_video(clean_url)
    except UnsupportedURL as exc:
        raise HTTPException(400, str(exc)) from exc
    except YTDLP_ERRORS as exc:
        raise HTTPException(422, clean_error(exc)) from exc
    card = {k: v for k, v in data["card"].items() if not k.startswith("_")}
    return {**card, "qualities": data["qualities"]}


# ------------------------------------------------------------------- inline playback
@app.get("/api/stream")
def stream(request: Request, url: str = Query(..., max_length=2000)):
    """Proxy a video's media file so the <video> tag can play it with autoplay.
    Used for TikTok and Rednote, whose CDNs reject hot-linking. Supports Range requests."""
    try:
        clean_url, _ = normalize_url(url)
        media = extractor.get_stream(clean_url)
    except UnsupportedURL as exc:
        raise HTTPException(400, str(exc)) from exc
    except YTDLP_ERRORS as exc:
        raise HTTPException(422, clean_error(exc)) from exc

    headers = dict(media["headers"])
    headers.setdefault("User-Agent", _UA)
    if request.headers.get("range"):
        headers["Range"] = request.headers["range"]
    try:
        upstream = urllib.request.urlopen(  # noqa: S310 - URL comes from yt-dlp for an allow-listed page
            urllib.request.Request(media["url"], headers=headers), timeout=20
        )
    except urllib.error.HTTPError as exc:
        raise HTTPException(416 if exc.code == 416 else 502, f"The video host answered {exc.code}.") from exc
    except Exception as exc:
        raise HTTPException(502, "Couldn't reach the video host.") from exc

    out = {
        k: upstream.headers[k]
        for k in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges")
        if upstream.headers.get(k)
    }
    out.setdefault("Accept-Ranges", "bytes")
    out["Cache-Control"] = "no-store"

    def body():
        try:
            while chunk := upstream.read(64 * 1024):
                yield chunk
        finally:
            upstream.close()

    return StreamingResponse(body(), status_code=upstream.status, headers=out)


# ---------------------------------------------------------------------- pinned links
class SourceRequest(BaseModel):
    url: str = Field(min_length=4, max_length=2000)


@app.get("/api/sources")
def list_sources():
    return {"sources": sources.all_sources()}


@app.post("/api/sources", status_code=201)
def add_source(req: SourceRequest):
    try:
        url, platform = normalize_url(req.url)
    except UnsupportedURL as exc:
        raise HTTPException(400, str(exc)) from exc
    sources.add(url)
    extractor.clear_feed_cache()
    return {"url": url, "platform": platform}


@app.delete("/api/sources")
def delete_source(url: str = Query(..., max_length=2000)):
    sources.remove(url)
    extractor.clear_feed_cache()
    return {"ok": True}


# -------------------------------------------------------------------------- downloads
@app.post("/api/downloads", status_code=202)
def start_download(req: DownloadRequest):
    try:
        url, _ = normalize_url(req.url)
    except UnsupportedURL as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        job = downloader.create_job(url, req.kind, req.quality)
    except downloader.Busy as exc:
        raise HTTPException(429, str(exc)) from exc
    except downloader.FFmpegMissing as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"job_id": job.id}


@app.get("/api/downloads/{job_id}")
def download_status(job_id: str):
    job = downloader.get_job(job_id)
    if not job:
        raise HTTPException(404, "This download has expired. Start it again.")
    return job.public()


@app.get("/api/downloads/{job_id}/file")
def download_file(job_id: str):
    job = downloader.get_job(job_id)
    if not job:
        raise HTTPException(404, "This download has expired. Start it again.")
    if job.status != "done" or not job.filepath or not job.filepath.exists():
        raise HTTPException(409, "The file isn't ready yet.")
    media_type = "audio/mpeg" if job.kind == "audio" else "video/mp4"
    return FileResponse(job.filepath, media_type=media_type, filename=job.filename)


# Mounted last so the /api routes above always win.
app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
