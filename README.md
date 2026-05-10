# podcast-summarizer-mcp

MCP server for natural-language YouTube channel discovery and Gemini-powered video summaries. No YouTube API key required. Works with Claude Desktop, Claude Code, OpenClaw.

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

## Example prompts

**Discover** (no state mutation):
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

## Model recommendations

| Model | Tool-use reliability |
|---|---|
| Claude Sonnet / Opus, GPT-4/5, Gemini 3.1 Pro | Excellent |
| Gemini 3 Flash | Good — occasionally hallucinates "I've added X" without calling the tool. The MCP returns `user_facing_message` strings to mitigate; switch to Pro if misreports persist. |

---

## Development

```bash
.venv/bin/pip install -e ".[dev]"

.venv/bin/python -m pytest tests/unit/                          # offline, ~1s
RUN_EXPENSIVE_TESTS=1 .venv/bin/python -m pytest tests/integration/  # real YouTube, free
RUN_OPENCLAW_TESTS=1 .venv/bin/python -m pytest tests/openclaw/      # real LLM, ~$0.07
```

See [`dogfood.md`](dogfood.md) for the manual OpenClaw test workflow and [`TEST_PLAN.md`](TEST_PLAN.md) for the TDD spec.

---

MIT License.
