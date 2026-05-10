"""Failing-first unit tests for tools/discovery.

Combines tools.rss_discovery (recent uploads list) with tools.ytdlp_metadata
(per-video duration). This is the no-key replacement for the legacy
YouTubeClient.get_channel_videos() path that used 3 YouTube Data API calls.

Both submodules are mocked so these tests run offline.
"""

from unittest.mock import patch

from tools.discovery import (
    DiscoveryClient,
    get_channel_videos,
    get_video_info,
)

# ----- get_video_info (single video metadata) -----


@patch("tools.discovery.ytdlp_metadata.get_video_metadata")
def test_get_video_info_passthrough_with_excluded_flag(mock_meta):
    mock_meta.return_value = {
        "video_id": "MO9ZTZPUwXY",
        "title": "Macro 2026",
        "channel_name": "Forward Guidance",
        "channel_id": "UCkrwgzhIBKccuDsi_SvZtnQ",
        "duration": 3725,
        "published_at": "2026-05-01",
        "url": "https://www.youtube.com/watch?v=MO9ZTZPUwXY",
        "is_live": False,
    }
    info = get_video_info("https://www.youtube.com/watch?v=MO9ZTZPUwXY")
    assert info["video_id"] == "MO9ZTZPUwXY"
    assert info["duration"] == 3725
    assert info["excluded_from_analysis"] is False


@patch("tools.discovery.ytdlp_metadata.get_video_metadata")
def test_get_video_info_marks_short_video_excluded(mock_meta):
    mock_meta.return_value = {
        "video_id": "shortvid001",
        "title": "Short clip",
        "channel_name": "C",
        "channel_id": "UC" + "x" * 22,
        "duration": 120,
        "published_at": "2026-05-01",
        "url": "https://www.youtube.com/watch?v=shortvid001",
        "is_live": False,
    }
    info = get_video_info(
        "https://www.youtube.com/watch?v=shortvid001", min_analysis_seconds=600
    )
    assert info["excluded_from_analysis"] is True


@patch("tools.discovery.ytdlp_metadata.get_video_metadata")
def test_get_video_info_returns_none_when_video_missing(mock_meta):
    mock_meta.return_value = None
    assert get_video_info("https://www.youtube.com/watch?v=missingxxxx") is None


# ----- get_channel_videos (RSS list × yt-dlp duration enrichment) -----


_RSS_ENTRIES = [
    {
        "video_id": "MO9ZTZPUwXY",
        "title": "Macro 2026",
        "channel_id": "UCkrwgzhIBKccuDsi_SvZtnQ",
        "channel_name": "Forward Guidance",
        "published_at": "2026-05-01T13:00:21+00:00",
        "url": "https://www.youtube.com/watch?v=MO9ZTZPUwXY",
    },
    {
        "video_id": "abcDEFghi12",
        "title": "Fed Reaction",
        "channel_id": "UCkrwgzhIBKccuDsi_SvZtnQ",
        "channel_name": "Forward Guidance",
        "published_at": "2026-04-28T19:30:00+00:00",
        "url": "https://www.youtube.com/watch?v=abcDEFghi12",
    },
    {
        "video_id": "zyxWVUtsr98",
        "title": "Quick clip",
        "channel_id": "UCkrwgzhIBKccuDsi_SvZtnQ",
        "channel_name": "Forward Guidance",
        "published_at": "2026-04-25T10:00:00+00:00",
        "url": "https://www.youtube.com/watch?v=zyxWVUtsr98",
    },
]


def _meta(vid: str, duration: int) -> dict:
    return {
        "video_id": vid,
        "title": f"T-{vid}",
        "channel_name": "Forward Guidance",
        "channel_id": "UCkrwgzhIBKccuDsi_SvZtnQ",
        "duration": duration,
        "published_at": "2026-05-01",
        "url": f"https://www.youtube.com/watch?v={vid}",
        "is_live": False,
    }


@patch("tools.discovery.ytdlp_metadata.get_video_metadata")
@patch("tools.discovery.rss_discovery.get_recent_videos")
def test_get_channel_videos_combines_rss_and_ytdlp(mock_rss, mock_meta):
    mock_rss.return_value = _RSS_ENTRIES
    mock_meta.side_effect = [
        _meta("MO9ZTZPUwXY", 3725),
        _meta("abcDEFghi12", 1800),
        _meta("zyxWVUtsr98", 300),
    ]
    out = get_channel_videos("UCkrwgzhIBKccuDsi_SvZtnQ", max_results=3, min_analysis_seconds=600)
    assert len(out) == 3
    # Order should match RSS order (newest first)
    assert [v["video_id"] for v in out] == ["MO9ZTZPUwXY", "abcDEFghi12", "zyxWVUtsr98"]
    # Duration plumbed through
    assert [v["duration"] for v in out] == [3725, 1800, 300]
    # Excluded flag respects min_analysis_seconds
    assert [v["excluded_from_analysis"] for v in out] == [False, False, True]
    # RSS published_at preserved (ISO 8601 with time, not yt-dlp's date-only)
    assert out[0]["published_at"] == "2026-05-01T13:00:21+00:00"


@patch("tools.discovery.ytdlp_metadata.get_video_metadata")
@patch("tools.discovery.rss_discovery.get_recent_videos")
def test_get_channel_videos_truncates_to_max_results(mock_rss, mock_meta):
    mock_rss.return_value = _RSS_ENTRIES
    mock_meta.side_effect = [_meta("MO9ZTZPUwXY", 3725)]
    out = get_channel_videos("UCkrwgzhIBKccuDsi_SvZtnQ", max_results=1)
    assert len(out) == 1
    # ytdlp should only have been called once
    assert mock_meta.call_count == 1


@patch("tools.discovery.ytdlp_metadata.get_video_metadata")
@patch("tools.discovery.rss_discovery.get_recent_videos")
def test_get_channel_videos_skips_videos_ytdlp_cannot_fetch(mock_rss, mock_meta):
    """If yt-dlp returns None for a video (private/deleted/region-locked),
    the entry is dropped from the result rather than crashing the whole call."""
    mock_rss.return_value = _RSS_ENTRIES
    mock_meta.side_effect = [
        _meta("MO9ZTZPUwXY", 3725),
        None,  # second video unavailable
        _meta("zyxWVUtsr98", 300),
    ]
    out = get_channel_videos("UCkrwgzhIBKccuDsi_SvZtnQ", max_results=3)
    assert [v["video_id"] for v in out] == ["MO9ZTZPUwXY", "zyxWVUtsr98"]


@patch("tools.discovery.ytdlp_metadata.get_video_metadata")
@patch("tools.discovery.rss_discovery.get_recent_videos")
def test_get_channel_videos_handles_empty_rss(mock_rss, mock_meta):
    mock_rss.return_value = []
    out = get_channel_videos("UCemptychannelxxxxxxxxx")
    assert out == []
    mock_meta.assert_not_called()


# ----- DiscoveryClient (class-style API to mirror legacy YouTubeClient) -----


@patch("tools.discovery.ytdlp_metadata.get_video_metadata")
@patch("tools.discovery.rss_discovery.get_recent_videos")
def test_discovery_client_methods_match_module_functions(mock_rss, mock_meta):
    """DiscoveryClient is a thin instance-bound facade so that server.py
    doesn't have to switch between `_yt.get_video_info(...)` and a
    free-function call style."""
    mock_rss.return_value = _RSS_ENTRIES[:1]
    mock_meta.return_value = _meta("MO9ZTZPUwXY", 3725)

    client = DiscoveryClient()
    a = client.get_video_info("https://www.youtube.com/watch?v=MO9ZTZPUwXY")
    b = client.get_channel_videos("UCkrwgzhIBKccuDsi_SvZtnQ", max_results=1)

    assert a is not None and a["video_id"] == "MO9ZTZPUwXY"
    assert len(b) == 1 and b[0]["video_id"] == "MO9ZTZPUwXY"
