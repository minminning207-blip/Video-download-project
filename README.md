# M2 AI

A black, responsive web app for watching and saving videos from YouTube, TikTok, Bilibili and Rednote
(Xiaohongshu). FastAPI + yt-dlp on the back end; Tailwind CSS and vanilla JavaScript on the front end.

## Project structure

```
video-downloader/
├── README.md
├── backend/
│   ├── requirements.txt
│   ├── .env.example
│   ├── data/sources.json     # created automatically: links you pinned to Home
│   └── app/
│       ├── main.py           # routes (feed, search, info, stream, sources, downloads)
│       ├── config.py         # env-driven settings
│       ├── platforms.py      # link extraction, platform detection, host allow-list
│       ├── extractor.py      # yt-dlp metadata, paged search, feed, stream resolving
│       ├── sources.py        # pinned links (how TikTok / Rednote videos reach Home)
│       ├── downloader.py     # background download jobs, progress, cleanup
│       ├── schemas.py, ytdl.py
└── frontend/
    ├── index.html            # layout, Tailwind config, player + download modal markup
    └── app.js
```

## Features

- **Home feed** that fills the page as soon as it opens, and keeps loading more as you scroll (no cap).
- **Platform tabs**: All, TikTok, YouTube, Rednote, Bilibili.
- **Shorts toggle**: switches the grid to portrait cards and fetches short-form videos.
- **Player with autoplay**: click any video. Use the arrow buttons, arrow keys, mouse wheel or a swipe to move to the next video. "Autoplay next" moves on when a video ends.
- **Download**: choose Video (MP4) with a resolution, or Audio only (MP3) with a bitrate.

## Setup

**Prerequisites:** Python 3.10 or newer and **ffmpeg** (for MP3 and for merging video + audio).

```bat
winget install ffmpeg
```
(macOS: `brew install ffmpeg`, Linux: `sudo apt install ffmpeg`)

**Windows (Command Prompt)**
```bat
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

**macOS / Linux**
```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Open **http://localhost:8000**. After updating files, press **Ctrl+F5** so the browser reloads the new `app.js`.
Interactive API docs are at `/docs`; `/api/health` should report `"ffmpeg": true`.

## Getting TikTok and Rednote videos onto Home

yt-dlp cannot search TikTok or Rednote by keyword, so those videos come from **links you add**:

1. Paste a TikTok video, TikTok profile (`https://www.tiktok.com/@name`) or Rednote note link and press Find videos.
2. Press **Add to home**. The link is saved in `backend/data/sources.json` and its videos appear in the feed, mixed in with YouTube and Bilibili.
3. Use the minus button on a card to remove that source again.

A TikTok profile adds all of its videos (loaded page by page). A Rednote link adds that single note, because yt-dlp can't list a Rednote profile. You can also set `FEED_SOURCES` in `.env`.

## Configuration (`backend/.env`, all optional)

| Variable | Purpose | Default |
|---|---|---|
| `FEED_QUERIES` | Comma-separated topics for the YouTube/Bilibili part of Home | `Myanmar music,Myanmar movie recap,AI tutorial` |
| `FEED_SOURCES` | Extra links always shown on Home | none |
| `COOKIES_FILE` | Netscape `cookies.txt` from a logged-in browser | none |
| `PROXY` | HTTP/SOCKS proxy for yt-dlp | none |
| `MAX_DURATION_SECONDS` | Reject longer downloads | 7200 |
| `JOB_TTL_SECONDS` | How long finished files stay available | 1800 |

## Things to know

- **Autoplay.** YouTube and Bilibili play in their official embedded players. TikTok and Rednote are streamed through this server (their CDNs block hot-linking). Browsers may start videos muted if you haven't interacted with the page yet. "Autoplay next" works for YouTube and TikTok/Rednote; Bilibili's embed doesn't report when it ends, so use the next button there.
- **Embeds can be blocked by the uploader.** If a YouTube or Bilibili video won't play, use Open original or Download.
- **Rednote and TikTok** are the least stable yt-dlp extractors and often need `COOKIES_FILE`. Image-only Rednote notes have no video.
- **Bilibili** blocks anonymous requests with HTTP 412 at times; the app signs its own search request and falls back to yt-dlp. 1080p and above needs cookies.
- **yt-dlp breaks regularly** when platforms change. Run `pip install -U yt-dlp` first when a site stops working.
- Only download content you own or have permission to save, and respect each platform's terms of service.

## Before putting this on the internet

This is built for local or private use. A public deployment needs authentication or rate limiting, HTTPS, and a compiled Tailwind stylesheet instead of the Play CDN. The `/api/stream` proxy in particular should not be exposed publicly without access control.
