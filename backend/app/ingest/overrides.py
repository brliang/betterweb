"""Domain metadata overrides (PLAN.md §4.1, reserved for V1 publisher controls).

The table is empty in V0, but ingestion already applies it: each row whose `url_pattern`
(a shell-style glob over the canonical URL, e.g. `https://example.com/blog/*`) matches a page
replaces one field of its analysis. Rows apply in insertion order, so a later row wins.
"""

import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.crawl.urls import TrackingParams, canonicalize, host_of, same_site
from app.db.web import DomainMetadataOverride
from app.enums import DocumentType
from app.ingest.extract import Analysis
from app.ingest.html import parse_datetime

TEXT_FIELDS = frozenset({"title", "author", "language", "excerpt"})
OVERRIDABLE = TEXT_FIELDS | {"type", "published_at", "canonical_url", "noindex"}
"""Fields a publisher may correct: type and metadata corrections, a canonical correction,
and opting a page out of indexing. Never anything that affects ranking (PLAN.md §12)."""


@dataclass(frozen=True)
class Override:
    url_pattern: str
    field: str
    value: object


async def load_overrides(session: AsyncSession, domain_id: int) -> list[Override]:
    rows = await session.execute(
        sa.select(
            DomainMetadataOverride.url_pattern,
            DomainMetadataOverride.field,
            DomainMetadataOverride.value,
        )
        .where(DomainMetadataOverride.domain_id == domain_id)
        .order_by(DomainMetadataOverride.id)
    )
    return [Override(*row) for row in rows.tuples()]


def apply_overrides(
    analysis: Analysis, url: str, overrides: Sequence[Override], tracking: TrackingParams
) -> tuple[Analysis, list[str]]:
    """The analysis with matching overrides applied, and the fields they changed. A value of
    the wrong shape for its field is skipped (reported as `<field>:invalid`)."""
    changes: dict[str, object] = {}
    applied = []
    for override in overrides:
        if not fnmatchcase(url, override.url_pattern):
            continue
        value = _validate(override.field, override.value, url, tracking)
        if value is _INVALID:
            applied.append(f"{override.field}:invalid")
            continue
        name = "declared_canonical" if override.field == "canonical_url" else override.field
        changes[name] = value
        applied.append(override.field)
    if not changes:
        return analysis, applied
    # Each value was checked against its field's type by _validate.
    return dataclasses.replace(analysis, **changes), applied  # type: ignore[arg-type]


_INVALID = object()


def _validate(field: str, value: object, url: str, tracking: TrackingParams) -> object:
    if field in TEXT_FIELDS:
        return value if isinstance(value, str) and value.strip() else _INVALID
    if field == "type":
        valid = isinstance(value, str) and value in {type_.value for type_ in DocumentType}
        return DocumentType(str(value)) if valid else _INVALID
    if field == "published_at":
        parsed = parse_datetime(value) if isinstance(value, str) else None
        return parsed if parsed is not None else _INVALID
    if field == "noindex":
        return value if isinstance(value, bool) else _INVALID
    if field == "canonical_url" and isinstance(value, str):
        canonical = canonicalize(value, tracking)
        # Same site only, like a page's own rel=canonical; the page itself means "none".
        if canonical is not None and same_site(host_of(canonical), host_of(url)):
            return None if canonical == url else canonical
    return _INVALID
