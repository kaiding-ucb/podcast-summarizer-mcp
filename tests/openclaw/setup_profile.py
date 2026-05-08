"""Bootstrap the isolated OpenClaw profile used for E2E tests.

Idempotent — safe to re-run to reset state. Resets:
  - profile config at ~/.openclaw-video-summarizer-test/openclaw.json
  - workspace SOUL.md
  - per-test state files (channels.json, video-state.json)

Reads GEMINI_API_KEY from $HOME/.config/video-analysis/video-analysis.env
or from the GEMINI_API_KEY env var. Refuses to run if neither is set.

Usage:
    python tests/openclaw/setup_profile.py            # bootstrap
    python tests/openclaw/setup_profile.py --reset    # also wipes state files
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

PROFILE_NAME = "video-summarizer-test"
HOME = Path.home()
PROFILE_DIR = HOME / f".openclaw-{PROFILE_NAME}"
WORKSPACE_DIR = PROFILE_DIR / "workspace-test"
CONFIG_PATH = PROFILE_DIR / "openclaw.json"
SOUL_PATH = WORKSPACE_DIR / "SOUL.md"
STATE_PATH = PROFILE_DIR / "video-state.json"
CHANNELS_PATH = PROFILE_DIR / "channels.json"

REPO_ROOT = Path(__file__).resolve().parents[2]
START_SCRIPT = REPO_ROOT / "start_server.sh"

GATEWAY_PORT = 19002


SOUL = """\
# Test Agent — video-summarizer

You exist to test the video-summarizer-mcp. Behavior:

- When the user asks to **add / remove / list channels**, use the channel
  registry tools (`add_tracked_channel`, `remove_tracked_channel`,
  `list_tracked_channels`).
- When the user asks to **look up info** about a channel ("does X have a
  YouTube?", "what does X cover?"), do NOT mutate the registry — search
  and report only.
- For **ambiguous requests** (multiple matches, vague topics), present
  candidates and ask for clarification rather than guessing.
- For **video summary** requests, use `analyze_video_start` then poll
  `analyze_video_result` until done.
- Be concise — single-paragraph responses unless presenting a list of
  candidates.
- Never fabricate a channel_id. If you can't resolve, say so.
"""


def find_gemini_key() -> str:
    """Find a Gemini API key via env var or the project's env file."""
    if (k := os.environ.get("GEMINI_API_KEY")):
        return k
    env_file = HOME / ".config" / "video-analysis" / "video-analysis.env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            m = re.match(r"^\s*GEMINI_API_KEY\s*=\s*(.+?)\s*$", line)
            if m:
                return m.group(1).strip().strip('"').strip("'")
    sys.exit(
        "Could not find GEMINI_API_KEY — set the env var or populate "
        "~/.config/video-analysis/video-analysis.env"
    )


def build_config(gemini_key: str) -> dict:
    return {
        "gateway": {
            "mode": "local",
            "port": GATEWAY_PORT,
            "bind": "loopback",
        },
        "models": {
            "mode": "merge",
            "providers": {
                "google": {
                    "baseUrl": "https://generativelanguage.googleapis.com/v1beta",
                    "apiKey": gemini_key,
                    "api": "google-generative-ai",
                    "models": [],
                }
            },
        },
        "agents": {
            "defaults": {
                "model": {"primary": "google/gemini-3-flash-preview"},
                "timeoutSeconds": 600,
            },
            "list": [
                {
                    "id": "testbot",
                    "name": "testbot",
                    "workspace": str(WORKSPACE_DIR),
                    "identity": {"name": "Video Summarizer Test Agent", "emoji": "🎬"},
                }
            ],
        },
        "mcp": {
            "servers": {
                "video-summarizer": {
                    "command": str(START_SCRIPT),
                    "args": [],
                    "env": {
                        "GEMINI_API_KEY": gemini_key,
                        "VIDEO_ANALYSIS_STATE_PATH": str(STATE_PATH),
                        "VIDEO_ANALYSIS_CHANNELS_PATH": str(CHANNELS_PATH),
                    },
                }
            }
        },
        "channels": {},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reset", action="store_true", help="Also delete state + channels JSON files"
    )
    args = parser.parse_args()

    gemini_key = find_gemini_key()

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

    config = build_config(gemini_key)
    CONFIG_PATH.write_text(json.dumps(config, indent=2))
    os.chmod(CONFIG_PATH, 0o600)
    SOUL_PATH.write_text(SOUL)

    if args.reset:
        for p in (STATE_PATH, CHANNELS_PATH):
            if p.exists():
                p.unlink()

    print(f"Profile bootstrapped: {PROFILE_DIR}")
    print(f"  Config:    {CONFIG_PATH}")
    print(f"  Workspace: {WORKSPACE_DIR}")
    print(f"  Channels:  {CHANNELS_PATH} (created lazily by MCP)")
    print(f"  State:     {STATE_PATH} (created lazily by MCP)")
    print(f"  MCP cmd:   {START_SCRIPT}")
    print(f"  Verify:    openclaw --profile {PROFILE_NAME} agents list")


if __name__ == "__main__":
    main()
