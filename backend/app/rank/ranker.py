"""Feed and search pages (PLAN.md §6.6): candidates, scores, composition, and persistence.

Each page is ranked when it is requested. A cursor names the first recommendation of a feed
(or search) session; later pages skip every document the session has served, so each page is
composed on its own: its exploration share, its interleaving and its per-domain cap all hold
per page. Every served item is stored in usr.recommendations with its full breakdown before it
is returned, so "why?" shows exactly what ranked it.
"""

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Embedding
from app.db.usr import Recommendation
from app.db.web import Document
from app.enums import ProfileVectorKind, Slice, Surface
from app.rank import candidates as sources
from app.rank.cards import DocumentCard, load_cards
from app.rank.compose import Pick, compose_page, slot_plan
from app.rank.context import UserContext, hard_filters, load_context
from app.rank.explain import (
    Breakdown,
    ComponentScore,
    Evidence,
    Exploration,
    LikedRef,
    Reason,
    TopicRef,
    reasons,
)
from app.rank.features import (
    CandidateSet,
    Linkers,
    hosts,
    linkers,
    load_candidates,
    nearest_liked,
)
from app.rank.scoring import (
    Component,
    Parameters,
    Scores,
    components,
    contributions,
    total,
    weight_table,
)
from app.rank.topics import Adjacency
from app.settings import Settings


class CursorError(ValueError):
    """The cursor doesn't name a session of this user's on this surface."""


@dataclass(frozen=True)
class ServedItem:
    recommendation_id: int
    position: int
    slice: Slice
    score: float
    document: DocumentCard
    reasons: list[Reason]


@dataclass(frozen=True)
class Page:
    items: list[ServedItem]
    next_cursor: str | None
    """None once nothing is left to serve."""


def item_reasons(breakdown: Breakdown, settings: Settings) -> list[Reason]:
    return reasons(
        breakdown,
        max_reasons=settings.reasons_max,
        min_contribution=settings.reason_min_contribution,
        examples=settings.reason_examples_max,
    )


@dataclass(frozen=True)
class Scored:
    candidates: CandidateSet
    values: dict[Component, Scores]
    contributed: dict[Component, Scores]
    weights: dict[Component, float]
    score: Scores
    index: dict[int, int]
    """Document ID -> candidate index."""

    def ranked(self, document_ids: Iterable[int]) -> list[int]:
        """Candidate indices of `document_ids`, best score first."""
        found = {self.index[d] for d in document_ids if d in self.index}
        return sorted(found, key=lambda i: (-self.score[i], self.candidates.ids[i]))


class Ranker:
    def __init__(
        self, session: AsyncSession, user_id: uuid.UUID, settings: Settings, now: datetime
    ) -> None:
        self._session = session
        self._user_id = user_id
        self._settings = settings
        self._now = now

    async def feed(self, cursor: str | None) -> Page:
        settings = self._settings
        context = await load_context(self._session, self._user_id, settings)
        start, served = await self._served(Surface.FEED, None, cursor)
        filters = self._filters(context, served, seen=True)
        await sources.enable_filtered_vector_search(self._session)

        n = settings.candidates_per_source
        model = settings.embedding_model
        interest = context.vectors.get(ProfileVectorKind.INTEREST)
        liked = context.vectors.get(ProfileVectorKind.LIKED)
        main = [
            *await sources.ppr_top(self._session, context, filters, n),
            *(
                await sources.nearest(self._session, interest, model, filters, n)
                if interest
                else []
            ),
            *(await sources.nearest(self._session, liked, model, filters, n) if liked else []),
            *await sources.recent_pinned(self._session, context, filters, n),
        ]
        band = (
            await sources.nearest(
                self._session,
                interest,
                model,
                filters,
                settings.explore_band_size,
                skip=settings.explore_band_skip,
            )
            if interest
            else []
        )
        covered = context.interests.covered()
        adjacent = await sources.topic_adjacent(
            self._session, context.interests.adjacent(), covered, filters, n
        )
        graph = await sources.graph_adjacent(
            self._session, context, covered, filters, n, settings.explore_graph_max_hops
        )

        scored = await self._score(context, [*main, *band, *adjacent, *graph])
        preferences = context.preferences
        picks = compose_page(
            slot_plan(
                settings.feed_page_size, preferences.exploration_pct, preferences.semantic_share
            ),
            {
                Slice.MAIN: scored.ranked(main),
                Slice.ADJACENT_SEMANTIC: scored.ranked([*band, *adjacent]),
                Slice.ADJACENT_GRAPH: scored.ranked(graph),
            },
            scored.candidates.domain_ids,
            settings.max_per_domain_per_page,
        )
        explorations = await self._explorations(scored, picks, adjacent, graph, context)
        return await self._serve(
            context, scored, picks, explorations, Surface.FEED, None, start, len(served)
        )

    async def search(self, query: str, vector: Embedding, cursor: str | None) -> Page:
        settings = self._settings
        context = await load_context(self._session, self._user_id, settings)
        start, served = await self._served(Surface.SEARCH, query, cursor)
        filters = self._filters(context, served, seen=False)
        await sources.enable_filtered_vector_search(self._session)
        found = await sources.nearest(
            self._session, vector, settings.embedding_model, filters, settings.candidates_per_source
        )
        scored = await self._score(context, found, query=vector)
        picks = [Pick(i, Slice.MAIN) for i in scored.ranked(found)[: settings.feed_page_size]]
        return await self._serve(
            context, scored, picks, {}, Surface.SEARCH, query, start, len(served)
        )

    def _filters(
        self, context: UserContext, served: Sequence[int], *, seen: bool
    ) -> list[sa.ColumnElement[bool]]:
        filters = hard_filters(context, self._settings, seen=seen)
        if served:
            filters.append(Document.id.not_in(served))
        return filters

    async def _served(
        self, surface: Surface, query: str | None, cursor: str | None
    ) -> tuple[int | None, list[int]]:
        """The session's first recommendation ID and the documents it has served so far."""
        if cursor is None:
            return None, []
        try:
            start = int(cursor)
        except ValueError as error:
            raise CursorError(f"invalid cursor {cursor!r}") from error
        session_filter = [
            Recommendation.user_id == self._user_id,
            Recommendation.surface == surface,
            Recommendation.query.is_not_distinct_from(query),
        ]
        first = await self._session.scalar(
            sa.select(Recommendation.id).where(Recommendation.id == start, *session_filter)
        )
        if first is None:
            raise CursorError(f"unknown cursor {cursor!r}")
        rows = await self._session.scalars(
            sa.select(Recommendation.document_id)
            .where(Recommendation.id >= start, *session_filter)
            .order_by(Recommendation.id)
        )
        return start, list(rows)

    async def _score(
        self, context: UserContext, document_ids: Sequence[int], query: Embedding | None = None
    ) -> Scored:
        settings = self._settings
        unique = list(dict.fromkeys(document_ids))
        candidates = await load_candidates(
            self._session, unique, context, settings, self._now, query
        )
        values = components(
            candidates.signals,
            Parameters(
                topic_blend=settings.interest_topic_blend,
                documents=context.documents,
                hide_threshold=settings.hide_penalty_threshold,
            ),
        )
        weights = weight_table(context.preferences.weights, settings.search_query_weight)
        contributed = contributions(values, weights)
        return Scored(
            candidates=candidates,
            values=values,
            contributed=contributed,
            weights=weights,
            score=total(contributed, len(candidates)),
            index={document_id: i for i, document_id in enumerate(candidates.ids)},
        )

    async def _explorations(
        self,
        scored: Scored,
        picks: Sequence[Pick],
        adjacent: Mapping[int, Adjacency],
        graph: Mapping[int, sources.GraphRoute],
        context: UserContext,
    ) -> dict[int, Exploration]:
        """Why each exploration pick was picked, keyed by candidate index."""
        names = context.interests.tree.names
        ids = scored.candidates.ids
        routes = {
            pick.candidate: graph[ids[pick.candidate]].path
            for pick in picks
            if pick.slice == Slice.ADJACENT_GRAPH and ids[pick.candidate] in graph
        }
        path_hosts = await hosts(self._session, sorted({d for p in routes.values() for d in p}))
        found: dict[int, Exploration] = {}
        for pick in picks:
            if pick.slice == Slice.ADJACENT_SEMANTIC:
                adjacency = adjacent.get(ids[pick.candidate])
                found[pick.candidate] = Exploration(
                    slice=pick.slice,
                    adjacent_topic=_topic(adjacency.shown, names) if adjacency else None,
                    interest_topic=_topic(adjacency.interest, names) if adjacency else None,
                )
            elif pick.slice == Slice.ADJACENT_GRAPH:
                tags = scored.candidates.tags[pick.candidate]
                found[pick.candidate] = Exploration(
                    slice=pick.slice,
                    outside_topic=_topic(tags[0][0], names) if tags else None,
                    path=[path_hosts[d] for d in routes.get(pick.candidate, [])],
                )
        return found

    async def _serve(
        self,
        context: UserContext,
        scored: Scored,
        picks: Sequence[Pick],
        explorations: Mapping[int, Exploration],
        surface: Surface,
        query: str | None,
        start: int | None,
        position: int,
    ) -> Page:
        settings = self._settings
        candidates = scored.candidates
        document_ids = [candidates.ids[pick.candidate] for pick in picks]
        cards = await load_cards(self._session, document_ids)
        linked = await linkers(self._session, context, document_ids)
        liked = await nearest_liked(self._session, context, document_ids, settings.embedding_model)
        rows: list[tuple[Recommendation, Breakdown]] = []
        for pick in picks:
            i = pick.candidate
            document_id = candidates.ids[i]
            breakdown = Breakdown(
                components=self._components(scored, i),
                evidence=self._evidence(
                    context, scored, i, linked.get(document_id), liked.get(document_id)
                ).model_copy(update={"exploration": explorations.get(i)}),
            )
            rows.append(
                (
                    Recommendation(
                        user_id=context.user_id,
                        document_id=document_id,
                        surface=surface,
                        query=query,
                        slice=pick.slice,
                        score=float(scored.score[i]),
                        components=breakdown.model_dump(mode="json"),
                        cycle_id=context.cycle_id,
                    ),
                    breakdown,
                )
            )
        self._session.add_all(recommendation for recommendation, _ in rows)
        await self._session.flush()
        items = [
            ServedItem(
                recommendation_id=recommendation.id,
                position=position + offset,
                slice=recommendation.slice,
                score=recommendation.score,
                document=cards[recommendation.document_id],
                reasons=item_reasons(breakdown, settings),
            )
            for offset, (recommendation, breakdown) in enumerate(rows)
        ]
        if not items:
            return Page(items=[], next_cursor=None)
        session_start = start if start is not None else items[0].recommendation_id
        more = len(items) >= settings.feed_page_size
        return Page(items=items, next_cursor=str(session_start) if more else None)

    def _components(self, scored: Scored, i: int) -> list[ComponentScore]:
        signals = scored.candidates.signals
        inputs: dict[Component, dict[str, float]] = {
            Component.INTEREST: {
                "cosine": signals.interest_cosine[i],
                "topic_overlap": signals.topic_overlap[i],
            },
            Component.PPR: {"ppr": signals.ppr[i]},
            Component.FEEDBACK: {
                "liked_cosine": signals.liked_cosine[i],
                "hidden_cosine": signals.hidden_cosine[i],
            },
            Component.RECENCY: {
                "age_days": signals.age_days[i],
                "half_life_days": signals.half_life_days[i],
            },
            Component.HIDE: {
                "max_hidden_similarity": signals.hidden_max_similarity[i],
                "threshold": self._settings.hide_penalty_threshold,
            },
        }
        if signals.query_cosine is not None:
            inputs[Component.QUERY] = {"cosine": signals.query_cosine[i]}
        return [
            ComponentScore(
                name=name,
                inputs={key: float(value) for key, value in inputs[name].items()},
                value=float(value[i]),
                weight=scored.weights[name],
                contribution=float(scored.contributed[name][i]),
            )
            for name, value in scored.values.items()
        ]

    def _evidence(
        self,
        context: UserContext,
        scored: Scored,
        i: int,
        linked: Linkers | None,
        liked: LikedRef | None,
    ) -> Evidence:
        candidates = scored.candidates
        examples = self._settings.reason_examples_max
        interests = context.interests
        strength: dict[int, float] = {}
        for topic, score in candidates.tags[i]:
            covering = interests.covering(topic)
            if covering is not None:
                strength[covering] = (
                    strength.get(covering, 0.0) + score * interests.weights[covering]
                )
        matched = sorted(strength, key=lambda t: (-strength[t], t))
        linked = linked or Linkers([], [])
        return Evidence(
            pinned_domain=context.pinned.get(candidates.domain_ids[i]),
            trusted_linkers=linked.trusted[:examples],
            trusted_linker_count=len(linked.trusted),
            other_linkers=linked.other[:examples],
            other_linker_count=len(linked.other),
            interest_topics=[_topic(t, interests.tree.names) for t in matched],
            nearest_liked=liked,
            age_days=max(0.0, float(candidates.signals.age_days[i])),
            published=candidates.published[i],
        )


def _topic(topic_id: int, names: Mapping[int, str]) -> TopicRef:
    return TopicRef(id=topic_id, name=names.get(topic_id, str(topic_id)))
