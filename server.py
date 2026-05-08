"""MCP Server for YouTube video analysis via Gemini 3 Flash Preview."""

import json
import os
import threading
from typing import Dict, List, Optional

from mcp.server.fastmcp import FastMCP

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
from mcp_idle_watchdog import install_idle_watchdog, track_request

mcp = FastMCP("Video Summarizer")

GEMINI_KEY = os.environ["GEMINI_API_KEY"]
STATE_PATH = os.environ.get(
    "VIDEO_ANALYSIS_STATE_PATH",
    os.path.expanduser("~/.video-summarizer-mcp/video-state.json"),
)

_yt = DiscoveryClient()
_gm = GeminiClient(GEMINI_KEY)
_state = StateStore(STATE_PATH)


@mcp.tool()
@track_request
def discover_new_videos(
    channel_ids: List[str],
    max_per_channel: int = 5,
    min_duration_seconds: int = 600,
) -> dict:
    """Discover new videos from YouTube channels since the last time this MCP saw them.

    State is tracked per channel in a server-managed JSON file. On the FIRST call for a
    channel, the most recent video is returned (and state is seeded) so the caller has
    something to analyze without ingesting the entire backlog.

    Filters applied:
      - Livestreams (duration == 0) are skipped
      - Videos shorter than min_duration_seconds are skipped (default 10 minutes)
      - Videos older than or equal to the last-seen video are skipped

    Args:
      channel_ids: YouTube channel IDs (e.g. "UCkrwgzhIBKccuDsi_SvZtnQ")
      max_per_channel: How many recent uploads to inspect per channel (default 5)
      min_duration_seconds: Minimum video length to include (default 600 = 10min)

    Returns:
      DiscoverResult with new_videos (newest first), skipped, channels_processed,
      and first_run_channels (channels that had no prior state).
    """
    new_videos: List[VideoInfo] = []
    skipped: List[SkippedVideo] = []
    first_run: List[str] = []

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


def _run_analysis(job_id: str, video_url: str, max_retries: int) -> None:
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
@track_request
def analyze_video_start(video_url: str, max_retries: int = 3) -> dict:
    """Launch Gemini video analysis as a background job. Returns in <1s with a job_id.

    Podcast-length videos take Gemini 3-15 min to analyze, which exceeds openclaw's
    60s MCP request timeout. This tool spawns a background thread and returns
    immediately; poll `analyze_video_result(job_id)` to get the finished analysis.

    Does NOT update discovery state. Caller is responsible for calling
    discover_new_videos (which manages state) beforehand.

    Args:
      video_url: Full YouTube URL (https://www.youtube.com/watch?v=...)
      max_retries: How many Gemini retries on empty/short output (default 3)

    Returns:
      { job_id, video_url, status: "pending" }
    """
    job_id = jobs_store.create(video_url)
    threading.Thread(
        target=_run_analysis,
        args=(job_id, video_url, max_retries),
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
@track_request
def analyze_video_result(job_id: str, wait_seconds: int = 10) -> dict:
    """Poll for an analyze_video_start result. Blocks up to wait_seconds for a state change.

    Default wait_seconds=10 was lowered from 45 for symmetry with x-search's
    search_x_result after a 2026-04-22 heartbeat where a blocking 45s wait
    exceeded openclaw's 60s MCP-client cap under concurrent load. Smaller
    blocking window = guaranteed return well under 60s at the cost of more
    poll round-trips (cheap).

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
    os.path.expanduser("~/.openclaw/video-analysis-batches.json"),
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
@track_request
def analyze_videos_batch_start(video_urls: List[str]) -> dict:
    """Submit a batch of YouTube videos to Gemini Batch API for async analysis.

    Batch processing is 50% cheaper than `analyze_video_start` and typically
    completes within minutes (24h SLA max). Use this for daily cron digests
    where you have multiple videos to analyze at once.

    Args:
      video_urls: List of full YouTube URLs (https://www.youtube.com/watch?v=...)

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

    batch_job_name = _gm.submit_batch(entries, display_name="video-analysis-batch")

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
@track_request
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
@track_request
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
@track_request
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


if __name__ == "__main__":
    install_idle_watchdog(
        server_name="video-analysis",
        # idle-exit disabled: holds no constrained external resource (HTTPS
        # to Gemini + YouTube, no persistent handle). Analysis runs in
        # background threads that the per-request tracker doesn't see, so
        # any idle-exit would orphan in-flight jobs. Keep the process alive
        # across long idle gaps to avoid the stale-transport trap.
        idle_timeout_seconds=0,
        # request_max applies only to synchronous tool calls (discover,
        # start, result, get_video_info, get_state) — all fast. Keep a
        # modest guard so a hung MCP request can't permanently wedge it.
        request_max_seconds=120,
    )
    mcp.run()
