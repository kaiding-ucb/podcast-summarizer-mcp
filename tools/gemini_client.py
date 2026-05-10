"""Gemini 3 Flash Preview client for native YouTube video analysis.

Adapted from gemini_analyzer.py. Two changes vs the original:
  1. Prompt is reordered so the Summary always comes before numbered Recommendations.
  2. After Gemini returns, all youtube.com/watch?v=... URLs in the analysis text are
     rewritten to use the real video_id. Gemini systematically hallucinates the URL
     hash even though the (MM:SS) timestamps it generates are correct.
"""

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from google import genai
from google.genai import types

DEFAULT_PROMPT = """You're a podcast analyzer who summarize Youtube videos and distills potential invesmtment recommendations.

## You task ##
Analyze if a video has explicit invesment recommendations:
1.  **Stock** Does this video recommend a specific stock and why?
2.  **Sector** Does this video recommend a specific sector and why?
3.  **Portfolio strategy** Does this video recommend a specific portfolio strategy and why?

If none of the above, focus on giving a simple summary of the video with timestamps to key moments. Timestamps should directly link to the video

## Exclude commercials and sponsors##
Exclude commercials and sponsors from the analysis. For example

This episode is brought to you by VanEck Semiconductor ETFs. You'll hear more about the VanEck Semiconductor ETF, ticker SMH, the largest semiconductor
ETF, and its newer VANX Fab Semiconductor ETF, ticker SMHX, later in the show.

## Output format ##
ALWAYS structure your response in this exact order:

**Section 1 - Video Summary & Key Moments** (always first)
A 2-4 sentence overview of what the video is about, who the speakers are, and the main themes. Then a bulleted list of key moments with timestamps that directly link to the video.

**Section 2 - Investment Recommendations** (always second, numbered 1, 2, 3, ...)
List each distinct stock / sector / portfolio strategy recommendation found in the video, numbered sequentially. For each one include: Recommendation (bullish/bearish/strategy name), Rationale, and supporting timestamped quotes.
If the video contains no investment recommendations, write "No explicit investment recommendations in this video." under Section 2.

### Example output ###

## Video Summary & Key Moments
This episode of *Forward Guidance* features Felix Jauvin, Quinn Thompson, and Tyler Neville discussing the disconnect between high equity valuations and deteriorating macro fundamentals. The conversation covers fake Middle East ceasefires, PCE re-acceleration, and the structural shift from software to AI hardware.
*   (33:04) Macro outlook - fundamentals don't justify SPX 3% off ATH
*   (37:42) CTAs short $37B in US equities, currently in aggressive buy mode
*   (48:47) Core PCE accelerating last 3 months - Fed "higher for longer"

## Investment Recommendations

### 1. Sector: Semiconductors & AI Compute (SMH) - Bullish
*   **Rationale:** Market is underpricing AI compute demand; described as non-linear and a national security imperative.
*   **Timestamps:**
    *   (40:40) "The market is underpricing the demand for compute here..."
    *   (40:12) "The AI complex is likely to lead any true sustainable bull market."

### 2. Portfolio Strategy: Concentrated Portfolios - Bullish
*   **Rationale:** In a macro world dominated by fiat debasement, diversification destroys returns. One clear macro factor explains 90-97% of asset price movements.
*   **Timestamps:**
    *   (0:14-0:17) "Diversification just destroys returns now because you've got one clear macro factor."
    *   (10:12-10:24) "You need concentrated portfolios as opposed to diverse portfolios."
"""

def get_default_prompt() -> str:
    """Return the prompt used when callers don't pass an explicit one.

    Override path: set $VIDEO_ANALYSIS_PROMPT_PATH to a file (tilde
    expansion supported). The file is read fresh on every call so users
    can edit prompts without restarting the MCP. Raises FileNotFoundError
    if the path is set but doesn't exist — fail loud rather than silently
    falling back, since silent fallback would mislead users editing the
    file.
    """
    override = os.environ.get("VIDEO_ANALYSIS_PROMPT_PATH")
    if override:
        return Path(override).expanduser().read_text(encoding="utf-8")
    return DEFAULT_PROMPT


_TIMESTAMP_RE = re.compile(r"\((\d{1,2}):(\d{2})\)")
# Matches the v= parameter inside any youtube.com/watch URL regardless of where v= sits
# in the query string (Gemini sometimes emits ?t=...&v=... instead of ?v=...&t=...).
_VIDEO_V_PARAM_RE = re.compile(r"(youtube\.com/watch\?(?:[^)\s]*?[?&])?v=)[A-Za-z0-9_-]+")


def rewrite_video_urls(text: str, real_video_id: str) -> str:
    """Replace any hallucinated youtube.com/watch v= hashes with the real video_id.

    Preserves any &t=Ns deep-link suffix Gemini emitted, since the seconds offset is correct.
    Handles both ?v=XXX&t=Ns and ?t=Ns&v=XXX orderings.
    """
    return _VIDEO_V_PARAM_RE.sub(rf"\g<1>{real_video_id}", text)


def validate_timestamps(text: str, video_duration_seconds: int) -> bool:
    if video_duration_seconds <= 0:
        return False
    for mm, ss in _TIMESTAMP_RE.findall(text):
        if int(mm) * 60 + int(ss) > video_duration_seconds:
            return False
    return True


class GeminiClient:
    MODEL = "gemini-3-flash-preview"
    MIN_TEXT_LEN = 50
    BATCH_TERMINAL_STATES = {
        "JOB_STATE_SUCCEEDED",
        "JOB_STATE_FAILED",
        "JOB_STATE_CANCELLED",
        "JOB_STATE_EXPIRED",
    }

    def __init__(self, api_key: str):
        self.client = genai.Client(api_key=api_key)

    # ---- Batch API (50% cheaper, async, up to 24h) ----

    def submit_batch(
        self,
        video_entries: List[Dict[str, object]],
        display_name: str = "video-analysis-batch",
        prompt: Optional[str] = None,
    ) -> str:
        """Upload a JSONL of video-analysis requests and create a batch job.

        Each entry must have: `key` (stable id, e.g. video_id), `video_url`.
        Returns the batch job name (e.g. "batches/xxx") used by poll/fetch.

        `prompt` overrides the default for THIS batch. If omitted,
        get_default_prompt() is used (which itself honours
        $VIDEO_ANALYSIS_PROMPT_PATH).
        """
        if not video_entries:
            raise ValueError("video_entries must not be empty")
        prompt_text = prompt if prompt is not None else get_default_prompt()

        lines: List[str] = []
        for e in video_entries:
            key = str(e["key"])
            video_url = str(e["video_url"])
            lines.append(
                json.dumps(
                    {
                        "key": key,
                        "request": {
                            "contents": [
                                {
                                    "role": "user",
                                    "parts": [
                                        {"text": prompt_text},
                                        {
                                            "file_data": {
                                                "mime_type": "video/*",
                                                "file_uri": video_url,
                                            }
                                        },
                                    ],
                                }
                            ],
                            "generation_config": {"media_resolution": "MEDIA_RESOLUTION_LOW"},
                        },
                    },
                    separators=(",", ":"),
                )
            )

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as f:
            f.write("\n".join(lines) + "\n")
            tmp_path = f.name

        try:
            uploaded = self.client.files.upload(
                file=tmp_path,
                config=types.UploadFileConfig(
                    display_name=display_name, mime_type="jsonl"
                ),
            )
            batch_job = self.client.batches.create(
                model=self.MODEL,
                src=uploaded.name,
                config={"display_name": display_name},
            )
            return batch_job.name
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    def get_batch_state(self, batch_job_name: str) -> str:
        """Return the current JOB_STATE_* name for a batch job."""
        job = self.client.batches.get(name=batch_job_name)
        return job.state.name

    def fetch_batch_results(
        self,
        batch_job_name: str,
        video_metadata: Dict[str, Dict[str, object]],
    ) -> Dict[str, dict]:
        """Download the batch output JSONL and return {key: AnalysisResult-dict}.

        `video_metadata` maps the same keys we submitted to
        {video_url, video_id, video_duration} so we can fill the AnalysisResult
        fields identically to the sync path (URL rewrite, timestamp validation,
        vaneck filter).
        """
        job = self.client.batches.get(name=batch_job_name)
        if job.state.name != "JOB_STATE_SUCCEEDED":
            raise RuntimeError(
                f"Batch {batch_job_name} state is {job.state.name}, not SUCCEEDED"
            )

        result_file_name = job.dest.file_name
        raw = self.client.files.download(file=result_file_name)
        text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)

        out: Dict[str, dict] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            key = obj.get("key")
            if key is None or key not in video_metadata:
                continue
            meta = video_metadata[key]
            video_url = str(meta["video_url"])
            video_id = str(meta["video_id"])
            video_duration = int(meta["video_duration"])

            # Response shape: { "response": GenerateContentResponse } or { "error": ... }
            response = obj.get("response") or {}
            err = obj.get("error") or response.get("error")

            analysis_text = ""
            try:
                candidates = response.get("candidates") or []
                if candidates:
                    parts = (candidates[0].get("content") or {}).get("parts") or []
                    analysis_text = "".join(
                        p.get("text", "") for p in parts if isinstance(p, dict)
                    ).strip()
            except Exception as e:  # noqa: BLE001 — parse guard
                err = err or f"parse_error: {e}"

            if not analysis_text or len(analysis_text) < self.MIN_TEXT_LEN:
                out[key] = {
                    "video_url": video_url,
                    "success": False,
                    "analysis": analysis_text,
                    "video_duration": video_duration,
                    "timestamps_valid": False,
                    "vaneck_excluded": True,
                    "attempts": 1,
                    "error": err or f"empty/too-short batch output ({len(analysis_text)} chars)",
                }
                continue

            analysis_text = rewrite_video_urls(analysis_text, video_id)
            out[key] = {
                "video_url": video_url,
                "success": True,
                "analysis": analysis_text,
                "video_duration": video_duration,
                "timestamps_valid": validate_timestamps(analysis_text, video_duration),
                "vaneck_excluded": "vaneck" not in analysis_text.lower(),
                "attempts": 1,
                "error": None,
            }
        return out

    def analyze_video(
        self,
        video_url: str,
        video_id: str,
        video_duration: int,
        max_retries: int = 3,
        prompt: Optional[str] = None,
    ) -> dict:
        """Analyze a video with Gemini. `prompt` overrides the default
        for this call only. If omitted, get_default_prompt() is used."""
        last_error: Optional[str] = None
        prompt_text = prompt if prompt is not None else get_default_prompt()
        for attempt in range(1, max_retries + 1):
            try:
                resp = self.client.models.generate_content(
                    model=self.MODEL,
                    contents=types.Content(
                        parts=[
                            types.Part(text=prompt_text),
                            types.Part(file_data=types.FileData(file_uri=video_url)),
                        ]
                    ),
                    config=types.GenerateContentConfig(media_resolution="MEDIA_RESOLUTION_LOW"),
                )
                text = (resp.text or "").strip()
            except Exception as e:
                last_error = f"exception on attempt {attempt}: {e}"
                continue

            if not text or len(text) < self.MIN_TEXT_LEN or text.strip(".") == "":
                last_error = f"empty/too-short output on attempt {attempt} ({len(text)} chars)"
                continue

            text = rewrite_video_urls(text, video_id)
            return {
                "video_url": video_url,
                "success": True,
                "analysis": text,
                "video_duration": video_duration,
                "timestamps_valid": validate_timestamps(text, video_duration),
                "vaneck_excluded": "vaneck" not in text.lower(),
                "attempts": attempt,
                "error": None,
            }

        return {
            "video_url": video_url,
            "success": False,
            "analysis": "",
            "video_duration": video_duration,
            "timestamps_valid": False,
            "vaneck_excluded": True,
            "attempts": max_retries,
            "error": last_error or "unknown failure",
        }
