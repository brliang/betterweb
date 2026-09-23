"""The curated suggested-sources list offered in the signup survey (PLAN.md §7 step 2).

Lives in data/suggested_sources.toml. `python -m scripts.verify_sources` checks that each feed is
reachable, allowed by robots.txt for our user agent, and still publishing.
"""

import tomllib
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

SOURCES_FILE = Path(__file__).resolve().parents[1] / "data" / "suggested_sources.toml"


class SuggestedSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    url: HttpUrl
    """The homepage; pinning the source pins this domain."""
    feed: HttpUrl
    topics: list[str] = Field(min_length=1)
    """External IDs of taxonomy topics the source mostly covers."""
    description: str = Field(min_length=1)

    @field_validator("url", "feed")
    @classmethod
    def https_only(cls, value: HttpUrl) -> HttpUrl:
        if value.scheme != "https":
            raise ValueError("must be https")
        return value

    @property
    def host(self) -> str:
        return urlsplit(str(self.url)).hostname or ""


class _SourcesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: list[SuggestedSource]


def load_suggested_sources(path: Path = SOURCES_FILE) -> list[SuggestedSource]:
    return _SourcesFile.model_validate(tomllib.loads(path.read_text())).source
