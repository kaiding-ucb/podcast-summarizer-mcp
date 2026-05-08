"""Tests for tools/gemini_client.py — URL rewriting and timestamp validation.

The actual Gemini API call is not exercised here (covered by manual smoke tests).
"""

from tools.gemini_client import rewrite_video_urls, validate_timestamps


def test_rewrite_video_urls_replaces_hallucinated_hash():
    text = "See ([12:08](https://www.youtube.com/watch?v=R9_W7fXn0fQ&t=728s)) for context."
    out = rewrite_video_urls(text, "UweigmKvoLs")
    assert "v=UweigmKvoLs&t=728s" in out
    assert "R9_W7fXn0fQ" not in out


def test_rewrite_video_urls_multiple_occurrences():
    text = (
        "(00:40) https://www.youtube.com/watch?v=BAD1&t=40s "
        "and (1:20) https://www.youtube.com/watch?v=BAD2&t=80s"
    )
    out = rewrite_video_urls(text, "GOOD")
    assert "v=GOOD&t=40s" in out
    assert "v=GOOD&t=80s" in out
    assert "BAD1" not in out and "BAD2" not in out


def test_rewrite_video_urls_swapped_param_order():
    text = "See ([19:35](https://www.youtube.com/watch?t=1175&v=R3G_6rO_gC8)) here."
    out = rewrite_video_urls(text, "GOOD")
    assert "v=GOOD" in out
    assert "R3G_6rO_gC8" not in out


def test_rewrite_video_urls_no_url_text_unchanged():
    text = "No URLs here, just (12:34) timestamp."
    assert rewrite_video_urls(text, "ANY") == text


def test_validate_timestamps_all_within_bounds():
    text = "Quotes at (5:00) and (15:30) and (40:12)."
    assert validate_timestamps(text, video_duration_seconds=3600) is True


def test_validate_timestamps_one_over_bounds():
    text = "Quote at (5:00) and (61:00) — second is over a 1hr video."
    assert validate_timestamps(text, video_duration_seconds=3600) is False


def test_validate_timestamps_zero_duration_returns_false():
    text = "Quote at (5:00)."
    assert validate_timestamps(text, video_duration_seconds=0) is False


def test_validate_timestamps_no_timestamps_returns_true():
    assert validate_timestamps("No timestamps here.", video_duration_seconds=3600) is True
