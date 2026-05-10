"""Failing-first unit tests for tools/channel_search.

Three public functions:
  - search_youtube_channels(query, max_results) -> List[ChannelCandidate]
  - resolve_youtube_channel(handle_or_url) -> ChannelInfo | None
  - get_channel_metadata(channel_id) -> ChannelInfo

All yt-dlp calls are mocked so these tests run offline.
"""

from unittest.mock import MagicMock, patch

import pytest

from tools.channel_search import (
    extract_handle,
    is_channel_handle,
    is_channel_url,
    get_channel_metadata,
    resolve_youtube_channel,
    score_candidate,
    search_youtube_channels,
)


# ---------- pure helpers ----------


def test_is_channel_handle_recognizes_at_prefix():
    assert is_channel_handle("@ForwardGuidance") is True
    assert is_channel_handle("@lex.fridman") is True


def test_is_channel_handle_rejects_non_at():
    assert is_channel_handle("Forward Guidance") is False
    assert is_channel_handle("https://www.youtube.com/@ForwardGuidance") is False


def test_is_channel_url_recognizes_youtube_urls():
    assert is_channel_url("https://www.youtube.com/@ForwardGuidance") is True
    assert is_channel_url("https://www.youtube.com/channel/UCkrwgzhIBKccuDsi_SvZtnQ") is True
    assert is_channel_url("https://www.youtube.com/c/ForwardGuidance") is True


def test_is_channel_url_rejects_non_channel_urls():
    assert is_channel_url("https://www.youtube.com/watch?v=MO9ZTZPUwXY") is False
    assert is_channel_url("https://example.com/foo") is False
    assert is_channel_url("@ForwardGuidance") is False


def test_extract_handle_from_handle_input():
    assert extract_handle("@ForwardGuidance") == "@ForwardGuidance"


def test_extract_handle_from_url():
    assert extract_handle("https://www.youtube.com/@ForwardGuidance") == "@ForwardGuidance"


def test_extract_handle_from_c_url():
    assert extract_handle("https://www.youtube.com/c/ForwardGuidance") == "@ForwardGuidance"


def test_extract_handle_returns_none_for_channel_id_url():
    """A /channel/UCxxxx URL has no handle; caller should use it as a
    direct channel_id, not try to derive a handle."""
    assert extract_handle("https://www.youtube.com/channel/UCkrwgzhIBKccuDsi_SvZtnQ") is None


# ---------- score_candidate (ranking heuristic) ----------


def test_score_candidate_higher_for_exact_name_match():
    a = score_candidate(query="Forward Guidance", name="Forward Guidance", hit_count=1)
    b = score_candidate(query="Forward Guidance", name="Some Random Channel", hit_count=1)
    assert a > b


def test_score_candidate_higher_with_more_hits():
    a = score_candidate(query="macro podcast", name="Macro Podcast", hit_count=5)
    b = score_candidate(query="macro podcast", name="Macro Podcast", hit_count=1)
    assert a > b


def test_score_candidate_handles_partial_match():
    score = score_candidate(query="Forward", name="Forward Guidance", hit_count=2)
    assert score > 0


# ---------- search_youtube_channels ----------


def _ytdlp_search_result(entries: list) -> dict:
    """Build the dict shape yt-dlp returns from `extract_info("ytsearchN:...")`."""
    return {"entries": entries, "extractor_key": "YoutubeSearch"}


def _entry(channel_id: str, channel: str, title: str = "Some Video") -> dict:
    return {
        "id": "VIDxxxxxxxx",
        "title": title,
        "channel": channel,
        "channel_id": channel_id,
        "uploader": channel,
        "uploader_url": f"https://www.youtube.com/channel/{channel_id}",
    }


@patch("tools.channel_search.YoutubeDL")
def test_search_youtube_channels_groups_by_channel_id(mock_ydl_cls):
    """Multiple video hits from the same channel should collapse to one
    candidate with a hit_count, not one candidate per video."""
    fg_id = "UCkrwgzhIBKccuDsi_SvZtnQ"
    other_id = "UC" + "x" * 22
    ydl = MagicMock()
    ydl.extract_info.return_value = _ytdlp_search_result(
        [
            _entry(fg_id, "Forward Guidance", "Macro 2026"),
            _entry(fg_id, "Forward Guidance", "Fed Reaction"),
            _entry(fg_id, "Forward Guidance", "Bond Selloff"),
            _entry(other_id, "Some Other Channel", "Random video about macro"),
        ]
    )
    cm = MagicMock()
    cm.__enter__.return_value = ydl
    cm.__exit__.return_value = False
    mock_ydl_cls.return_value = cm

    candidates = search_youtube_channels("Forward Guidance", max_results=5)
    assert len(candidates) == 2  # one per unique channel_id
    # Forward Guidance should rank higher (3 hits + name match)
    assert candidates[0]["channel_id"] == fg_id
    assert candidates[0]["name"] == "Forward Guidance"


@patch("tools.channel_search.YoutubeDL")
def test_search_youtube_channels_truncates_to_max_results(mock_ydl_cls):
    ydl = MagicMock()
    ydl.extract_info.return_value = _ytdlp_search_result(
        [
            _entry(f"UC{'a' * 22}", "Channel A"),
            _entry(f"UC{'b' * 22}", "Channel B"),
            _entry(f"UC{'c' * 22}", "Channel C"),
            _entry(f"UC{'d' * 22}", "Channel D"),
        ]
    )
    cm = MagicMock()
    cm.__enter__.return_value = ydl
    cm.__exit__.return_value = False
    mock_ydl_cls.return_value = cm

    out = search_youtube_channels("query", max_results=2)
    assert len(out) == 2


@patch("tools.channel_search.YoutubeDL")
def test_search_youtube_channels_includes_channel_url(mock_ydl_cls):
    cid = f"UC{'a' * 22}"
    ydl = MagicMock()
    ydl.extract_info.return_value = _ytdlp_search_result(
        [_entry(cid, "Test Channel")]
    )
    cm = MagicMock()
    cm.__enter__.return_value = ydl
    cm.__exit__.return_value = False
    mock_ydl_cls.return_value = cm

    out = search_youtube_channels("test")
    assert out[0]["channel_url"] == f"https://www.youtube.com/channel/{cid}"


@patch("tools.channel_search.YoutubeDL")
def test_search_youtube_channels_handles_empty_results(mock_ydl_cls):
    ydl = MagicMock()
    ydl.extract_info.return_value = _ytdlp_search_result([])
    cm = MagicMock()
    cm.__enter__.return_value = ydl
    cm.__exit__.return_value = False
    mock_ydl_cls.return_value = cm

    out = search_youtube_channels("nonexistent gibberish query")
    assert out == []


@patch("tools.channel_search.YoutubeDL")
def test_search_youtube_channels_uses_ytsearch_prefix(mock_ydl_cls):
    """yt-dlp recognizes the magic 'ytsearchN:...' prefix as a YouTube
    search query. We verify the wrapper passes one through."""
    ydl = MagicMock()
    ydl.extract_info.return_value = _ytdlp_search_result([])
    cm = MagicMock()
    cm.__enter__.return_value = ydl
    cm.__exit__.return_value = False
    mock_ydl_cls.return_value = cm

    search_youtube_channels("Forward Guidance", max_results=5)
    args, _ = ydl.extract_info.call_args
    assert args[0].startswith("ytsearch")
    assert "Forward Guidance" in args[0]


# ---------- resolve_youtube_channel ----------


@patch("tools.channel_search.YoutubeDL")
def test_resolve_youtube_channel_from_handle(mock_ydl_cls):
    cid = "UCkrwgzhIBKccuDsi_SvZtnQ"
    ydl = MagicMock()
    ydl.extract_info.return_value = {
        "id": cid,
        "channel": "Forward Guidance",
        "channel_id": cid,
        "uploader_id": "@ForwardGuidance",
        "description": "A macro podcast.",
        "channel_follower_count": 1200000,
        "webpage_url": "https://www.youtube.com/@ForwardGuidance",
    }
    cm = MagicMock()
    cm.__enter__.return_value = ydl
    cm.__exit__.return_value = False
    mock_ydl_cls.return_value = cm

    info = resolve_youtube_channel("@ForwardGuidance")
    assert info is not None
    assert info["channel_id"] == cid
    assert info["name"] == "Forward Guidance"
    assert info["handle"] == "@ForwardGuidance"


@patch("tools.channel_search.YoutubeDL")
def test_resolve_youtube_channel_from_full_url(mock_ydl_cls):
    cid = "UCkrwgzhIBKccuDsi_SvZtnQ"
    ydl = MagicMock()
    ydl.extract_info.return_value = {
        "id": cid,
        "channel": "Forward Guidance",
        "channel_id": cid,
        "uploader_id": "@ForwardGuidance",
        "webpage_url": "https://www.youtube.com/channel/" + cid,
    }
    cm = MagicMock()
    cm.__enter__.return_value = ydl
    cm.__exit__.return_value = False
    mock_ydl_cls.return_value = cm

    info = resolve_youtube_channel("https://www.youtube.com/channel/" + cid)
    assert info["channel_id"] == cid


@patch("tools.channel_search.YoutubeDL")
def test_resolve_youtube_channel_returns_none_for_nonexistent(mock_ydl_cls):
    from yt_dlp.utils import DownloadError

    ydl = MagicMock()
    ydl.extract_info.side_effect = DownloadError("Channel not found")
    cm = MagicMock()
    cm.__enter__.return_value = ydl
    cm.__exit__.return_value = False
    mock_ydl_cls.return_value = cm

    assert resolve_youtube_channel("@thisdoesnotexistxyz12345") is None


def test_resolve_youtube_channel_rejects_non_channel_input():
    """A video URL or random string is not a channel reference."""
    assert resolve_youtube_channel("https://www.youtube.com/watch?v=MO9ZTZPUwXY") is None
    assert resolve_youtube_channel("just some text") is None


# ---------- get_channel_metadata ----------


@patch("tools.channel_search.YoutubeDL")
def test_get_channel_metadata_returns_full_info(mock_ydl_cls):
    cid = "UCkrwgzhIBKccuDsi_SvZtnQ"
    ydl = MagicMock()
    ydl.extract_info.return_value = {
        "id": cid,
        "channel": "Forward Guidance",
        "channel_id": cid,
        "uploader_id": "@ForwardGuidance",
        "description": "A macro podcast hosted by Jack Farley.",
        "channel_follower_count": 1234567,
        "webpage_url": "https://www.youtube.com/@ForwardGuidance",
        "entries": [
            {"title": "Macro 2026", "id": "MO9ZTZPUwXY"},
            {"title": "Fed Reaction", "id": "abcDEFghi12"},
            {"title": "Bond Selloff", "id": "zyxWVUtsr98"},
        ],
    }
    cm = MagicMock()
    cm.__enter__.return_value = ydl
    cm.__exit__.return_value = False
    mock_ydl_cls.return_value = cm

    info = get_channel_metadata(cid)
    assert info["channel_id"] == cid
    assert info["name"] == "Forward Guidance"
    assert info["handle"] == "@ForwardGuidance"
    assert info["subscriber_count"] == 1234567
    assert "macro podcast" in info["description"].lower()
    assert info["recent_video_titles"][:3] == ["Macro 2026", "Fed Reaction", "Bond Selloff"]


@patch("tools.channel_search.YoutubeDL")
def test_get_channel_metadata_missing_subscriber_count_returns_none_field(mock_ydl_cls):
    cid = "UCsmallchannelxxxxxxxxx"
    ydl = MagicMock()
    ydl.extract_info.return_value = {
        "id": cid,
        "channel": "Tiny Channel",
        "channel_id": cid,
        "webpage_url": "https://www.youtube.com/channel/" + cid,
        # no channel_follower_count
        "entries": [],
    }
    cm = MagicMock()
    cm.__enter__.return_value = ydl
    cm.__exit__.return_value = False
    mock_ydl_cls.return_value = cm

    info = get_channel_metadata(cid)
    assert info["subscriber_count"] is None
    assert info["recent_video_titles"] == []


def test_get_channel_metadata_rejects_non_channel_id():
    with pytest.raises(ValueError):
        get_channel_metadata("@ForwardGuidance")
    with pytest.raises(ValueError):
        get_channel_metadata("not a channel id")
