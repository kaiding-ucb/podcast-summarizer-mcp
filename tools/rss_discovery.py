"""YouTube channel discovery via the public RSS Atom feed.

YouTube exposes a no-key Atom feed at
  https://www.youtube.com/feeds/videos.xml?channel_id=UCxxxxx
that lists the channel's most recent ~15 uploads. No auth, no quota,
no ToS grey area — it's the sanctioned alternative to youtube.search.list.

This module replaces the YouTube Data API v3 calls (channels.list +
playlistItems.list) for video discovery. Per-video duration must still
be fetched via yt-dlp (RSS doesn't include it).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

_FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{20,24}$")
_USER_AGENT = "podcast-summarizer-mcp/0.2 (+https://github.com/)"

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}


class RSSFetchError(RuntimeError):
    """Raised on HTTP/network failure or unparseable feed."""


def feed_url_for_channel(channel_id: str) -> str:
    """Return the RSS feed URL for a YouTube channel.

    Channel IDs always start with `UC` followed by 22 chars. Handles
    (e.g. `@ForwardGuidance`) must be resolved to a channel ID first
    via tools.channel_search; this function refuses anything that
    doesn't look like a UCxxxx id to avoid silent feed-not-found cases.
    """
    if not _CHANNEL_ID_RE.match(channel_id):
        raise ValueError(
            f"channel_id must look like 'UCxxxxxxxxxxxxxxxxxxxxxx', got: {channel_id!r}"
        )
    return _FEED_URL.format(cid=channel_id)


def fetch_channel_feed(channel_id: str, timeout: int = 10) -> str:
    """GET the channel's RSS feed and return raw XML as a str.

    Raises RSSFetchError on HTTP errors / network failures. Callers
    that want resilience should retry; this function is intentionally
    a thin wrapper.
    """
    url = feed_url_for_channel(channel_id)
    req = Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except HTTPError as e:
        raise RSSFetchError(f"HTTP {e.code} fetching {url}: {e.reason}") from e
    except URLError as e:
        raise RSSFetchError(f"Network error fetching {url}: {e.reason}") from e


def parse_feed(xml: str) -> Dict[str, Any]:
    """Parse an Atom feed body into channel metadata + entries.

    Returns:
      {
        "channel_id": "UCxxxxx",
        "channel_name": "...",
        "entries": [
          {"video_id", "title", "channel_id", "channel_name", "published_at", "url"},
          ...
        ]
      }
    """
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise RSSFetchError(f"malformed RSS XML: {e}") from e

    cid_el = root.find("yt:channelId", _NS)
    if cid_el is None or not (cid_el.text or "").strip():
        raise RSSFetchError("RSS feed missing <yt:channelId>")
    channel_id = cid_el.text.strip()

    name_el = root.find("atom:title", _NS)
    channel_name = (name_el.text or "").strip() if name_el is not None else ""

    entries: List[Dict[str, Any]] = []
    for entry in root.findall("atom:entry", _NS):
        vid = entry.find("yt:videoId", _NS)
        if vid is None or not (vid.text or "").strip():
            continue
        video_id = vid.text.strip()

        title_el = entry.find("atom:title", _NS)
        title = (title_el.text or "").strip() if title_el is not None else ""

        published_el = entry.find("atom:published", _NS)
        published_at = (published_el.text or "").strip() if published_el is not None else ""

        entry_cid = entry.find("yt:channelId", _NS)
        entry_channel_id = (
            entry_cid.text.strip() if entry_cid is not None and entry_cid.text else channel_id
        )

        author_name_el = entry.find("atom:author/atom:name", _NS)
        entry_channel_name = (
            (author_name_el.text or "").strip() if author_name_el is not None else channel_name
        )

        entries.append(
            {
                "video_id": video_id,
                "title": title,
                "channel_id": entry_channel_id,
                "channel_name": entry_channel_name,
                "published_at": published_at,
                "url": f"https://www.youtube.com/watch?v={video_id}",
            }
        )

    # Defensive sort: newest first by published_at (YouTube already returns
    # them this way but we don't trust it across yt-side changes)
    entries.sort(key=lambda e: e.get("published_at", ""), reverse=True)

    return {
        "channel_id": channel_id,
        "channel_name": channel_name,
        "entries": entries,
    }


def get_recent_videos(
    channel_id: str, max_results: int = 15, timeout: int = 10
) -> List[Dict[str, Any]]:
    """Convenience: fetch + parse + truncate. Returns just the entry dicts."""
    xml = fetch_channel_feed(channel_id, timeout=timeout)
    parsed = parse_feed(xml)
    return parsed["entries"][:max_results]
