# podcast-summarizer-mcp

An MCP (Model Context Protocol) server that lets your AI assistant
discover, track, and summarize YouTube videos in natural language.

- **Channel discovery without a YouTube API key** — `"Add Forward
  Guidance to my channels"` resolves to a real channel via yt-dlp.
- **Tracked-channel registry** — the MCP remembers what you follow,
  with optional tags for grouping.
- **Video summaries via Gemini 3 Flash** — native multimodal video
  analysis with timestamped key moments.
- **Multi-video parallel** — fire summaries on N videos at once;
  wall-clock ≈ slowest single video.
- **Prompt customization** — point an env var at any prompt file,
  or override per call. Ships with 4 example prompts.

Works with **Claude Desktop**, **Claude Code**, **OpenClaw**, and any
other host that speaks the MCP stdio protocol.

---

## Quick start (5 minutes)

You need Python 3.10+ and a Gemini API key (free) from
<https://aistudio.google.com>.

### 1. Install from source

```bash
git clone https://github.com/kaiding-ucb/podcast-summarizer-mcp
cd podcast-summarizer-mcp
python3 -m venv .venv && .venv/bin/pip install -e .

# Note the absolute path — you'll paste it into your host config below.
echo "$(pwd)/.venv/bin/podcast-summarizer-mcp"
```

### 2. Configure ONE host

Pick whichever you use. Replace `/ABSOLUTE/PATH` with what step 1 echoed,
and `AIza...` with your Gemini key.

#### Claude Desktop
Edit `~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):
```json
{
  "mcpServers": {
    "podcast-summarizer": {
      "command": "/ABSOLUTE/PATH",
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
  -- /ABSOLUTE/PATH

claude mcp list   # should show podcast-summarizer
```

#### OpenClaw
Add to `~/.openclaw/openclaw.json` under `mcp.servers`:
```json
"podcast-summarizer": {
  "command": "/ABSOLUTE/PATH",
  "env": { "GEMINI_API_KEY": "AIza..." }
}
```
Restart OpenClaw (`pkill -f openclaw-gateway; openclaw`).

### 3. Smoke-test it

In your host's chat, ask:

> **Add Forward Guidance to my channels**

Then verify it actually wrote to disk:

```bash
cat ~/.podcast-summarizer-mcp/channels.json
```

You should see one entry whose `channel_id` starts with `UC` and `name`
contains "Forward Guidance". Then try:

> **What channels am I tracking?**

Should reply with the channel you just added. Then try:

> **Summarize this video: https://www.youtube.com/watch?v=MO9ZTZPUwXY**

Takes 3–10 minutes (real Gemini analysis). The agent will start the job,
poll for completion, and return a structured summary with timestamps.

### Reset state if needed

```bash
rm -rf ~/.podcast-summarizer-mcp/   # wipes channel registry + per-channel state
```

---

## Install

You'll need:

1. **Python 3.10+**
2. A **Gemini API key** from <https://aistudio.google.com> (free tier OK)

Then install the server:

```bash
# Recommended: uvx (no virtualenv management, works on Mac/Linux/Windows)
uvx install podcast-summarizer-mcp

# Or pipx
pipx install podcast-summarizer-mcp

# Or pip into a venv of your choice
pip install podcast-summarizer-mcp
```

This puts a `podcast-summarizer-mcp` binary on your PATH. That's the
command your MCP host will run.

> **Until the package lands on PyPI**, install from source:
> ```bash
> git clone https://github.com/kaiding-ucb/podcast-summarizer-mcp
> cd podcast-summarizer-mcp
> python3 -m venv .venv && .venv/bin/pip install -e .
> # Point your MCP host at /full/path/to/podcast-summarizer-mcp/.venv/bin/podcast-summarizer-mcp
> ```

---

## Configure your MCP host

### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "podcast-summarizer": {
      "command": "podcast-summarizer-mcp",
      "env": {
        "GEMINI_API_KEY": "AIza..."
      }
    }
  }
}
```

Restart Claude Desktop. The MCP appears under the 🔌 icon; you can
now ask Claude to "add Forward Guidance to my channels" or
"summarize this video: https://...".

### Claude Code

```bash
claude mcp add podcast-summarizer \
  --env GEMINI_API_KEY=AIza... \
  -- podcast-summarizer-mcp
```

Or drop a `.mcp.json` at the root of any Claude Code project:

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

### OpenClaw

Edit `~/.openclaw/openclaw.json` and add the server entry:

```json
{
  "mcp": {
    "servers": {
      "podcast-summarizer": {
        "command": "podcast-summarizer-mcp",
        "env": { "GEMINI_API_KEY": "AIza..." }
      }
    }
  }
}
```

---

## Tools & capabilities

The MCP exposes **13 tools** that your AI assistant picks between
based on what you ask. You don't call them directly — just describe
what you want in natural language.

### All tools at a glance

| Tool | Purpose |
|---|---|
| **Channel discovery** *(no YouTube API key required)* | |
| `search_youtube_channels(query, max_results=5)` | Fuzzy search YouTube for channels matching a free-text query |
| `resolve_youtube_channel(handle_or_url)` | Resolve a `@handle` or full channel URL to a ChannelInfo |
| `get_channel_metadata(channel_id)` | Subscriber count, description, recent video titles |
| **Channel registry** *(MCP-owned state at `~/.podcast-summarizer-mcp/channels.json`)* | |
| `add_tracked_channel(channel_id, name, handle?, tags?)` | Add a channel to the registry (idempotent) |
| `remove_tracked_channel(channel_id)` | Remove a channel from the registry |
| `list_tracked_channels(tag?)` | List all tracked channels, optionally filtered by tag |
| **Video discovery & analysis** | |
| `discover_new_videos(channel_ids?, tag?, ...)` | Return videos posted since the last poll, per channel |
| `get_video_info(video_url)` | Cheap metadata-only lookup (no Gemini cost) |
| `analyze_video_start(video_url, prompt?)` | Launch Gemini analysis as a background job, returns a `job_id` |
| `analyze_video_result(job_id, wait_seconds=10)` | Poll for the analyze_video_start result |
| `analyze_videos_batch_start(video_urls, prompt?)` | 50% cheaper batch path for non-urgent multi-video |
| `analyze_videos_batch_result(batch_job_name)` | Poll for the batch result |
| `get_state(channel_ids?)` | Read-only view of the per-channel last-seen state |

### What you can actually do with it

#### 1. Search and discover channels in natural language

The agent picks `search_youtube_channels`, `resolve_youtube_channel`,
or `get_channel_metadata` automatically. **None of these mutate state**,
so they're safe to use freely for exploration.

Try in chat:

> Does Andrej Karpathy have a YouTube channel?
>
> What does the Forward Guidance channel cover lately?
>
> I'm looking for a good YouTube channel about advanced semiconductor packaging — give me three options.
>
> Find me a few investing podcasts.

For ambiguous topic-only queries the agent presents 2–3 candidates and
asks you to pick rather than guessing.

#### 2. Add / remove channels in your tracked list

Adds and removes survive restarts (state lives at
`~/.podcast-summarizer-mcp/channels.json`). Tags are arbitrary strings
for grouping — useful when you want one MCP backing multiple agents
or topic buckets.

Try in chat:

> Add Forward Guidance to my channel list
>
> Add @ForwardGuidanceBW
>
> Add the All-In podcast YouTube channel
>
> Add Forward Guidance and Macro Voices
>
> What channels am I tracking?
>
> Remove Forward Guidance from my channels

With tagging:

> Add Forward Guidance and tag it "macro"
>
> Show me my macro-tagged channels

#### 3. Summarize videos from your channels

Two paths: summarize a specific video, or sweep your tracked channels
for new uploads and summarize them.

Single video (~3–10 minutes, full Gemini cost):

> Summarize this video: https://www.youtube.com/watch?v=MO9ZTZPUwXY

Across all your tracked channels:

> Discover any new videos from my tracked channels and summarize the newest one
>
> Summarize today's new videos from all my channels in parallel

For overnight backlogs (50% cheaper, async, up to 24h SLA):

> I have a backlog of 20 videos to summarize — no rush, do it overnight to save cost

The agent picks `analyze_video_start` (sync, parallel) for interactive
requests and `analyze_videos_batch_start` only when you explicitly
opt into the slower-but-cheaper batch path.

---

## Configuration

All optional — set in the host's `env` block.

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | (**required**) | Your Gemini API key |
| `VIDEO_ANALYSIS_CHANNELS_PATH` | `~/.podcast-summarizer-mcp/channels.json` | Tracked-channel registry |
| `VIDEO_ANALYSIS_STATE_PATH` | `~/.podcast-summarizer-mcp/video-state.json` | Per-channel last-seen video |
| `VIDEO_ANALYSIS_JOBS_PATH` | `~/.podcast-summarizer-mcp/jobs.json` | Async analysis job store |
| `VIDEO_ANALYSIS_BATCH_METADATA_PATH` | `~/.podcast-summarizer-mcp/batches.json` | Batch-job metadata cache |
| `VIDEO_ANALYSIS_PROMPT_PATH` | (uses built-in default) | Path to a custom analysis prompt |

### Customizing the analysis prompt

The default prompt is tuned for investment podcasts. To change it
host-wide:

```bash
# Use one of the bundled examples
export VIDEO_ANALYSIS_PROMPT_PATH=/path/to/site-packages/prompts/technical-talk.md

# Or your own
export VIDEO_ANALYSIS_PROMPT_PATH=~/my-prompts/news-briefing.md
```

The MCP re-reads the file on every analysis call — edit and the next
analysis uses the updated prompt; no restart needed.

For per-call overrides, the LLM can pass a `prompt=...` argument to
`analyze_video_start`. Useful when one specific video needs a
different framing.

Bundled examples in [`prompts/`](prompts/):
- `investment-podcast.md` (default)
- `technical-talk.md` — engineering conference talks
- `interview.md` — long-form Q&A, focuses on the guest
- `news-briefing.md` — daily wraps, market summaries

---

## Model recommendations

The MCP works with any LLM the host supports, but tool-use reliability
varies. From our testing:

| Model | Tool-use reliability | Notes |
|---|---|---|
| Claude Sonnet / Opus | Excellent | Reliably calls tools and quotes returned state |
| GPT-4 / GPT-5 | Excellent | Same |
| Gemini 3.1 Pro | Excellent | |
| Gemini 3 Flash | Good (with caveats) | Occasionally hallucinates "I've added X" without calling the tool. The MCP returns rich `user_facing_message` strings to mitigate this — Flash usually quotes them verbatim. If you see misreports, switch to Pro. |

---

## Development

```bash
git clone https://github.com/kaiding-ucb/podcast-summarizer-mcp
cd podcast-summarizer-mcp
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# Unit tests (offline, ~1s)
.venv/bin/python -m pytest tests/unit/

# Integration tests (real YouTube, free; gated)
RUN_EXPENSIVE_TESTS=1 .venv/bin/python -m pytest tests/integration/

# OpenClaw E2E tests (real LLM + tools, ~$0.07)
# See dogfood.md for the OpenClaw test profile setup
RUN_OPENCLAW_TESTS=1 .venv/bin/python -m pytest tests/openclaw/
```

For a manual test workflow (spinning up an isolated OpenClaw
instance and chatting with the agent), see [`dogfood.md`](dogfood.md).

For the full TDD spec the test suite was built against, see
[`TEST_PLAN.md`](TEST_PLAN.md).

---

## License

MIT
