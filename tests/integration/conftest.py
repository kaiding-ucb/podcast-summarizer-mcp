"""Integration tier — hits real YouTube (and optionally Gemini).

Gated behind RUN_EXPENSIVE_TESTS=1. CI default skips these.

Constants below pin the fixture targets to Forward Guidance, which is a
stable enough channel (>100k subs, weekly cadence) for tests to rely on.
If the channel ever disappears, swap CHANNEL_ID + VIDEO_URL for another
known-stable target.
"""

import os

import pytest

# Forward Guidance — Blockworks-published macro podcast.
# The bare handle @ForwardGuidance is taken by an unrelated user; the
# canonical channel uses @ForwardGuidanceBW (BW = Blockworks).
CHANNEL_ID = "UCkrwgzhIBKccuDsi_SvZtnQ"
CHANNEL_HANDLE = "@ForwardGuidanceBW"
VIDEO_URL = "https://www.youtube.com/watch?v=MO9ZTZPUwXY"
VIDEO_ID = "MO9ZTZPUwXY"


def pytest_collection_modifyitems(config, items):
    """Auto-skip expensive tests unless RUN_EXPENSIVE_TESTS=1."""
    if os.environ.get("RUN_EXPENSIVE_TESTS") == "1":
        return
    skip_marker = pytest.mark.skip(
        reason="set RUN_EXPENSIVE_TESTS=1 to run real-API integration tests"
    )
    for item in items:
        if "expensive" in item.keywords:
            item.add_marker(skip_marker)
