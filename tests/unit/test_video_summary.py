"""TC-A2..A10 — video summary pipeline unit tests.

(TC-A1 — full real-Gemini analysis — lives in tests/integration/test_analyze_real.py
and tests/openclaw/test_video_summary.py.)

These cover the deterministic edges of analyze_video / discover_new_videos:
  - TC-A2 short-video filter
  - TC-A3 livestream filter
  - TC-A4 invalid URL fail-fast
  - TC-A5 retry on empty Gemini output
  - TC-A7 vaneck filter logic
  - TC-A9 discovery state persistence (idempotent re-poll)
  - TC-A10 registry-aware discover_new_videos
(TC-A6 + TC-A8 already covered by tests/unit/test_gemini_client.py.)
"""

from unittest.mock import MagicMock, patch

import pytest


# ---------- TC-A4 — invalid URL fails fast (no Gemini token spend) ----------


@patch("tools.discovery.ytdlp_metadata.get_video_metadata")
def test_TC_A4_invalid_url_returns_none(mock_meta):
    from tools.discovery import get_video_info

    mock_meta.return_value = None
    info = get_video_info("https://www.youtube.com/watch?v=NOTAREALVID")
    assert info is None
    mock_meta.assert_called_once()


# ---------- TC-A5 — retry on empty Gemini output ----------


def test_TC_A5_retry_on_empty_then_success():
    """gemini_client.analyze_video should retry on empty output and
    surface attempts=2 with success=True when the second attempt works."""
    from tools.gemini_client import GeminiClient

    gc = GeminiClient.__new__(GeminiClient)  # bypass __init__ (no API key)
    gc.client = MagicMock()
    # First attempt: empty text. Second attempt: valid output.
    valid_text = (
        "## Video Summary & Key Moments\n"
        "Test summary of a podcast.\n"
        "* (12:34) Important moment\n\n"
        "## Investment Recommendations\n"
        "No explicit investment recommendations in this video."
    )
    resp1 = MagicMock(text="")
    resp2 = MagicMock(text=valid_text)
    gc.client.models.generate_content.side_effect = [resp1, resp2]

    result = gc.analyze_video(
        video_url="https://www.youtube.com/watch?v=MO9ZTZPUwXY",
        video_id="MO9ZTZPUwXY",
        video_duration=3000,
        max_retries=3,
    )
    assert result["success"] is True
    assert result["attempts"] == 2
    assert "Video Summary" in result["analysis"]


def test_TC_A5_max_retries_exhausted_returns_error():
    from tools.gemini_client import GeminiClient

    gc = GeminiClient.__new__(GeminiClient)
    gc.client = MagicMock()
    gc.client.models.generate_content.return_value = MagicMock(text="")

    result = gc.analyze_video(
        video_url="https://www.youtube.com/watch?v=xxxxxxxxxxx",
        video_id="xxxxxxxxxxx",
        video_duration=3000,
        max_retries=2,
    )
    assert result["success"] is False
    assert result["attempts"] == 2
    assert result["error"] is not None
    assert "empty" in result["error"].lower() or "short" in result["error"].lower()


# ---------- TC-A7 — vaneck filter logic ----------


def test_TC_A7_vaneck_excluded_when_text_contains_vaneck():
    from tools.gemini_client import GeminiClient

    gc = GeminiClient.__new__(GeminiClient)
    gc.client = MagicMock()
    text = (
        "## Video Summary & Key Moments\n"
        "This episode is brought to you by VanEck Semiconductor ETFs.\n"
        "* (10:00) Some moment\n\n"
        "## Investment Recommendations\n"
        "No explicit investment recommendations in this video."
    )
    gc.client.models.generate_content.return_value = MagicMock(text=text)
    result = gc.analyze_video(
        video_url="https://www.youtube.com/watch?v=xxxxxxxxxxx",
        video_id="xxxxxxxxxxx",
        video_duration=1800,
    )
    assert result["success"] is True
    # When the analysis text contains "vaneck", vaneck_excluded becomes False
    # (i.e. we DID NOT exclude vaneck from the output — the regex test).
    assert result["vaneck_excluded"] is False


def test_TC_A7_vaneck_excluded_when_text_clean():
    from tools.gemini_client import GeminiClient

    gc = GeminiClient.__new__(GeminiClient)
    gc.client = MagicMock()
    text = (
        "## Video Summary & Key Moments\n"
        "A clean podcast about macro markets.\n"
        "* (10:00) Some moment\n\n"
        "## Investment Recommendations\n"
        "No explicit investment recommendations in this video."
    )
    gc.client.models.generate_content.return_value = MagicMock(text=text)
    result = gc.analyze_video(
        video_url="https://www.youtube.com/watch?v=xxxxxxxxxxx",
        video_id="xxxxxxxxxxx",
        video_duration=1800,
    )
    # No vaneck mention → vaneck_excluded is True (excluded by virtue of absence)
    assert result["vaneck_excluded"] is True


# ---------- TC-A9 — discovery state persistence ----------


def test_TC_A9_discover_state_persisted_between_calls(tmp_path):
    """First call seeds state with the latest video and returns it as
    a first-run hit. Second call against the same channel returns no
    new videos because the latest is now last_seen."""
    from tools.state import StateStore

    state = StateStore(str(tmp_path / "video-state.json"))

    # First call — channel has no last_seen
    assert state.get_last_video_id("UCkrwgzhIBKccuDsi_SvZtnQ") is None

    state.update_channel(
        "UCkrwgzhIBKccuDsi_SvZtnQ",
        "MO9ZTZPUwXY",
        "2026-05-01T13:00:21+00:00",
    )
    assert state.get_last_video_id("UCkrwgzhIBKccuDsi_SvZtnQ") == "MO9ZTZPUwXY"

    # Re-instantiate (simulates a new MCP server process) — state survives.
    state2 = StateStore(str(tmp_path / "video-state.json"))
    assert state2.get_last_video_id("UCkrwgzhIBKccuDsi_SvZtnQ") == "MO9ZTZPUwXY"


# ---------- TC-A10 — registry-aware discover_new_videos ----------


def test_TC_A10_discover_reads_from_registry_when_channel_ids_omitted(
    tmp_path, monkeypatch
):
    """server.discover_new_videos should fall back to the channel
    registry when the caller doesn't pass channel_ids."""
    monkeypatch.setenv("GEMINI_API_KEY", "stub")
    monkeypatch.setenv("VIDEO_ANALYSIS_STATE_PATH", str(tmp_path / "video-state.json"))
    monkeypatch.setenv("VIDEO_ANALYSIS_CHANNELS_PATH", str(tmp_path / "channels.json"))

    # Force re-import of server with new env paths
    import importlib

    import server as server_module

    importlib.reload(server_module)

    server_module._channels.add(
        channel_id="UCkrwgzhIBKccuDsi_SvZtnQ",
        name="Forward Guidance",
        handle="@ForwardGuidanceBW",
        tags=["macro"],
    )
    server_module._channels.add(
        channel_id="UC" + "x" * 22,
        name="Other Channel",
        tags=["semis"],
    )

    # Mock the discovery client so we don't hit real YouTube
    captured_channel_ids: list[list[str]] = []

    def fake_get_channel_videos(cid, **kwargs):
        captured_channel_ids.append(cid)
        return []

    monkeypatch.setattr(
        server_module._yt, "get_channel_videos", fake_get_channel_videos
    )

    # Call without channel_ids — should iterate the registry's IDs
    server_module.discover_new_videos()
    assert sorted(captured_channel_ids) == sorted(
        ["UCkrwgzhIBKccuDsi_SvZtnQ", "UC" + "x" * 22]
    )

    # Call with tag filter — should only get the macro one
    captured_channel_ids.clear()
    server_module.discover_new_videos(tag="macro")
    assert captured_channel_ids == ["UCkrwgzhIBKccuDsi_SvZtnQ"]

    # Call with explicit channel_ids — should ignore registry
    captured_channel_ids.clear()
    server_module.discover_new_videos(
        channel_ids=["UCexplicitxxxxxxxxxxxxx"], tag="ignored"
    )
    assert captured_channel_ids == ["UCexplicitxxxxxxxxxxxxx"]


# ---------- TC-A2/A3 — duration filters in discover ----------


def test_TC_A2_short_video_skipped_with_reason(tmp_path, monkeypatch):
    """A video shorter than min_duration_seconds should land in `skipped`
    with reason `too_short_<N>s`, not in `new_videos`."""
    monkeypatch.setenv("GEMINI_API_KEY", "stub")
    monkeypatch.setenv("VIDEO_ANALYSIS_STATE_PATH", str(tmp_path / "video-state.json"))
    monkeypatch.setenv("VIDEO_ANALYSIS_CHANNELS_PATH", str(tmp_path / "channels.json"))
    import importlib
    import server as server_module
    importlib.reload(server_module)

    short_video = {
        "video_id": "shortvid001",
        "title": "Short clip",
        "channel_id": "UCkrwgzhIBKccuDsi_SvZtnQ",
        "channel_name": "Forward Guidance",
        "duration": 120,
        "published_at": "2026-05-01T13:00:21+00:00",
        "url": "https://www.youtube.com/watch?v=shortvid001",
        "excluded_from_analysis": True,
    }
    monkeypatch.setattr(
        server_module._yt, "get_channel_videos", lambda cid, **kw: [short_video]
    )

    out = server_module.discover_new_videos(
        channel_ids=["UCkrwgzhIBKccuDsi_SvZtnQ"], min_duration_seconds=600
    )
    assert out["new_videos"] == []
    assert len(out["skipped"]) == 1
    assert out["skipped"][0]["reason"].startswith("too_short_")
    assert out["skipped"][0]["video_id"] == "shortvid001"


def test_TC_A3_livestream_skipped_with_reason(tmp_path, monkeypatch):
    """duration=0 indicates a livestream; should be skipped, not analyzed."""
    monkeypatch.setenv("GEMINI_API_KEY", "stub")
    monkeypatch.setenv("VIDEO_ANALYSIS_STATE_PATH", str(tmp_path / "video-state.json"))
    monkeypatch.setenv("VIDEO_ANALYSIS_CHANNELS_PATH", str(tmp_path / "channels.json"))
    import importlib
    import server as server_module
    importlib.reload(server_module)

    livestream = {
        "video_id": "livestream1",
        "title": "Live now",
        "channel_id": "UCkrwgzhIBKccuDsi_SvZtnQ",
        "channel_name": "Forward Guidance",
        "duration": 0,
        "published_at": "2026-05-08T14:00:00+00:00",
        "url": "https://www.youtube.com/watch?v=livestream1",
        "excluded_from_analysis": True,
    }
    monkeypatch.setattr(
        server_module._yt, "get_channel_videos", lambda cid, **kw: [livestream]
    )

    out = server_module.discover_new_videos(channel_ids=["UCkrwgzhIBKccuDsi_SvZtnQ"])
    assert out["new_videos"] == []
    assert len(out["skipped"]) == 1
    assert out["skipped"][0]["reason"] == "livestream"
