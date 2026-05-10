"""Disk-backed async job store for analyze_video_start / analyze_video_result.

Why disk-backed, not in-memory:
  Some MCP hosts spawn a fresh server process per session, request, or
  config-reload. A job created by `analyze_video_start` in process A may
  later be polled by `analyze_video_result` in process B. An in-memory
  dict loses jobs across processes — the symptom would be polls returning
  {status: "not_found"} for job_ids the host had just minted. Persisting
  to a single JSON file on disk (fcntl-locked, atomic writes) lets every
  MCP process see the same store.

Worker liveness:
  The actual Gemini call runs in a thread inside the process that handled
  `analyze_video_start`. If that process dies (host restart, OS kill,
  crash) the thread goes with it and the job would sit in "running"
  forever. A monitor thread per job writes `last_heartbeat_at` every
  HEARTBEAT_INTERVAL_SECONDS. When any poll sees status in {pending,
  running} with a heartbeat older than STALE_THRESHOLD_SECONDS, the job
  is flipped to "error" with error="worker process lost (no heartbeat)".
"""

from __future__ import annotations

import fcntl
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

_TERMINAL_STATUSES = frozenset({"done", "error"})

JOB_TTL_SECONDS = 6 * 60 * 60  # purge terminal jobs older than 6h on next create
HEARTBEAT_INTERVAL_SECONDS = 30  # monitor thread writes this often while running
STALE_THRESHOLD_SECONDS = 3 * HEARTBEAT_INTERVAL_SECONDS  # 90s without heartbeat → dead

POLL_INTERVAL_SECONDS = 1.0  # wait_for_terminal polls the file this often


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).timestamp()
    except (ValueError, TypeError):
        return None


class JobStore:
    VERSION = 1

    def __init__(self, path: str) -> None:
        self.path = Path(path).expanduser()
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._atomic_write({"version": self.VERSION, "jobs": {}})

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
            return {"version": self.VERSION, "jobs": {}}

    def _atomic_write(self, data: Dict[str, Any]) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def create(self, video_url: str) -> str:
        job_id = str(uuid.uuid4())
        now = _now_iso()

        def _do() -> None:
            data = self._read_unlocked()
            jobs = data.setdefault("jobs", {})
            self._prune_locked(jobs)
            jobs[job_id] = {
                "job_id": job_id,
                "video_url": video_url,
                "status": "pending",
                "created_at": now,
                "started_at": None,
                "finished_at": None,
                "last_heartbeat_at": now,
                "result": None,
                "error": None,
                "attempts": 0,
                "worker_pid": os.getpid(),
            }
            self._atomic_write(data)

        self._with_lock(_do)
        return job_id

    def update(self, job_id: str, **fields) -> None:
        def _do() -> None:
            data = self._read_unlocked()
            jobs = data.setdefault("jobs", {})
            job = jobs.get(job_id)
            if job is None:
                return
            job.update(fields)
            self._atomic_write(data)

        self._with_lock(_do)

    def heartbeat(self, job_id: str) -> None:
        self.update(job_id, last_heartbeat_at=_now_iso())

    def get(self, job_id: str) -> Optional[dict]:
        """Read + auto-mark-stale in one shot. Returns None for unknown job_ids."""
        def _do() -> Optional[dict]:
            data = self._read_unlocked()
            jobs = data.setdefault("jobs", {})
            job = jobs.get(job_id)
            if job is None:
                return None
            if self._mark_stale_locked(job):
                self._atomic_write(data)
            return dict(job)

        return self._with_lock(_do)

    def wait_for_terminal(self, job_id: str, wait_seconds: float) -> Optional[dict]:
        """Poll the file until job is terminal or wait_seconds elapses."""
        deadline = time.monotonic() + max(0.0, wait_seconds)
        while True:
            snap = self.get(job_id)
            if snap is None:
                return None
            if snap["status"] in _TERMINAL_STATUSES:
                return snap
            if time.monotonic() >= deadline:
                return snap
            time.sleep(POLL_INTERVAL_SECONDS)

    def _mark_stale_locked(self, job: Dict[str, Any]) -> bool:
        """If job is non-terminal and heartbeat is too old, flip to error. Caller holds lock."""
        if job["status"] in _TERMINAL_STATUSES:
            return False
        hb_ts = _parse_iso(job.get("last_heartbeat_at"))
        if hb_ts is None:
            return False
        if time.time() - hb_ts < STALE_THRESHOLD_SECONDS:
            return False
        job["status"] = "error"
        job["error"] = (
            f"worker process lost (no heartbeat for {int(time.time() - hb_ts)}s; "
            f"worker_pid={job.get('worker_pid')})"
        )
        job["finished_at"] = _now_iso()
        return True

    def _prune_locked(self, jobs: Dict[str, dict]) -> None:
        """Drop terminal jobs older than JOB_TTL_SECONDS. Caller holds lock."""
        cutoff = time.time() - JOB_TTL_SECONDS
        stale_ids = []
        for jid, job in list(jobs.items()):
            if job["status"] not in _TERMINAL_STATUSES:
                continue
            finished_ts = _parse_iso(job.get("finished_at"))
            if finished_ts is not None and finished_ts < cutoff:
                stale_ids.append(jid)
        for jid in stale_ids:
            jobs.pop(jid, None)


# Shared default path — under the same root as channels.json + video-state.json.
# Override with VIDEO_ANALYSIS_JOBS_PATH env var.
_DEFAULT_JOBS_PATH = os.environ.get(
    "VIDEO_ANALYSIS_JOBS_PATH",
    os.path.expanduser("~/.podcast-summarizer-mcp/jobs.json"),
)

store = JobStore(_DEFAULT_JOBS_PATH)


def start_heartbeat_monitor(job_id: str) -> threading.Event:
    """Spawn a daemon thread that writes heartbeats every HEARTBEAT_INTERVAL_SECONDS
    until the returned Event is set. Call event.set() when the worker finishes to stop.
    """
    stop = threading.Event()

    def _monitor() -> None:
        while not stop.wait(HEARTBEAT_INTERVAL_SECONDS):
            try:
                store.heartbeat(job_id)
            except Exception:
                return

    threading.Thread(target=_monitor, daemon=True, name=f"hb-{job_id[:8]}").start()
    return stop
