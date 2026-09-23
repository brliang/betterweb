"""The embed stage end to end: documents in, embeddings, topic tags and spend out (PLAN.md §6.1
step 4, §6.4, milestone M5)."""

import math
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.crawl import frontier
from app.crawl.cycle import start_or_resume_cycle
from app.db.base import Embedding
from app.db.web import (
    CrawlCycle,
    Document,
    DocumentEmbedding,
    DocumentTopic,
    ProviderSpend,
    Topic,
    Url,
)
from app.embed.inputs import embedding_input
from app.embed.stage import EmbedStage
from app.embed.tagging import tag_documents
from app.enums import DocumentType, SpendPurpose
from app.providers.openrouter import ProviderError
from app.providers.spend import SpendMeter
from app.settings import Settings
from app.spend import month_spend_usd, open_meter
from tests.fakes import FakeEmbeddings, unit_vector

pytestmark = pytest.mark.anyio

SETTINGS = Settings(
    _env_file=None, embedding_batch_size=2, tag_min_similarity=0.5, tag_max_gap=0.35
)
MODEL = "fake-embedding"
UPDATED = datetime(2026, 9, 23, 3, tzinfo=UTC)
TEXT = "Body text of the document, long enough to say something."


@pytest.fixture
async def cycle(session: AsyncSession) -> CrawlCycle:
    return await start_or_resume_cycle(session, SETTINGS)


async def add_document(
    session: AsyncSession, path: str, title: str | None = "A title", text: str | None = TEXT
) -> int:
    url = f"https://example.com/{path}"
    url_id = (await frontier.ensure_urls(session, [url]))[url]
    domain_id = await session.scalar(sa.select(Url.domain_id).where(Url.id == url_id))
    assert domain_id is not None
    document = Document(
        canonical_url_id=url_id,
        domain_id=domain_id,
        type=DocumentType.ARTICLE,
        title=title,
        text=text,
        updated_at=UPDATED,
    )
    session.add(document)
    await session.flush()
    return document.id


def vector_for(title: str | None, text: str | None = TEXT) -> Embedding:
    """What the fake provider returns for a document with this title and text."""
    return unit_vector(embedding_input(title, None, text, SETTINGS.embed_text_max_chars))


def blend(vector: Embedding, similarity: float, seed: str) -> Embedding:
    """A unit vector with cosine `similarity` to `vector`."""
    noise = unit_vector(seed)
    along = sum(a * b for a, b in zip(noise, vector, strict=True))
    orthogonal = [n - along * v for n, v in zip(noise, vector, strict=True)]
    norm = math.sqrt(sum(x * x for x in orthogonal))
    rest = math.sqrt(1 - similarity**2)
    return [similarity * v + rest * o / norm for v, o in zip(vector, orthogonal, strict=True)]


async def add_topic(
    session: AsyncSession, name: str, embedding: Embedding, model: str = MODEL
) -> int:
    topic = Topic(
        external_id=name,
        name=name,
        tier=1,
        description=f"About {name}.",
        embedding=embedding,
        embedding_model=model,
    )
    session.add(topic)
    await session.flush()
    return topic.id


async def embed(
    session: AsyncSession,
    cycle: CrawlCycle,
    provider: FakeEmbeddings | None = None,
    meter: SpendMeter | None = None,
    settings: Settings = SETTINGS,
) -> dict[str, int]:
    meter = meter or SpendMeter(budget_usd=1.0)
    stage = EmbedStage(session, cycle, provider or FakeEmbeddings(meter=meter), meter, settings)
    return dict(await stage.run())


async def embeddings(session: AsyncSession) -> dict[int, DocumentEmbedding]:
    rows = await session.scalars(sa.select(DocumentEmbedding))
    return {row.document_id: row for row in rows}


async def tags(session: AsyncSession, document_id: int) -> dict[int, float]:
    rows = await session.execute(
        sa.select(DocumentTopic.topic_id, DocumentTopic.score)
        .where(DocumentTopic.document_id == document_id)
        .order_by(DocumentTopic.score.desc())
    )
    return dict(rows.tuples().all())


async def test_documents_are_embedded_and_tagged(session: AsyncSession, cycle: CrawlCycle) -> None:
    first = await add_document(session, "1", "Gardens")
    second = await add_document(session, "2", "Tides")
    third = await add_document(session, "3", "Stars")
    gardens = vector_for("Gardens")
    exact = await add_topic(session, "exact", gardens)
    near = await add_topic(session, "near", blend(gardens, 0.7, "near"))
    await add_topic(session, "below", blend(gardens, 0.4, "below"))
    await add_topic(session, "other-model", gardens, model="another-model")

    provider = FakeEmbeddings()
    counts = await embed(session, cycle, provider)

    # Two documents per request (EMBEDDING_BATCH_SIZE), as plain passages.
    assert provider.calls == [
        [f"Gardens\n\n{TEXT}", f"Tides\n\n{TEXT}"],
        [f"Stars\n\n{TEXT}"],
    ]
    rows = await embeddings(session)
    assert set(rows) == {first, second, third}
    assert rows[first].model == MODEL
    assert rows[first].vector == pytest.approx(gardens, abs=1e-6)
    assert rows[first].document_updated_at == UPDATED
    # Nearest topics of the same model, at or above TAG_MIN_SIMILARITY.
    assert list(await tags(session, first)) == [exact, near]
    assert list((await tags(session, first)).values()) == pytest.approx([1.0, 0.7], abs=1e-4)
    assert await tags(session, second) == {}  # random vectors: nothing similar enough
    assert counts == {"embedded": 3, "tags": 2, "unchanged": 0, "empty": 0}
    assert cycle.stats["embed"] == {"counts": counts, "spend_usd": 0.0}


async def test_at_most_tag_max_topics(session: AsyncSession, cycle: CrawlCycle) -> None:
    document = await add_document(session, "1", "Gardens")
    gardens = vector_for("Gardens")
    for n in range(5):
        await add_topic(session, f"topic-{n}", blend(gardens, 0.9 - n / 20, f"t{n}"))
    await embed(session, cycle)
    assert len(await tags(session, document)) == SETTINGS.tag_max_topics == 3


async def test_only_topics_close_to_the_best_one(session: AsyncSession, cycle: CrawlCycle) -> None:
    document = await add_document(session, "1", "Gardens")
    gardens = vector_for("Gardens")
    best = await add_topic(session, "best", blend(gardens, 0.4, "best"))
    close = await add_topic(session, "close", blend(gardens, 0.36, "close"))
    await add_topic(session, "far", blend(gardens, 0.3, "far"))
    settings = Settings(_env_file=None, tag_min_similarity=0.2, tag_max_gap=0.05)
    await embed(session, cycle, settings=settings)
    assert list(await tags(session, document)) == [best, close]


async def test_unchanged_documents_cost_nothing(session: AsyncSession, cycle: CrawlCycle) -> None:
    document = await add_document(session, "1")
    await embed(session, cycle)

    provider = FakeEmbeddings()
    assert await embed(session, cycle, provider) == {
        "embedded": 1,  # counts add up across runs of the cycle's stage
        "untagged": 1,  # no topics in this test
        "unchanged": 0,
        "empty": 0,
    }
    assert provider.calls == []

    # An update that doesn't touch the embedded text is checked but not re-embedded.
    later = UPDATED + timedelta(days=1)
    await session.execute(
        sa.update(Document)
        .where(Document.id == document)
        .values(author="Someone", updated_at=later)
    )
    counts = await embed(session, cycle, provider)
    assert provider.calls == []
    assert counts["unchanged"] == 1
    assert (await embeddings(session))[document].document_updated_at == later


async def test_changed_documents_are_reembedded_and_retagged(
    session: AsyncSession, cycle: CrawlCycle
) -> None:
    document = await add_document(session, "1", "Gardens")
    tides = await add_topic(session, "tides", vector_for("Tides"))
    await embed(session, cycle)
    assert await tags(session, document) == {}

    await session.execute(
        sa.update(Document)
        .where(Document.id == document)
        .values(title="Tides", updated_at=UPDATED + timedelta(days=1))
    )
    provider = FakeEmbeddings()
    await embed(session, cycle, provider)
    assert provider.calls == [[f"Tides\n\n{TEXT}"]]
    assert (await embeddings(session))[document].vector == pytest.approx(
        vector_for("Tides"), abs=1e-6
    )
    assert list(await tags(session, document)) == [tides]


async def test_a_new_model_replaces_the_old_embeddings(
    session: AsyncSession, cycle: CrawlCycle
) -> None:
    document = await add_document(session, "1")
    await embed(session, cycle, FakeEmbeddings("old-model"))
    await embed(session, cycle, FakeEmbeddings("new-model"))
    rows = await session.scalars(sa.select(DocumentEmbedding.model))
    assert rows.all() == ["new-model"]
    assert document in await embeddings(session)


async def test_documents_with_nothing_to_embed(session: AsyncSession, cycle: CrawlCycle) -> None:
    await add_document(session, "1", title=None, text=None)
    await add_document(session, "2", title="Only a title", text=None)
    provider = FakeEmbeddings()
    counts = await embed(session, cycle, provider)
    assert provider.calls == [["Only a title"]]
    assert (counts["empty"], counts["embedded"]) == (1, 1)


async def test_without_topic_embeddings_documents_are_embedded_untagged(
    session: AsyncSession, cycle: CrawlCycle
) -> None:
    document = await add_document(session, "1", "Gardens")
    await add_topic(session, "gardens", vector_for("Gardens"), model="another-model")
    counts = await embed(session, cycle)
    assert (counts["embedded"], counts["untagged"]) == (1, 1)
    assert await tags(session, document) == {}

    # `taxonomy embed` then tags every embedded document.
    await session.execute(sa.update(Topic).values(embedding_model=MODEL))
    assert await tag_documents(session, MODEL, SETTINGS) == 1
    assert len(await tags(session, document)) == 1


async def test_spend_is_recorded_and_capped(session: AsyncSession, cycle: CrawlCycle) -> None:
    for n in range(5):
        await add_document(session, str(n), f"Document {n}")
    settings = Settings(_env_file=None, embedding_batch_size=2, provider_monthly_spend_cap_usd=1)
    meter = await open_meter(session, settings)
    # $0.25 a document: two batches of two fit in the $1 cap, the third batch doesn't.
    provider = FakeEmbeddings(meter=meter, usd_per_text=0.25)
    counts = await embed(session, cycle, provider, meter, settings)

    assert counts["embedded"] == 4
    assert len(await embeddings(session)) == 4
    stats = cycle.stats["embed"]
    assert isinstance(stats, dict)
    assert (stats["spend_usd"], stats["stopped"]) == (1.0, "spend_cap")
    rows = (await session.scalars(sa.select(ProviderSpend).order_by(ProviderSpend.id))).all()
    assert [(row.purpose, row.model, row.requests, row.tokens, row.cost_usd) for row in rows] == [
        (SpendPurpose.EMBED_DOCUMENTS, MODEL, 1, 2, 0.5),
        (SpendPurpose.EMBED_DOCUMENTS, MODEL, 1, 2, 0.5),
    ]
    assert all(row.cycle_id == cycle.id for row in rows)
    assert await month_spend_usd(session) == 1.0

    # The cap holds for the rest of the month; the document left waits.
    meter = await open_meter(session, settings)
    assert meter.remaining_usd == 0
    counts = await embed(session, cycle, FakeEmbeddings(meter=meter, usd_per_text=0.25), meter)
    assert counts["embedded"] == 4

    # A raised cap lets it through.
    raised = Settings(_env_file=None, provider_monthly_spend_cap_usd=2)
    meter = await open_meter(session, raised)
    await embed(session, cycle, FakeEmbeddings(meter=meter, usd_per_text=0.25), meter, raised)
    assert len(await embeddings(session)) == 5


async def test_only_this_months_spend_counts(session: AsyncSession) -> None:
    now = datetime(2026, 9, 23, 12, tzinfo=UTC)
    for created_at, cost in [
        (datetime(2026, 8, 31, 23, 59, tzinfo=UTC), 5.0),  # last month
        (datetime(2026, 9, 1, tzinfo=UTC), 0.25),
        (datetime(2026, 9, 20, tzinfo=UTC), 0.5),
    ]:
        session.add(
            ProviderSpend(
                created_at=created_at,
                purpose=SpendPurpose.EMBED_TOPICS,
                model=MODEL,
                requests=1,
                tokens=10,
                cost_usd=cost,
                estimated=False,
            )
        )
    await session.flush()
    assert await month_spend_usd(session, now) == 0.75
    settings = Settings(_env_file=None, provider_monthly_spend_cap_usd=1)
    assert (await open_meter(session, settings, now)).remaining_usd == 0.25
    over = Settings(_env_file=None, provider_monthly_spend_cap_usd=0.5)
    assert (await open_meter(session, over, now)).remaining_usd == 0


class FailsOnTheSecondRequest(FakeEmbeddings):
    requests = 0

    async def embed_documents(self, texts: Sequence[str]) -> list[Embedding]:
        self.requests += 1
        if self.requests == 2:
            self.error = ProviderError("POST /embeddings returned 400")
        return await super().embed_documents(texts)


async def test_a_provider_failure_stops_the_stage(session: AsyncSession, cycle: CrawlCycle) -> None:
    for n in range(4):
        await add_document(session, str(n), f"Document {n}")
    provider = FailsOnTheSecondRequest()
    counts = await embed(session, cycle, provider)
    assert counts["embedded"] == 2
    stats = cycle.stats["embed"]
    assert isinstance(stats, dict)
    assert stats["stopped"] == "provider_error"

    # The next cycle picks up the rest.
    provider.error = None
    await embed(session, cycle, provider)
    assert len(await embeddings(session)) == 4


class Killed(BaseException):
    """Stands in for a kill: like SIGKILL, nothing in the stage handles it."""


async def test_a_killed_stage_resumes_with_the_documents_left(
    session: AsyncSession, cycle: CrawlCycle, monkeypatch: pytest.MonkeyPatch
) -> None:
    for n in range(4):
        await add_document(session, str(n), f"Document {n}")
    calls = 0

    async def killed_on_the_second_batch(*args: object, **kwargs: object) -> int:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise Killed
        return 0

    monkeypatch.setattr("app.embed.stage.tag_documents", killed_on_the_second_batch)
    await add_topic(session, "topic", unit_vector("topic"))
    with pytest.raises(Killed):
        await embed(session, cycle)
    await session.rollback()
    assert len(await embeddings(session)) == 2

    monkeypatch.undo()
    resumed = await start_or_resume_cycle(session, SETTINGS)
    counts = await embed(session, resumed)
    assert len(await embeddings(session)) == 4
    assert counts["embedded"] == 4  # counted across both runs
