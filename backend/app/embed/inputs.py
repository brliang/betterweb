"""What a document's embedding is made of (PLAN.md §6.4): its title, excerpt and the start of
its text. Documents are embedded as plain passages, with no instruction."""

import hashlib
import re

from app.ingest.text import ELLIPSIS

PART_SEPARATOR = "\n\n"
LAST_WORD = re.compile(r"\s+\S*$")
"""The whitespace before the last (possibly partial) word."""


def truncate_words(text: str, max_chars: int) -> str:
    """At most `max_chars` of `text`, cut at a word boundary when the cut falls mid-word."""
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    if not text[max_chars].isspace() and (last := LAST_WORD.search(cut)) and last.start() > 0:
        cut = cut[: last.start()]
    return cut.rstrip()


def _repeats_text(excerpt: str, text: str) -> bool:
    """The excerpt is the start of the text (the extractor's fallback), so adds nothing."""
    start = " ".join(excerpt.removesuffix(ELLIPSIS).split())
    return " ".join(text[: len(excerpt) * 2].split()).startswith(start)


def embedding_input(
    title: str | None, excerpt: str | None, text: str | None, max_text_chars: int
) -> str:
    """The text to embed for a document; empty if it has nothing to embed."""
    parts = [title or ""]
    if excerpt and not (text and _repeats_text(excerpt, text)):
        parts.append(excerpt)
    if text:
        parts.append(truncate_words(text, max_text_chars))
    return PART_SEPARATOR.join(part.strip() for part in parts if part.strip())


def input_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
