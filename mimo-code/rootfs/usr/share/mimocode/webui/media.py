"""Rich media tag parsing and segment models for AI replies.

Parses tags like [IMAGE:source], [VOICE:text], [FILE:source] etc.
from AI response text and splits into structured segments.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_TAG_RE = re.compile(r"\[(IMAGE|VOICE|FILE|VIDEO|GIF|CARD):(.+?)\]")
_HTML_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(slots=True)
class TextSegment:
    text: str


@dataclass(slots=True)
class ImageSegment:
    source: str


@dataclass(slots=True)
class VoiceSegment:
    text: str


@dataclass(slots=True)
class FileSegment:
    source: str


@dataclass(slots=True)
class VideoSegment:
    source: str


@dataclass(slots=True)
class GifSegment:
    source: str


@dataclass(slots=True)
class CardSegment:
    source: str


Segment = TextSegment | ImageSegment | VoiceSegment | FileSegment | VideoSegment | GifSegment | CardSegment


def parse_reply_segments(reply: str) -> list[Segment]:
    """Parse AI reply text into structured segments.

    Handles tags like [IMAGE:camera.front_door], [VOICE:你好], [FILE:/path/to/file].
    """
    segments: list[Segment] = []
    last_end = 0

    for match in _TAG_RE.finditer(reply):
        before = reply[last_end:match.start()].strip()
        if before:
            segments.append(TextSegment(text=before))

        tag = match.group(1).strip().upper()
        payload = _HTML_TAG_RE.sub("", match.group(2)).strip()

        if tag == "IMAGE" and payload:
            segments.append(ImageSegment(source=payload))
        elif tag == "VOICE" and payload:
            segments.append(VoiceSegment(text=payload))
        elif tag == "FILE" and payload:
            segments.append(FileSegment(source=payload))
        elif tag == "VIDEO" and payload:
            segments.append(VideoSegment(source=payload))
        elif tag == "GIF" and payload:
            segments.append(GifSegment(source=payload))
        elif tag == "CARD" and payload:
            segments.append(CardSegment(source=payload))

        last_end = match.end()

    trailing = reply[last_end:].strip()
    if trailing:
        segments.append(TextSegment(text=trailing))

    if not segments and reply.strip():
        segments.append(TextSegment(text=reply.strip()))

    return segments


def has_media_segments(segments: list[Segment]) -> bool:
    """Check if any segment is a media type."""
    return any(
        isinstance(s, (ImageSegment, VoiceSegment, FileSegment, VideoSegment, GifSegment, CardSegment))
        for s in segments
    )
