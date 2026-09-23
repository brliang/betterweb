"""Text normalization shared by the extractors and the dedup stage (PLAN.md §6.3)."""

import hashlib
import re
import unicodedata

SPACES = re.compile(r"[^\S\n]+")
BLANK_LINES = re.compile(r"\n{3,}")
ELLIPSIS = "…"


def normalize_text(text: str, max_chars: int) -> str:
    """Stored document text: NFC, runs of spaces collapsed, lines trimmed, at most one blank
    line in a row, cut off at `max_chars`."""
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = (SPACES.sub(" ", line).strip() for line in text.split("\n"))
    return BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()[:max_chars]


def content_hash(text: str) -> str:
    """Hash of text normalized for exact-duplicate matching: case, width variants and
    whitespace don't count (boilerplate is already gone: extraction keeps the main text)."""
    folded = " ".join(unicodedata.normalize("NFKC", text).casefold().split())
    return hashlib.sha256(folded.encode()).hexdigest()


def word_count(text: str | None) -> int:
    return len(text.split()) if text else 0


def excerpt(text: str, max_chars: int) -> str:
    """The start of `text`, cut at a word boundary."""
    flat = " ".join(text.split())
    if len(flat) <= max_chars:
        return flat
    cut = flat[: max_chars - len(ELLIPSIS) + 1].rsplit(" ", 1)[0].rstrip(" ,;:.")
    return cut + ELLIPSIS
