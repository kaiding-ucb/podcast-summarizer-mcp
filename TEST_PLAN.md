# Test Plan — video-summarizer-mcp

TDD spec for the `feat/no-api-discovery` branch. Tests are written first; implementation must make them pass.

## Test taxonomy

Three tiers, fastest-to-slowest:

| Tier | Location | Speed | LLM? | Real APIs? | Cost / run | Run on |
|---|---|---|---|---|---|---|
| **Unit** | `tests/unit/` | <5s total | no | mocked | $0 | every save (watch mode) |
| **Integration** | `tests/integration/` | ~30–60s | no | yes (YouTube + 1 cheap Gemini call) | <$0.05 | pre-commit / CI |
| **OpenClaw E2E** | `tests/openclaw/` | ~5–10 min | yes | yes (Gemini Flash) | <$0.50 | manually before merge |

All tiers use pytest. CI runs unit + integration. OpenClaw tier is manual due to cost and LLM nondeterminism.

## Directory layout

```
tests/
├── conftest.py                          # shared fixtures, env-var setup
├── unit/
│   ├── test_rss_discovery.py            # NEW: RSS feed parser
│   ├── test_ytdlp_metadata.py           # NEW: yt-dlp wrapper
│   ├── test_channel_search.py           # NEW: search ranking + dedup
│   ├── test_channel_resolve.py          # NEW: handle/URL → channel_id
│   ├── test_channel_registry.py         # NEW: add/remove/list state
│   ├── test_discover_videos.py          # NEW: registry-aware discover
│   ├── test_gemini_client.py            # EXISTING: keep, kept untouched
│   ├── test_state.py                    # EXISTING: keep
│   └── test_existing_youtube_client.py  # MOVED: legacy regex/parsing tests
├── integration/
│   ├── test_rss_real.py                 # hit real YouTube RSS
│   ├── test_ytdlp_real.py               # real yt-dlp metadata fetch
│   ├── test_search_real.py              # real channel search
│   ├── test_resolve_real.py             # real handle resolution
│   ├── test_analyze_real.py             # ONE Gemini analyze for ~15s video (~$0.01)
│   └── fixtures/                        # JSON snapshots for offline replay
│       ├── rss_forward_guidance.xml
│       ├── ytdlp_search_results.json
│       └── channel_about_pages/
└── openclaw/
    ├── README.md                        # how to run the test profile
    ├── conftest.py                      # spins up profile, message helper, registry helper
    ├── profile_template/
    │   ├── openclaw.json
    │   └── workspace-test/
    │       ├── SOUL.md
    │       └── AGENTS.md
    ├── test_search_clear_queries.py     # clear NL queries → correct channel adds
    ├── test_search_vague_queries.py     # ambiguous queries → clarification, no false adds
    ├── test_lookup_only.py              # "does X have a channel?" → no mutation
    ├── test_removal_and_list.py         # remove + list operations
    └── test_video_summary.py            # full discover → analyze → check output structure
```

## Layer 3: OpenClaw test profile setup

### 1. Install OpenClaw on Mac

OpenClaw is currently only deployed on DGX. For the test profile, install locally on Mac (avoids SSH overhead, matches OSS-contributor experience).

```bash
# (verify exact package name first — likely npm-distributed)
nvm use 22 && npm install -g <openclaw-package>
which openclaw  # should resolve under nvm path
```

If npm-distributed unavailable, fall back to running on DGX with `--profile video-summarizer-test`. Same isolation, slower iteration.

### 2. Initialize the isolated profile

```bash
openclaw --profile video-summarizer-test onboard
# Creates ~/.openclaw-video-summarizer-test/{openclaw.json, agents/, ...}
```

This produces a totally separate state tree. Nothing in `~/.openclaw/` (finance) or `~/.openclaw-career/` is touched.

### 3. Configure `~/.openclaw-video-summarizer-test/openclaw.json`

Minimum config — single agent, single MCP, no Telegram, cheap model:

```json
{
  "gateway": {
    "port": 19002,
    "host": "127.0.0.1"
  },
  "models": {
    "providers": {
      "google": { "apiKey": "${GEMINI_API_KEY}" }
    }
  },
  "agents": {
    "list": [{ "id": "testbot" }],
    "defaults": {
      "model": { "primary": "google/gemini-3-flash-preview" }
    }
  },
  "mcp": {
    "servers": {
      "video-summarizer": {
        "command": "/Users/kai/Documents/test/video-summarizer-mcp/start_server.sh",
        "env": {
          "GEMINI_API_KEY": "${GEMINI_API_KEY}",
          "VIDEO_ANALYSIS_STATE_PATH": "${HOME}/.openclaw-video-summarizer-test/video-state.json",
          "VIDEO_ANALYSIS_CHANNELS_PATH": "${HOME}/.openclaw-video-summarizer-test/channels.json",
          "VIDEO_ANALYSIS_BATCH_METADATA_PATH": "${HOME}/.openclaw-video-summarizer-test/batches.json"
        }
      }
    }
  },
  "channels": {}
}
```

**Why these knobs:**
- `gateway.port: 19002` — never collides with finance (18789) or career (whatever it uses)
- Only Google provider — no Anthropic/xAI/Ollama clutter
- `gemini-3-flash-preview` — cheapest model that handles long tool chains; ~10× cheaper than Pro for the same flow
- MCP env paths under the profile dir — registry/state never crosses into finance state files
- `channels: {}` — no Telegram. Tests use `openclaw agent --message` directly, which is more programmatic than chat.

### 4. Test agent persona

`~/.openclaw-video-summarizer-test/workspace-test/SOUL.md`:

```markdown
# Test Agent — video-summarizer

You exist to test the video-summarizer MCP. Behavior:
- When user asks to add/remove/list channels, use the channel registry tools
- When user asks to look up info, do NOT mutate the registry — search and report
- For ambiguous requests (multiple matches, vague topics), ask for clarification rather than guessing
- For video summary requests, use analyze_video_start + poll analyze_video_result
- Be concise — single-paragraph responses unless presenting a list of candidates
```

Single-purpose persona. No domain bias. Mirrors how a generic LLM caller would interact.

### 5. Test driver — programmatic message sending

`tests/openclaw/conftest.py` provides a helper that runs:

```bash
openclaw --profile video-summarizer-test agent \
  --agent testbot \
  --message "<test query>" \
  --reply-channel stdout \
  --format json
```

Captures the JSON response (model output + tool call log). Tests assert on:
1. **Tool calls made** (which tools, with what args)
2. **Registry final state** (read `channels.json` directly)
3. **Response text** (regex/keyword match for clarification phrases or candidate listings)

Each test starts by **resetting** `channels.json` to empty so tests don't bleed into each other.

### 6. Cost ceiling

Per full E2E run:
- Channel search/registry tests (~14 cases): each <2k tokens via Flash → ~$0.01 total
- Video analysis tests (~6 cases, includes one full ~30min podcast analysis): ~$0.20–0.40
- Total: **<$0.50 per full E2E sweep**

Budget guard: skip `test_video_summary.py` long-form cases by default; enable with `--run-expensive` flag.

---

## Test cases — channel search & discovery

For each NL test: starting state is **empty registry**. Pass criteria are evaluated on the **registry state** and **tool calls** after one agent turn (or one user-confirmation round-trip where applicable).

### Clear queries (high-confidence add expected)

**TC-S1: Exact channel name**
- Query: `"Add Forward Guidance to my channel list"`
- Expected tool calls: `search_youtube_channels("Forward Guidance")` → `add_tracked_channel(channel_id=<UCxxx>, name="Forward Guidance", ...)`
- Pass: Registry contains exactly one entry; `name` contains "Forward Guidance" (case-insensitive); `channel_id` starts with `UC`.

**TC-S2: Direct URL**
- Query: `"Add https://www.youtube.com/@AndrejKarpathy to channels"`
- Expected: `resolve_youtube_channel("@AndrejKarpathy")` (NOT search) → `add_tracked_channel(...)`
- Pass: Registry has one entry whose `name` ≈ "Andrej Karpathy"; no `search_youtube_channels` call in log.

**TC-S3: Bare handle**
- Query: `"Add @lexfridman"`
- Expected: `resolve_youtube_channel("@lexfridman")` → `add_tracked_channel(...)`
- Pass: One registry entry with name containing "Lex Fridman".

**TC-S4: Multiple-word channel name**
- Query: `"Add the All-In podcast YouTube channel"`
- Expected: `search_youtube_channels(...)` → top candidate selected → `add_tracked_channel(...)`
- Pass: One entry; name contains "All-In" or "All In".

**TC-S5: Multi-add in one turn**
- Query: `"Add Forward Guidance and Macro Voices"`
- Expected: TWO `add_tracked_channel` calls (one per channel)
- Pass: Registry size = 2; both entries present.

**TC-S6: Person → resolves to their show**
- Query: `"Add Erik Townsend"`
- Expected: search → presents Macro Voices (his show) → asks user to confirm before adding (because "Erik Townsend" alone doesn't have a personal channel; this is interpretive)
- Pass: NO add on first turn; agent response mentions "Macro Voices" and asks for confirmation. After follow-up "yes": registry has one entry.

### Vague queries (no false adds; agent must clarify or refuse)

**TC-V1: Topic only, no channel name**
- Query: `"Add a good YouTube channel about advanced semiconductor packaging"`
- Expected: agent presents 2–4 candidate channels with rationale; does NOT add any.
- Pass: Registry empty after turn; response contains ≥2 channel candidates.

**TC-V2: Misremembered name**
- Query: `"Add that podcast — Forward Guidance? Or Forward Markets?"`
- Expected: search both terms; report findings; ask user.
- Pass: Registry empty; response mentions "Forward Guidance" was found and "Forward Markets" was/wasn't.

**TC-V3: Too generic**
- Query: `"Add a good investing podcast"`
- Expected: agent refuses to guess; asks for narrower input.
- Pass: Registry empty; no `add_tracked_channel` call; response contains a clarifying question (`?` present).

**TC-V4: Disambiguation needed (multiple matches, similar prominence)**
- Query: `"Add Joe Rogan"`
- Expected: search returns JRE main + JRE Clips + others; agent presents top 2–3 with sub counts; asks which.
- Pass: Registry empty after first turn; response lists multiple options. After follow-up "the main one": registry has the highest-subscribed JRE channel.

**TC-V5: Cancel mid-flow**
- Query 1: `"Add Forward Guidance"` — agent confirms or asks
- Query 2: `"actually never mind"`
- Pass: Registry empty after turn 2.

**TC-V6: Channel with confusing name (verify, don't fabricate)**
- Query: `"Add Bloomberg's Odd Lots podcast"`
- Expected: search → finds Bloomberg Podcasts channel (Odd Lots lives there) OR a dedicated Odd Lots channel if exists → presents finding to user
- Pass: Registry empty until user confirms; no fabricated channel_id (we verify the added channel_id resolves on YouTube).

**TC-V7: Misspelling tolerance**
- Query: `"Add Ferward Guidence podcast"` (typos)
- Expected: search still finds Forward Guidance; agent flags "did you mean Forward Guidance?" and confirms.
- Pass: Registry contains Forward Guidance after confirmation; no entry with the misspelled name.

**TC-V8: Non-English channel**
- Query: `"Add 中国财经报道"` (Chinese name)
- Expected: search handles non-ASCII; either resolves or asks user to provide URL.
- Pass: No crash; either correct add or clean clarification ask.

### Lookup-only queries (must not mutate)

**TC-L1: "Does X have a channel?"**
- Query: `"Does Andrej Karpathy have a YouTube channel?"`
- Expected: `search_youtube_channels` or `resolve_youtube_channel`; NO `add_tracked_channel`.
- Pass: Registry unchanged (still empty); response describes channel.

**TC-L2: Browsing**
- Query: `"What does the Forward Guidance channel cover lately?"`
- Expected: `search_youtube_channels` + `get_channel_metadata`; NO add.
- Pass: Registry unchanged; response mentions recent video titles.

### List & remove

**TC-R1: List empty**
- Setup: empty registry
- Query: `"What channels am I tracking?"`
- Expected: `list_tracked_channels()` → response says nothing tracked.
- Pass: response contains "no channels" or equivalent (case-insensitive).

**TC-R2: List populated**
- Setup: pre-seed registry with 3 channels
- Query: `"What channels am I tracking?"`
- Pass: Response mentions all 3 channel names.

**TC-R3: Remove by name**
- Setup: registry has Forward Guidance + Macro Voices
- Query: `"Remove Forward Guidance from my channels"`
- Expected: `list_tracked_channels` → match by name → `remove_tracked_channel(channel_id)`.
- Pass: Registry has only Macro Voices remaining.

**TC-R4: Remove ambiguous**
- Setup: registry has two channels both named "Markets" (synthetic test)
- Query: `"Remove Markets"`
- Expected: agent asks which one.
- Pass: Both entries still present after turn; response has clarifying question.

---

## Test cases — video summary output

These exercise `discover_new_videos` and `analyze_video_start/result` against real Gemini and YouTube. Each picks a known stable fixture URL.

**TC-A1: Standard podcast (~30–60 min)**
- Fixture: a known podcast video URL (pick one from Forward Guidance ~30 min — short for cost)
- Expected: full pipeline succeeds.
- Pass criteria:
  - `success: true`
  - `analysis` contains both `## Video Summary & Key Moments` and `## Investment Recommendations` headers
  - `timestamps_valid: true`
  - At least 3 timestamps `(MM:SS)` present in analysis
  - All `youtube.com/watch?v=` URLs in output match the input video_id (URL-rewrite applied)
  - `vaneck_excluded` correctly true/false based on content

**TC-A2: Short video (<10 min) — discovery filter**
- Setup: call `discover_new_videos([known_short_video_channel])` with `min_duration_seconds=600`
- Expected: video lands in `skipped` with reason `too_short_<N>s`, not in `new_videos`
- Pass: Skipped list contains expected video_id with correct reason.

**TC-A3: Livestream — discovery filter**
- Setup: call `discover_new_videos` against a channel known to have an active livestream
- Expected: livestream skipped with reason `livestream`
- Pass: Skipped list contains the livestream video_id.

**TC-A4: Invalid URL — fail fast**
- Query: `analyze_video_start("https://www.youtube.com/watch?v=NOTAREALVIDEO123")`
- Expected: poll returns `status: "error"`, error mentions "video not found"
- Pass: Error returned within ~5s; no Gemini token spend (assert by checking response shape — error came from YT metadata fetch, not Gemini).

**TC-A5: Retry on empty Gemini output**
- Setup: monkeypatch Gemini to return empty string on attempt 1, valid output on attempt 2
- Expected: `attempts: 2`, `success: true`
- Pass: Attempts counter increments; final result is the valid output.

**TC-A6: Timestamp validation rejects out-of-bounds**
- Setup: synthetic Gemini output with timestamp `(99:99)` for a 30-min video
- Expected: `timestamps_valid: false`
- Pass: Validation flag false; analysis text still returned.

**TC-A7: VanEck filter logic**
- Setup: synthetic Gemini outputs (a) containing "VanEck Semiconductor ETFs" and (b) without
- Pass: (a) has `vaneck_excluded: true` (treated as containing the sponsor); (b) has `vaneck_excluded: true` only if "vaneck" not present.

**TC-A8: URL rewrite — Gemini hallucinated hash**
- Setup: synthetic Gemini output with `youtube.com/watch?v=FAKEHASH&t=120s` for real video_id `REALxxxxxx`
- Expected: output has `v=REALxxxxxx&t=120s` after rewrite
- Pass: The `&t=120s` suffix preserved; `v=` value replaced.

**TC-A9: Discovery state persistence**
- Setup: call `discover_new_videos([channel])` twice in succession
- Expected: First call returns the latest video (first-run behavior); second returns empty `new_videos` list (state was updated).
- Pass: First call → 1 new video; second call → 0 new videos.

**TC-A10: Channel registry → discover integration**
- Setup: empty registry → `add_tracked_channel(UCxxx, tags=["test"])` → `discover_new_videos(tag="test")` (no channel_ids arg)
- Expected: discover reads from registry, returns videos for that channel.
- Pass: New videos returned without explicit `channel_ids` arg.

---

## Cost & runtime guards

- All integration + OpenClaw tests skip by default unless `RUN_EXPENSIVE_TESTS=1` is set
- `pytest -m "not expensive"` is the CI default
- One full E2E sweep budget: <$0.50
- Each test isolates registry by writing to a tempdir-scoped path via the `VIDEO_ANALYSIS_CHANNELS_PATH` env override
- Real YouTube tests use stable fixtures (channels >100k subs, unlikely to disappear); fallback to recorded fixtures if a channel goes dark

## TDD ordering

Implementation order — write each test first, then code until it passes:

1. **Unit tests for `tools/rss_discovery.py`** — RSS parsing
2. **Unit tests for `tools/ytdlp_metadata.py`** — yt-dlp wrapper
3. **Refactor `discover_new_videos`** to use both — existing test_state.py still passes
4. **Unit tests for `tools/channel_search.py`** — search ranking
5. **Unit tests for `tools/channel_registry.py`** — add/remove/list with tempfile
6. **Wire new MCP tools in `server.py`** — Layer 1 MCP Inspector validates wiring
7. **Integration tests** (TC-A1, TC-A4, TC-A9 minimum) for real-API smoke
8. **OpenClaw test profile config** (this doc, section "Layer 3")
9. **OpenClaw E2E tests** — clear queries (TC-S1–S6) first, then vague (TC-V1–V8), then summary (TC-A1)

Each step's tests must be red before its implementation lands.
