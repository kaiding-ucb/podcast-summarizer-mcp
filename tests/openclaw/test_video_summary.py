"""TC-A1 — full Gemini analysis through the OpenClaw agent.

This is the most expensive test in the entire suite. Real Gemini call
on a 30–60 min Forward Guidance podcast costs ~$0.10–0.30 and takes
3–10 minutes. Gated behind RUN_OPENCLAW_TESTS=1 + RUN_EXPENSIVE_TESTS=1.

The Gemini analysis itself is also covered offline at the gemini_client
level by tests/unit/test_video_summary.py (TC-A5 retry + TC-A7 vaneck
filter). This OpenClaw test exists to prove the full chain — agent
calls analyze_video_start, polls analyze_video_result, returns the
finished summary in chat.
"""

import os

import pytest

from tests.openclaw.conftest import send_message

VIDEO_URL = "https://www.youtube.com/watch?v=MO9ZTZPUwXY"
VIDEO_ID = "MO9ZTZPUwXY"


@pytest.mark.openclaw
def test_TC_A1_full_summary_through_agent(clean_registry: None):
    if os.environ.get("RUN_EXPENSIVE_TESTS") != "1":
        pytest.skip("set RUN_EXPENSIVE_TESTS=1 to run the paid Gemini test")

    resp = send_message(
        f"Summarize this YouTube video for me: {VIDEO_URL}. "
        "Wait for it to finish and report what it says about macro outlook."
    )
    text = resp.visible_text
    # Loose checks — LLM phrasing varies, but content must reflect actual analysis
    assert len(text) > 200, f"summary too short: {text[:200]!r}"
    # The video is "Passive Easing Is Fueling The Next Inflation Wave" —
    # any plausible summary should mention macro / inflation / market topics
    lowered = text.lower()
    has_topic_signal = any(
        kw in lowered
        for kw in ("inflation", "macro", "fed", "market", "podcast", "easing")
    )
    assert has_topic_signal, f"summary doesn't reflect video topic: {text[:300]!r}"
