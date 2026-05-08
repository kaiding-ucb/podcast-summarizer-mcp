"""Tests for tools/youtube_client.py — pure helpers + mocked API calls."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.youtube_client import YouTubeClient  # noqa: E402


def test_extract_video_id_watch_url():
    assert YouTubeClient.extract_video_id("https://www.youtube.com/watch?v=SpniolSSPws") == "SpniolSSPws"


def test_extract_video_id_short_url():
    assert YouTubeClient.extract_video_id("https://youtu.be/SpniolSSPws") == "SpniolSSPws"


def test_extract_video_id_with_extra_params():
    assert (
        YouTubeClient.extract_video_id("https://www.youtube.com/watch?v=SpniolSSPws&t=120s")
        == "SpniolSSPws"
    )


def test_extract_video_id_passthrough():
    assert YouTubeClient.extract_video_id("SpniolSSPws") == "SpniolSSPws"


def test_parse_duration_minutes_seconds():
    assert YouTubeClient.parse_duration("PT4M13S") == 253


def test_parse_duration_hours():
    assert YouTubeClient.parse_duration("PT1H30M5S") == 5405


def test_parse_duration_livestream():
    assert YouTubeClient.parse_duration("P0D") == 0
    assert YouTubeClient.parse_duration("PT0S") == 0


def test_parse_duration_empty():
    assert YouTubeClient.parse_duration("") == 0
    assert YouTubeClient.parse_duration(None) == 0


def _fake_video_response(video_id="abc", duration="PT15M0S"):
    return {
        "items": [
            {
                "snippet": {
                    "title": "Test Video",
                    "channelTitle": "Test Channel",
                    "channelId": "UC123",
                    "publishedAt": "2026-04-10T12:00:00Z",
                },
                "contentDetails": {"duration": duration},
            }
        ]
    }


@patch("tools.youtube_client.build")
def test_get_video_info_success(mock_build):
    fake_yt = MagicMock()
    fake_yt.videos().list().execute.return_value = _fake_video_response()
    mock_build.return_value = fake_yt
    yc = YouTubeClient("FAKE_KEY")
    info = yc.get_video_info("https://www.youtube.com/watch?v=abc")
    assert info["video_id"] == "abc"
    assert info["title"] == "Test Video"
    assert info["duration"] == 900
    assert info["excluded_from_analysis"] is False


@patch("tools.youtube_client.build")
def test_get_video_info_too_short_excluded(mock_build):
    fake_yt = MagicMock()
    fake_yt.videos().list().execute.return_value = _fake_video_response(duration="PT5M0S")
    mock_build.return_value = fake_yt
    yc = YouTubeClient("FAKE_KEY")
    info = yc.get_video_info("https://www.youtube.com/watch?v=abc", min_analysis_seconds=600)
    assert info["duration"] == 300
    assert info["excluded_from_analysis"] is True


@patch("tools.youtube_client.build")
def test_get_video_info_not_found(mock_build):
    fake_yt = MagicMock()
    fake_yt.videos().list().execute.return_value = {"items": []}
    mock_build.return_value = fake_yt
    yc = YouTubeClient("FAKE_KEY")
    assert yc.get_video_info("https://www.youtube.com/watch?v=missing") is None


@patch("tools.youtube_client.build")
def test_get_video_info_quota_exceeded_raises(mock_build):
    fake_yt = MagicMock()
    fake_yt.videos().list().execute.side_effect = Exception("quotaExceeded: oh no")
    mock_build.return_value = fake_yt
    yc = YouTubeClient("FAKE_KEY")
    import pytest

    with pytest.raises(Exception, match="quotaExceeded"):
        yc.get_video_info("https://www.youtube.com/watch?v=abc")
