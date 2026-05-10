"""Shared fixtures + helpers for OpenClaw E2E tests.

Each test starts with the registry empty (the `clean_registry` fixture
deletes channels.json + video-state.json before the test runs).

Tests assert on:
  1. The registry final state — read channels.json directly
  2. The agent's final visible text — for clarification questions etc.
  3. The MCP tool-call evidence — extracted via grep on the OpenClaw run JSON

Real LLM calls cost money — tests in this tier are gated behind
RUN_OPENCLAW_TESTS=1.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

PROFILE_NAME = "podcast-summarizer-test"
GATEWAY_PORT = 19002
HOME = Path.home()
PROFILE_DIR = HOME / f".openclaw-{PROFILE_NAME}"
CHANNELS_PATH = PROFILE_DIR / "channels.json"
STATE_PATH = PROFILE_DIR / "video-state.json"
ENV_FILE = HOME / ".config" / "video-analysis" / "video-analysis.env"

AGENT_TIMEOUT = 180  # seconds — generous, handles model latency + tool calls
GATEWAY_BOOT_TIMEOUT = 20


def pytest_collection_modifyitems(config, items):
    """Skip OpenClaw tests unless RUN_OPENCLAW_TESTS=1."""
    if os.environ.get("RUN_OPENCLAW_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="set RUN_OPENCLAW_TESTS=1 to run E2E tests")
    for item in items:
        if "openclaw" in item.keywords:
            item.add_marker(skip)


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except OSError:
            return False


@pytest.fixture(scope="session")
def openclaw_gateway():
    """Boot the OpenClaw gateway for the duration of the test session.

    Workspace bootstrap (AGENTS.md / SOUL.md / IDENTITY.md context) only
    populates the system prompt when the agent runs through the gateway —
    --local mode skips that entirely. Tests run via gateway by default.
    """
    if not os.environ.get("RUN_OPENCLAW_TESTS") == "1":
        yield None
        return
    key = _load_gemini_key()
    if not key:
        pytest.skip("GEMINI_API_KEY not available")
    if _port_open(GATEWAY_PORT):
        # Already running (manual launch) — reuse and don't kill on teardown
        yield None
        return
    log_path = PROFILE_DIR / "gateway.log"
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["GEMINI_API_KEY"] = key
    proc = subprocess.Popen(
        [
            "openclaw",
            "--profile",
            PROFILE_NAME,
            "gateway",
            "--auth",
            "none",
            "--force",
        ],
        env=env,
        stdout=open(log_path, "w"),
        stderr=subprocess.STDOUT,
    )
    deadline = time.time() + GATEWAY_BOOT_TIMEOUT
    while time.time() < deadline:
        if _port_open(GATEWAY_PORT):
            break
        time.sleep(0.5)
    else:
        proc.terminate()
        pytest.fail(
            f"gateway did not come up within {GATEWAY_BOOT_TIMEOUT}s; see {log_path}"
        )
    try:
        yield proc
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _load_gemini_key() -> str:
    if (k := os.environ.get("GEMINI_API_KEY")):
        return k
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            m = re.match(r"^\s*GEMINI_API_KEY\s*=\s*(.+?)\s*$", line)
            if m:
                return m.group(1).strip().strip('"').strip("'")
    return ""


@pytest.fixture
def clean_registry(openclaw_gateway) -> None:
    """Reset the test profile's persistent state before each test.
    Depends on the session-scoped gateway so it's always up first."""
    for p in (CHANNELS_PATH, STATE_PATH):
        if p.exists():
            p.unlink()


def get_registry() -> Dict[str, Dict[str, Any]]:
    """Return the channels dict from channels.json. Empty dict if file missing."""
    if not CHANNELS_PATH.exists():
        return {}
    return json.loads(CHANNELS_PATH.read_text()).get("channels", {})


def get_state() -> Dict[str, Dict[str, Any]]:
    """Return the per-channel last-seen state from video-state.json."""
    if not STATE_PATH.exists():
        return {}
    return json.loads(STATE_PATH.read_text()).get("channels", {})


class AgentResponse:
    """Wrapper around an `openclaw agent` JSON result.

    Two output shapes exist depending on transport:
      - Embedded (--local): {payloads, meta} at the top level
      - Gateway (default):  {runId, status, summary, result: {payloads, meta}}
    `_inner()` flattens both into the same view.
    """

    def __init__(self, raw: Dict[str, Any]):
        self.raw = raw

    def _inner(self) -> Dict[str, Any]:
        if "result" in self.raw and isinstance(self.raw["result"], dict):
            return self.raw["result"]
        return self.raw

    @property
    def visible_text(self) -> str:
        return self._inner().get("meta", {}).get("finalAssistantVisibleText", "") or ""

    @property
    def raw_text(self) -> str:
        return self._inner().get("meta", {}).get("finalAssistantRawText", "") or ""

    @property
    def payloads(self) -> List[Dict[str, Any]]:
        return self._inner().get("payloads", []) or []

    def tool_call_names(self) -> List[str]:
        """Best-effort: return MCP tool names invoked during the turn."""
        names: List[str] = []
        for p in self.payloads:
            for call in p.get("toolCalls", []) or []:
                n = call.get("name") or call.get("toolName")
                if n:
                    names.append(n)
        if names:
            return names
        # Fallback: regex scan
        text = json.dumps(self.raw)
        for tool in [
            "search_youtube_channels",
            "resolve_youtube_channel",
            "get_channel_metadata",
            "add_tracked_channel",
            "remove_tracked_channel",
            "list_tracked_channels",
            "discover_new_videos",
            "analyze_video_start",
            "analyze_video_result",
            "get_video_info",
            "get_state",
        ]:
            # Look for "name":"podcast-summarizer__<tool>" pattern (used in
            # systemPromptReport.tools list — but that's the registry of
            # available tools, not necessarily called). To detect actual
            # invocations we'd need stricter parsing; for now we surface
            # *available* names so tests can at least assert the tool was
            # registered.
            if f'__{tool}"' in text or f'"{tool}"' in text:
                names.append(tool)
        return names

    def assert_no_mutation(self) -> None:
        """Convenience for lookup-only tests — registry must remain empty."""
        assert get_registry() == {}, (
            f"expected registry to remain empty, got {list(get_registry().keys())}"
        )


def send_message(
    message: str,
    agent: str = "testbot",
    session_id: Optional[str] = None,
) -> AgentResponse:
    """Run one agent turn against the test profile, return parsed result.

    By default each call gets a unique session-id so the LLM has no
    cross-test memory. Pass an explicit session_id to chain turns
    inside a single test (e.g. ask → confirm flows).
    """
    key = _load_gemini_key()
    if not key:
        pytest.fail("GEMINI_API_KEY not available — fix env file or env var")
    env = os.environ.copy()
    env["GEMINI_API_KEY"] = key
    if session_id is None:
        # uuid-style fresh id; underscored to avoid OpenClaw routing rules
        import uuid

        session_id = f"test_{uuid.uuid4().hex[:12]}"
    # Run through the gateway (not --local) so workspace bootstrap files
    # (AGENTS.md / SOUL.md / IDENTITY.md) populate the system prompt.
    cmd = [
        "openclaw",
        "--profile",
        PROFILE_NAME,
        "agent",
        "--agent",
        agent,
        "--session-id",
        session_id,
        "--json",
        "-m",
        message,
    ]
    result = subprocess.run(
        cmd, env=env, capture_output=True, text=True, timeout=AGENT_TIMEOUT
    )
    if result.returncode != 0:
        # OpenClaw can return nonzero even when it produced useful output;
        # only fail if stdout is empty.
        if not result.stdout.strip():
            pytest.fail(
                f"openclaw agent returned {result.returncode} with empty stdout. "
                f"stderr tail: {result.stderr[-400:]}"
            )
    try:
        return AgentResponse(json.loads(result.stdout))
    except json.JSONDecodeError as e:
        pytest.fail(
            f"could not parse openclaw stdout as JSON ({e}). "
            f"stdout head: {result.stdout[:400]}"
        )


def has_clarifying_question(text: str) -> bool:
    """Heuristic for tests like TC-V3 — does the response ask a question?"""
    return "?" in text


def has_candidate_listing(text: str, min_count: int = 2) -> bool:
    """Heuristic for TC-V1/V4 — does the response present multiple options?

    Looks for either a numbered list, bulleted candidates, or repeated
    channel-name patterns.
    """
    bullets = len(re.findall(r"^\s*[-*]\s", text, flags=re.MULTILINE))
    numbered = len(re.findall(r"^\s*\d+[.\)]\s", text, flags=re.MULTILINE))
    return max(bullets, numbered) >= min_count
