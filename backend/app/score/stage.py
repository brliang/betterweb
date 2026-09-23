"""The scoring stage of a crawl cycle (PLAN.md §6.1 step 5, §6.5).

1. Load the document graph and every user's pins (each covering its domain's `www.` twin).
2. Solve personalized PageRank for the global surfer (seeded evenly across all users' pinned
   domains, each user weighted equally) and for each user (seeded from their own pins), in one
   power iteration. With no pins at all, the global surfer is plain PageRank.
3. Replace `web.global_scores` and `web.domain_scores` (the mean PageRank of a domain's
   documents: the expected score of a new page there), and each user's top-K in `usr.user_ppr`.
4. Re-estimate frontier priorities from the new scores (PLAN.md §6.2).
5. Refresh every user's profile vectors.

Everything is written in one transaction, so a killed stage leaves the previous scores and a
re-run starts over. This is the one crawl-cycle stage that reads `usr`; the `discovery_score`
role's grants cover everything it writes.
"""

import asyncio
import logging
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime

import numpy as np
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.crawl.frontier import recompute_priorities
from app.db.usr import User, UserPpr
from app.db.web import CrawlCycle, DomainScore, GlobalScore
from app.pins import pinned_domains
from app.score.graph import load_graph
from app.score.pagerank import (
    Graph,
    Indices,
    Scores,
    blend,
    personalized_pagerank,
    seed_vector,
)
from app.score.profiles import refresh_profile_vectors
from app.settings import Settings

logger = logging.getLogger(__name__)

STATS_KEY = "scores"

INSERT_GLOBAL_SCORES = sa.text(
    "INSERT INTO web.global_scores (document_id, cycle_id, pagerank) "
    "SELECT v.id, :cycle_id, v.score "
    "FROM unnest(CAST(:ids AS bigint[]), CAST(:scores AS float8[])) AS v(id, score) "
    "JOIN web.documents d ON d.id = v.id"
)
INSERT_DOMAIN_SCORES = sa.text(
    "INSERT INTO web.domain_scores (domain_id, cycle_id, score) "
    "SELECT v.id, :cycle_id, v.score "
    "FROM unnest(CAST(:ids AS bigint[]), CAST(:scores AS float8[])) AS v(id, score) "
    "JOIN web.domains d ON d.id = v.id"
)
INSERT_USER_PPR = sa.text(
    "INSERT INTO usr.user_ppr (user_id, document_id, score, cycle_id) "
    "SELECT :user_id, v.id, v.score, :cycle_id "
    "FROM unnest(CAST(:ids AS bigint[]), CAST(:scores AS float8[])) AS v(id, score) "
    "JOIN web.documents d ON d.id = v.id"
)


def top_k(scores: Scores, k: int) -> Indices:
    """Indices of the (at most) `k` highest positive scores, in no particular order."""
    positive = np.flatnonzero(scores > 0)
    if len(positive) <= k:
        return positive
    return positive[np.argpartition(-scores[positive], k - 1)[:k]]


def domain_means(graph: Graph, scores: Scores) -> tuple[list[int], list[float]]:
    """Each domain's mean document score, for domains with a positive one."""
    domains, index = np.unique(graph.domains, return_inverse=True)
    means = np.bincount(index, weights=scores) / np.bincount(index)
    positive = means > 0
    return domains[positive].tolist(), means[positive].tolist()


class ScoreStage:
    def __init__(
        self, session: AsyncSession, cycle: CrawlCycle, settings: Settings, now: datetime
    ) -> None:
        self._session = session
        self._cycle = cycle
        self._settings = settings
        self._now = now
        self.counts: Counter[str] = Counter()

    async def run(self) -> Counter[str]:
        session = self._session
        settings = self._settings
        graph = await load_graph(session)
        users = list(await session.scalars(sa.select(User.id).order_by(User.id)))
        pins: defaultdict[uuid.UUID, list[int]] = defaultdict(list)
        for user_id, domain_id, _ in await session.execute(pinned_domains()):
            pins[user_id].append(domain_id)

        seeded: dict[uuid.UUID, Scores] = {}
        for user_id in users:
            vector = seed_vector(graph, pins[user_id])
            if vector is not None:
                seeded[user_id] = vector
        columns = [blend(list(seeded.values()), graph.size), *seeded.values()]
        pagerank = await asyncio.to_thread(
            personalized_pagerank,
            graph,
            np.column_stack(columns),
            damping=settings.ppr_damping,
            tol=settings.ppr_tol,
            max_iter=settings.ppr_max_iter,
        )
        if not pagerank.converged:
            logger.warning(
                "PageRank did not converge in %d iterations (PPR_MAX_ITER); using the last one",
                pagerank.iterations,
            )

        await self._store_global(graph, pagerank.scores[:, 0])
        await session.execute(sa.delete(UserPpr))
        for column, user_id in enumerate(seeded, start=1):
            await self._store_user_ppr(graph, user_id, pagerank.scores[:, column])
        self.counts["frontier_priorities"] = await recompute_priorities(session, settings)
        for user_id in users:
            kinds = await refresh_profile_vectors(session, user_id, settings, self._now)
            self.counts["profile_vectors"] += len(kinds)

        seed_domains = {domain for user_id in seeded for domain in pins[user_id]}
        self.counts.update(
            documents=graph.size,
            links=len(graph.sources),
            users=len(users),
            seeded_users=len(seeded),
            seed_domains=len(seed_domains & set(graph.domains.tolist())),
        )
        self._cycle.stats = {
            **self._cycle.stats,
            STATS_KEY: {
                "counts": dict(self.counts),
                "pagerank": {"iterations": pagerank.iterations, "converged": pagerank.converged},
            },
        }
        await session.commit()
        logger.info(
            "scores stage: %s, PageRank in %d iterations",
            dict(sorted(self.counts.items())),
            pagerank.iterations,
        )
        return self.counts

    async def _store_global(self, graph: Graph, scores: Scores) -> None:
        session = self._session
        positive = np.flatnonzero(scores > 0)
        await session.execute(sa.delete(GlobalScore))
        await session.execute(
            INSERT_GLOBAL_SCORES,
            {
                "cycle_id": self._cycle.id,
                "ids": graph.documents[positive].tolist(),
                "scores": scores[positive].tolist(),
            },
        )
        self.counts["global_scores"] = len(positive)
        domain_ids, means = domain_means(graph, scores)
        await session.execute(sa.delete(DomainScore))
        await session.execute(
            INSERT_DOMAIN_SCORES, {"cycle_id": self._cycle.id, "ids": domain_ids, "scores": means}
        )
        self.counts["domain_scores"] = len(domain_ids)

    async def _store_user_ppr(self, graph: Graph, user_id: uuid.UUID, scores: Scores) -> None:
        top = top_k(scores, self._settings.user_ppr_top_k)
        await self._session.execute(
            INSERT_USER_PPR,
            {
                "user_id": user_id,
                "cycle_id": self._cycle.id,
                "ids": graph.documents[top].tolist(),
                "scores": scores[top].tolist(),
            },
        )
        self.counts["user_ppr"] += len(top)


async def run_score_stage(
    session: AsyncSession,
    cycle: CrawlCycle,
    settings: Settings,
    now: datetime | None = None,
) -> Counter[str]:
    """Run the scoring stage; a re-run recomputes everything from the current graph."""
    return await ScoreStage(session, cycle, settings, now or datetime.now(UTC)).run()
