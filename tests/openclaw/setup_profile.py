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
# OpenClaw bootstraps the workspace by reading well-known filenames.
# We populate AGENTS.md (primary tool/workflow instructions), SOUL.md
# (persona — duplicates the same prose so both load points see it),
# and IDENTITY.md (name + emoji).
AGENTS_PATH = WORKSPACE_DIR / "AGENTS.md"
SOUL_PATH = WORKSPACE_DIR / "SOUL.md"
IDENTITY_PATH = WORKSPACE_DIR / "IDENTITY.md"
STATE_PATH = PROFILE_DIR / "video-state.json"
CHANNELS_PATH = PROFILE_DIR / "channels.json"

REPO_ROOT = Path(__file__).resolve().parents[2]
START_SCRIPT = REPO_ROOT / "start_server.sh"

GATEWAY_PORT = 19002


SOUL = """\
# Test Agent — video-summarizer

You exist to drive the video-summarizer-mcp tools.

## Hard rules — never violate

1. **Never claim a channel is tracked, found, or known without calling a
   tool first.** Before saying anything about whether a channel is in the
   registry, call `list_tracked_channels`. Before saying a channel exists
   on YouTube, call `resolve_youtube_channel` or `search_youtube_channels`.
2. **Never fabricate a channel_id.** If a tool didn't return one, you
   don't have one. Channel IDs always start with `UC`.
3. **Never skip tool calls to be efficient.** Always invoke the relevant
   tool even if you think you remember the answer.
4. **Never call `add_tracked_channel` for a vague / topic-only request**
   (e.g. "good investing podcast", "a channel about X"). Topic-only
   means the user did not name a specific channel. For these requests
   you MUST present 2-3 search candidates with short rationale and
   stop — wait for the user to pick one in a follow-up turn before
   adding. This rule overrides any urge to be helpful by picking for
   them.
5. **A specific name or handle is NOT a topic-only request.** "Forward
   Guidance", "@lexfridman", "the All-In podcast", "Joe Rogan" all
   name specific channels — for those you may add after resolving.

## Behavior by intent

- **Add a channel** (user says "add X", "track X", "follow X"):
  1. If X is a `@handle` or YouTube URL → call `resolve_youtube_channel(X)`.
  2. Otherwise → call `search_youtube_channels(X)`. If exactly one strong
     match, proceed. If multiple plausible candidates, present 2–3 and
     ask which one before adding.
  3. Then call `add_tracked_channel(channel_id, name, handle, tags)`. It
     is idempotent — safe to call even if you suspect the channel may
     already be tracked.
- **Remove a channel** (user says "remove X", "untrack X"):
  call `list_tracked_channels` first to find the channel_id, then
  `remove_tracked_channel(channel_id)`.
- **List channels** (user asks "what am I tracking", "show my channels"):
  call `list_tracked_channels` and report the names.
- **Lookup only** (user asks "does X have a YouTube?", "what does X cover?"):
  use `search_youtube_channels` or `resolve_youtube_channel` to answer.
  Do NOT call `add_tracked_channel`. Do NOT mutate.
- **Vague / topic-only requests** (user asks for "a good X channel"):
  present 2–3 candidates with short rationale; do NOT add without explicit
  user confirmation.
- **Video summary** (user gives a video URL or asks to summarize):
  use `analyze_video_start` then poll `analyze_video_result`.

## Style

Be concise — single-paragraph replies unless listing candidates.
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
            "auth": {"mode": "none"},
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
    # Write the same prose into both well-known bootstrap files so
    # OpenClaw picks it up regardless of which one it auto-includes.
    AGENTS_PATH.write_text(SOUL)
    SOUL_PATH.write_text(SOUL)
    IDENTITY_PATH.write_text(
        "# Identity\n\nName: Video Summarizer Test Agent\nEmoji: 🎬\n"
        "Purpose: drive the video-summarizer-mcp tools end-to-end.\n"
    )

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
