import pytest

from app.embed.inputs import embedding_input, input_hash, truncate_words


@pytest.mark.parametrize(
    ("text", "max_chars", "expected"),
    [
        ("short", 10, "short"),
        ("one two three", 7, "one two"),  # cut at a space
        ("one two three", 8, "one two"),  # the space itself
        ("one two three", 5, "one"),  # mid-word
        ("onetwothree", 5, "onetw"),  # no space to cut at
        ("one\n\ntwo three", 6, "one"),
    ],
)
def test_truncate_words(text: str, max_chars: int, expected: str) -> None:
    assert truncate_words(text, max_chars) == expected


@pytest.mark.parametrize(
    ("title", "excerpt", "text", "expected"),
    [
        ("Title", "A summary.", "Body text.", "Title\n\nA summary.\n\nBody text."),
        # The excerpt is the start of the text (the extractor's fallback): not repeated.
        (
            "Title",
            "Body text that…",
            "Body  text\nthat goes on.",
            "Title\n\nBody  text\nthat goes on.",
        ),
        ("Title", "Body text.", "Body text.", "Title\n\nBody text."),
        (None, "A summary.", None, "A summary."),
        ("Title", None, "  ", "Title"),
        (None, None, None, ""),
        ("  ", "", "", ""),
    ],
)
def test_embedding_input(
    title: str | None, excerpt: str | None, text: str | None, expected: str
) -> None:
    assert embedding_input(title, excerpt, text, max_text_chars=100) == expected


def test_embedding_input_caps_the_text_only() -> None:
    result = embedding_input("A long title", "An excerpt", "word " * 100, max_text_chars=20)
    assert result == "A long title\n\nAn excerpt\n\nword word word word"


def test_input_hash() -> None:
    assert input_hash("a") == input_hash("a")
    assert input_hash("a") != input_hash("a ")
