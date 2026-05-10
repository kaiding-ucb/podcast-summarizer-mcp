"""Natural-language YouTube channel discovery — no API key required.

Three public functions:
  - search_youtube_channels(query, max_results=5) -> List[ChannelCandidate]
  - resolve_youtube_channel(handle_or_url) -> ChannelInfo | None
  - get_channel_metadata(channel_id) -> ChannelInfo

All powered by yt-dlp scraping the public YouTube site. Search uses the
"ytsearchN:" magic prefix that yt-dlp recognizes; resolve/metadata extract
from channel pages directly.

For interactive use only — not throttled. yt-dlp from cloud-VPS IPs may
hit "confirm you're not a bot" gates; residential / Tailscale traffic
is unaffected.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

_HANDLE_RE = re.compile(r"^@[A-Za-z0-9_.-]{2,40}$")
_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{20,24}$")
_CHANNEL_URL_PATTERNS = [
    re.compile(r"^https?://(?:www\.)?youtube\.com/@([A-Za-z0-9_.-]{2,40})/?$"),
    re.compile(r"^https?://(?:www\.)?youtube\.com/c/([A-Za-z0-9_.-]+)/?$"),
    re.compile(r"^https?://(?:www\.)?youtube\.com/channel/(UC[A-Za-z0-9_-]{20,24})/?$"),
    re.compile(r"^https?://(?:www\.)?youtube\.com/user/([A-Za-z0-9_.-]+)/?$"),
]

_BASE_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    "noplaylist": True,
    "retries": 0,
}


# ---------- pure helpers ----------


def is_channel_handle(s: str) -> bool:
    return bool(_HANDLE_RE.match(s))


def is_channel_url(s: str) -> bool:
    return any(p.match(s) for p in _CHANNEL_URL_PATTERNS)


def extract_handle(handle_or_url: str) -> Optional[str]:
    """Extract `@handle` from either a bare handle or a youtube.com/@/c/user URL.

    Returns None for /channel/UCxxx URLs (those don't carry a handle —
    callers should use the UC id directly). Returns None for unknown input.
    """
    if is_channel_handle(handle_or_url):
        return handle_or_url
    for pat in _CHANNEL_URL_PATTERNS[:2] + [_CHANNEL_URL_PATTERNS[3]]:  # @, c/, user/
        m = pat.match(handle_or_url)
        if m:
            return f"@{m.group(1)}"
    # /channel/UCxxx URLs don't have a handle
    return None


def _channel_id_from_url(url: str) -> Optional[str]:
    m = _CHANNEL_URL_PATTERNS[2].match(url)  # /channel/UCxxx
    return m.group(1) if m else None


# ---------- ranking heuristic ----------


def score_candidate(query: str, name: str, hit_count: int) -> float:
    """Rank a channel candidate against the search query.

    Combines:
      - exact-name match: large boost
      - token overlap (lowercased): proportional to overlap fraction
      - hit_count: log-ish growth so a 5-hit channel ranks above 1-hit
        but a 10-hit channel doesn't dominate purely on volume
    """
    q = query.strip().lower()
    n = name.strip().lower()
    if not q or not n:
        return 0.0
    score = 0.0
    if q == n:
        score += 100.0
    elif q in n or n in q:
        score += 30.0
    q_tokens = set(re.findall(r"\w+", q))
    n_tokens = set(re.findall(r"\w+", n))
    if q_tokens:
        score += 15.0 * (len(q_tokens & n_tokens) / len(q_tokens))
    # hit_count contribution (saturating)
    score += min(hit_count, 10) * 2.0
    return score


# ---------- search_youtube_channels ----------


def search_youtube_channels(
    query: str,
    max_results: int = 5,
    raw_pool_size: int = 20,
) -> List[Dict[str, Any]]:
    """Find YouTube channels matching a free-text query.

    Uses yt-dlp's `ytsearchN:` magic prefix to fetch up to `raw_pool_size`
    video results, then groups by channel_id and ranks the unique channels
    by score_candidate(). Returns the top `max_results`.

    Each candidate dict:
      {channel_id, name, channel_url, hit_count, confidence_score}

    Description / subscriber_count are not populated here (that's a
    second yt-dlp call per candidate). Callers wanting those details
    should follow up with get_channel_metadata().
    """
    if not query.strip():
        return []
    search_term = f"ytsearch{raw_pool_size}:{query}"
    try:
        with YoutubeDL({**_BASE_OPTS, "extract_flat": True}) as ydl:
            raw = ydl.extract_info(search_term, download=False)
    except DownloadError as e:
        logging.info("yt-dlp search failed for %r: %s", query, e)
        return []

    entries = (raw or {}).get("entries") or []
    by_channel: Dict[str, Dict[str, Any]] = {}
    for e in entries:
        cid = e.get("channel_id")
        if not cid:
            continue
        if cid not in by_channel:
            by_channel[cid] = {
                "channel_id": cid,
                "name": e.get("channel") or e.get("uploader") or "",
                "channel_url": f"https://www.youtube.com/channel/{cid}",
                "hit_count": 0,
            }
        by_channel[cid]["hit_count"] += 1

    candidates: List[Dict[str, Any]] = []
    for c in by_channel.values():
        c["confidence_score"] = score_candidate(query, c["name"], c["hit_count"])
        candidates.append(c)
    candidates.sort(key=lambda c: c["confidence_score"], reverse=True)
    return candidates[:max_results]


# ---------- resolve_youtube_channel ----------


def resolve_youtube_channel(handle_or_url: str) -> Optional[Dict[str, Any]]:
    """Resolve a handle (@x) or full channel URL to a ChannelInfo dict.

    Returns None for video URLs, free-text strings, or channels yt-dlp
    can't load. Callers passing a /channel/UCxxx URL get an info dict
    without a `handle` field.
    """
    if not handle_or_url:
        return None
    handle_or_url = handle_or_url.strip()

    target_url: Optional[str] = None
    if is_channel_handle(handle_or_url):
        target_url = f"https://www.youtube.com/{handle_or_url}"
    elif is_channel_url(handle_or_url):
        target_url = handle_or_url
    else:
        return None

    try:
        with YoutubeDL({**_BASE_OPTS, "extract_flat": "in_playlist", "playlist_items": "0"}) as ydl:
            raw = ydl.extract_info(target_url, download=False)
    except DownloadError as e:
        logging.info("yt-dlp could not resolve channel %r: %s", handle_or_url, e)
        return None

    if raw is None:
        return None
    return _channel_info_from_raw(raw, fallback_url=target_url)


# ---------- get_channel_metadata ----------


def get_channel_metadata(channel_id: str) -> Dict[str, Any]:
    """Fetch full metadata for a known channel_id.

    Raises ValueError for inputs that don't look like UCxxxx ids — callers
    should normalize via resolve_youtube_channel first.
    """
    if not _CHANNEL_ID_RE.match(channel_id):
        raise ValueError(
            f"channel_id must look like 'UCxxxxxxxxxxxxxxxxxxxxxx', got: {channel_id!r}"
        )
    target_url = f"https://www.youtube.com/channel/{channel_id}"
    with YoutubeDL({**_BASE_OPTS, "extract_flat": "in_playlist", "playlistend": 5}) as ydl:
        raw = ydl.extract_info(target_url, download=False)
    return _channel_info_from_raw(raw or {}, fallback_url=target_url)


# ---------- shared parsing ----------


def _channel_info_from_raw(raw: Dict[str, Any], fallback_url: str) -> Dict[str, Any]:
    """Normalize yt-dlp's channel-page extract into a ChannelInfo dict."""
    cid = raw.get("channel_id") or raw.get("id") or ""
    if not _CHANNEL_ID_RE.match(cid):
        cid = _channel_id_from_url(raw.get("webpage_url") or fallback_url) or cid

    name = raw.get("channel") or raw.get("uploader") or raw.get("title") or ""
    description = raw.get("description") or ""
    subs = raw.get("channel_follower_count")

    handle = raw.get("uploader_id") or ""
    if handle and not handle.startswith("@"):
        handle = f"@{handle}"
    if not handle:
        # Fall back to extracting from webpage_url
        handle = extract_handle(raw.get("webpage_url") or fallback_url) or ""

    entries = raw.get("entries") or []
    titles = [e.get("title", "") for e in entries if e.get("title")]

    return {
        "channel_id": cid,
        "name": name,
        "handle": handle or None,
        "description": description,
        "subscriber_count": subs if isinstance(subs, int) else None,
        "channel_url": raw.get("webpage_url") or fallback_url,
        "recent_video_titles": titles[:5],
    }
