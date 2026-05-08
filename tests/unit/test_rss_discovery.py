"""Failing-first unit tests for tools/rss_discovery.

YouTube exposes a no-key Atom feed at
  https://www.youtube.com/feeds/videos.xml?channel_id=UCxxxxx
This module wraps fetching + parsing it.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from tools.rss_discovery import (
    RSSFetchError,
    feed_url_for_channel,
    fetch_channel_feed,
    get_recent_videos,
    parse_feed,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_feed_url_for_channel_uses_official_endpoint():
    url = feed_url_for_channel("UCkrwgzhIBKccuDsi_SvZtnQ")
    assert url == "https://www.youtube.com/feeds/videos.xml?channel_id=UCkrwgzhIBKccuDsi_SvZtnQ"


def test_feed_url_rejects_non_uc_id():
    """Channel IDs must start with 'UC' — guard against handle-leakage."""
    with pytest.raises(ValueError):
        feed_url_for_channel("@ForwardGuidance")
    with pytest.raises(ValueError):
        feed_url_for_channel("not-a-channel-id")


def test_parse_feed_extracts_channel_metadata():
    parsed = parse_feed(_load("rss_sample_feed.xml"))
    assert parsed["channel_id"] == "UCkrwgzhIBKccuDsi_SvZtnQ"
    assert parsed["channel_name"] == "Forward Guidance"


def test_parse_feed_extracts_three_entries_in_published_order():
    parsed = parse_feed(_load("rss_sample_feed.xml"))
    assert len(parsed["entries"]) == 3
    # newest first (matches YouTube RSS order, but we still sort defensively)
    ids = [e["video_id"] for e in parsed["entries"]]
    assert ids == ["MO9ZTZPUwXY", "abcDEFghi12", "zyxWVUtsr98"]


def test_parse_feed_entry_shape():
    parsed = parse_feed(_load("rss_sample_feed.xml"))
    e = parsed["entries"][0]
    assert e["video_id"] == "MO9ZTZPUwXY"
    assert e["title"] == "The Macro Outlook for 2026"
    assert e["channel_id"] == "UCkrwgzhIBKccuDsi_SvZtnQ"
    assert e["channel_name"] == "Forward Guidance"
    assert e["published_at"] == "2026-05-01T13:00:21+00:00"
    assert e["url"] == "https://www.youtube.com/watch?v=MO9ZTZPUwXY"


def test_parse_feed_empty_channel_returns_no_entries():
    parsed = parse_feed(_load("rss_empty_feed.xml"))
    assert parsed["channel_id"] == "UCEMPTYxxxxxxxxxxxxxxx"
    assert parsed["entries"] == []


def test_parse_feed_malformed_xml_raises():
    with pytest.raises(RSSFetchError):
        parse_feed("<not really xml at all<<<>")


def test_parse_feed_missing_channel_id_raises():
    """If the feed somehow lacks <yt:channelId>, surface that as a parse error
    rather than silently returning empty data."""
    bad = '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    with pytest.raises(RSSFetchError):
        parse_feed(bad)


@patch("tools.rss_discovery.urlopen")
def test_fetch_channel_feed_uses_correct_url(mock_urlopen):
    fake_xml = _load("rss_sample_feed.xml")
    mock_urlopen.return_value.__enter__.return_value.read.return_value = fake_xml.encode("utf-8")

    out = fetch_channel_feed("UCkrwgzhIBKccuDsi_SvZtnQ")

    args, kwargs = mock_urlopen.call_args
    # url is positional or in `url=`
    called_url = args[0] if args else kwargs.get("url")
    # If a Request object was passed, get its full_url
    if hasattr(called_url, "full_url"):
        called_url = called_url.full_url
    assert "feeds/videos.xml?channel_id=UCkrwgzhIBKccuDsi_SvZtnQ" in called_url
    assert isinstance(out, str)
    assert "Forward Guidance" in out


@patch("tools.rss_discovery.urlopen")
def test_fetch_channel_feed_wraps_http_errors(mock_urlopen):
    from urllib.error import HTTPError

    mock_urlopen.side_effect = HTTPError(
        url="x", code=404, msg="Not Found", hdrs=None, fp=None
    )
    with pytest.raises(RSSFetchError):
        fetch_channel_feed("UCnonexistentxxxxxxxxx")


@patch("tools.rss_discovery.fetch_channel_feed")
def test_get_recent_videos_combines_fetch_and_parse(mock_fetch):
    mock_fetch.return_value = _load("rss_sample_feed.xml")
    videos = get_recent_videos("UCkrwgzhIBKccuDsi_SvZtnQ")
    assert len(videos) == 3
    assert videos[0]["video_id"] == "MO9ZTZPUwXY"
    mock_fetch.assert_called_once_with("UCkrwgzhIBKccuDsi_SvZtnQ", timeout=10)


@patch("tools.rss_discovery.fetch_channel_feed")
def test_get_recent_videos_max_results_truncates(mock_fetch):
    mock_fetch.return_value = _load("rss_sample_feed.xml")
    videos = get_recent_videos("UCkrwgzhIBKccuDsi_SvZtnQ", max_results=2)
    assert len(videos) == 2
    assert [v["video_id"] for v in videos] == ["MO9ZTZPUwXY", "abcDEFghi12"]
