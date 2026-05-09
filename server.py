"""MCP Server for YouTube video analysis via Gemini 3 Flash Preview."""

import json
import os
import threading
from typing import Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from tools import channel_search
from tools.channel_registry import ChannelRegistry
from tools.discovery import DiscoveryClient
from tools.gemini_client import GeminiClient
from tools.jobs import store as jobs_store, start_heartbeat_monitor, _now_iso
from tools.models import (
    AnalysisResult,
    ChannelState,
    DiscoverResult,
    SkippedVideo,
    StateSnapshot,
    VideoInfo,
)
from tools.state import StateStore

mcp = FastMCP("Video Summarizer")

GEMINI_KEY = os.environ["GEMINI_API_KEY"]
STATE_PATH = os.environ.get(
    "VIDEO_ANALYSIS_STATE_PATH",
    os.path.expanduser("~/.video-summarizer-mcp/video-state.json"),
)
CHANNELS_PATH = os.environ.get(
    "VIDEO_ANALYSIS_CHANNELS_PATH",
    os.path.expanduser("~/.video-summarizer-mcp/channels.json"),
)

_yt = DiscoveryClient()
_gm = GeminiClient(GEMINI_KEY)
_state = StateStore(STATE_PATH)
_channels = ChannelRegistry(CHANNELS_PATH)


@mcp.tool()
def discover_new_videos(
    channel_ids: Optional[List[str]] = None,
    tag: Optional[str] = None,
    max_per_channel: int = 5,
    min_duration_seconds: int = 600,
) -> dict:
    """Discover new videos from YouTube channels since the last time this MCP saw them.

    Two modes for selecting channels:
      1. Pass `channel_ids` explicitly (legacy behavior).
      2. Omit `channel_ids` and the MCP reads from its tracked-channel registry
         (managed by add_tracked_channel / remove_tracked_channel). Use `tag` to
         filter the registry — e.g. tag="macro" returns only channels tagged macro.

    State is tracked per channel in a server-managed JSON file. On the FIRST call for a
    channel, the most recent video is returned (and state is seeded) so the caller has
    something to analyze without ingesting the entire backlog.

    Filters applied:
      - Livestreams (duration == 0) are skipped
      - Videos shorter than min_duration_seconds are skipped (default 10 minutes)
      - Videos older than or equal to the last-seen video are skipped

    Args:
      channel_ids: Optional list of YouTube channel IDs. If omitted, the registry is used.
      tag: Optional tag filter for registry-based mode (ignored if channel_ids given).
      max_per_channel: How many recent uploads to inspect per channel (default 5)
      min_duration_seconds: Minimum video length to include (default 600 = 10min)

    Returns:
      DiscoverResult with new_videos (newest first), skipped, channels_processed,
      and first_run_channels (channels that had no prior state).
    """
    new_videos: List[VideoInfo] = []
    skipped: List[SkippedVideo] = []
    first_run: List[str] = []

    if channel_ids is None:
        channel_ids = _channels.get_channel_ids(tag=tag)

    for cid in channel_ids:
        last_seen = _state.get_last_video_id(cid)
        recent = _yt.get_channel_videos(
            cid, max_results=max_per_channel, min_analysis_seconds=min_duration_seconds
        )
        if not recent:
            continue

        if last_seen is None:
            top = recent[0]
            if top["duration"] == 0:
                skipped.append(SkippedVideo(video_id=top["video_id"], title=top.get("title"), reason="livestream"))
            elif top["duration"] < min_duration_seconds:
                skipped.append(
                    SkippedVideo(video_id=top["video_id"], title=top.get("title"), reason=f"too_short_{top['duration']}s")
                )
            else:
                new_videos.append(VideoInfo(**top))
                first_run.append(cid)
            _state.update_channel(cid, top["video_id"], top["published_at"], analyzed_at=None)
            continue

        newest_to_seed: Optional[dict] = None
        for v in recent:
            if v["video_id"] == last_seen:
                break
            if newest_to_seed is None:
                newest_to_seed = v
            if v["duration"] == 0:
                skipped.append(SkippedVideo(video_id=v["video_id"], title=v.get("title"), reason="livestream"))
                continue
            if v["duration"] < min_duration_seconds:
                skipped.append(
                    SkippedVideo(video_id=v["video_id"], title=v.get("title"), reason=f"too_short_{v['duration']}s")
                )
                continue
            new_videos.append(VideoInfo(**v))

        if newest_to_seed is not None:
            _state.update_channel(cid, newest_to_seed["video_id"], newest_to_seed["published_at"], analyzed_at=None)

    return DiscoverResult(
        new_videos=new_videos,
        skipped=skipped,
        channels_processed=len(channel_ids),
        first_run_channels=first_run,
    ).model_dump()


def _run_analysis(
    job_id: str,
    video_url: str,
    max_retries: int,
    prompt: Optional[str] = None,
) -> None:
    """Background worker: metadata lookup + Gemini analysis, writes to jobs_store.

    A sibling heartbeat-monitor thread writes `last_heartbeat_at` every
    HEARTBEAT_INTERVAL_SECONDS so that polls from other MCP processes can detect
    a dead worker (this process crashed or got killed mid-analysis) and flip
    the job to error instead of leaving it stuck in "running".
    """
    jobs_store.update(job_id, status="running", started_at=_now_iso(), last_heartbeat_at=_now_iso())
    stop_heartbeat = start_heartbeat_monitor(job_id)
    try:
        info = _yt.get_video_info(video_url)
        if info is None:
            jobs_store.update(
                job_id,
                status="error",
                error="video not found or YouTube API error",
                finished_at=_now_iso(),
            )
            return
        result = _gm.analyze_video(
            video_url=video_url,
            video_id=info["video_id"],
            video_duration=info["duration"],
            max_retries=max_retries,
            prompt=prompt,
        )
        payload = AnalysisResult(**result).model_dump()
        if payload.get("success"):
            jobs_store.update(
                job_id,
                status="done",
                result=payload,
                attempts=payload.get("attempts", 0),
                finished_at=_now_iso(),
            )
        else:
            jobs_store.update(
                job_id,
                status="error",
                result=payload,
                error=payload.get("error") or "analysis failed",
                attempts=payload.get("attempts", 0),
                finished_at=_now_iso(),
            )
    except Exception as exc:  # noqa: BLE001 — job-boundary, report to caller
        jobs_store.update(
            job_id,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
            finished_at=_now_iso(),
        )
    finally:
        stop_heartbeat.set()


@mcp.tool()
def analyze_video_start(
    video_url: str,
    max_retries: int = 3,
    prompt: Optional[str] = None,
) -> dict:
    """Launch Gemini video analysis as a background job. Returns in <1s with a job_id.

    **This is the default for both single-video and multi-video requests.**
    For multiple videos, fire this tool N times back-to-back (each call
    returns in <1s with its own job_id) — the analyses run concurrently
    in background threads, so wall-clock time is bounded by the slowest
    single video (~3-10 min), NOT N × per-video time. Then poll
    `analyze_video_result(job_id)` for each job_id.

    Use `analyze_videos_batch_start` ONLY when the user explicitly says
    "no rush", "overnight", or for scheduled / cron digests where minutes-
    to-hours latency is acceptable in exchange for 50% cost savings.

    Podcast-length videos take Gemini 3-15 min to analyze, which exceeds typical
    MCP host request timeouts (Claude Desktop, Claude Code, OpenClaw all cap
    individual tool calls at ~60s). This tool spawns a background thread and
    returns immediately; poll `analyze_video_result(job_id)` to get the
    finished analysis.

    Does NOT update discovery state. Caller is responsible for calling
    discover_new_videos (which manages state) beforehand.

    Args:
      video_url: Full YouTube URL (https://www.youtube.com/watch?v=...)
      max_retries: How many Gemini retries on empty/short output (default 3)
      prompt: Optional override for the analysis prompt. If None, the
        default ships with an investment-podcast persona — set
        $VIDEO_ANALYSIS_PROMPT_PATH to change the host-wide default,
        or pass `prompt` here for a one-off override (e.g. "summarize
        this technical talk in 5 bullets").

    Returns:
      { job_id, video_url, status: "pending" }
    """
    job_id = jobs_store.create(video_url)
    threading.Thread(
        target=_run_analysis,
        args=(job_id, video_url, max_retries, prompt),
        daemon=True,
        name=f"analyze-{job_id[:8]}",
    ).start()
    state = jobs_store.get(job_id) or {}
    return {
        "job_id": job_id,
        "video_url": video_url,
        "status": state.get("status", "pending"),
    }


@mcp.tool()
def analyze_video_result(job_id: str, wait_seconds: int = 10) -> dict:
    """Poll for an analyze_video_start result. Blocks up to wait_seconds for a state change.

    Default wait_seconds=10 keeps the blocking window well under the typical
    60s MCP request timeout enforced by hosts. Smaller blocking window =
    guaranteed return well under 60s at the cost of more poll round-trips
    (cheap, ~1KB per poll).

    Recommended pattern: call with wait_seconds=10 each poll. Cap total
    polling at ~90 iterations (~15 min) — most podcasts finish within 3-5 min.
    If a poll returns an MCP-transport error (not a `status: "error"`
    payload), the underlying job is likely still alive on the server —
    re-poll with the SAME job_id rather than starting over.

    Args:
      job_id: The id returned by analyze_video_start
      wait_seconds: How many seconds to block waiting for completion (default 10,
        clamped to [0, 55] to stay safely under the MCP 60s request timeout)

    Returns:
      { job_id, status, video_url, created_at, started_at, finished_at,
        result?, error?, attempts }
      - status="pending" or "running": still processing — poll again.
      - status="done": result holds the AnalysisResult dict (analysis, timestamps_valid,
        video_duration, vaneck_excluded, attempts, error=null).
      - status="error": error holds the message; result may hold a partial AnalysisResult.
      - status="not_found": job_id unknown (job purged after 6h TTL or wrong id).
    """
    wait_seconds = max(0, min(int(wait_seconds), 55))
    state = jobs_store.wait_for_terminal(job_id, wait_seconds=wait_seconds)
    if state is None:
        return {"status": "not_found", "job_id": job_id}
    return state


_BATCH_METADATA_PATH = os.environ.get(
    "VIDEO_ANALYSIS_BATCH_METADATA_PATH",
    os.path.expanduser("~/.video-summarizer-mcp/batches.json"),
)
_batch_meta_lock = threading.Lock()


def _load_batch_metadata() -> Dict[str, dict]:
    if not os.path.exists(_BATCH_METADATA_PATH):
        return {}
    try:
        with open(_BATCH_METADATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_batch_metadata(data: Dict[str, dict]) -> None:
    os.makedirs(os.path.dirname(_BATCH_METADATA_PATH), exist_ok=True)
    tmp = _BATCH_METADATA_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, _BATCH_METADATA_PATH)


@mcp.tool()
def analyze_videos_batch_start(
    video_urls: List[str],
    prompt: Optional[str] = None,
) -> dict:
    """Submit a batch of YouTube videos to Gemini Batch API for async analysis.

    **Use this ONLY when the user explicitly says "no rush", "overnight",
    "do it later", or for scheduled cron digests.** Batch is 50% cheaper
    than `analyze_video_start` but the wall-clock SLA is up to 24 hours
    (typically 15-60 min). For interactive requests — even multi-video
    ones like "summarize today's new videos" — prefer `analyze_video_start`
    fired N times in parallel (~5-10 min wall-clock for any N).

    Args:
      video_urls: List of full YouTube URLs (https://www.youtube.com/watch?v=...)
      prompt: Optional override for the analysis prompt. If None, the
        default ships with an investment-podcast persona — set
        $VIDEO_ANALYSIS_PROMPT_PATH to change the host-wide default,
        or pass `prompt` here for a per-batch override.

    Returns:
      { batch_job_name, video_count, video_urls, status: "pending",
        skipped: [{video_url, reason}] }
    """
    if not video_urls:
        return {"error": "video_urls must not be empty"}

    entries: List[dict] = []
    metadata: Dict[str, dict] = {}
    skipped: List[dict] = []

    for url in video_urls:
        info = _yt.get_video_info(url)
        if info is None:
            skipped.append({"video_url": url, "reason": "video not found"})
            continue
        key = info["video_id"]
        entries.append({"key": key, "video_url": url})
        metadata[key] = {
            "video_url": url,
            "video_id": info["video_id"],
            "video_duration": info["duration"],
            "title": info.get("title"),
        }

    if not entries:
        return {"error": "no valid videos to analyze", "skipped": skipped}

    batch_job_name = _gm.submit_batch(
        entries, display_name="video-analysis-batch", prompt=prompt
    )

    with _batch_meta_lock:
        all_meta = _load_batch_metadata()
        all_meta[batch_job_name] = {
            "video_urls": [e["video_url"] for e in entries],
            "metadata": metadata,
        }
        _save_batch_metadata(all_meta)

    return {
        "batch_job_name": batch_job_name,
        "video_count": len(entries),
        "video_urls": [e["video_url"] for e in entries],
        "status": "pending",
        "skipped": skipped,
    }


@mcp.tool()
def analyze_videos_batch_result(batch_job_name: str) -> dict:
    """Poll the Gemini Batch API once; if done, return per-video results.

    Call repeatedly until status is SUCCEEDED / FAILED / CANCELLED / EXPIRED.
    Recommended cadence: every 30-60s.

    Args:
      batch_job_name: Name returned by analyze_videos_batch_start

    Returns:
      { status, batch_job_name, results? (on SUCCEEDED), error? (on terminal failure) }
      - results: { video_id: AnalysisResult }
    """
    try:
        state = _gm.get_batch_state(batch_job_name)
    except Exception as e:  # noqa: BLE001 — wrap to MCP-safe
        return {"status": "error", "error": f"{type(e).__name__}: {e}", "batch_job_name": batch_job_name}

    if state not in _gm.BATCH_TERMINAL_STATES:
        return {"status": state, "batch_job_name": batch_job_name}

    if state != "JOB_STATE_SUCCEEDED":
        return {"status": state, "error": f"batch ended in state {state}", "batch_job_name": batch_job_name}

    with _batch_meta_lock:
        all_meta = _load_batch_metadata()
    info = all_meta.get(batch_job_name)
    if info is None:
        return {
            "status": state,
            "error": "batch metadata not found — was this batch submitted via analyze_videos_batch_start?",
            "batch_job_name": batch_job_name,
        }

    results = _gm.fetch_batch_results(batch_job_name, info["metadata"])
    return {
        "status": state,
        "batch_job_name": batch_job_name,
        "video_count": len(results),
        "results": results,
    }


@mcp.tool()
def get_video_info(video_url: str, min_duration_seconds: int = 600) -> dict:
    """Cheap metadata-only lookup for a single YouTube video (1 YouTube API unit).

    Use this to inspect a video's title, duration, channel, and publish date without
    burning Gemini tokens.

    Args:
      video_url: Full YouTube URL or just the video ID
      min_duration_seconds: Threshold for the excluded_from_analysis flag (default 600)
    """
    info = _yt.get_video_info(video_url, min_analysis_seconds=min_duration_seconds)
    if info is None:
        return {"error": "video not found", "video_url": video_url}
    return VideoInfo(**info).model_dump()


@mcp.tool()
def get_state(channel_ids: Optional[List[str]] = None) -> dict:
    """Read-only view of the discovery state file.

    Returns the last-seen video_id, published_at, and analyzed_at for each channel
    that has been processed. Useful for answering "what's the latest video I've seen
    from channel X?" without re-hitting YouTube.

    Args:
      channel_ids: If provided, return only these channels. Otherwise return all.
    """
    rows = _state.snapshot(channel_ids)
    return StateSnapshot(channels=[ChannelState(**r) for r in rows]).model_dump()


# ---------- Channel discovery (no-key YouTube search) ----------


@mcp.tool()
def search_youtube_channels(query: str, max_results: int = 5) -> dict:
    """Search YouTube for channels matching a free-text query.

    Use this when the user names a channel ("Forward Guidance", "All-In podcast")
    or describes one ("a good macro investing channel") and you need to identify
    candidate channels before adding to the registry.

    No YouTube API key required — uses yt-dlp scraping under the hood.

    Args:
      query: Free-text search term, e.g. "Forward Guidance" or "macro investing".
      max_results: How many distinct channels to return, ranked by relevance (default 5).

    Returns:
      { "candidates": [
          { "channel_id", "name", "channel_url", "hit_count",
            "confidence_score" },
          ...
        ] }
      Empty list if nothing matches. Subscriber count + recent videos are NOT
      populated here — call get_channel_metadata(channel_id) for that.
    """
    candidates = channel_search.search_youtube_channels(query, max_results=max_results)
    return {"candidates": candidates, "query": query}


@mcp.tool()
def resolve_youtube_channel(handle_or_url: str) -> dict:
    """Resolve a YouTube handle (@name) or channel URL to channel metadata.

    Use this when the user gives an exact handle or URL — skips the search step.
    Returns None / error for video URLs, free-text strings, or channels that
    can't be loaded.

    Args:
      handle_or_url: e.g. "@ForwardGuidance" or
                    "https://www.youtube.com/@ForwardGuidance" or
                    "https://www.youtube.com/channel/UCxxxxx".

    Returns:
      { channel_id, name, handle, description, subscriber_count,
        channel_url, recent_video_titles } on success.
      { "error": "..." } on failure.
    """
    info = channel_search.resolve_youtube_channel(handle_or_url)
    if info is None:
        return {"error": "could not resolve channel", "input": handle_or_url}
    return info


@mcp.tool()
def get_channel_metadata(channel_id: str) -> dict:
    """Fetch full metadata for a known channel_id.

    Use after search_youtube_channels to enrich a candidate with subscriber
    count, description, and recent video titles before presenting to the user.

    Args:
      channel_id: YouTube channel ID (must start with "UC").

    Returns:
      { channel_id, name, handle, description, subscriber_count,
        channel_url, recent_video_titles }.
    """
    try:
        return channel_search.get_channel_metadata(channel_id)
    except ValueError as e:
        return {"error": str(e), "channel_id": channel_id}


# ---------- Tracked-channel registry (MCP-owned state) ----------


@mcp.tool()
def add_tracked_channel(
    channel_id: str,
    name: str,
    handle: Optional[str] = None,
    tags: Optional[List[str]] = None,
) -> dict:
    """Add a channel to the registry. **You MUST call this tool to add a
    channel — claiming "I've added X" without invoking it leaves the
    user's registry empty and the user has no way to know until they
    next ask "what am I tracking" and discover the missing channels.**

    Idempotent: re-adding the same channel_id updates name/handle/tags but
    preserves the original added_at timestamp. Tags are arbitrary strings —
    use them to group channels (e.g. ["macro"], ["semis", "podcast"]).

    Args:
      channel_id: YouTube channel ID, must start with "UC".
      name: Display name (the user-friendly label).
      handle: Optional "@handle" (cosmetic, helps users identify the channel).
      tags: Optional list of grouping strings (default empty).

    Returns:
      { added: true,
        channel: {channel_id, name, handle, tags, added_at},
        registry_total: <int>,
        registry_now_contains: [name, name, ...],   # alphabetical
        user_facing_message: "Added X. Registry now has N channels: ..." }
      Use `user_facing_message` verbatim when telling the user the result —
      it carries the freshly-verified state and prevents misreporting.
    """
    try:
        result = _channels.add(
            channel_id=channel_id, name=name, handle=handle, tags=tags or []
        )
    except ValueError as e:
        return {"error": str(e), "channel_id": channel_id}
    record = result["record"]
    total = result["total_count"]
    names = result["all_names"]
    return {
        "added": True,
        "channel": {"channel_id": channel_id, **record},
        "registry_total": total,
        "registry_now_contains": names,
        "user_facing_message": (
            f"Added {name} ({handle or channel_id}). "
            f"Registry now has {total} channel{'s' if total != 1 else ''}: "
            f"{', '.join(names)}."
        ),
    }


@mcp.tool()
def remove_tracked_channel(channel_id: str) -> dict:
    """Remove a channel from the registry. **You MUST call this tool to
    remove — claiming "I've removed X" without invoking it leaves the
    channel still tracked and produces silently wrong state.**

    No-op (returns removed=false) if the channel_id wasn't tracked.
    Does NOT clear the per-channel last-seen state, so re-adding later
    won't re-ingest the backlog.

    Args:
      channel_id: YouTube channel ID.

    Returns:
      { removed: true|false,
        channel_id,
        registry_total: <int>,
        registry_now_contains: [name, name, ...],
        user_facing_message: "Removed X. Registry now has N channels: ..." }
      Use `user_facing_message` verbatim when reporting back to the user.
    """
    result = _channels.remove(channel_id)
    removed = result["removed"]
    total = result["total_count"]
    names = result["all_names"]
    if removed:
        msg = (
            f"Removed channel {channel_id}. "
            f"Registry now has {total} channel{'s' if total != 1 else ''}"
            + (f": {', '.join(names)}." if names else ".")
        )
    else:
        msg = (
            f"No change — {channel_id} was not in the registry. "
            f"Registry still has {total} channel{'s' if total != 1 else ''}"
            + (f": {', '.join(names)}." if names else ".")
        )
    return {
        "removed": removed,
        "channel_id": channel_id,
        "registry_total": total,
        "registry_now_contains": names,
        "user_facing_message": msg,
    }


@mcp.tool()
def list_tracked_channels(tag: Optional[str] = None) -> dict:
    """List all channels currently in the registry. **Always call this
    tool when the user asks "what am I tracking" or similar — never
    answer from memory or prior conversation context, since the
    registry can be mutated by other clients between turns.**

    Args:
      tag: If provided, return only channels carrying this tag.

    Returns:
      { channels: [{channel_id, name, handle, tags, added_at}, ...],
        count, tag,
        user_facing_message: "Tracking N channels: ..." or "No channels tracked." }
      Use `user_facing_message` verbatim when reporting back.
    """
    channels = _channels.list_channels(tag=tag)
    count = len(channels)
    if count == 0:
        msg = "No channels are currently tracked."
        if tag:
            msg = f"No channels tagged {tag!r} are currently tracked."
    else:
        names = [c.get("name", c["channel_id"]) for c in channels]
        suffix = f" tagged {tag!r}" if tag else ""
        msg = (
            f"Tracking {count} channel{'s' if count != 1 else ''}"
            f"{suffix}: {', '.join(names)}."
        )
    return {
        "channels": channels,
        "count": count,
        "tag": tag,
        "user_facing_message": msg,
    }


def main() -> None:
    """Entry point for the `video-summarizer-mcp` console script.

    Communicates over stdio with any MCP host (Claude Desktop, Claude
    Code, OpenClaw, etc.). Process lifecycle is the host's responsibility.
    """
    mcp.run()


if __name__ == "__main__":
    main()
