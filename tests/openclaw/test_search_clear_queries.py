"""TC-S1..S6 — clear natural-language channel queries should add the
correct channel to the registry without unnecessary clarification.

Per TEST_PLAN.md. Each test starts with an empty registry and asserts
on the registry's final state.

Note on LLM nondeterminism: assertions use *what* changed in the
registry rather than *how* the agent phrased its reply. Channel names
are matched case-insensitively and tolerant of subtitle suffixes.
"""

import pytest

from tests.openclaw.conftest import get_registry, send_message

# Forward Guidance ground truth (verified via integration tests).
FG_CHANNEL_ID = "UCkrwgzhIBKccuDsi_SvZtnQ"
FG_HANDLE = "@ForwardGuidanceBW"


def _registry_has(channel_name_substring: str) -> bool:
    for cid, rec in get_registry().items():
        if channel_name_substring.lower() in (rec.get("name", "") or "").lower():
            return True
    return False


# ---------- TC-S1 — exact channel name ----------


@pytest.mark.openclaw
def test_S1_add_by_exact_name(clean_registry: None):
    resp = send_message("Add Forward Guidance to my channel list.")
    reg = get_registry()
    assert len(reg) >= 1, (
        f"expected ≥1 entry; got {list(reg.keys())}; reply: {resp.visible_text[:200]!r}"
    )
    assert _registry_has("Forward Guidance"), (
        f"no Forward Guidance entry; registry: {reg}"
    )
    # All channel_ids must look like real UC ids — guards against fabrication.
    for cid in reg:
        assert cid.startswith("UC"), f"fabricated channel_id: {cid}"


# ---------- TC-S2 — direct URL ----------


@pytest.mark.openclaw
def test_S2_add_by_direct_url(clean_registry: None):
    resp = send_message(
        "Add https://www.youtube.com/@ForwardGuidanceBW to channels."
    )
    reg = get_registry()
    assert FG_CHANNEL_ID in reg, (
        f"expected {FG_CHANNEL_ID} in registry; got {list(reg.keys())}; "
        f"reply: {resp.visible_text[:200]!r}"
    )


# ---------- TC-S3 — bare handle ----------


@pytest.mark.openclaw
def test_S3_add_by_bare_handle(clean_registry: None):
    resp = send_message("Add @ForwardGuidanceBW to my channels.")
    reg = get_registry()
    assert FG_CHANNEL_ID in reg, (
        f"expected {FG_CHANNEL_ID} in registry; got {list(reg.keys())}; "
        f"reply: {resp.visible_text[:200]!r}"
    )


# ---------- TC-S4 — multi-word channel name ----------


@pytest.mark.openclaw
def test_S4_add_multiword_name(clean_registry: None):
    resp = send_message("Add the All-In podcast YouTube channel.")
    reg = get_registry()
    assert len(reg) >= 1, (
        f"expected ≥1 entry; reply: {resp.visible_text[:200]!r}"
    )
    # Tolerant: "All In" with or without hyphen
    has_all_in = any(
        "all-in" in (r.get("name", "") or "").lower()
        or "all in" in (r.get("name", "") or "").lower()
        for r in reg.values()
    )
    assert has_all_in, f"no All-In entry; registry: {reg}"


# ---------- TC-S5 — multi-add in one turn ----------


@pytest.mark.openclaw
def test_S5_multi_add_in_one_turn(clean_registry: None):
    resp = send_message("Add Forward Guidance and Macro Voices to my channels.")
    reg = get_registry()
    has_fg = _registry_has("Forward Guidance")
    has_mv = _registry_has("Macro Voices")
    assert has_fg and has_mv, (
        f"expected both Forward Guidance and Macro Voices; "
        f"got fg={has_fg}, mv={has_mv}; registry: {reg}; "
        f"reply: {resp.visible_text[:300]!r}"
    )


# ---------- TC-S6 — person resolves to their show (interpretive) ----------


@pytest.mark.openclaw
def test_S6_person_to_show_no_blind_add(clean_registry: None):
    """'Erik Townsend' should NOT be auto-added — it's a person, not a
    channel name. The agent should either find Macro Voices (his show)
    and ask for confirmation, or refuse to fabricate.
    """
    resp = send_message("Add Erik Townsend to my channels.")
    reg = get_registry()
    text = resp.visible_text.lower()
    # Two acceptable behaviors:
    #   (a) Agent surfaces Macro Voices as a candidate; registry empty.
    #   (b) Agent adds Macro Voices (one entry) after recognizing the
    #       host->show mapping confidently.
    # Either way, no entry whose name is literally "Erik Townsend"
    # (that channel doesn't exist on YouTube).
    for cid, rec in reg.items():
        name = (rec.get("name", "") or "").lower()
        assert "erik townsend" != name.strip(), (
            f"agent fabricated an Erik Townsend channel: {rec}"
        )
    # The reply should reference the host or show explicitly so the
    # user can confirm — covers both (a) and (b).
    assert (
        "macro voices" in text
        or "erik" in text  # acknowledges the person
    ), f"reply doesn't mention Erik or Macro Voices: {resp.visible_text[:300]!r}"
