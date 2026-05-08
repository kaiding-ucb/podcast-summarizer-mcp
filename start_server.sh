#!/bin/bash
# OSS-friendly launcher: prefers the venv that lives next to this script
# (.venv/), falls back to the system python3 if no venv is set up.
# GEMINI_API_KEY (and any optional VIDEO_ANALYSIS_* path overrides) must
# be passed in by the MCP host (env block in claude_desktop_config.json,
# openclaw.json mcp.servers entry, etc.) — we do not source any user
# dotfile here so the MCP can ship clean across machines.

set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

if [ -x ".venv/bin/python3" ]; then
  PY=".venv/bin/python3"
elif [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  PY="$(command -v python3)"
fi

exec "$PY" server.py
