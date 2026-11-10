from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Any


PAUSE_MARKER = re.compile(
    r"\[(?:pause\s*:?\s*)?(\d+(?:\.\d+)?)s\]"
    r"|(?<![\w.])(\d+(?:\.\d+)?)s(?!\w)"
    r"|(?<!\.)\.{3,5}(?!\.)",
    re.IGNORECASE,
)
WORD = re.compile(r"\b[\w]+(?:['’][\w]+)*\b", re.UNICODE)
MAX_PAUSE_MS = 300_000
MAX_SEGMENTS = 10_000
MAX_INPUT_CHARS = 2_000_000
DEFAULT_CHUNK_CHARS = 6_000

NARRATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["segments"],
    "properties": {
        "segments": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_SEGMENTS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "pause_after_ms"],
                "properties": {
                    "text": {"type": "string"},
                    "pause_after_ms": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": MAX_PAUSE_MS,
                    },
                },
            },
        }
    },
}


class ScriptValidationError(ValueError):
    pass


@dataclass
class NarrationSegment:
    text: str
    pause_after_ms: int = 0
    explicit_pause: bool = False


def parse_explicit_pauses(text: str) -> list[NarrationSegment]:
    """Split marker text without changing any narration text."""
    segments: list[NarrationSegment] = []
    cursor = 0
    for match in PAUSE_MARKER.finditer(text):
        seconds = (
            float(match.group(1) or match.group(2))
            if match.group(1) or match.group(2)
            else len(match.group(0))
        )
        pause_ms = round(seconds * 1000)
        if pause_ms > MAX_PAUSE_MS:
            raise ScriptValidationError("Pause markers may not exceed 5 minutes.")
        segments.append(NarrationSegment(text[cursor:match.start()], pause_ms, True))
        cursor = match.end()
    if cursor < len(text) or not segments:
        segments.append(NarrationSegment(text[cursor:]))
    return segments


def segments_to_text(segments: list[NarrationSegment]) -> str:
    return "".join(segment.text for segment in segments)


def validate_structured_script(payload: Any) -> list[NarrationSegment]:
    if not isinstance(payload, dict) or set(payload) != {"segments"}:
        raise ScriptValidationError("Expected an object containing only 'segments'.")
    raw_segments = payload["segments"]
    if not isinstance(raw_segments, list) or not 1 <= len(raw_segments) <= MAX_SEGMENTS:
        raise ScriptValidationError("The narration must contain at least one valid segment.")

    segments: list[NarrationSegment] = []
    for index, raw in enumerate(raw_segments):
        if not isinstance(raw, dict) or set(raw) != {"text", "pause_after_ms"}:
            raise ScriptValidationError(f"Segment {index + 1} has an invalid shape.")
        text = raw["text"]
        pause = raw["pause_after_ms"]
        if not isinstance(text, str):
            raise ScriptValidationError(f"Segment {index + 1} text must be a string.")
        if isinstance(pause, bool) or not isinstance(pause, int):
            raise ScriptValidationError(f"Segment {index + 1} pause must be integer milliseconds.")
        if not 0 <= pause <= MAX_PAUSE_MS:
            raise ScriptValidationError(
                f"Segment {index + 1} pause must be between 0 and {MAX_PAUSE_MS} ms."
            )
        if not text.strip() and pause == 0:
            raise ScriptValidationError(f"Segment {index + 1} is empty.")
        segments.append(NarrationSegment(text, pause))
    if not any(segment.text.strip() for segment in segments):
        raise ScriptValidationError("The narration contains no spoken text.")
    return segments


def word_tokens(text: str) -> list[str]:
    return WORD.findall(text)


def validate_word_preservation(
    original_text: str,
    segments: list[NarrationSegment],
) -> None:
    original = word_tokens(segments_to_text(parse_explicit_pauses(original_text)))
    generated = word_tokens(segments_to_text(segments))
    if original != generated:
        matcher = difflib.SequenceMatcher(a=original, b=generated, autojunk=False)
        operation = next(
            (tag for tag, *_ in matcher.get_opcodes() if tag != "equal"),
            "unknown",
        )
        raise ScriptValidationError(
            "Preserve exact wording validation failed "
            f"({operation}; {len(original)} source words, {len(generated)} generated words)."
        )


def split_at_explicit_pauses(text: str) -> list[NarrationSegment]:
    """Retain user-authored timing boundaries while removing them from speech text."""
    return parse_explicit_pauses(text)


def chunk_text(text: str, max_chars: int = DEFAULT_CHUNK_CHARS) -> list[str]:
    if max_chars < 100:
        raise ValueError("Chunk size must be at least 100 characters.")
    if len(text) > MAX_INPUT_CHARS:
        raise ScriptValidationError(
            f"Input exceeds the {MAX_INPUT_CHARS:,}-character project limit."
        )
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        limit = min(start + max_chars, len(text))
        if limit == len(text):
            end = limit
        else:
            minimum = start + max_chars // 2
            matches = list(re.finditer(r"(?:\n+|[.!?][\"')\]]?\s+|\s+)", text[start:limit]))
            safe = [match for match in matches if start + match.end() >= minimum]
            end = start + (safe[-1].end() if safe else max_chars)
        chunks.append(text[start:end])
        start = end
    if "".join(chunks) != text:
        raise AssertionError("Text chunking changed input.")
    return chunks


def serialize_segments(segments: list[NarrationSegment]) -> str:
    """Serialize approved segment timing back to the editor's pause syntax."""
    pieces: list[str] = []
    for segment in segments:
        pieces.append(segment.text)
        if segment.pause_after_ms:
            whole, fraction = divmod(segment.pause_after_ms, 1000)
            if fraction:
                seconds = f"{whole}.{fraction:03d}".rstrip("0")
            else:
                seconds = str(whole)
            pieces.append(f"[pause {seconds}s]")
    return "".join(pieces)


def edit_segment(
    segments: list[NarrationSegment], index: int, text: str
) -> None:
    if not 0 <= index < len(segments):
        raise IndexError("Narration segment index is out of range.")
    if not isinstance(text, str):
        raise TypeError("Segment text must be a string.")
    segments[index].text = text


def set_pause(segments: list[NarrationSegment], index: int, pause_after_ms: int) -> None:
    if not 0 <= index < len(segments):
        raise IndexError("Narration segment index is out of range.")
    if isinstance(pause_after_ms, bool) or not isinstance(pause_after_ms, int):
        raise ScriptValidationError("Pause must be integer milliseconds.")
    if not 0 <= pause_after_ms <= MAX_PAUSE_MS:
        raise ScriptValidationError(
            f"Pause must be between 0 and {MAX_PAUSE_MS} ms."
        )
    segments[index].pause_after_ms = pause_after_ms
    segments[index].explicit_pause = True


def split_segment(
    segments: list[NarrationSegment], index: int, char_offset: int
) -> None:
    if not 0 <= index < len(segments):
        raise IndexError("Narration segment index is out of range.")
    segment = segments[index]
    if not 0 < char_offset < len(segment.text):
        raise ValueError("Split position must be inside the segment text.")
    right = NarrationSegment(
        segment.text[char_offset:],
        segment.pause_after_ms,
        segment.explicit_pause,
    )
    segment.text = segment.text[:char_offset]
    segment.pause_after_ms = 0
    segment.explicit_pause = False
    segments.insert(index + 1, right)


def merge_segments(segments: list[NarrationSegment], index: int) -> None:
    if not 0 <= index < len(segments) - 1:
        raise IndexError("There is no following segment to merge.")
    first, second = segments[index], segments[index + 1]
    first.text += second.text
    first.pause_after_ms = second.pause_after_ms
    first.explicit_pause = second.explicit_pause
    del segments[index + 1]


@dataclass
class ScriptDraft:
    original_text: str
    mode: str = "Preserve exact wording"
    candidate: list[NarrationSegment] | None = None
    approved: list[NarrationSegment] | None = None

    def propose(self, segments: list[NarrationSegment]) -> None:
        candidate = validate_structured_script(
            {"segments": [
                {"text": segment.text, "pause_after_ms": segment.pause_after_ms}
                for segment in segments
            ]}
        )
        if self.mode == "Preserve exact wording":
            validate_word_preservation(self.original_text, candidate)
        self.candidate = candidate

    def accept(self) -> list[NarrationSegment]:
        if self.candidate is None:
            raise ScriptValidationError("There is no generated script to accept.")
        if self.mode == "Preserve exact wording":
            validate_word_preservation(self.original_text, self.candidate)
        self.approved = [
            NarrationSegment(s.text, s.pause_after_ms, s.explicit_pause)
            for s in self.candidate
        ]
        return self.approved

    def reject(self) -> None:
        self.candidate = None

    def restore_original(self) -> list[NarrationSegment]:
        self.candidate = None
        self.approved = parse_explicit_pauses(self.original_text)
        return self.approved


def highlight_opcodes(original: str, revised: str) -> list[tuple[str, str]]:
    matcher = difflib.SequenceMatcher(a=original, b=revised, autojunk=False)
    return [
        (tag, revised[j1:j2])
        for tag, _, _, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    ]
