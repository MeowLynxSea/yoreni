"""Bounded summaries of model context captured during a Run."""

from dataclasses import dataclass
from typing import TypedDict

SUMMARY_CONTINUATION_TEXT = "Continue the active task from the working-memory checkpoint above."


class TranscriptSummary(TypedDict):
    text: str
    last_active: str


@dataclass(frozen=True, slots=True)
class TranscriptSummaryCheckpoint:
    """A replacement summary and the raw transcript boundary it covers."""

    summary: str
    transcript_boundary: int


