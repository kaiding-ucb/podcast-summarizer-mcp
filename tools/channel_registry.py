"""MCP-owned channel registry.

A JSON file mapping channel_id -> {name, handle, tags, added_at} so that
the MCP itself remembers which channels the user is tracking. This lets
discover_new_videos default to the registry instead of requiring the
caller to pass channel_ids on every call.

Storage shape:
{
  "version": 1,
  "channels": {
    "UCkrwgzhIBKccuDsi_SvZtnQ": {
      "name": "Forward Guidance",
      "handle": "@ForwardGuidance",
      "tags": ["macro"],
      "added_at": "2026-05-08T..."
    },
    ...
  }
}

Concurrency: same atomic-write + fcntl-lock pattern as tools/state.py
so multiple MCP-server processes can mutate it without races.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{20,24}$")


class ChannelRegistry:
    VERSION = 1

    def __init__(self, path: str):
        self.path = Path(path).expanduser()
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._atomic_write({"version": self.VERSION, "channels": {}})
        else:
            # Heal a corrupt file rather than crashing on init
            try:
                self._read_unlocked()
            except json.JSONDecodeError:
                self._atomic_write({"version": self.VERSION, "channels": {}})

    # ----- internals -----

    def _with_lock(self, fn: Callable[[], Any]) -> Any:
        with open(self.lock_path, "w") as lf:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            try:
                return fn()
            finally:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)

    def _read_unlocked(self) -> Dict[str, Any]:
        with open(self.path, "r") as f:
            data = json.load(f)
        if "channels" not in data:
            data["channels"] = {}
        return data

    def _atomic_write(self, data: Dict[str, Any]) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    # ----- public API -----

    def add(
        self,
        channel_id: str,
        name: str,
        handle: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Add (or update) a channel. Returns the stored record."""
        if not _CHANNEL_ID_RE.match(channel_id):
            raise ValueError(
                f"channel_id must look like 'UCxxxxxxxxxxxxxxxxxxxxxx', got: {channel_id!r}"
            )
        record = {
            "name": name,
            "handle": handle,
            "tags": list(tags) if tags else [],
            "added_at": datetime.now(timezone.utc).isoformat(),
        }

        def _do() -> Dict[str, Any]:
            data = self._safe_read()
            existing = data["channels"].get(channel_id)
            if existing:
                # Preserve original added_at on update
                record["added_at"] = existing.get("added_at", record["added_at"])
            data["channels"][channel_id] = record
            self._atomic_write(data)
            return record

        return self._with_lock(_do)

    def remove(self, channel_id: str) -> bool:
        """Remove a channel. Returns True if it existed, False otherwise."""

        def _do() -> bool:
            data = self._safe_read()
            if channel_id in data["channels"]:
                del data["channels"][channel_id]
                self._atomic_write(data)
                return True
            return False

        return self._with_lock(_do)

    def list_channels(self, tag: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return all channels, optionally filtered by tag."""
        data = self._safe_read()
        out: List[Dict[str, Any]] = []
        for cid, rec in data["channels"].items():
            if tag is not None and tag not in (rec.get("tags") or []):
                continue
            out.append({"channel_id": cid, **rec})
        out.sort(key=lambda r: r.get("name", "").lower())
        return out

    def get_channel_ids(self, tag: Optional[str] = None) -> List[str]:
        return [c["channel_id"] for c in self.list_channels(tag=tag)]

    def get(self, channel_id: str) -> Optional[Dict[str, Any]]:
        rec = self._safe_read()["channels"].get(channel_id)
        if rec is None:
            return None
        return {"channel_id": channel_id, **rec}

    # ----- safe read with self-healing for corrupt files -----

    def _safe_read(self) -> Dict[str, Any]:
        try:
            return self._read_unlocked()
        except (FileNotFoundError, json.JSONDecodeError):
            return {"version": self.VERSION, "channels": {}}
