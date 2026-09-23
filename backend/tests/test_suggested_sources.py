import pytest
from pydantic import ValidationError

from app.suggested_sources import SuggestedSource, load_suggested_sources
from app.taxonomy import load_taxonomy


def test_sources_file_is_valid() -> None:
    sources = load_suggested_sources()
    assert sources
    hosts = [source.host for source in sources]
    assert len(set(hosts)) == len(hosts), "one entry per site"
    assert len({source.name for source in sources}) == len(sources)


def test_sources_reference_existing_topics() -> None:
    topic_ids = {topic.external_id for topic in load_taxonomy()}
    unknown = {
        source.name: sorted(set(source.topics) - topic_ids)
        for source in load_suggested_sources()
        if not set(source.topics) <= topic_ids
    }
    assert unknown == {}


def test_sources_must_use_https() -> None:
    with pytest.raises(ValidationError, match="https"):
        SuggestedSource.model_validate(
            {
                "name": "Plain",
                "url": "http://example.com/",
                "feed": "https://example.com/feed",
                "topics": ["596"],
                "description": "Insecure.",
            }
        )
