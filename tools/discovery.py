"""No-key YouTube discovery: RSS for the recent-uploads list, yt-dlp for
per-video duration. Replaces the legacy YouTubeClient (3× YouTube Data
API calls) for the default `DISCOVERY_BACKEND=rss` path.

Public surface mirrors the legacy YouTubeClient so server.py just swaps
the import:
  - get_video_info(url, min_analysis_seconds) -> dict | None
  - get_channel_videos(channel_id, max_results, min_analysis_seconds) -> list[dict]
  - DiscoveryClient (instance facade with the same two methods)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from tools import rss_discovery, ytdlp_metadata


def get_video_info(
    video_url_or_id: str, min_analysis_seconds: int = 600
) -> Optional[Dict[str, Any]]:
    """Single-video metadata via yt-dlp. Returns the legacy YouTubeClient
    shape (with `excluded_from_analysis`), or None if the video is missing."""
    raw = ytdlp_metadata.get_video_metadata(video_url_or_id)
    if raw is None:
        return None
    duration = raw["duration"]
    return {
        "video_id": raw["video_id"],
        "title": raw["title"],
        "channel_name": raw["channel_name"],
        "channel_id": raw["channel_id"],
        "duration": duration,
        "published_at": raw["published_at"],
        "url": raw["url"],
        "excluded_from_analysis": duration < min_analysis_seconds,
    }


def get_channel_videos(
    channel_id: str,
    max_results: int = 5,
    min_analysis_seconds: int = 600,
) -> List[Dict[str, Any]]:
    """Recent uploads with duration. RSS gives the title+published_at+id
    list cheaply; yt-dlp fills in duration per kept entry.

    `max_results` caps both the RSS slice and the yt-dlp call count, so
    a channel with N kept videos costs N yt-dlp scrapes (1–3s each).
    """
    rss_entries = rss_discovery.get_recent_videos(channel_id, max_results=max_results)
    # Defensive cap — guard against an RSS implementation change returning
    # more than asked. Keeps yt-dlp call budget bounded by max_results.
    rss_entries = rss_entries[:max_results]
    out: List[Dict[str, Any]] = []
    for entry in rss_entries:
        meta = get_video_info(entry["url"], min_analysis_seconds=min_analysis_seconds)
        if meta is None:
            continue
        # Prefer the RSS published_at — it has a wall-clock time, while
        # yt-dlp's upload_date is date-only. Both are ISO 8601-prefix
        # compatible so lex-comparable for state.py.
        meta["published_at"] = entry["published_at"]
        # Force the channel_id from RSS — yt-dlp occasionally returns an
        # empty string for embed-only videos.
        if entry.get("channel_id"):
            meta["channel_id"] = entry["channel_id"]
        out.append(meta)
    return out


class DiscoveryClient:
    """Thin facade so server.py can hold a single `_yt = DiscoveryClient()`
    in place of the legacy `_yt = YouTubeClient(api_key)`."""

    def get_video_info(
        self, video_url_or_id: str, min_analysis_seconds: int = 600
    ) -> Optional[Dict[str, Any]]:
        return get_video_info(video_url_or_id, min_analysis_seconds=min_analysis_seconds)

    def get_channel_videos(
        self,
        channel_id: str,
        max_results: int = 5,
        min_analysis_seconds: int = 600,
    ) -> List[Dict[str, Any]]:
        return get_channel_videos(
            channel_id,
            max_results=max_results,
            min_analysis_seconds=min_analysis_seconds,
        )
