"""yt-dlp wrapper — replaces the YouTube Data API v3 videos.list call.

yt-dlp scrapes the public YouTube watch page (no API key, no quota) and
extracts the same metadata fields. ~1–3s per call vs ~0.2s for the API,
so this is for cron-paced workloads, not high-throughput.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

_VIDEO_URL_PATTERNS = [
    re.compile(r"(?:youtube\.com/watch\?v=|youtu\.be/)([A-Za-z0-9_-]+)"),
    re.compile(r"youtube\.com/embed/([A-Za-z0-9_-]+)"),
]
_BARE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

_DEFAULT_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    "extract_flat": False,
    # We only need metadata; avoid yt-dlp probing for download formats it
    # doesn't need to know about.
    "noplaylist": True,
    # Caller is the one that retries / backs off.
    "retries": 0,
}


class YtDlpError(RuntimeError):
    """Raised on unexpected yt-dlp failures (not on private/deleted videos)."""


def extract_video_id(url_or_id: str) -> str:
    """Extract a YouTube video ID from a URL or pass through a bare ID."""
    for pat in _VIDEO_URL_PATTERNS:
        m = pat.search(url_or_id)
        if m:
            return m.group(1)
    return url_or_id


def _to_video_url(url_or_id: str) -> str:
    """Always pass a fully-qualified URL to yt-dlp — bare IDs sometimes
    confuse it on macOS (it tries to interpret as a search term)."""
    if _BARE_ID_RE.match(url_or_id):
        return f"https://www.youtube.com/watch?v={url_or_id}"
    return url_or_id


def _format_upload_date(yyyymmdd: Optional[str]) -> str:
    """yt-dlp returns 'YYYYMMDD'. Convert to 'YYYY-MM-DD' so callers
    can compare lexicographically with the RSS published_at strings."""
    if not yyyymmdd or len(yyyymmdd) != 8:
        return ""
    return f"{yyyymmdd[0:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"


def is_livestream(info: Dict[str, Any]) -> bool:
    """Return True for live or scheduled-live videos.

    `is_live=True` covers active streams. `live_status='is_upcoming'`
    covers scheduled streams that yt-dlp recognizes as not yet a
    normal video.
    """
    if info.get("is_live"):
        return True
    if info.get("live_status") in {"is_upcoming", "is_live"}:
        return True
    return False


def get_video_metadata(
    url_or_id: str, ydl_opts: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """Return normalized metadata for a YouTube video, or None if missing.

    The returned shape mirrors what the legacy YouTubeClient.get_video_info
    produced, so the rest of the codebase doesn't change:
      {video_id, title, channel_name, channel_id, duration, published_at, url, is_live}

    Returns None for private/deleted/region-locked videos. Raises
    YtDlpError on truly unexpected failures (network errors, yt-dlp
    internal bugs) so the caller can decide whether to retry.
    """
    url = _to_video_url(url_or_id)
    opts = {**_DEFAULT_OPTS, **(ydl_opts or {})}
    try:
        with YoutubeDL(opts) as ydl:
            raw = ydl.extract_info(url, download=False)
    except DownloadError as e:
        logging.info("yt-dlp could not extract %s: %s", url, e)
        return None
    except YtDlpError:
        raise
    except Exception as e:  # noqa: BLE001 — wrap everything else
        raise YtDlpError(f"yt-dlp failed for {url}: {e}") from e

    if raw is None:
        return None

    duration = raw.get("duration") or 0
    if is_livestream(raw):
        duration = 0

    return {
        "video_id": raw.get("id") or extract_video_id(url),
        "title": raw.get("title") or "",
        "channel_name": raw.get("channel") or raw.get("uploader") or "",
        "channel_id": raw.get("channel_id") or "",
        "duration": int(duration) if duration is not None else 0,
        "published_at": _format_upload_date(raw.get("upload_date")),
        "url": raw.get("webpage_url") or url,
        "is_live": bool(is_livestream(raw)),
    }
