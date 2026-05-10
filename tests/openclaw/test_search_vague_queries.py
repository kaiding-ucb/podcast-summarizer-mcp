"""TC-V1..V8 — vague natural-language queries.

Pass criteria are about *restraint*: the agent must NOT silently add
fabricated channels. Either it presents candidates and asks, or it
asks a clarifying question.

Per TEST_PLAN.md.
"""

import pytest

from tests.openclaw.conftest import (
    get_registry,
    has_candidate_listing,
    has_clarifying_question,
    send_message,
)


# ---------- TC-V1 — topic only ----------


@pytest.mark.openclaw
def test_V1_topic_only_no_add(clean_registry: None):
    resp = send_message(
        "Add a good YouTube channel about advanced semiconductor packaging."
    )
    reg = get_registry()
    assert len(reg) == 0, (
        f"expected NO adds for a topic-only query; "
        f"got {list(reg.values())}; reply: {resp.visible_text[:300]!r}"
    )
    text = resp.visible_text
    # Must either ask a question OR present candidates
    assert has_clarifying_question(text) or has_candidate_listing(text), (
        f"expected clarification or candidates; reply: {text[:300]!r}"
    )


# ---------- TC-V2 — misremembered name ----------


@pytest.mark.openclaw
def test_V2_misremembered_name(clean_registry: None):
    resp = send_message(
        "Add that podcast — Forward Guidance? Or maybe Forward Markets?"
    )
    reg = get_registry()
    text = resp.visible_text.lower()
    # Acceptable: agent didn't add anything yet (asked for clarification)
    # OR agent added the one that exists (Forward Guidance) only.
    if len(reg) == 0:
        assert has_clarifying_question(resp.visible_text) or has_candidate_listing(
            resp.visible_text
        ), f"empty registry but no clarification: {text[:300]!r}"
    elif len(reg) == 1:
        only = list(reg.values())[0]
        assert "forward guidance" in (only.get("name", "") or "").lower(), (
            f"registry has unexpected single add: {only}"
        )
    else:
        pytest.fail(f"agent added too many channels for an ambiguous query: {reg}")


# ---------- TC-V3 — too generic ----------


@pytest.mark.openclaw
def test_V3_too_generic_should_clarify(clean_registry: None):
    resp = send_message("Add a good investing podcast.")
    reg = get_registry()
    assert len(reg) == 0, (
        f"too-generic query should not auto-add; got {list(reg.values())}; "
        f"reply: {resp.visible_text[:300]!r}"
    )
    # Either ask a question, OR present multiple candidates so user picks
    assert has_clarifying_question(resp.visible_text) or has_candidate_listing(
        resp.visible_text
    ), f"reply lacks question/candidates: {resp.visible_text[:300]!r}"


# ---------- TC-V4 — disambiguation needed ----------


@pytest.mark.openclaw
def test_V4_disambiguation(clean_registry: None):
    """'Add Joe Rogan' should surface multiple channels (JRE, JRE Clips,
    Joe Rogan Podcast, etc.). Agent should present options or ask."""
    resp = send_message("Add Joe Rogan.")
    reg = get_registry()
    text = resp.visible_text
    if len(reg) == 0:
        # Asked / presented candidates — good
        assert has_clarifying_question(text) or has_candidate_listing(text), (
            f"no add but no clarification: {text[:300]!r}"
        )
    elif len(reg) == 1:
        # Agent picked one confidently. Acceptable IF the channel actually
        # belongs to Joe Rogan — match on name OR handle (the canonical
        # JRE channel is named "PowerfulJRE" with handle @joerogan).
        only = list(reg.values())[0]
        ident = ((only.get("name", "") or "") + " " + (only.get("handle", "") or "")).lower()
        assert "rogan" in ident or "jre" in ident, (
            f"single add doesn't match query: {only}"
        )
    else:
        # Multi-add for "Joe Rogan" is too eager — fail.
        pytest.fail(f"agent added too many channels: {reg}")


# ---------- TC-V5 — cancel mid-flow (multi-turn) ----------


@pytest.mark.openclaw
def test_V5_cancel_mid_flow(clean_registry: None):
    """Two-turn: ask to add → cancel. Registry must remain empty."""
    import uuid

    sid = f"test_v5_{uuid.uuid4().hex[:8]}"

    # Turn 1 — propose adding something ambiguous so agent presents options
    resp1 = send_message(
        "Add a good macro investing channel.", session_id=sid
    )
    # Turn 2 — cancel
    resp2 = send_message("Actually never mind, don't add anything.", session_id=sid)
    reg = get_registry()
    assert reg == {}, (
        f"registry not empty after cancel: {reg}; "
        f"turn1: {resp1.visible_text[:200]!r}; turn2: {resp2.visible_text[:200]!r}"
    )


# ---------- TC-V6 — verify don't fabricate ----------


@pytest.mark.openclaw
def test_V6_verify_dont_fabricate(clean_registry: None):
    resp = send_message("Add Bloomberg's Odd Lots podcast.")
    reg = get_registry()
    # Whatever ends up in the registry, the channel_id must look real
    # (UC + ~22 chars) AND the agent must have actually resolved it,
    # not invented one. We can't verify the second from logs alone, so
    # we assert structure and leave content for manual review.
    for cid, rec in reg.items():
        assert cid.startswith("UC"), f"fabricated channel_id: {cid}"
        assert len(cid) >= 22, f"channel_id too short to be real: {cid}"


# ---------- TC-V7 — misspelling tolerance ----------


@pytest.mark.openclaw
def test_V7_misspelling_tolerance(clean_registry: None):
    """Typo'd name: agent should still find Forward Guidance via search.
    Either adds it (acceptable — clear what user meant) or asks
    "did you mean Forward Guidance?" before adding."""
    resp = send_message("Add Ferward Guidence podcast.")
    reg = get_registry()
    text = resp.visible_text.lower()

    if len(reg) == 1:
        # Agent recognized the typo and added the right one
        only = list(reg.values())[0]
        assert "forward guidance" in (only.get("name", "") or "").lower(), (
            f"single add isn't Forward Guidance: {only}; reply: {text[:200]!r}"
        )
    else:
        # 0 adds → agent asked for confirmation; reply should mention
        # the corrected name OR ask for clarification
        assert (
            "forward guidance" in text
            or has_clarifying_question(resp.visible_text)
        ), f"no add and no recognition of typo: {text[:300]!r}"


# ---------- TC-V8 — non-English ----------


@pytest.mark.openclaw
def test_V8_non_english(clean_registry: None):
    """Non-ASCII channel name: shouldn't crash. Agent either resolves
    via search or asks for the URL/handle to disambiguate."""
    resp = send_message("Add 中国财经报道")
    reg = get_registry()
    # Whatever happens, no crash and any added channel_id is well-formed
    for cid in reg:
        assert cid.startswith("UC"), f"fabricated channel_id: {cid}"
    # Visible text must exist (non-empty response)
    assert resp.visible_text.strip(), "agent returned empty response"
