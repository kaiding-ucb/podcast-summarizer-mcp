"""Tests for the customizable analysis prompt.

Two override paths must work:
  A. $VIDEO_ANALYSIS_PROMPT_PATH points to a file → that file is used as the default
  B. analyze_video(prompt=...) / submit_batch(prompt=...) per-call override
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tools.gemini_client import DEFAULT_PROMPT, GeminiClient, get_default_prompt


# ---------- get_default_prompt() ----------


def test_get_default_prompt_returns_constant_when_env_unset(monkeypatch):
    monkeypatch.delenv("VIDEO_ANALYSIS_PROMPT_PATH", raising=False)
    assert get_default_prompt() is DEFAULT_PROMPT


def test_get_default_prompt_reads_file_when_env_set(monkeypatch, tmp_path: Path):
    custom = tmp_path / "my-prompt.md"
    custom.write_text("Summarize this video in three bullet points.\n")
    monkeypatch.setenv("VIDEO_ANALYSIS_PROMPT_PATH", str(custom))
    assert get_default_prompt() == "Summarize this video in three bullet points.\n"


def test_get_default_prompt_expands_user_in_path(monkeypatch, tmp_path: Path):
    """Tilde paths like ~/.config/... must work too."""
    custom = tmp_path / "p.md"
    custom.write_text("hello")
    # Path.expanduser reads $HOME on Unix
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("VIDEO_ANALYSIS_PROMPT_PATH", "~/p.md")
    assert get_default_prompt() == "hello"


def test_get_default_prompt_raises_when_file_missing(monkeypatch):
    monkeypatch.setenv("VIDEO_ANALYSIS_PROMPT_PATH", "/no/such/file/exists.md")
    with pytest.raises(FileNotFoundError):
        get_default_prompt()


# ---------- analyze_video(prompt=...) ----------


def _make_client_with_capture():
    """Build a GeminiClient that records the prompt text passed to Gemini."""
    gc = GeminiClient.__new__(GeminiClient)
    gc.client = MagicMock()
    captured = {}

    def capture(model, contents, config):
        # `contents` is types.Content(parts=[Part(text=PROMPT), Part(file_data=...)])
        prompt_text = contents.parts[0].text
        captured["prompt"] = prompt_text
        captured["model"] = model
        return MagicMock(
            text=(
                "## Video Summary & Key Moments\nA test summary.\n"
                "* (10:00) A moment\n\n"
                "## Investment Recommendations\n"
                "No explicit investment recommendations in this video."
            )
        )

    gc.client.models.generate_content.side_effect = capture
    return gc, captured


def test_analyze_video_uses_default_prompt_when_omitted():
    gc, captured = _make_client_with_capture()
    gc.analyze_video(
        video_url="https://www.youtube.com/watch?v=xxxxxxxxxxx",
        video_id="xxxxxxxxxxx",
        video_duration=600,
    )
    assert captured["prompt"] == DEFAULT_PROMPT


def test_analyze_video_uses_custom_prompt_when_provided():
    gc, captured = _make_client_with_capture()
    custom = "You are a tech-talk summarizer. Output bullets with timestamps."
    gc.analyze_video(
        video_url="https://www.youtube.com/watch?v=xxxxxxxxxxx",
        video_id="xxxxxxxxxxx",
        video_duration=600,
        prompt=custom,
    )
    assert captured["prompt"] == custom


def test_analyze_video_per_call_prompt_overrides_env(monkeypatch, tmp_path: Path):
    """When both env-default and per-call prompt are provided, per-call wins."""
    env_prompt_file = tmp_path / "env.md"
    env_prompt_file.write_text("FROM ENV FILE")
    monkeypatch.setenv("VIDEO_ANALYSIS_PROMPT_PATH", str(env_prompt_file))

    gc, captured = _make_client_with_capture()
    gc.analyze_video(
        video_url="https://www.youtube.com/watch?v=xxxxxxxxxxx",
        video_id="xxxxxxxxxxx",
        video_duration=600,
        prompt="PER-CALL OVERRIDE",
    )
    assert captured["prompt"] == "PER-CALL OVERRIDE"


def test_analyze_video_uses_env_prompt_when_no_per_call(monkeypatch, tmp_path: Path):
    env_prompt_file = tmp_path / "env.md"
    env_prompt_file.write_text("FROM ENV FILE")
    monkeypatch.setenv("VIDEO_ANALYSIS_PROMPT_PATH", str(env_prompt_file))

    gc, captured = _make_client_with_capture()
    gc.analyze_video(
        video_url="https://www.youtube.com/watch?v=xxxxxxxxxxx",
        video_id="xxxxxxxxxxx",
        video_duration=600,
    )
    assert captured["prompt"] == "FROM ENV FILE"


# ---------- submit_batch(prompt=...) ----------


def _client_capturing_batch_jsonl(monkeypatch):
    gc = GeminiClient.__new__(GeminiClient)
    gc.client = MagicMock()
    captured = {}

    # Capture the JSONL contents passed via files.upload (it's a temp file)
    def fake_upload(file, config):
        captured["jsonl"] = Path(file).read_text(encoding="utf-8")
        return MagicMock(name="files/abc")

    gc.client.files.upload.side_effect = fake_upload
    gc.client.batches.create.return_value = MagicMock(name="batches/xyz")
    return gc, captured


def test_submit_batch_uses_default_prompt_when_omitted(monkeypatch):
    gc, captured = _client_capturing_batch_jsonl(monkeypatch)
    gc.submit_batch(
        video_entries=[{"key": "v1", "video_url": "https://www.youtube.com/watch?v=v1"}]
    )
    assert DEFAULT_PROMPT[:50] in captured["jsonl"]


def test_submit_batch_uses_custom_prompt_when_provided(monkeypatch):
    gc, captured = _client_capturing_batch_jsonl(monkeypatch)
    # ASCII-only — JSONL encoding will pass it through unchanged
    custom = "Custom batch prompt: short bullets only."
    gc.submit_batch(
        video_entries=[{"key": "v1", "video_url": "https://www.youtube.com/watch?v=v1"}],
        prompt=custom,
    )
    assert custom in captured["jsonl"]
    # And the default should NOT also appear (per-call replaces, doesn't append)
    assert DEFAULT_PROMPT[:80] not in captured["jsonl"]
