"""TC-L1..L2 — lookup-only queries must NOT mutate the registry.

Per TEST_PLAN.md.
"""

import pytest

from tests.openclaw.conftest import get_registry, send_message


@pytest.mark.openclaw
def test_L1_does_x_have_a_channel(clean_registry: None):
    resp = send_message("Does Andrej Karpathy have a YouTube channel?")
    assert get_registry() == {}, (
        f"lookup-only must not mutate; reply: {resp.visible_text[:200]!r}"
    )
    text = resp.visible_text.lower()
    # Reply should reference the question topic
    assert "karpathy" in text or "channel" in text, (
        f"reply ignores question: {resp.visible_text[:200]!r}"
    )


@pytest.mark.openclaw
def test_L2_what_does_x_cover(clean_registry: None):
    """Pre-condition: empty registry. Asking 'what does X cover' is a
    pure search query — must not add."""
    resp = send_message("What does the Forward Guidance YouTube channel cover lately?")
    reg = get_registry()
    assert reg == {}, (
        f"lookup-only must not mutate; got {reg}; "
        f"reply: {resp.visible_text[:200]!r}"
    )
