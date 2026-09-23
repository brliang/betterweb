"""The adapted IAB taxonomy (PLAN.md §6.4): load, adapt, seed into web.topics, embed.

Inputs live in backend/data/taxonomy: the vendored IAB file, `adaptation.toml` (what to prune,
promote and add) and `descriptions.json` (one LLM-written sentence per topic, generated once by
`python -m scripts.describe_topics` and committed).
"""

import csv
import json
import logging
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.web import Topic
from app.providers.embeddings import EmbeddingProvider

logger = logging.getLogger(__name__)

TAXONOMY_DIR = Path(__file__).resolve().parents[1] / "data" / "taxonomy"
ADAPTATION_FILE = TAXONOMY_DIR / "adaptation.toml"
DESCRIPTIONS_FILE = TAXONOMY_DIR / "descriptions.json"
MAX_TIER = 2
"""Tiers kept from the source; deeper topics are kept only if promoted (PLAN.md §6.4)."""
ADDED_ID_PREFIX = "x-"
IAB_TIER_COLUMNS = slice(3, 7)
"""Columns `Tier 1` .. `Tier 4` in the IAB file."""


class TaxonomyError(Exception):
    """The adaptation or descriptions don't fit the source taxonomy."""


@dataclass(frozen=True)
class TopicSpec:
    external_id: str
    name: str
    parent_id: str | None
    """The parent's external ID."""
    tier: int


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Source(_Strict):
    file: str
    version: str
    url: str
    sha256: str


class _Prune(_Strict):
    id: str
    reason: str = Field(min_length=1)


class _Promote(_Strict):
    id: str
    name: str | None = None


class _Add(_Strict):
    id: str = Field(pattern=rf"^{ADDED_ID_PREFIX}[a-z0-9-]+$")
    name: str
    parent: str | None = None


class Adaptation(_Strict):
    source: _Source
    prune: list[_Prune] = []
    promote: list[_Promote] = []
    add: list[_Add] = []


class Description(_Strict):
    name: str
    """The topic name the description was written for; a rename makes it stale."""
    description: str = Field(min_length=1)


def load_adaptation(path: Path = ADAPTATION_FILE) -> Adaptation:
    return Adaptation.model_validate(tomllib.loads(path.read_text()))


def read_iab(path: Path) -> list[TopicSpec]:
    """Every row of an IAB Content Taxonomy 3.x TSV, at every tier."""
    with path.open(newline="", encoding="utf-8") as file:
        rows = list(csv.reader(file, delimiter="\t"))
    specs = []
    for row in rows[2:]:  # two header rows
        external_id, parent_id, name = (cell.strip() for cell in row[:3])
        tier = sum(1 for cell in row[IAB_TIER_COLUMNS] if cell.strip())
        specs.append(TopicSpec(external_id, name, parent_id or None, tier))
    return specs


def adapt(source: Sequence[TopicSpec], adaptation: Adaptation) -> list[TopicSpec]:
    """Apply prunes, promotions and additions; keep tiers up to MAX_TIER.

    Returns topics parents-first. Raises TaxonomyError for any entry that doesn't match the
    source, so a taxonomy upgrade can't silently skip part of the adaptation.
    """
    by_id = {spec.external_id: spec for spec in source}
    if len(by_id) != len(source):
        raise TaxonomyError("duplicate IDs in the source taxonomy")

    def ancestors(external_id: str) -> list[str]:
        chain = []
        parent = by_id[external_id].parent_id
        while parent is not None:
            chain.append(parent)
            parent = by_id[parent].parent_id
        return chain

    pruned = {prune.id for prune in adaptation.prune}
    for external_id in pruned | {promotion.id for promotion in adaptation.promote}:
        if external_id not in by_id:
            raise TaxonomyError(f"{external_id} is not in the source taxonomy")
    kept = {
        external_id
        for external_id in by_id
        if external_id not in pruned and pruned.isdisjoint(ancestors(external_id))
    }

    promoted: dict[str, TopicSpec] = {}
    for promotion in adaptation.promote:
        spec = by_id[promotion.id]
        if promotion.id not in kept:
            raise TaxonomyError(f"{promotion.id} is promoted but pruned")
        if spec.tier <= MAX_TIER:
            raise TaxonomyError(f"{promotion.id} is already tier {spec.tier}; nothing to promote")
        tier_1 = ancestors(promotion.id)[-1]
        promoted[promotion.id] = TopicSpec(promotion.id, promotion.name or spec.name, tier_1, 2)

    topics = [
        promoted.get(spec.external_id, spec)
        for spec in source
        if spec.external_id in kept and (spec.tier <= MAX_TIER or spec.external_id in promoted)
    ]
    ids = {topic.external_id for topic in topics}
    for addition in adaptation.add:
        if addition.id in ids:
            raise TaxonomyError(f"{addition.id} is added twice")
        if addition.parent is not None and addition.parent not in ids:
            raise TaxonomyError(f"{addition.id}'s parent {addition.parent} is not a kept topic")
        tier = 1 if addition.parent is None else 2
        topics.append(TopicSpec(addition.id, addition.name, addition.parent, tier))
        ids.add(addition.id)
    return sorted(topics, key=lambda topic: topic.tier)


def load_taxonomy(adaptation_path: Path = ADAPTATION_FILE) -> list[TopicSpec]:
    adaptation = load_adaptation(adaptation_path)
    return adapt(read_iab(adaptation_path.parent / adaptation.source.file), adaptation)


def load_descriptions(path: Path = DESCRIPTIONS_FILE) -> dict[str, Description]:
    if not path.exists():
        return {}
    raw: dict[str, object] = json.loads(path.read_text())
    return {external_id: Description.model_validate(entry) for external_id, entry in raw.items()}


def save_descriptions(
    descriptions: Mapping[str, Description], path: Path = DESCRIPTIONS_FILE
) -> None:
    raw = {external_id: entry.model_dump() for external_id, entry in descriptions.items()}
    path.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n")


def stale_descriptions(
    topics: Iterable[TopicSpec], descriptions: Mapping[str, Description]
) -> list[TopicSpec]:
    """Topics with no description, or one written for a different name."""
    return [
        topic
        for topic in topics
        if (entry := descriptions.get(topic.external_id)) is None or entry.name != topic.name
    ]


def topic_path(name: str, parent_name: str | None) -> str:
    """`Tier 1 > Tier 2`, which disambiguates names like `Health` under different parents."""
    return name if parent_name is None else f"{parent_name} > {name}"


def embedding_text(path: str, description: str) -> str:
    return f"{path}: {description}"


@dataclass(frozen=True)
class SeedStats:
    inserted: int
    updated: int
    deleted: int


async def seed_topics(
    session: AsyncSession, topics: Sequence[TopicSpec], descriptions: Mapping[str, Description]
) -> SeedStats:
    """Make web.topics match `topics`: insert, update and delete by external ID.

    Idempotent. A topic whose name, parent or description changes loses its embedding, so the
    next `embed_topics` redoes it. Deleting a topic cascades to user interests and document
    tags on it. The caller commits.
    """
    if stale := stale_descriptions(topics, descriptions):
        names = ", ".join(topic.name for topic in stale[:5])
        raise TaxonomyError(
            f"{len(stale)} topics lack a current description ({names}, ...): "
            "run `python -m scripts.describe_topics`"
        )

    existing = {row.external_id: row for row in await session.scalars(sa.select(Topic))}
    inserted = updated = 0
    ids: dict[str, int] = {}
    for spec in topics:  # parents first, so parent IDs are known
        parent_id = ids[spec.parent_id] if spec.parent_id is not None else None
        values = {
            "name": spec.name,
            "parent_id": parent_id,
            "tier": spec.tier,
            "description": descriptions[spec.external_id].description,
        }
        row = existing.get(spec.external_id)
        if row is None:
            row = Topic(external_id=spec.external_id, **values)
            session.add(row)
            inserted += 1
        elif any(getattr(row, key) != value for key, value in values.items()):
            for key, value in values.items():
                setattr(row, key, value)
            row.embedding = None
            row.embedding_model = None
            updated += 1
        await session.flush()
        ids[spec.external_id] = row.id

    gone = [row for external_id, row in existing.items() if external_id not in ids]
    for row in sorted(gone, key=lambda row: row.tier, reverse=True):  # children first
        await session.delete(row)
        await session.flush()
    return SeedStats(inserted=inserted, updated=updated, deleted=len(gone))


async def embed_topics(
    session: AsyncSession, provider: EmbeddingProvider, instruction: str, *, redo_all: bool = False
) -> int:
    """Embed topics that have no embedding from the provider's model. Returns how many.

    Topics are embedded as queries with `instruction` (TOPIC_EMBEDDING_INSTRUCTION), so tagging
    compares them against plain document embeddings. `redo_all` re-embeds everything, e.g.
    after changing the instruction. The caller commits.
    """
    topics = {row.id: row for row in await session.scalars(sa.select(Topic).order_by(Topic.id))}
    todo = [
        row
        for row in topics.values()
        if redo_all or row.embedding is None or row.embedding_model != provider.model
    ]
    if not todo:
        return 0
    texts = []
    for row in todo:
        if row.description is None:
            raise TaxonomyError(f"topic {row.external_id} has no description; seed first")
        parent_name = topics[row.parent_id].name if row.parent_id is not None else None
        texts.append(embedding_text(topic_path(row.name, parent_name), row.description))
    vectors = await provider.embed_queries(texts, instruction)
    for row, vector in zip(todo, vectors, strict=True):
        row.embedding = vector
        row.embedding_model = provider.model
    await session.flush()
    return len(todo)
