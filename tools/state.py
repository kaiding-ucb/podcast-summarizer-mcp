"""Persistent state for last-seen-video-per-channel.

JSON file with a sidecar .lock file for cross-process serialization.
Writes are atomic via tmp + os.replace so a crash mid-write cannot corrupt the file.
"""

import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


class StateStore:
    VERSION = 1

    def __init__(self, path: str):
        self.path = Path(path).expanduser()
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._atomic_write({"version": self.VERSION, "channels": {}})

    def _with_lock(self, fn: Callable[[], Any]) -> Any:
        with open(self.lock_path, "w") as lf:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            try:
                return fn()
            finally:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)

    def _read_unlocked(self) -> Dict[str, Any]:
        try:
            with open(self.path, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"version": self.VERSION, "channels": {}}

    def _atomic_write(self, data: Dict[str, Any]) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def get_last_video_id(self, channel_id: str) -> Optional[str]:
        return self._read_unlocked().get("channels", {}).get(channel_id, {}).get("last_video_id")

    def update_channel(
        self,
        channel_id: str,
        video_id: str,
        published_at: str,
        analyzed_at: Optional[str] = None,
    ) -> None:
        def _do() -> None:
            data = self._read_unlocked()
            data.setdefault("channels", {})[channel_id] = {
                "last_video_id": video_id,
                "last_published_at": published_at,
                "last_analyzed_at": analyzed_at or datetime.now(timezone.utc).isoformat(),
            }
            self._atomic_write(data)

        self._with_lock(_do)

    def snapshot(self, channel_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        data = self._read_unlocked()
        channels = data.get("channels", {})
        if channel_ids is not None:
            wanted = set(channel_ids)
            return [
                {"channel_id": cid, **channels.get(cid, {})}
                for cid in channel_ids
                if cid in wanted
            ]
        return [{"channel_id": cid, **state} for cid, state in channels.items()]
