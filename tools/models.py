"""Pydantic models for video-analysis-mcp tool returns."""

from typing import List, Optional

from pydantic import BaseModel, Field


class VideoInfo(BaseModel):
    video_id: str
    title: str
    channel_name: str
    channel_id: Optional[str] = None
    duration: int = Field(description="Duration in seconds; 0 = livestream or unknown")
    published_at: str = Field(description="ISO 8601 UTC timestamp")
    url: str
    excluded_from_analysis: bool = Field(
        default=False,
        description="True if duration is below the configured min for Gemini analysis",
    )


class SkippedVideo(BaseModel):
    video_id: str
    title: Optional[str] = None
    reason: str = Field(
        description="One of: livestream, too_short, already_seen, duration_unknown"
    )


class DiscoverResult(BaseModel):
    new_videos: List[VideoInfo] = Field(
        description="Videos newer than the last-seen video for each channel, after filters"
    )
    skipped: List[SkippedVideo] = Field(default_factory=list)
    channels_processed: int
    first_run_channels: List[str] = Field(
        default_factory=list,
        description="Channels with no prior state — seeded with the latest video and returned as a single new video",
    )


class AnalysisResult(BaseModel):
    video_url: str
    success: bool
    analysis: str = Field(description="Gemini's analysis text, with deep-link URLs rewritten to the real video_id")
    video_duration: int
    timestamps_valid: bool = Field(
        description="True if all (MM:SS) timestamps in the analysis are within the video duration"
    )
    vaneck_excluded: bool = Field(
        description="True if the analysis text does not mention 'vaneck' (sponsor leakage check)"
    )
    attempts: int
    error: Optional[str] = None


class ChannelState(BaseModel):
    channel_id: str
    last_video_id: Optional[str] = None
    last_published_at: Optional[str] = None
    last_analyzed_at: Optional[str] = None


class StateSnapshot(BaseModel):
    channels: List[ChannelState]
