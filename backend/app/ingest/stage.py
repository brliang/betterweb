"""The extract stage of a crawl cycle (PLAN.md §6.1 steps 2-3): classify, extract and dedup
every page the fetch stage stored in `web.raw_pages`, then follow its links.

For each page, in one transaction:
1. Analyze it (classify, extract, links, declared canonical, robots directives) in a worker
   thread, and apply any domain metadata overrides.
2. Follow its links: enqueue them one step further from the page's frontier position (unless
   the page says `nofollow`), plus its declared canonical URL at the page's own position.
3. Unless the page says `noindex`: dedup it to a document (a new one if no strategy matches),
   update the document if this page is its content source, replace the document's outlinks,
   and log every URL whose document changed.
4. Delete the raw page.

A kill loses at most the page in progress; a re-run picks up the pages still waiting.
Finally, URLs that redirect take the document of their target.
"""

import asyncio
import logging
from collections import Counter
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.crawl import frontier
from app.crawl.frontier import Candidate, Position
from app.crawl.robots import product_token
from app.crawl.urls import TrackingParams
from app.db.web import CrawlCycle, Document, FrontierEntry, Link, RawPage, Url
from app.ingest.dedup import (
    CanonicalUrlStrategy,
    ContentHashStrategy,
    DedupCandidate,
    DedupStrategy,
    assign,
    decide,
    resolve_redirects,
)
from app.ingest.extract import Analysis, OutLink, analyze
from app.ingest.overrides import Override, apply_overrides, load_overrides
from app.settings import Settings

logger = logging.getLogger(__name__)

STATS_KEY = "extract"


def default_strategies(settings: Settings) -> list[DedupStrategy]:
    return [CanonicalUrlStrategy(), ContentHashStrategy(settings.dedup_hash_min_words)]


class ExtractStage:
    def __init__(
        self,
        session: AsyncSession,
        cycle: CrawlCycle,
        settings: Settings,
        *,
        strategies: Sequence[DedupStrategy] | None = None,
    ) -> None:
        self._session = session
        self._cycle = cycle
        self._settings = settings
        self._strategies = strategies or default_strategies(settings)
        self._tracking = TrackingParams(settings.tracking_params)
        self._product = product_token(settings.user_agent)
        self._overrides: dict[int, list[Override]] = {}
        stage = cycle.stats.get(STATS_KEY)
        counts = stage.get("counts") if isinstance(stage, dict) else None
        self.counts: Counter[str] = Counter(counts if isinstance(counts, dict) else {})

    async def _commit(self) -> None:
        self._cycle.stats = {**self._cycle.stats, STATS_KEY: {"counts": dict(self.counts)}}
        await self._session.commit()

    async def run(self) -> Counter[str]:
        waiting = (
            await self._session.scalars(sa.select(RawPage.url_id).order_by(RawPage.url_id))
        ).all()
        logger.info("extracting %d pages", len(waiting))
        for url_id in waiting:
            await self._page(url_id)
        self.counts["dedup.redirect"] += await resolve_redirects(self._session)
        await self._commit()
        logger.info("extract stage done: %s", dict(sorted(self.counts.items())))
        return self.counts

    async def _page(self, url_id: int) -> None:
        session = self._session
        row = (
            await session.execute(
                sa.select(
                    Url.url,
                    Url.domain_id,
                    RawPage.content_type,
                    RawPage.charset,
                    RawPage.body,
                    RawPage.robots_tag,
                )
                .join(Url, Url.id == RawPage.url_id)
                .where(RawPage.url_id == url_id)
            )
        ).one_or_none()
        if row is None:
            return
        url, domain_id, content_type, charset, body, robots_tag = row
        self.counts["pages"] += 1
        try:
            analysis = await asyncio.to_thread(
                analyze,
                url,
                content_type,
                charset,
                body,
                robots_tag,
                self._settings,
                product=self._product,
            )
        except ValueError as error:
            logger.info("can't extract %s: %s", url, error)
            self.counts["failed"] += 1
            analysis = None
        except Exception:
            # A bug in an extractor must not stall every later page: log it and move on.
            logger.exception("extracting %s failed", url)
            self.counts["error"] += 1
            analysis = None
        else:
            if analysis is None:
                self.counts["not_a_document"] += 1
        if analysis is not None:
            analysis = await self._apply_overrides(analysis, url, domain_id)
            await self._ingest(url_id, analysis)
        await session.execute(sa.delete(RawPage).where(RawPage.url_id == url_id))
        await self._commit()

    async def _apply_overrides(self, analysis: Analysis, url: str, domain_id: int) -> Analysis:
        if domain_id not in self._overrides:
            self._overrides[domain_id] = await load_overrides(self._session, domain_id)
        analysis, applied = apply_overrides(
            analysis, url, self._overrides[domain_id], self._tracking
        )
        self.counts.update(f"override.{field}" for field in applied)
        return analysis

    async def _ingest(self, url_id: int, analysis: Analysis) -> None:
        self.counts[f"type.{analysis.type}"] += 1
        self.counts[f"classifier.{analysis.classifier}"] += 1
        canonical = analysis.declared_canonical
        links = () if analysis.nofollow else analysis.links
        links = tuple(link for link in links if link.url != canonical)
        await self._follow(url_id, canonical, links)
        if analysis.noindex:
            self.counts["noindex"] += 1
            return

        canonical_id = url_id
        if canonical is not None:
            self.counts["declared_canonical"] += 1
            canonical_id = (await frontier.ensure_urls(self._session, [canonical]))[canonical]
        page = DedupCandidate(url_id, canonical_id, analysis.content_hash, analysis.word_count)
        decision = await decide(
            self._session, page, self._strategies, self._settings.dedup_min_confidence
        )
        self.counts[f"dedup.{decision.method}"] += 1
        if decision.document_id is None:
            document_id = await self._create_document(canonical_id, analysis)
            is_source = True
        else:
            document_id = decision.document_id
            is_source = await self._is_content_source(document_id, url_id, canonical_id)
        if is_source:
            await self._update_document(document_id, analysis, links)
        await assign(self._session, sorted({url_id, canonical_id}), document_id, decision)

    async def _follow(self, url_id: int, canonical: str | None, links: Sequence[OutLink]) -> None:
        """Enqueue the page's links and declared canonical from its frontier position."""
        row = (
            await self._session.execute(
                sa.select(FrontierEntry.internal_depth, FrontierEntry.external_hops).where(
                    FrontierEntry.url_id == url_id
                )
            )
        ).one_or_none()
        if row is None:
            return  # dropped since it was fetched; nothing to follow from
        position = Position(*row)
        candidates = [
            Candidate(link.url, position.follow(internal=link.internal)) for link in links
        ]
        if canonical is not None:
            # Like a redirect within the site: the same distance from the seeds.
            candidates.append(Candidate(canonical, position))
        changes = await frontier.enqueue(self._session, candidates, self._settings)
        self.counts["enqueued"] += sum(changes.values())

    async def _create_document(self, canonical_id: int, analysis: Analysis) -> int:
        domain_id = await self._session.scalar(
            sa.select(Url.domain_id).where(Url.id == canonical_id)
        )
        insert = (
            pg_insert(Document)
            .values(canonical_url_id=canonical_id, domain_id=domain_id, type=analysis.type)
            .on_conflict_do_nothing(index_elements=[Document.canonical_url_id])
            .returning(Document.id)
        )
        document_id = await self._session.scalar(insert)
        if document_id is None:  # a document already claims this canonical URL
            document_id = (
                await self._session.execute(
                    sa.select(Document.id).where(Document.canonical_url_id == canonical_id)
                )
            ).scalar_one()
        self.counts["documents.new"] += 1
        return document_id

    async def _is_content_source(self, document_id: int, url_id: int, canonical_id: int) -> bool:
        """Whether this page's content should update the document: it is the document's
        canonical URL, or it declares that URL and the canonical URL itself has never been
        fetched. A duplicate found by content never overwrites the original."""
        row = (
            await self._session.execute(
                sa.select(Document.canonical_url_id, Url.content_hash)
                .join(Url, Url.id == Document.canonical_url_id)
                .where(Document.id == document_id)
            )
        ).one()
        document_canonical, canonical_hash = row
        return document_canonical == url_id or (
            document_canonical == canonical_id and canonical_hash is None
        )

    async def _update_document(
        self, document_id: int, analysis: Analysis, links: Sequence[OutLink]
    ) -> None:
        session = self._session
        await session.execute(
            sa.update(Document)
            .where(Document.id == document_id)
            .values(
                type=analysis.type,
                title=analysis.title,
                author=analysis.author,
                published_at=analysis.published_at,
                language=analysis.language,
                text=analysis.text,
                excerpt=analysis.excerpt,
                word_count=analysis.word_count,
                content_hash=analysis.content_hash,
            )
        )
        self.counts["documents.updated"] += 1
        # The document's outlinks are those of its current content.
        await session.execute(sa.delete(Link).where(Link.src_document_id == document_id))
        if not links:
            return
        ids = await frontier.ensure_urls(session, [link.url for link in links])
        await session.execute(
            pg_insert(Link)
            .values(
                [
                    {
                        "src_document_id": document_id,
                        "dst_url_id": ids[link.url],
                        "anchor_text": link.anchor_text or None,
                        "is_internal": link.internal,
                    }
                    for link in links
                ]
            )
            .on_conflict_do_nothing()
        )
        self.counts["links"] += len(links)


async def run_extract_stage(
    session: AsyncSession, cycle: CrawlCycle, settings: Settings
) -> Counter[str]:
    """Run (or resume) the extract stage: every raw page waiting, whichever cycle fetched it."""
    return await ExtractStage(session, cycle, settings).run()
