"""Small immutable values shared by sources, recognizers and consumers."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass(frozen=True)
class InputMessage:
    source_adapter: str
    source_label: str
    source_message_id: str | None
    text: str
    captured_at: datetime


@dataclass(frozen=True)
class LinkCandidate:
    raw_url: str
    start: int = 0
    end: int = 0


@dataclass(frozen=True)
class LinkInfo:
    raw_url: str
    normalized_url: str
    scheme: str
    recognizer_id: str
    provider_id: str | None
    kind: str
    identity_key: str
    confidence: float
    reason: str


@dataclass(frozen=True)
class RecognitionResult:
    candidate: LinkCandidate | None
    status: Literal['recognized', 'generic', 'ambiguous', 'unknown', 'invalid', 'error']
    selected: LinkInfo | None = None
    matches: tuple[LinkInfo, ...] = ()
    diagnostics: tuple[str, ...] = ()
