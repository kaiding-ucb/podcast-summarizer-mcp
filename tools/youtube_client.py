"""YouTube Data API v3 client. Adapted from youtube_service.py."""

import logging
import re
from typing import Any, Dict, List, Optional

from googleapiclient.discovery import build

_VIDEO_URL_PATTERNS = [
    re.compile(r"(?:youtube\.com/watch\?v=|youtu\.be/)([^&\n?#]+)"),
    re.compile(r"youtube\.com/embed/([^&\n?#]+)"),
]
_DURATION_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


class YouTubeClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.youtube = build("youtube", "v3", developerKey=api_key, cache_discovery=False)

    @staticmethod
    def extract_video_id(url: str) -> str:
        for pat in _VIDEO_URL_PATTERNS:
            m = pat.search(url)
            if m:
                return m.group(1)
        return url

    @staticmethod
    def parse_duration(duration_str: str) -> int:
        if not duration_str or duration_str.startswith("P0D") or duration_str == "PT0S":
            return 0
        m = _DURATION_RE.match(duration_str)
        if not m:
            logging.warning("Could not parse YouTube duration: %s", duration_str)
            return 0
        h, mn, sec = (int(x or 0) for x in m.groups())
        return h * 3600 + mn * 60 + sec

    def get_video_info(self, video_url: str, min_analysis_seconds: int = 600) -> Optional[Dict[str, Any]]:
        video_id = self.extract_video_id(video_url)
        try:
            resp = self.youtube.videos().list(part="snippet,contentDetails", id=video_id).execute()
        except Exception as e:
            logging.error("YouTube videos().list failed for %s: %s", video_id, e)
            if "quotaExceeded" in str(e):
                raise
            return None
        if not resp.get("items"):
            return None
        v = resp["items"][0]
        sn = v["snippet"]
        duration = self.parse_duration(v["contentDetails"].get("duration", "PT0S"))
        return {
            "video_id": video_id,
            "title": sn.get("title", "Unknown Title"),
            "channel_name": sn.get("channelTitle", "Unknown Channel"),
            "channel_id": sn.get("channelId"),
            "duration": duration,
            "published_at": sn.get("publishedAt", ""),
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "excluded_from_analysis": duration < min_analysis_seconds,
        }

    def get_channel_videos(
        self,
        channel_id: str,
        max_results: int = 5,
        min_analysis_seconds: int = 600,
    ) -> List[Dict[str, Any]]:
        """Return up to max_results most recent uploads, newest first.

        Uses the channel's uploads playlist (1 unit) + N videos.list calls (N units).
        Avoids search.list (100 units).
        """
        try:
            chan = self.youtube.channels().list(part="contentDetails", id=channel_id).execute()
        except Exception as e:
            logging.error("YouTube channels().list failed for %s: %s", channel_id, e)
            if "quotaExceeded" in str(e):
                raise
            return []
        if not chan.get("items"):
            return []
        uploads_pl = chan["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
        try:
            pl = self.youtube.playlistItems().list(
                part="snippet", playlistId=uploads_pl, maxResults=max_results
            ).execute()
        except Exception as e:
            logging.error("YouTube playlistItems().list failed for %s: %s", channel_id, e)
            if "quotaExceeded" in str(e):
                raise
            return []
        videos: List[Dict[str, Any]] = []
        for item in pl.get("items", []):
            vid = item["snippet"]["resourceId"]["videoId"]
            info = self.get_video_info(
                f"https://www.youtube.com/watch?v={vid}",
                min_analysis_seconds=min_analysis_seconds,
            )
            if info:
                info["channel_id"] = channel_id
                videos.append(info)
        videos.sort(key=lambda x: x.get("published_at", ""), reverse=True)
        return videos
