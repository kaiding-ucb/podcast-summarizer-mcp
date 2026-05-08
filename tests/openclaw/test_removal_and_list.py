"""TC-R1..R4 — list and remove operations.

Some tests pre-seed the registry directly via the ChannelRegistry API
to avoid burning LLM cycles on the setup phase.
"""

import sys
from pathlib import Path

import pytest

# Ensure repo root on path so we can import the registry directly for seeding
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.openclaw.conftest import (
    CHANNELS_PATH,
    get_registry,
    send_message,
)
from tools.channel_registry import ChannelRegistry


def _seed_registry(rows: list[dict]) -> None:
    """Pre-seed the test profile's channels.json directly."""
    reg = ChannelRegistry(str(CHANNELS_PATH))
    for r in rows:
        reg.add(
            channel_id=r["channel_id"],
            name=r["name"],
            handle=r.get("handle"),
            tags=r.get("tags", []),
        )


# ---------- TC-R1 — list empty ----------


@pytest.mark.openclaw
def test_R1_list_empty(clean_registry: None):
    resp = send_message("What channels am I tracking?")
    text = resp.visible_text.lower()
    matched = (
        "no channel" in text
        or "not tracking" in text
        or ("aren't" in text and "tracking" in text)
        or ("not currently" in text and "channel" in text)
        or "empty" in text
        or "any channel" in text
    )
    assert matched, f"expected empty-tracking phrase, got: {resp.visible_text[:200]!r}"
    assert get_registry() == {}


# ---------- TC-R2 — list populated ----------


@pytest.mark.openclaw
def test_R2_list_populated(clean_registry: None):
    _seed_registry(
        [
            {
                "channel_id": "UCkrwgzhIBKccuDsi_SvZtnQ",
                "name": "Forward Guidance",
                "handle": "@ForwardGuidanceBW",
                "tags": ["macro"],
            },
            {
                "channel_id": "UC" + "x" * 22,
                "name": "Mock Macro Voices",
                "handle": "@mockmacrovoices",
                "tags": ["macro"],
            },
        ]
    )
    resp = send_message("What channels am I tracking?")
    text = resp.visible_text.lower()
    # Must reference both seeded channels
    assert "forward guidance" in text, f"FG missing: {resp.visible_text[:300]!r}"
    assert "macro voices" in text, f"Macro Voices missing: {resp.visible_text[:300]!r}"


# ---------- TC-R3 — remove by name ----------


@pytest.mark.openclaw
def test_R3_remove_by_name(clean_registry: None):
    _seed_registry(
        [
            {
                "channel_id": "UCkrwgzhIBKccuDsi_SvZtnQ",
                "name": "Forward Guidance",
                "handle": "@ForwardGuidanceBW",
            },
            {
                "channel_id": "UC" + "x" * 22,
                "name": "Mock Macro Voices",
                "handle": "@mockmacrovoices",
            },
        ]
    )
    resp = send_message("Remove Forward Guidance from my channels.")
    reg = get_registry()
    assert "UCkrwgzhIBKccuDsi_SvZtnQ" not in reg, (
        f"Forward Guidance still tracked; reg: {list(reg.keys())}; "
        f"reply: {resp.visible_text[:200]!r}"
    )
    # Other channel should remain
    assert "UC" + "x" * 22 in reg, "agent removed too much"


# ---------- TC-R4 — remove ambiguous ----------


@pytest.mark.openclaw
def test_R4_remove_ambiguous(clean_registry: None):
    """Two seeded channels both contain 'Macro' — agent should ask."""
    _seed_registry(
        [
            {
                "channel_id": "UC" + "a" * 22,
                "name": "Macro Voices",
                "handle": "@macrovoices",
            },
            {
                "channel_id": "UC" + "b" * 22,
                "name": "Macro Watch",
                "handle": "@macrowatch",
            },
        ]
    )
    resp = send_message("Remove Macro from my channels.")
    reg = get_registry()
    text = resp.visible_text.lower()
    if len(reg) == 2:
        # Asked for clarification — perfect
        assert "?" in resp.visible_text or "which" in text, (
            f"no clarification despite ambiguity: {resp.visible_text[:300]!r}"
        )
    elif len(reg) == 1:
        # Removed only ONE despite ambiguity — acceptable if agent picked
        # decisively (e.g. exact-match heuristic)
        pass
    else:
        pytest.fail(
            f"agent removed too much for an ambiguous 'Remove Macro' request: {reg}"
        )
