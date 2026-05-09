# Dogfood Guide

How to spin up an isolated OpenClaw instance, load this MCP, and try it
out with natural-language queries — without touching your existing
OpenClaw setups (finance / career / etc.).

The test profile lives at `~/.openclaw-video-summarizer-test/` and is
fully separate from `~/.openclaw/` and `~/.openclaw-career/`.

---

## 0. One-time setup (~3 minutes)

```bash
cd /Users/kai/Documents/test/video-summarizer-mcp

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

This creates everything fresh at `~/.openclaw-video-summarizer-test/`,
completely separate from your other OpenClaw instances. The script:

- Writes `openclaw.json` (gateway port 19002, single `testbot` agent,
  only the video-summarizer MCP, Gemini Flash as the model).
- Reads your Gemini key from `~/.config/video-analysis/video-analysis.env`.
- Writes `AGENTS.md` + `SOUL.md` + `IDENTITY.md` in the workspace —
  defines the agent's persona and behavior rules.
- Wipes `channels.json` and `video-state.json` (the `--reset` flag).

To start over from a clean slate at any point, re-run with `--reset`.

---

## 2. Verify the profile registered correctly

```bash
openclaw --profile video-summarizer-test agents list
```

Expected output:
```
Agents:
- testbot (default)
  Identity: 🎬 Video Summarizer Test Agent (config)
  Workspace: ~/.openclaw-video-summarizer-test/workspace-test
  ...
```

---

## 3. Start the gateway (same terminal as step 2 is fine)

Step 2 (`agents list`) printed and exited, so this terminal is free.
Run the gateway here:

```bash
GEMINI_API_KEY=$(grep ^GEMINI_API_KEY ~/.config/video-analysis/video-analysis.env | cut -d= -f2-) \
  openclaw --profile video-summarizer-test gateway --auth none --force
```

This is a **long-running foreground process** — it will keep printing
logs and won't return to a prompt. You'll see boot output ending with
the gateway listening on `127.0.0.1:19002`. Leave it running and don't
Ctrl-C until you're done testing.

The MCP server (`start_server.sh`) is spawned on demand by the agent —
you don't need to launch it separately.

---

## 4. Open a second terminal for queries

Because step 3's gateway is occupying the first terminal, **open a new
terminal window/tab** for everything below. The new terminal starts
fresh, so re-export the env var and define a one-line helper:

```bash
export GEMINI_API_KEY=$(grep ^GEMINI_API_KEY ~/.config/video-analysis/video-analysis.env | cut -d= -f2-)

ask() {
  local sid="manual_$(date +%s)_$$"
  openclaw --profile video-summarizer-test agent \
    --agent testbot --session-id "$sid" --json -m "$1" \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('result',d).get('meta',{}).get('finalAssistantVisibleText','(no text)'))"
}
```

Each `ask` call runs in a fresh session, so the LLM has no cross-call
memory. To chain turns (e.g. ask → confirm), call
`openclaw agent ... --session-id <same-id>` twice in a row with the
same id.

---

## 5. Try the canonical examples

### Clear queries (should add)

```bash
ask "Add Forward Guidance to my channel list"
ask "Add @ForwardGuidanceBW"
ask "Add the All-In podcast YouTube channel"
ask "Add Forward Guidance and Macro Voices"
```

### Lookup-only (should NOT add)

```bash
ask "Does Andrej Karpathy have a YouTube channel?"
ask "What does the Forward Guidance channel cover lately?"
```

### Vague (should ask for clarification, not pick for you)

```bash
ask "Add a good investing podcast"
ask "Add a YouTube channel about advanced semiconductor packaging"
```

### Inspect / manage

```bash
ask "What channels am I tracking?"
ask "Remove Forward Guidance from my channels"
```

---

## 6. Inspect state directly

```bash
# What's in the registry right now
cat ~/.openclaw-video-summarizer-test/channels.json | python3 -m json.tool

# Per-channel last-seen video state (after discover_new_videos calls)
cat ~/.openclaw-video-summarizer-test/video-state.json | python3 -m json.tool

# Gateway logs (tail to watch live)
tail -50 ~/.openclaw-video-summarizer-test/gateway.log
```

---

## 7. Test the video-summary path (paid Gemini)

After adding at least one channel:

```bash
ask "Discover any new videos from my tracked channels and summarize the newest one"
```

Or analyze a specific video directly:

```bash
ask "Summarize this video: https://www.youtube.com/watch?v=MO9ZTZPUwXY"
```

Each summary costs ~$0.10–0.30 and takes 3–10 minutes. The agent calls
`analyze_video_start`, polls `analyze_video_result`, then reports back.

---

## 8. Run the automated test suite for sanity

| Command | Time | Cost | What it covers |
|---|---|---|---|
| `.venv/bin/python -m pytest tests/unit/` | ~1s | $0 | All unit tests, fully offline |
| `RUN_EXPENSIVE_TESTS=1 .venv/bin/python -m pytest tests/integration/ -k "not analyze_real"` | ~7s | $0 | Real YouTube + yt-dlp, no Gemini |
| `RUN_OPENCLAW_TESTS=1 .venv/bin/python -m pytest tests/openclaw/` | ~5 min | ~$0.07 | Full E2E through the agent |
| `RUN_OPENCLAW_TESTS=1 RUN_EXPENSIVE_TESTS=1 .venv/bin/python -m pytest tests/openclaw/` | ~10 min | ~$0.30 | Adds the TC-A1 Gemini video summary |

**Important:** before running the OpenClaw tests, **stop the manual
gateway** from step 3 — pytest starts its own session-scoped gateway
fixture, and a port conflict will break everything.

```bash
pkill -f "openclaw.*video-summarizer-test"
.venv/bin/python tests/openclaw/setup_profile.py --reset
RUN_OPENCLAW_TESTS=1 .venv/bin/python -m pytest tests/openclaw/
```

---

## Common gotchas

| Symptom | Cause | Fix |
|---|---|---|
| `Address already in use` on gateway start | Old gateway still running on 19002 | `--force` flag is already in step 3, or `pkill -f "openclaw.*video-summarizer-test"` |
| `EMBEDDED FALLBACK: unauthorized` in stderr | Gateway started with auth but client has no token | Use `--auth none` (already in step 3) |
| Agent says "already tracked" when registry is empty | LLM session memory leaked across calls (default `--session-id` is shared) | The `ask` helper generates a fresh session per call — use it instead of bare `openclaw agent` |
| `API key expired` from Gemini | Local key rotated | Refresh `~/.config/video-analysis/video-analysis.env` (e.g. from DGX), then re-run `setup_profile.py --reset` |
| Agent picks a channel for a vague query without asking | LLM mood — SOUL.md is hardened against this but Flash varies | Reset the profile and retry; or escalate to `gemini-3.1-pro-preview` in `openclaw.json` for stricter rule-following |
| `bootstrapFiles: []` in the systemPromptReport | Running with `--local` instead of via gateway | Use the gateway path (no `--local` flag); only the gateway loads workspace docs |

---

## Cleanup when done

```bash
pkill -f "openclaw.*video-summarizer-test"   # stop gateway
rm -rf ~/.openclaw-video-summarizer-test     # wipe profile entirely
```

The MCP repo at `/Users/kai/Documents/test/video-summarizer-mcp/` and
your other OpenClaw instances are untouched.

---

## Where things live

```
~/.openclaw-video-summarizer-test/
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

Repo: <https://github.com/kaiding-ucb/video-summarizer-mcp> (private)
