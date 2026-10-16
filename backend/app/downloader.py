"""Background download jobs.

Flow: POST /api/downloads creates a Job and returns its id immediately. A worker thread
runs yt-dlp (updating `progress`), the browser polls the job, and when it is `done` the
browser fetches the file with a normal GET so the native download manager takes over.
Finished files are deleted after JOB_TTL_SECONDS.
"""
from __future__ import annotations

import re
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yt_dlp

from . import config
from .ytdl import base_opts, clean_error

HAS_FFMPEG = shutil.which("ffmpeg") is not None
ACTIVE = ("queued", "downloading", "processing")


class Busy(RuntimeError):
    """Too many jobs are already queued."""


class FFmpegMissing(RuntimeError):
    """MP3 conversion requires ffmpeg."""


@dataclass
class Job:
    id: str
    url: str
    kind: str       # "video" | "audio"
    quality: str    # video: "best" or a height like "720"; audio: kbps like "192"
    dir: Path
    status: str = "queued"   # queued | downloading | processing | done | error
    progress: float = 0.0
    title: str | None = None
    filename: str | None = None
    filepath: Path | None = None
    error: str | None = None
    created: float = field(default_factory=time.time)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "progress": round(self.progress, 1),
            "title": self.title,
            "filename": self.filename,
            "error": self.error,
        }


JOBS: dict[str, Job] = {}
_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=config.MAX_CONCURRENT_DOWNLOADS, thread_name_prefix="dl")


# --------------------------------------------------------------------------- public
def create_job(url: str, kind: str, quality: str) -> Job:
    if kind == "audio" and not HAS_FFMPEG:
        raise FFmpegMissing("MP3 conversion needs ffmpeg, which isn't installed on the server.")

    with _lock:
        if sum(1 for j in JOBS.values() if j.status in ACTIVE) >= config.MAX_QUEUED_JOBS:
            raise Busy("The server is busy. Try again in a minute.")
        job_id = uuid.uuid4().hex
        job_dir = config.DOWNLOAD_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        job = Job(id=job_id, url=url, kind=kind, quality=quality, dir=job_dir)
        JOBS[job_id] = job

    _executor.submit(_run, job)
    return job


def get_job(job_id: str) -> Job | None:
    with _lock:
        return JOBS.get(job_id)


def start_cleanup_thread(stop: threading.Event) -> threading.Thread:
    _purge_orphans()

    def loop() -> None:
        while not stop.wait(60):
            cutoff = time.time() - config.JOB_TTL_SECONDS
            with _lock:
                expired = [j for j in JOBS.values() if j.created < cutoff and j.status not in ACTIVE]
                for job in expired:
                    JOBS.pop(job.id, None)
            for job in expired:
                shutil.rmtree(job.dir, ignore_errors=True)

    thread = threading.Thread(target=loop, name="cleanup", daemon=True)
    thread.start()
    return thread


# -------------------------------------------------------------------------- internals
def _purge_orphans() -> None:
    """Remove leftovers from a previous run. Only touches folders this app created."""
    for path in config.DOWNLOAD_DIR.iterdir():
        if path.is_dir() and re.fullmatch(r"[0-9a-f]{32}", path.name):
            shutil.rmtree(path, ignore_errors=True)


def _format_selector(kind: str, quality: str) -> dict[str, Any]:
    if kind == "audio":
        return {
            "format": "ba/b",
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": quality}
            ],
        }

    if not HAS_FFMPEG:
        # Can't merge separate video+audio streams: take a single pre-muxed file.
        if quality == "best":
            return {"format": "b"}
        return {"format": f"b[height<={int(quality)}]/b"}

    if quality == "best":
        fmt = "bv*+ba/b"
    else:
        h = int(quality)
        fmt = f"bv*[height<={h}]+ba/b[height<={h}]/bv*+ba/b"
    return {
        "format": fmt,
        "merge_output_format": "mp4",
        # Within the height cap prefer H.264 + AAC so the MP4 plays everywhere.
        "format_sort": ["res", "vcodec:h264", "acodec:aac"],
    }


def _hooks(job: Job):
    seen: dict[str, tuple[int, int]] = {}

    def on_progress(d: dict[str, Any]) -> None:
        if d.get("status") != "downloading":
            return
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        seen[d.get("filename") or "?"] = (d.get("downloaded_bytes") or 0, total)
        done = sum(v[0] for v in seen.values())
        overall = sum(v[1] for v in seen.values())
        if overall:
            # Video and audio arrive as separate files, so keep the bar monotonic
            # and leave headroom for merging / conversion.
            job.progress = max(job.progress, min(90.0, done / overall * 100))

    def on_postprocess(d: dict[str, Any]) -> None:
        if d.get("status") == "started":
            job.status = "processing"
            job.progress = max(job.progress, 92.0)

    return on_progress, on_postprocess


def _locate_output(job: Job) -> Path:
    wanted = ".mp3" if job.kind == "audio" else ".mp4"
    files = [
        p for p in job.dir.iterdir()
        if p.is_file() and p.suffix.lower() not in (".part", ".ytdl", ".temp")
    ]
    candidates = [p for p in files if p.suffix.lower() == wanted] or files
    if not candidates:
        raise FileNotFoundError("The download finished but no file was produced.")
    return max(candidates, key=lambda p: p.stat().st_size)


def _run(job: Job) -> None:
    job.status = "downloading"
    try:
        on_progress, on_postprocess = _hooks(job)
        opts = base_opts(
            outtmpl=str(job.dir / "%(title).120B.%(ext)s"),
            windowsfilenames=True,
            concurrent_fragment_downloads=4,
            progress_hooks=[on_progress],
            postprocessor_hooks=[on_postprocess],
            **_format_selector(job.kind, job.quality),
        )
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(job.url, download=False)
            if not info or info.get("_type") == "playlist":
                raise ValueError("That link points to a playlist or profile, not a single video.")
            duration = info.get("duration")
            if duration and duration > config.MAX_DURATION_SECONDS:
                raise ValueError(
                    f"This video is longer than the {config.MAX_DURATION_SECONDS // 60}-minute limit."
                )
            job.title = info.get("title")
            ydl.process_ie_result(info, download=True)  # reuse the metadata, no second lookup

        job.filepath = _locate_output(job)
        job.filename = job.filepath.name
        job.progress = 100.0
        job.status = "done"
    except Exception as exc:  # noqa: BLE001 - surface any failure to the user
        job.status = "error"
        job.error = clean_error(exc)
        shutil.rmtree(job.dir, ignore_errors=True)
