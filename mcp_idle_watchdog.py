"""Shared MCP idle watchdog — auto-exits process after idle, with awareness
of in-flight tool calls.

Why: When the OpenClaw client disconnects (cron job ends, TUI closes, session
times out), the MCP server process can be orphaned and accumulate forever.
This module installs a background thread that exits the process via
os._exit(0) when:
  - No stdin activity AND no in-flight requests for `idle_timeout_seconds`
    (default 5 min), OR
  - A single request has been running longer than `request_max_seconds`
    (default 10 min) — guards against hung Gemini/IB/HTTP calls leaking the
    server forever.

Tool calls are tracked via the `track_request` decorator (or context manager).
Without it, the watchdog falls back to stdin-only tracking — which can kill
the server mid-call if a tool runs longer than the idle timeout.

Usage:
    from mcp_idle_watchdog import install_idle_watchdog, track_request

    install_idle_watchdog(
        server_name="video-analysis",
        idle_timeout_seconds=300,
        request_max_seconds=1200,  # video calls can take a while
    )

    @mcp.tool()
    @track_request
    def analyze_video(video_url: str) -> dict:
        ...

    mcp.run()

Logs every start, idle-exit, and hung-request-exit to
/home/kaiding314159/openclaw-mcp-logs/idle-watchdog.log (or
MCP_IDLE_WATCHDOG_LOG env override). Failures to write to the log are
silent — never crash the MCP just because logging is broken.
"""
import os
import sys
import time
import threading
import functools
from datetime import datetime, timezone

DEFAULT_LOG_PATH = "/home/kaiding314159/openclaw-mcp-logs/idle-watchdog.log"


class _State:
    """Process-wide watchdog state. Single instance shared across the module."""

    def __init__(self):
        self.last_activity = time.time()
        self.active_requests = {}  # request_id -> (start_ts, tool_name)
        self.lock = threading.Lock()
        self._next_id = 0

    def mark_activity(self):
        self.last_activity = time.time()

    def request_start(self, tool_name: str) -> int:
        with self.lock:
            rid = self._next_id
            self._next_id += 1
            self.active_requests[rid] = (time.time(), tool_name)
        self.mark_activity()
        return rid

    def request_end(self, rid: int):
        with self.lock:
            self.active_requests.pop(rid, None)
        self.mark_activity()

    def oldest_request(self):
        """Return (age_seconds, tool_name) of oldest in-flight request, or (0, '')."""
        with self.lock:
            if not self.active_requests:
                return 0.0, ""
            rid, (start_ts, tool_name) = min(
                self.active_requests.items(), key=lambda kv: kv[1][0]
            )
            return time.time() - start_ts, tool_name


_state = _State()


def _log(server_name: str, event: str, detail: str = ""):
    """Append a single line to the shared idle-watchdog log. Never raises."""
    log_path = os.environ.get("MCP_IDLE_WATCHDOG_LOG", DEFAULT_LOG_PATH)
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        line = f"[{ts}] pid={os.getpid()} server={server_name} event={event}"
        if detail:
            line += f" {detail}"
        with open(log_path, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def track_request(func):
    """Decorator: bump activity on entry and exit, and mark the call as in-flight.

    Apply BELOW @mcp.tool() so MCP wraps the original function:
        @mcp.tool()
        @track_request
        def my_tool(...): ...
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        rid = _state.request_start(func.__name__)
        try:
            return func(*args, **kwargs)
        finally:
            _state.request_end(rid)
    return wrapper


def install_idle_watchdog(
    server_name: str,
    idle_timeout_seconds: int = 300,
    request_max_seconds: int = 600,
    on_exit=None,
    timeout_seconds: int = None,  # deprecated alias for idle_timeout_seconds
):
    """Install the idle watchdog. Safe to call once per process.

    Args:
        server_name: Identifier for this MCP server (e.g. "x-search"). Used in
                     log lines so leaks per server are attributable.
        idle_timeout_seconds: Auto-exit after this many seconds of no stdin
                              activity AND no in-flight requests (default 300 = 5 min).
        request_max_seconds: Auto-exit if a single request runs longer than this
                             (default 600 = 10 min). Backstop against hung calls.
                             Set higher for slow tools (video analysis ~20 min).
        on_exit: Optional callback invoked just before os._exit(0).
        timeout_seconds: Deprecated alias for idle_timeout_seconds.
    """
    if timeout_seconds is not None:
        idle_timeout_seconds = timeout_seconds

    # Non-positive sentinels disable the respective guards. Idle-exit was
    # originally a layer-1 defense against leaked MCP processes holding IB
    # Gateway clientIds; layer-3 (the root-cron watchdog at :4001 connection
    # count ≥ 28) is a better-scoped reactive defense. Disabling idle-exit
    # eliminates the stale-MCP-transport problem during long idle gaps
    # (overnight, travel) without losing the real safety net.
    idle_exit_enabled = idle_timeout_seconds > 0
    request_max_enabled = request_max_seconds > 0

    original_read = sys.stdin.buffer.read

    def _tracked_read(*args, **kwargs):
        _state.mark_activity()
        return original_read(*args, **kwargs)

    sys.stdin.buffer.read = _tracked_read

    idle_label = f"{idle_timeout_seconds}s" if idle_exit_enabled else "disabled"
    request_max_label = f"{request_max_seconds}s" if request_max_enabled else "disabled"
    _log(
        server_name, "started",
        f"idle_timeout={idle_label} request_max={request_max_label}",
    )

    def _watchdog():
        while True:
            time.sleep(60)

            # Check 1: hung request guard — kill if any single call has run too long
            oldest_age, tool_name = _state.oldest_request()
            if request_max_enabled and oldest_age > request_max_seconds:
                msg = (
                    f"Tool '{tool_name}' has been running {int(oldest_age)}s "
                    f"(> request_max {request_max_seconds}s) — auto-exiting"
                )
                print(f"⚠️  {msg}", file=sys.stderr, flush=True)
                _log(
                    server_name, "hung-request-exit",
                    f"tool={tool_name} age_seconds={int(oldest_age)}",
                )
                if on_exit is not None:
                    try:
                        on_exit()
                    except Exception:
                        pass
                os._exit(0)

            # Check 2: don't kill for idle if work is in flight
            if oldest_age > 0:
                continue

            # Check 3: idle exit (skipped when idle_timeout_seconds <= 0)
            if not idle_exit_enabled:
                continue
            idle = time.time() - _state.last_activity
            if idle > idle_timeout_seconds:
                msg = f"No stdin activity for {int(idle)}s — auto-exiting MCP server"
                print(f"⚠️  {msg}", file=sys.stderr, flush=True)
                _log(server_name, "idle-exit", f"idle_seconds={int(idle)}")
                if on_exit is not None:
                    try:
                        on_exit()
                    except Exception as e:
                        print(f"on_exit callback failed: {e}", file=sys.stderr, flush=True)
                        _log(server_name, "on-exit-error", str(e)[:200])
                os._exit(0)

    threading.Thread(
        target=_watchdog,
        daemon=True,
        name="mcp-idle-watchdog",
    ).start()
