# Podcast Summarizer MCP 📝

MCP that works with **OpenClaw, Claude Desktop, Claude Code**. Search & discover YouTube channels via natural language and summarize videos with **no transcripts or subtitles required**.

[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-Ready-brightgreen.svg)](https://modelcontextprotocol.io/)
[![PyPI](https://img.shields.io/pypi/v/podcast-summarizer-mcp.svg?color=blue)](https://pypi.org/project/podcast-summarizer-mcp/)

[![Claude Desktop](https://img.shields.io/badge/Claude_Desktop-Ready-D97757.svg)](https://claude.ai/download)
[![Claude Code](https://img.shields.io/badge/Claude_Code-Ready-CB785C.svg)](https://docs.claude.com/en/docs/claude-code)
[![OpenClaw](https://img.shields.io/badge/OpenClaw-Ready-7C3AED.svg)](https://github.com/openclawai)

---

## Quick start (5 minutes)

Python 3.10+ and a Gemini API key (free) from
<https://aistudio.google.com>.

### 1. Install

```bash
pip install podcast-summarizer-mcp
```

This puts `podcast-summarizer-mcp` on your PATH. (Or use `pipx install`
/ `uvx install` if you prefer isolated tools.)

### 2. Configure ONE host

Pick whichever you use. Replace `AIza...` with your Gemini key.

#### Claude Desktop
Edit `~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):
```json
{
  "mcpServers": {
    "podcast-summarizer": {
      "command": "podcast-summarizer-mcp",
      "env": { "GEMINI_API_KEY": "AIza..." }
    }
  }
}
```
Quit Claude Desktop fully (⌘Q) and reopen. Click the 🔌 icon —
`podcast-summarizer` should be listed.

#### Claude Code
```bash
claude mcp add podcast-summarizer \
  --env GEMINI_API_KEY=AIza... \
  -- podcast-summarizer-mcp

claude mcp list   # should show podcast-summarizer
```

#### OpenClaw
Add to `~/.openclaw/openclaw.json` under `mcp.servers`:
```json
"podcast-summarizer": {
  "command": "podcast-summarizer-mcp",
  "env": { "GEMINI_API_KEY": "AIza..." }
}
```
Restart OpenClaw (`pkill -f openclaw-gateway; openclaw`).

> **Installing from source instead?** `git clone`, `cd`, then
> `python3 -m venv .venv && .venv/bin/pip install -e .`, and use the
> absolute path `$(pwd)/.venv/bin/podcast-summarizer-mcp` for the
> `command` field above.

---
## Example prompts

**Discover**:
> Does Andrej Karpathy have a YouTube channel?
>
> Find me a few investing podcasts.

**Track**:
> Add Forward Guidance to my channels
>
> Add @ForwardGuidanceBW
>
> What channels am I tracking?
>
> Remove Forward Guidance

**Summarize**:
> Summarize this video: https://www.youtube.com/watch?v=MO9ZTZPUwXY
>
> Summarize today's new videos from all my channels in parallel
>
> I have a 20-video backlog — no rush, do it overnight to save cost

The agent picks `analyze_video_start` (parallel, full price) by default
and `analyze_videos_batch_start` (50% off, async) only when you say
"no rush" / "overnight".

---

## 📱 Use via Telegram, WhatsApp & More (OpenClaw)

Connect this MCP to **Telegram, WhatsApp, Discord** and 20+ messaging
platforms via [OpenClaw](https://openclaw.ai) — a self-hosted AI
gateway. Talk to your podcast research agent from your phone, anywhere.

```
Telegram → OpenClaw agent (Claude / Gemini / GPT) → podcast-summarizer-mcp → Gemini + YouTube
```

OpenClaw routes messages from your chat platform of choice to an AI
agent. The agent talks to this MCP over standard stdio — no Python
wrapper or shim required.

### Setup

```bash
# 1. Install OpenClaw (Node 22+)
npm install -g openclaw

# 2. Add a Telegram bot token (interactive — paste BotFather token)
openclaw configure --section channels

# 3. Add this MCP + agent + model to ~/.openclaw/openclaw.json:
#    (already covered in Quick Start — use the OpenClaw snippet)
openclaw config set agents.defaults.model "anthropic/claude-sonnet-4-5"

# 4. Start the gateway
openclaw gateway
```
---

## Tools

13 tools. The agent picks; you don't call them directly.

| Tool | Purpose |
|---|---|
| `search_youtube_channels(query, max_results=5)` | Fuzzy channel search |
| `resolve_youtube_channel(handle_or_url)` | `@handle` or URL → channel |
| `get_channel_metadata(channel_id)` | Subs, description, recent titles |
| `add_tracked_channel(channel_id, name, handle?, tags?)` | Add to registry |
| `remove_tracked_channel(channel_id)` | Remove from registry |
| `list_tracked_channels(tag?)` | List tracked channels |
| `discover_new_videos(channel_ids?, tag?, ...)` | New videos since last poll |
| `get_video_info(video_url)` | Metadata only (no Gemini cost) |
| `analyze_video_start(video_url, prompt?)` | Launch Gemini analysis (returns `job_id`) |
| `analyze_video_result(job_id)` | Poll for the result |
| `analyze_videos_batch_start(video_urls, prompt?)` | 50%-cheaper batch path (24h SLA) |
| `analyze_videos_batch_result(batch_job_name)` | Poll batch result |
| `get_state(channel_ids?)` | Per-channel last-seen video |

Registry state lives at `~/.podcast-summarizer-mcp/channels.json`.

---

## Configuration

Set in the host's `env` block. Only `GEMINI_API_KEY` is required.

| Variable | Default |
|---|---|
| `GEMINI_API_KEY` | **required** |
| `VIDEO_ANALYSIS_CHANNELS_PATH` | `~/.podcast-summarizer-mcp/channels.json` |
| `VIDEO_ANALYSIS_STATE_PATH` | `~/.podcast-summarizer-mcp/video-state.json` |
| `VIDEO_ANALYSIS_JOBS_PATH` | `~/.podcast-summarizer-mcp/jobs.json` |
| `VIDEO_ANALYSIS_BATCH_METADATA_PATH` | `~/.podcast-summarizer-mcp/batches.json` |
| `VIDEO_ANALYSIS_PROMPT_PATH` | unset → built-in investment-podcast prompt |

`VIDEO_ANALYSIS_PROMPT_PATH` is re-read on every analysis call (no restart). Bundled examples in [`prompts/`](prompts/): `investment-podcast.md`, `technical-talk.md`, `interview.md`, `news-briefing.md`.

---

MIT License.
