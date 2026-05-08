"""Failing-first unit tests for tools/channel_registry.

The registry is an MCP-owned JSON file mapping channel_id -> metadata
(name, handle, tags, added_at). Tools that mutate it: add, remove, list.
Mirrors the atomic-write + fcntl-lock pattern in tools/state.py.
"""

import json
from pathlib import Path

import pytest

from tools.channel_registry import ChannelRegistry


@pytest.fixture
def registry_path(tmp_path: Path) -> Path:
    return tmp_path / "channels.json"


@pytest.fixture
def registry(registry_path: Path) -> ChannelRegistry:
    return ChannelRegistry(str(registry_path))


# ---------- empty / init ----------


def test_new_registry_starts_empty(registry: ChannelRegistry):
    assert registry.list_channels() == []


def test_new_registry_creates_file_on_init(registry_path: Path):
    ChannelRegistry(str(registry_path))
    assert registry_path.exists()
    data = json.loads(registry_path.read_text())
    assert "channels" in data


# ---------- add ----------


def test_add_channel_persists_full_record(registry: ChannelRegistry, registry_path: Path):
    registry.add(
        channel_id="UCkrwgzhIBKccuDsi_SvZtnQ",
        name="Forward Guidance",
        handle="@ForwardGuidance",
        tags=["macro", "podcast"],
    )
    data = json.loads(registry_path.read_text())
    rec = data["channels"]["UCkrwgzhIBKccuDsi_SvZtnQ"]
    assert rec["name"] == "Forward Guidance"
    assert rec["handle"] == "@ForwardGuidance"
    assert rec["tags"] == ["macro", "podcast"]
    assert "added_at" in rec  # ISO timestamp


def test_add_is_idempotent_and_updates_metadata(registry: ChannelRegistry):
    cid = "UCkrwgzhIBKccuDsi_SvZtnQ"
    registry.add(channel_id=cid, name="Forward Guidance", tags=["macro"])
    registry.add(channel_id=cid, name="Forward Guidance", tags=["macro", "podcast"])
    listed = registry.list_channels()
    assert len(listed) == 1
    assert listed[0]["tags"] == ["macro", "podcast"]


def test_add_rejects_invalid_channel_id(registry: ChannelRegistry):
    with pytest.raises(ValueError):
        registry.add(channel_id="@ForwardGuidance", name="x")
    with pytest.raises(ValueError):
        registry.add(channel_id="not a channel id", name="x")


def test_add_with_no_tags_defaults_to_empty_list(registry: ChannelRegistry):
    cid = "UCkrwgzhIBKccuDsi_SvZtnQ"
    registry.add(channel_id=cid, name="Forward Guidance")
    listed = registry.list_channels()
    assert listed[0]["tags"] == []


# ---------- remove ----------


def test_remove_channel_persists(registry: ChannelRegistry):
    cid = "UCkrwgzhIBKccuDsi_SvZtnQ"
    registry.add(channel_id=cid, name="Forward Guidance")
    assert len(registry.list_channels()) == 1
    registry.remove(cid)
    assert registry.list_channels() == []


def test_remove_nonexistent_channel_is_noop(registry: ChannelRegistry):
    # Should not raise
    registry.remove("UCdoesnotexistxxxxxxxxx")
    assert registry.list_channels() == []


# ---------- list with tag filter ----------


def test_list_returns_all_when_no_tag(registry: ChannelRegistry):
    registry.add(channel_id="UCkrwgzhIBKccuDsi_SvZtnQ", name="FG", tags=["macro"])
    registry.add(channel_id="UC" + "x" * 22, name="Other", tags=["semis"])
    listed = registry.list_channels()
    assert len(listed) == 2


def test_list_filters_by_tag(registry: ChannelRegistry):
    registry.add(channel_id="UCkrwgzhIBKccuDsi_SvZtnQ", name="FG", tags=["macro"])
    registry.add(channel_id="UC" + "x" * 22, name="Other", tags=["semis"])
    registry.add(channel_id="UC" + "y" * 22, name="Both", tags=["macro", "semis"])

    macro_only = registry.list_channels(tag="macro")
    assert {c["channel_id"] for c in macro_only} == {
        "UCkrwgzhIBKccuDsi_SvZtnQ",
        "UC" + "y" * 22,
    }

    semis_only = registry.list_channels(tag="semis")
    assert {c["channel_id"] for c in semis_only} == {
        "UC" + "x" * 22,
        "UC" + "y" * 22,
    }


def test_list_empty_when_tag_unmatched(registry: ChannelRegistry):
    registry.add(channel_id="UCkrwgzhIBKccuDsi_SvZtnQ", name="FG", tags=["macro"])
    assert registry.list_channels(tag="energy") == []


# ---------- get_channel_ids helper ----------


def test_get_channel_ids_returns_just_ids(registry: ChannelRegistry):
    registry.add(channel_id="UCkrwgzhIBKccuDsi_SvZtnQ", name="FG", tags=["macro"])
    registry.add(channel_id="UC" + "x" * 22, name="Other", tags=["semis"])
    ids = registry.get_channel_ids()
    assert isinstance(ids, list)
    assert set(ids) == {"UCkrwgzhIBKccuDsi_SvZtnQ", "UC" + "x" * 22}


def test_get_channel_ids_filters_by_tag(registry: ChannelRegistry):
    registry.add(channel_id="UCkrwgzhIBKccuDsi_SvZtnQ", name="FG", tags=["macro"])
    registry.add(channel_id="UC" + "x" * 22, name="Other", tags=["semis"])
    assert registry.get_channel_ids(tag="macro") == ["UCkrwgzhIBKccuDsi_SvZtnQ"]


# ---------- crash safety ----------


def test_corrupt_file_falls_back_to_empty(tmp_path: Path):
    p = tmp_path / "corrupt.json"
    p.write_text("{not valid json")
    reg = ChannelRegistry(str(p))
    # Init should heal the file rather than raise
    assert reg.list_channels() == []
    # And the file should now be valid JSON
    json.loads(p.read_text())


def test_atomic_write_leaves_no_tmp_files(registry_path: Path):
    reg = ChannelRegistry(str(registry_path))
    reg.add(channel_id="UCkrwgzhIBKccuDsi_SvZtnQ", name="FG")
    leftovers = list(registry_path.parent.glob("*.tmp"))
    assert leftovers == []
