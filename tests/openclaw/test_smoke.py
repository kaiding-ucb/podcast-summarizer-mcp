"""One-shot smoke test for the OpenClaw test profile.

Verifies the plumbing — gateway running, MCP server connecting, agent
producing a response — before we run the full TC suite.
"""

import pytest

from tests.openclaw.conftest import (
    AgentResponse,
    get_registry,
    send_message,
)


@pytest.mark.openclaw
def test_smoke_agent_responds(clean_registry: None):
    resp = send_message("Hi, who are you in one sentence?")
    assert isinstance(resp, AgentResponse)
    assert len(resp.visible_text) > 0
    assert get_registry() == {}  # smoke shouldn't mutate


@pytest.mark.openclaw
def test_smoke_list_empty_registry(clean_registry: None):
    """Asking what channels are tracked when none are should produce a
    response that mentions "no channels" or "empty" — and not mutate."""
    resp = send_message("What channels am I currently tracking?")
    text = resp.visible_text.lower()
    # Accept any phrasing that conveys an empty registry. Broad on purpose
    # since LLMs vary their wording across runs.
    matched = (
        "no channel" in text
        or "not tracking" in text
        or ("aren't" in text and "tracking" in text)
        or ("not currently" in text and "channel" in text)
        or "any channel" in text
        or "empty" in text
    )
    assert matched, f"Expected an empty-registry phrase, got: {resp.visible_text[:200]!r}"
    assert get_registry() == {}
