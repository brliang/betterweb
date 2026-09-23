"""The real saved pages in tests/fixtures/pages (see its manifest.toml)."""

import gzip
import tomllib
from dataclasses import dataclass
from pathlib import Path

PAGES = Path(__file__).parent / "fixtures" / "pages"


@dataclass(frozen=True)
class SavedPage:
    name: str
    url: str
    content_type: str
    charset: str | None
    robots_tag: str | None
    license: str
    expected: dict[str, object]

    @property
    def body(self) -> bytes:
        return gzip.decompress((PAGES / f"{self.name}.gz").read_bytes())


def saved_pages() -> list[SavedPage]:
    manifest = tomllib.loads((PAGES / "manifest.toml").read_text())
    return [
        SavedPage(
            name=page["name"],
            url=page["url"],
            content_type=page["content_type"],
            charset=page.get("charset"),
            robots_tag=page.get("robots_tag"),
            license=page["license"],
            expected=page["expected"],
        )
        for page in manifest["page"]
    ]
