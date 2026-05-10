# Prompt library

Each `.md` file here is a complete analysis prompt you can point the
MCP at via:

```bash
export VIDEO_ANALYSIS_PROMPT_PATH=/path/to/podcast-summarizer-mcp/prompts/<file>.md
```

(Or pass `prompt=...` to `analyze_video_start` / `analyze_videos_batch_start`
for a per-call override.)

| File | Best for |
|---|---|
| `investment-podcast.md` | Macro / investing podcasts (the built-in default) |
| `technical-talk.md` | Engineering conference talks, deep dives, tutorials |
| `interview.md` | Long-form interviews — focus on the guest's story / claims |
| `news-briefing.md` | News digests, market wraps, daily summaries |

These are starting points. Copy the closest one, edit to taste, and
point the env var at your edited file. The MCP re-reads the file on
every analysis call, so iteration is fast — no restart needed.
