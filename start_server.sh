#!/bin/bash
cd "$(dirname "$0")"
set -a; source "$HOME/.config/video-analysis/video-analysis.env"; set +a
exec "$HOME/venvs/video-analysis/bin/python3" server.py
