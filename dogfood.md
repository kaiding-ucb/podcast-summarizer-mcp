# Dogfood Guide

How to spin up an isolated OpenClaw instance, load this MCP, and try it
out with natural-language queries — without touching your existing
OpenClaw setups (finance / career / etc.).

The test profile lives at `~/.openclaw-podcast-summarizer-test/` and is
fully separate from `~/.openclaw/` and `~/.openclaw-career/`.

---

## 0. One-time setup (~3 minutes)

```bash
cd /Users/kai/Documents/test/podcast-summarizer-mcp

# Create venv + install deps
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# Confirm OpenClaw is on PATH
openclaw --version   # should print 2026.5.7 or later
```

If `openclaw` isn't found:
```bash
nvm use 22 && npm install -g openclaw
```

---

## 1. Bootstrap the isolated test profile

```bash
.venv/bin/python tests/openclaw/setup_profile.py --reset
```

This creates everything fresh at `~/.openclaw-podcast-summarizer-test/`,
completely separate from your other OpenClaw instances. The script:

- Writes `openclaw.json` (gateway port 19002, single `testbot` agent,
  only the podcast-summarizer MCP, Gemini Flash as the model).
- Reads your Gemini key from `~/.config/video-analysis/video-analysis.env`.
- Writes `AGENTS.md` + `SOUL.md` + `IDENTITY.md` in the workspace —
  defines the agent's persona and behavior rules.
- Wipes `channels.json` and `video-state.json` (the `--reset` flag).

To start over from a clean slate at any point, re-run with `--reset`.

---

## 2. Verify the profile registered correctly

```bash
openclaw --profile podcast-summarizer-test agents list
```

Expected output:
```
Agents:
- testbot (default)
  Identity: 🎬 Podcast Summarizer Test Agent (config)
  Workspace: ~/.openclaw-podcast-summarizer-test/workspace-test
  ...
```

---

## 3. Open the TUI — single terminal, interactive chat (recommended)

```bash
GEMINI_API_KEY=$(grep ^GEMINI_API_KEY ~/.config/video-analysis/video-analysis.env | cut -d= -f2-) \
  openclaw --profile podcast-summarizer-test chat --local
```

That's it. You'll get an interactive terminal UI bound to the test
profile. Type messages, press enter, watch the agent call MCP tools
and reply. No second terminal, no gateway management, no helper
function. Quit with Ctrl-D or `:quit`.

`--local` runs the agent embedded in this process so there's no
gateway needed. The MCP server is spawned automatically on demand.

> **Note on session memory**: by default the TUI uses session
> `main`, so messages within one TUI run share context. Quit and
> restart for a clean session.

---

## 4. Try the canonical examples

In the TUI prompt, type these as messages (no quotes, no `ask`):

### Clear queries (should add)

```
Add Forward Guidance to my channel list
Add @ForwardGuidanceBW
Add the All-In podcast YouTube channel
Add Forward Guidance and Macro Voices
```

### Lookup-only (should NOT add)

```
Does Andrej Karpathy have a YouTube channel?
What does the Forward Guidance channel cover lately?
```

### Vague (should ask for clarification, not pick for you)

```
Add a good investing podcast
Add a YouTube channel about advanced semiconductor packaging
```

### Inspect / manage

```
What channels am I tracking?
Remove Forward Guidance from my channels
```

---

## 5. Inspect state directly

Open a second terminal (any time, doesn't disrupt the TUI):

```bash
# What's in the registry right now
cat ~/.openclaw-podcast-summarizer-test/channels.json | python3 -m json.tool

# Per-channel last-seen video state (after discover_new_videos calls)
cat ~/.openclaw-podcast-summarizer-test/video-state.json | python3 -m json.tool
```

---

## Customizing the analysis prompt

The default prompt (in `tools/gemini_client.py:DEFAULT_PROMPT` and
`prompts/investment-podcast.md`) is tuned for investment podcasts —
it asks Gemini to extract stock/sector/strategy recommendations and
exclude sponsor reads.

For other content types, override:

**Host-wide (env var, persistent across all calls):**
```bash
export VIDEO_ANALYSIS_PROMPT_PATH=/Users/kai/Documents/test/podcast-summarizer-mcp/prompts/technical-talk.md
.venv/bin/python tests/openclaw/setup_profile.py --reset   # picks up new env var
openclaw --profile podcast-summarizer-test chat --local
```

The MCP re-reads the file on every analysis call — edit the file and
the next analysis uses the updated prompt; no MCP restart needed.

**Per-call (LLM agent passes it in the tool call):**
The MCP tools `analyze_video_start` and `analyze_videos_batch_start`
accept a `prompt` argument. In the TUI you can ask things like:

```
Summarize this technical talk in 5 bullets focused on architecture
decisions: https://www.youtube.com/watch?v=...
```

The agent will call `analyze_video_start(video_url=..., prompt="...")`
with a one-off prompt for that video.

**Provided prompt library (`prompts/`):**
- `investment-podcast.md` (the default)
- `technical-talk.md` — engineering conference talks, deep dives
- `interview.md` — long-form Q&A, focuses on the guest
- `news-briefing.md` — daily wraps, market summaries

Copy the closest one, edit to taste, point `VIDEO_ANALYSIS_PROMPT_PATH`
at your edited file.

---

## 6. Test the video-summary path (paid Gemini)

After adding at least one channel, in the TUI:

```
Discover any new videos from my tracked channels and summarize the newest one
```

Or analyze a specific video directly:

```
Summarize this video: https://www.youtube.com/watch?v=MO9ZTZPUwXY
```

Each summary costs ~$0.10–0.30 and takes 3–10 minutes. The agent calls
`analyze_video_start`, polls `analyze_video_result`, then reports back.

---

## 7. Run the automated test suite for sanity

| Command | Time | Cost | What it covers |
|---|---|---|---|
| `.venv/bin/python -m pytest tests/unit/` | ~1s | $0 | All unit tests, fully offline |
| `RUN_EXPENSIVE_TESTS=1 .venv/bin/python -m pytest tests/integration/ -k "not analyze_real"` | ~7s | $0 | Real YouTube + yt-dlp, no Gemini |
| `RUN_OPENCLAW_TESTS=1 .venv/bin/python -m pytest tests/openclaw/` | ~5 min | ~$0.07 | Full E2E through the agent |
| `RUN_OPENCLAW_TESTS=1 RUN_EXPENSIVE_TESTS=1 .venv/bin/python -m pytest tests/openclaw/` | ~10 min | ~$0.30 | Adds the TC-A1 Gemini video summary |

**Important:** quit the TUI from step 3 first — pytest starts its own
session-scoped gateway fixture, and a port conflict on 19002 will
break everything.

```bash
.venv/bin/python tests/openclaw/setup_profile.py --reset
RUN_OPENCLAW_TESTS=1 .venv/bin/python -m pytest tests/openclaw/
```

---

## Appendix A — scriptable / non-interactive testing

If you want to drive the agent from a shell script (e.g. for repeated
canned queries), you can skip the TUI and use the gateway + `ask`
helper pattern:

**Terminal 1** — start the gateway (long-running):
```bash
GEMINI_API_KEY=$(grep ^GEMINI_API_KEY ~/.config/video-analysis/video-analysis.env | cut -d= -f2-) \
  openclaw --profile podcast-summarizer-test gateway --auth none --force
```

**Terminal 2** — fire queries:
```bash
export GEMINI_API_KEY=$(grep ^GEMINI_API_KEY ~/.config/video-analysis/video-analysis.env | cut -d= -f2-)

ask() {
  local sid="manual_$(date +%s)_$$"
  openclaw --profile podcast-summarizer-test agent \
    --agent testbot --session-id "$sid" --json -m "$1" \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('result',d).get('meta',{}).get('finalAssistantVisibleText','(no text)'))"
}

ask "Add Forward Guidance"
ask "What channels am I tracking?"
```

Each `ask` runs in a fresh session — no LLM cross-call memory. To
chain a multi-turn conversation, call `openclaw agent ... --session-id <id>`
twice with the same id.

This is what the automated tests in `tests/openclaw/` use under the hood.

---

## Common gotchas

| Symptom | Cause | Fix |
|---|---|---|
| TUI doesn't seem to call any tools | LLM mood; you may have hit a Flash refusal | Quit, restart TUI (fresh session), retry with a more explicit message |
| `Address already in use` on port 19002 | Old gateway / old TUI still running | `pkill -f "openclaw.*podcast-summarizer-test"` |
| `API key expired` from Gemini | Local key rotated | Refresh `~/.config/video-analysis/video-analysis.env`, then re-run `setup_profile.py --reset` |
| Agent picks a channel for a vague query without asking | LLM mood — SOUL.md is hardened against this but Flash varies | Quit + restart TUI; or escalate to `gemini-3.1-pro-preview` in `~/.openclaw-podcast-summarizer-test/openclaw.json` for stricter rule-following |
| Agent says "already tracked" when you know the registry is empty | TUI session memory bleeding (you added it earlier in the same TUI session) | Quit and re-launch the TUI for a fresh session; or run `setup_profile.py --reset` to wipe the registry on disk |

---

## Cleanup when done

```bash
pkill -f "openclaw.*podcast-summarizer-test"   # stop gateway
rm -rf ~/.openclaw-podcast-summarizer-test     # wipe profile entirely
```

The MCP repo at `/Users/kai/Documents/test/podcast-summarizer-mcp/` and
your other OpenClaw instances are untouched.

---

## Where things live

```
~/.openclaw-podcast-summarizer-test/
├── openclaw.json               # profile config (gateway port, MCP entry, model)
├── channels.json               # registry — edited by add/remove_tracked_channel
├── video-state.json            # per-channel last-seen video id (discover state)
├── gateway.log                 # gateway boot + runtime logs
├── agents/testbot/             # agent state + sessions
└── workspace-test/
    ├── AGENTS.md               # primary persona + behavior rules (loaded by gateway)
    ├── SOUL.md                 # duplicate of AGENTS.md (belt-and-braces)
    └── IDENTITY.md             # name + emoji
```

Repo: <https://github.com/kaiding-ucb/podcast-summarizer-mcp> (private)
