"""Tests for tools/state.py — atomic writes, locking, snapshots."""

import json
import os

from tools.state import StateStore


def test_creates_file_on_init(tmp_path):
    p = tmp_path / "state.json"
    StateStore(str(p))
    assert p.exists()
    data = json.loads(p.read_text())
    assert data == {"version": 1, "channels": {}}


def test_get_last_video_id_unknown_channel(tmp_path):
    s = StateStore(str(tmp_path / "state.json"))
    assert s.get_last_video_id("UCxxx") is None


def test_update_and_get(tmp_path):
    s = StateStore(str(tmp_path / "state.json"))
    s.update_channel("UC1", "vid_a", "2026-04-10T12:00:00Z")
    assert s.get_last_video_id("UC1") == "vid_a"
    s.update_channel("UC1", "vid_b", "2026-04-11T12:00:00Z")
    assert s.get_last_video_id("UC1") == "vid_b"


def test_atomic_write_no_partial_file(tmp_path):
    p = tmp_path / "state.json"
    s = StateStore(str(p))
    s.update_channel("UC1", "vid_a", "2026-04-10T12:00:00Z")
    on_disk = json.loads(p.read_text())
    assert "channels" in on_disk
    assert on_disk["channels"]["UC1"]["last_video_id"] == "vid_a"
    assert "last_analyzed_at" in on_disk["channels"]["UC1"]


def test_snapshot_filtering(tmp_path):
    s = StateStore(str(tmp_path / "state.json"))
    s.update_channel("UC1", "v1", "2026-04-10T12:00:00Z")
    s.update_channel("UC2", "v2", "2026-04-10T13:00:00Z")
    all_rows = s.snapshot()
    assert len(all_rows) == 2
    one = s.snapshot(["UC1"])
    assert len(one) == 1 and one[0]["channel_id"] == "UC1"


def test_no_lock_file_lingers_in_state_dir(tmp_path):
    p = tmp_path / "state.json"
    s = StateStore(str(p))
    s.update_channel("UC1", "v1", "2026-04-10T12:00:00Z")
    assert (tmp_path / "state.json.lock").exists()
    assert json.loads(p.read_text())["channels"]["UC1"]["last_video_id"] == "v1"


def test_state_path_env_override(tmp_path, monkeypatch):
    custom = tmp_path / "nested" / "dir" / "custom-state.json"
    s = StateStore(str(custom))
    assert custom.exists()
    s.update_channel("UC1", "v1", "2026-04-10T12:00:00Z")
    assert s.get_last_video_id("UC1") == "v1"
