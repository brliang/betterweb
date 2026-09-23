import pytest

from app.ingest.text import content_hash, excerpt, normalize_text, word_count


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", ""),
        ("  one  two\t ", "one two"),
        ("a\r\nb\rc", "a\nb\nc"),
        ("a\n\n\n\n  b  ", "a\n\nb"),
        ("cafe\u0301", "caf\u00e9"),  # combining accent -> NFC
        ("x" * 20, "x" * 10),  # cut off
    ],
)
def test_normalize_text(text: str, expected: str) -> None:
    assert normalize_text(text, max_chars=10) == expected


def test_content_hash_ignores_case_width_and_whitespace() -> None:
    base = content_hash("Hello World, this is a page.")
    assert content_hash("  hello\n\nWORLD,   this is a page. ") == base
    fullwidth_hello = "\uff28\uff45\uff4c\uff4c\uff4f"
    assert content_hash(f"{fullwidth_hello} World, this is a page.") == base
    assert content_hash("Hello World, this is another page.") != base


@pytest.mark.parametrize(
    ("text", "expected"), [(None, 0), ("", 0), ("one", 1), (" one two\nthree ", 3)]
)
def test_word_count(text: str | None, expected: int) -> None:
    assert word_count(text) == expected


@pytest.mark.parametrize(
    ("text", "max_chars", "expected"),
    [
        ("short text", 20, "short text"),
        ("line one\n\nline two", 50, "line one line two"),
        ("The quick brown fox jumps over the lazy dog", 20, "The quick brown fox…"),
        ("The quick brown fox, jumps", 21, "The quick brown fox…"),
    ],
)
def test_excerpt(text: str, max_chars: int, expected: str) -> None:
    result = excerpt(text, max_chars)
    assert result == expected
    assert len(result) <= max_chars
