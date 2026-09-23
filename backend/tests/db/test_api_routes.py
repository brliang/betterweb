"""The V0 API end to end (PLAN.md §8, milestone M7), every request running as the
`discovery_api` role so its grants are proven to suffice."""

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_now, get_query_embedder, get_session
from app.auth import create_login_link, ensure_user
from app.db.usr import (
    Event,
    Feedback,
    Pin,
    Recommendation,
    SurveyResponse,
    UserInterest,
    UserProfileVector,
    UserSettings,
)
from app.db.web import CrawlCycle, Domain, FrontierEntry, ProviderSpend, Topic, Url
from app.enums import EventKind, PinSource, ProfileVectorKind, RankingPreset, SpendPurpose
from app.main import create_app
from app.preferences import suggested_sources
from app.search import QueryEmbedder
from app.settings import Settings, get_settings
from tests.db.builders import MODEL, add_document, add_topic, direction, link, set_ppr
from tests.fakes import FakeEmbeddings, unit_vector

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 9, 23, 12, tzinfo=UTC)
ADMIN = "author@example.com"
SETTINGS = Settings(
    _env_file=None,
    embedding_model=MODEL,
    feed_page_size=3,
    admin_emails=[ADMIN.upper()],
    provider_monthly_spend_cap_usd=1.0,
)
SEARCH_COST = 0.25

Client = httpx2.AsyncClient
SignIn = Callable[[str | None], Awaitable[Client]]


@pytest.fixture
async def sign_in(session: AsyncSession) -> AsyncIterator[SignIn]:
    """A client signed in as the given email (created on first use), or anonymous for None."""
    await session.execute(sa.text("SET LOCAL ROLE discovery_api"))
    app = create_app()
    embedder = QueryEmbedder(
        SETTINGS, lambda meter: FakeEmbeddings(MODEL, meter=meter, usd_per_text=SEARCH_COST)
    )

    async def test_session() -> AsyncSession:
        return session

    app.dependency_overrides[get_session] = test_session
    app.dependency_overrides[get_settings] = lambda: SETTINGS
    app.dependency_overrides[get_now] = lambda: NOW
    app.dependency_overrides[get_query_embedder] = lambda: embedder
    clients: list[Client] = []

    async def client(email: str | None) -> Client:
        http = Client(transport=httpx2.ASGITransport(app=app), base_url="https://testserver")
        clients.append(http)
        if email is not None:
            user_id = await ensure_user(session, email)
            link_url = await create_login_link(session, user_id, SETTINGS, NOW)
            [token] = parse_qs(urlsplit(link_url).query)["token"]
            response = await http.post("/auth/session", json={"token": token})
            assert response.status_code == 200, response.text
        return http

    yield client
    for http in clients:
        await http.aclose()


async def user_id_of(session: AsyncSession, email: str) -> uuid.UUID:
    return await ensure_user(session, email)


# Auth


async def test_a_login_link_signs_in_once(session: AsyncSession, sign_in: SignIn) -> None:
    anonymous = await sign_in(None)
    user_id = await ensure_user(session, "Reader@Example.com")
    link_url = await create_login_link(session, user_id, SETTINGS, NOW)
    assert link_url.startswith("http://localhost:5173/login?token=")
    [token] = parse_qs(urlsplit(link_url).query)["token"]

    response = await anonymous.post("/auth/session", json={"token": token})
    assert response.status_code == 200
    assert response.json() == {
        "email": "reader@example.com",
        "timezone": "UTC",
        "survey_completed": False,
        "is_admin": False,
    }
    cookie = response.headers["set-cookie"].lower()
    assert all(flag in cookie for flag in ("httponly", "secure", "samesite=lax"))
    assert (await anonymous.get("/me")).status_code == 200

    other = await sign_in(None)
    reused = await other.post("/auth/session", json={"token": token})
    assert reused.status_code == 401

    assert (await anonymous.post("/auth/logout")).status_code == 204
    assert (await anonymous.get("/me")).status_code == 401


async def test_expired_links_and_sessions_are_refused(
    session: AsyncSession, sign_in: SignIn
) -> None:
    user_id = await ensure_user(session, "late@example.com")
    link_url = await create_login_link(
        session, user_id, SETTINGS, NOW - timedelta(minutes=SETTINGS.login_token_ttl_minutes + 1)
    )
    [token] = parse_qs(urlsplit(link_url).query)["token"]
    anonymous = await sign_in(None)
    assert (await anonymous.post("/auth/session", json={"token": token})).status_code == 401

    client = await sign_in("reader@example.com")
    await session.execute(sa.text("UPDATE usr.sessions SET expires_at = :now"), {"now": NOW})
    assert (await client.get("/me")).status_code == 401


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/me"),
        ("GET", "/feed"),
        ("GET", "/search?q=x"),
        ("GET", "/settings"),
        ("GET", "/pins"),
        ("POST", "/pins"),
        ("POST", "/survey"),
        ("POST", "/feedback"),
        ("POST", "/events"),
        ("GET", "/recommendations/1/why"),
        ("GET", "/admin/metrics"),
    ],
)
async def test_user_routes_need_a_session(sign_in: SignIn, method: str, path: str) -> None:
    anonymous = await sign_in(None)
    response = await anonymous.request(method, path, json={})
    assert response.status_code == 401


# Survey, settings and pins


@pytest.fixture
async def topics(session: AsyncSession) -> dict[str, int]:
    """A two-level tree with embeddings from the configured model."""
    tech = await add_topic(session, "Technology", external_id="596")
    ai = await add_topic(session, "AI", tech)
    await session.execute(
        sa.update(Topic).values(embedding=direction((0, 1.0)), embedding_model=MODEL)
    )
    return {"tech": tech, "ai": ai}


def survey(topics: dict[str, int], **changes: object) -> dict[str, object]:
    answers: dict[str, object] = {
        "interests": [
            {"topic_id": topics["ai"], "level": "very_interested"},
            {"topic_id": topics["tech"], "level": "interested"},
        ],
        "sites": ["Blog.Example.com/posts/1?utm_source=x", suggested_sources()[0].host],
        "content_types": ["article", "post"],
        "exploration_pct": 0.35,
        "preset": "trust_my_sources",
    }
    return {"version": 1, "answers": answers | changes}


async def test_the_survey_is_stored_and_applied(
    session: AsyncSession, sign_in: SignIn, topics: dict[str, int]
) -> None:
    client = await sign_in("reader@example.com")
    response = await client.post("/survey", json=survey(topics))
    assert response.status_code == 201, response.text
    user_id = await user_id_of(session, "reader@example.com")

    interests = dict(
        (
            await session.execute(
                sa.select(UserInterest.topic_id, UserInterest.weight).where(
                    UserInterest.user_id == user_id
                )
            )
        )
        .tuples()
        .all()
    )
    assert interests == {topics["ai"]: 2.0, topics["tech"]: 1.0}

    pins = dict(
        (
            await session.execute(
                sa.select(Domain.host, Pin.source).join(Pin).where(Pin.user_id == user_id)
            )
        )
        .tuples()
        .all()
    )
    suggested = suggested_sources()[0]
    assert pins == {"blog.example.com": PinSource.SURVEY, suggested.host: PinSource.SUGGESTED}
    # Each pin enqueued its homepage (and the page entered) as seeds; a suggested source its feed.
    seeds = set(
        await session.scalars(
            sa.select(Url.url).join(FrontierEntry, FrontierEntry.url_id == Url.id)
        )
    )
    assert {"https://blog.example.com/", "https://blog.example.com/posts/1"} <= seeds
    feeds = await session.scalar(sa.select(Domain.feed_urls).where(Domain.host == suggested.host))
    assert feeds == [str(suggested.feed)]

    row = await session.get(UserSettings, user_id)
    assert row is not None
    assert (row.preset, row.exploration_pct, row.content_types, row.summaries_opt_in) == (
        "trust_my_sources",
        0.35,
        ["article", "post"],
        False,
    )
    # The interest profile vector exists right away, not only after the next cycle.
    assert await session.get(UserProfileVector, (user_id, ProfileVectorKind.INTEREST))

    me = (await client.get("/me")).json()
    assert me["survey_completed"] is True
    latest = (await client.get("/survey/latest")).json()
    assert latest["answers"]["preset"] == "trust_my_sources"

    # Taking it again replaces interests and pins, and keeps the history.
    again = survey(
        topics,
        sites=["blog.example.com"],
        interests=[{"topic_id": topics["ai"], "level": "interested"}],
    )
    assert (await client.post("/survey", json=again)).status_code == 201
    assert await session.scalar(sa.select(sa.func.count()).select_from(Pin)) == 1
    assert await session.scalar(sa.select(sa.func.count()).select_from(SurveyResponse)) == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"interests": [{"topic_id": 999_999, "level": "interested"}]},
        {"sites": ["not a site"]},
        {"sites": ["mailto:someone@example.com"]},
        {"exploration_pct": 0.5},
        {"interests": []},
        {"content_types": []},
    ],
)
async def test_invalid_survey_answers_are_rejected(
    sign_in: SignIn, topics: dict[str, int], changes: dict[str, object]
) -> None:
    client = await sign_in("reader@example.com")
    response = await client.post("/survey", json=survey(topics, **changes))
    assert response.status_code == 422


async def test_latest_survey_is_404_before_one_is_taken(sign_in: SignIn) -> None:
    client = await sign_in("reader@example.com")
    assert (await client.get("/survey/latest")).status_code == 404


async def test_settings_default_then_update(
    session: AsyncSession, sign_in: SignIn, topics: dict[str, int]
) -> None:
    client = await sign_in("reader@example.com")
    defaults = (await client.get("/settings")).json()
    assert defaults["preset"] == "balanced"
    assert defaults["exploration_pct"] == SETTINGS.exploration_pct
    assert defaults["exploration_choices"] == [0.1, 0.2, 0.35]
    assert "page" not in defaults["content_types"]
    assert defaults["summaries_opt_in"] is False
    assert defaults["interests"] == []

    update = {
        "preset": "fresh",
        "exploration_pct": 0.1,
        "content_types": ["paper"],
        "summaries_opt_in": True,
        "interests": [{"topic_id": topics["ai"], "level": "very_interested"}],
    }
    response = await client.put("/settings", json=update)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["weights"] == SETTINGS.ranking_presets[RankingPreset.FRESH].model_dump()
    assert {k: body[k] for k in update} == update
    assert (await client.get("/settings")).json() == body

    bad = await client.put("/settings", json=update | {"exploration_pct": 0.9})
    assert bad.status_code == 422


async def test_pins(session: AsyncSession, sign_in: SignIn) -> None:
    client = await sign_in("reader@example.com")
    response = await client.post("/pins", json={"site": "https://www.Example.org/about"})
    assert response.status_code == 201
    pinned = response.json()
    assert (pinned["host"], pinned["source"], pinned["name"]) == ("www.example.org", "survey", None)
    again = await client.post("/pins", json={"site": "www.example.org"})
    assert again.json()["domain_id"] == pinned["domain_id"]
    assert [p["host"] for p in (await client.get("/pins")).json()] == ["www.example.org"]
    assert await session.scalar(
        sa.select(sa.func.count())
        .select_from(FrontierEntry)
        .join(Url, Url.id == FrontierEntry.url_id)
        .where(Url.url == "https://www.example.org/")
    )

    assert (await client.post("/pins", json={"site": "localhost"})).status_code == 422
    assert (await client.delete(f"/pins/{pinned['domain_id']}")).status_code == 204
    assert (await client.get("/pins")).json() == []
    assert (await client.delete(f"/pins/{pinned['domain_id']}")).status_code == 404


async def test_catalog(sign_in: SignIn, topics: dict[str, int]) -> None:
    client = await sign_in(None)
    taxonomy = (await client.get("/taxonomy")).json()
    assert [(t["name"], t["tier"]) for t in taxonomy] == [("Technology", 1), ("AI", 2)]
    sources = (await client.get("/suggested-sources")).json()
    assert len(sources) == len(suggested_sources())
    # Topics resolve to taxonomy rows by IAB ID where the taxonomy has them.
    technology = [s for s in sources if "596" in suggested_sources()[sources.index(s)].topics]
    assert all({"id": topics["tech"], "name": "Technology"} in s["topics"] for s in technology)


# Feed, why, search


@pytest.fixture
async def corpus(session: AsyncSession, topics: dict[str, int]) -> dict[str, int]:
    """reader pins mine.example (three AI articles, one linking out to friend.example)."""
    user_id = await ensure_user(session, "reader@example.com")
    ai = {topics["ai"]: 0.4}
    docs = {
        name: await add_document(
            session, f"https://mine.example/{name}", vector=direction((0, 1.0), (i, 0.5)), topics=ai
        )
        for i, name in enumerate(["a", "b", "c"], start=1)
    }
    docs["f"] = await add_document(
        session,
        "https://friend.example/f",
        vector=unit_vector(f"{SETTINGS.search_query_instruction}: night trains"),
        title="Night trains",
    )
    await link(session, docs["a"], "https://friend.example/f")
    session.add(
        Pin(
            user_id=user_id,
            domain_id=await session.scalar(
                sa.select(Domain.id).where(Domain.host == "mine.example")
            ),
            source=PinSource.SURVEY,
        )
    )
    await set_ppr(
        session, user_id, {docs["a"]: 0.4, docs["b"]: 0.3, docs["c"]: 0.2, docs["f"]: 0.1}
    )
    return docs


async def test_the_feed_pages_and_explains(
    session: AsyncSession, sign_in: SignIn, corpus: dict[str, int]
) -> None:
    client = await sign_in("reader@example.com")
    first = (await client.get("/feed")).json()
    assert len(first["items"]) == 3
    item = first["items"][0]
    assert set(item) == {"recommendation_id", "position", "slice", "score", "document", "reasons"}
    assert item["document"]["domain"] == "mine.example"
    assert item["reasons"][0] == {"kind": "sources", "text": "From mine.example, a site you pinned"}

    second = (await client.get("/feed", params={"cursor": first["next_cursor"]})).json()
    served = [i["document"]["id"] for i in first["items"] + second["items"]]
    assert sorted(served) == sorted(corpus.values())
    assert second["next_cursor"] is None
    assert (await client.get("/feed", params={"cursor": "x"})).status_code == 400

    why = await client.get(f"/recommendations/{item['recommendation_id']}/why")
    assert why.status_code == 200
    body = why.json()
    assert body["document"] == item["document"]
    assert body["reasons"] == item["reasons"]
    assert {c["name"] for c in body["components"]} >= {"interest", "ppr", "recency"}
    assert body["evidence"]["pinned_domain"] == "mine.example"
    assert (
        await session.scalar(
            sa.select(sa.func.count()).select_from(Event).where(Event.kind == EventKind.WHY_OPEN)
        )
        == 1
    )

    stranger = await sign_in("stranger@example.com")
    assert (
        await stranger.get(f"/recommendations/{item['recommendation_id']}/why")
    ).status_code == 404


async def test_private_signals_stay_private(
    session: AsyncSession, sign_in: SignIn, corpus: dict[str, int]
) -> None:
    reader = await sign_in("reader@example.com")
    other = await sign_in("other@example.com")
    other_id = await user_id_of(session, "other@example.com")
    session.add(
        Pin(
            user_id=other_id,
            domain_id=await session.scalar(
                sa.select(Domain.id).where(Domain.host == "mine.example")
            ),
            source=PinSource.SURVEY,
        )
    )
    for name in ("a", "b"):
        response = await reader.post(
            "/feedback", json={"document_id": corpus[name], "kind": "hide"}
        )
        assert response.status_code == 201
    reader_feed = {i["document"]["id"] for i in (await reader.get("/feed")).json()["items"]}
    other_feed = {i["document"]["id"] for i in (await other.get("/feed")).json()["items"]}
    assert corpus["a"] not in reader_feed
    assert corpus["b"] not in reader_feed
    assert {corpus["a"], corpus["b"]} <= other_feed


async def test_search(session: AsyncSession, sign_in: SignIn, corpus: dict[str, int]) -> None:
    client = await sign_in("reader@example.com")
    response = await client.get("/search", params={"q": "  night   trains "})
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["items"][0]["document"]["title"] == "Night trains"
    assert page["items"][0]["reasons"][0]["text"] == "Matches your search"
    spend = (
        (await session.execute(sa.select(ProviderSpend.purpose, ProviderSpend.cost_usd)))
        .tuples()
        .all()
    )
    assert spend == [(SpendPurpose.SEARCH, SEARCH_COST)]
    row = await session.get(Recommendation, page["items"][0]["recommendation_id"])
    assert row is not None
    assert row.query == "night trains"

    # The next page reuses the cached query embedding: no second charge.
    more = await client.get("/search", params={"q": "night trains", "cursor": page["next_cursor"]})
    assert more.status_code == 200
    assert await session.scalar(sa.select(sa.func.count()).select_from(ProviderSpend)) == 1


async def test_search_stops_at_the_spend_cap(session: AsyncSession, sign_in: SignIn) -> None:
    client = await sign_in("reader@example.com")
    for query in ("one", "two", "three", "four"):
        assert (await client.get("/search", params={"q": query})).status_code == 200
    capped = await client.get("/search", params={"q": "five"})
    assert capped.status_code == 503
    assert "spend cap" in capped.json()["detail"]


async def test_search_without_a_provider(sign_in: SignIn) -> None:
    client = await sign_in("reader@example.com")
    app = client._transport.app  # type: ignore[attr-defined]  # the ASGI app under test
    app.dependency_overrides[get_query_embedder] = lambda: None
    response = await client.get("/search", params={"q": "trains"})
    assert response.status_code == 503


# Feedback and events


async def test_feedback(session: AsyncSession, sign_in: SignIn, corpus: dict[str, int]) -> None:
    client = await sign_in("reader@example.com")
    page = (await client.get("/feed")).json()
    item = page["items"][1]
    like = await client.post(
        "/feedback",
        json={
            "document_id": item["document"]["id"],
            "kind": "like",
            "recommendation_id": item["recommendation_id"],
            "position": item["position"],
        },
    )
    assert like.status_code == 201
    liked = like.json()
    assert liked["reason_text"] is None
    user_id = await user_id_of(session, "reader@example.com")
    event = await session.scalar(sa.select(Event).where(Event.kind == EventKind.LIKE))
    assert event is not None
    assert (event.recommendation_id, event.position) == (item["recommendation_id"], 1)
    # The liked vector exists right away.
    assert await session.get(UserProfileVector, (user_id, ProfileVectorKind.LIKED))

    reason = await client.patch(f"/feedback/{liked['id']}", json={"reason_text": "Great maps"})
    assert reason.json()["reason_text"] == "Great maps"
    too_long = "x" * (SETTINGS.feedback_reason_max_chars + 1)
    assert (
        await client.patch(f"/feedback/{liked['id']}", json={"reason_text": too_long})
    ).status_code == 422

    stranger = await sign_in("stranger@example.com")
    assert (
        await stranger.patch(f"/feedback/{liked['id']}", json={"reason_text": "x"})
    ).status_code == 404
    # Someone else's recommendation, or one that served another document, is refused.
    foreign = {
        "document_id": item["document"]["id"],
        "kind": "like",
        "recommendation_id": item["recommendation_id"],
    }
    assert (await stranger.post("/feedback", json=foreign)).status_code == 422
    mismatch = foreign | {"document_id": page["items"][0]["document"]["id"]}
    assert (await client.post("/feedback", json=mismatch)).status_code == 422
    assert (
        await client.post("/feedback", json={"document_id": 999_999, "kind": "hide"})
    ).status_code == 422

    block = await client.post(
        "/feedback", json={"document_id": corpus["f"], "kind": "block_domain"}
    )
    assert block.status_code == 201
    kinds = set(await session.scalars(sa.select(Feedback.kind)))
    assert kinds == {"like", "block_domain"}


async def test_events(session: AsyncSession, sign_in: SignIn, corpus: dict[str, int]) -> None:
    client = await sign_in("reader@example.com")
    items = (await client.get("/feed")).json()["items"]
    events = [
        {
            "kind": "impression",
            "document_id": i["document"]["id"],
            "recommendation_id": i["recommendation_id"],
            "surface": "feed",
            "position": i["position"],
        }
        for i in items
    ]
    events.append(events[0] | {"kind": "click"})
    response = await client.post("/events", json={"events": events})
    assert response.json() == {"recorded": 4}
    counts = dict(
        (await session.execute(sa.select(Event.kind, sa.func.count()).group_by(Event.kind)))
        .tuples()
        .all()
    )
    assert counts == {EventKind.IMPRESSION: 3, EventKind.CLICK: 1}

    too_many = {"events": events[:1] * (SETTINGS.events_max_batch + 1)}
    assert (await client.post("/events", json=too_many)).status_code == 422
    like = events[0] | {"kind": "like"}
    assert (await client.post("/events", json={"events": [like]})).status_code == 422
    stranger = await sign_in("stranger@example.com")
    assert (await stranger.post("/events", json={"events": events[:1]})).status_code == 422


# Admin


async def test_admin_needs_an_admin(sign_in: SignIn) -> None:
    client = await sign_in("reader@example.com")
    assert (await client.get("/admin/cycles")).status_code == 403
    assert (await client.get("/admin/metrics")).status_code == 403


async def test_admin_cycles_and_metrics(
    session: AsyncSession, sign_in: SignIn, corpus: dict[str, int]
) -> None:
    admin = await sign_in(ADMIN)
    assert (await admin.get("/me")).json()["is_admin"] is True
    cycle = CrawlCycle(
        page_budget=10,
        pages_fetched=7,
        started_at=NOW - timedelta(hours=2),
        finished_at=NOW - timedelta(hours=1),
        stats={"fetch": {"fetched": 7}},
    )
    session.add(cycle)
    await session.flush()
    session.add(
        ProviderSpend(
            cycle_id=cycle.id,
            purpose=SpendPurpose.EMBED_DOCUMENTS,
            model=MODEL,
            requests=1,
            tokens=10,
            cost_usd=0.5,
            estimated=False,
        )
    )
    [latest] = [c for c in (await admin.get("/admin/cycles")).json() if c["id"] == cycle.id]
    assert (latest["duration_s"], latest["spend_usd"], latest["pages_fetched"]) == (3600, 0.5, 7)
    assert latest["stats"] == {"fetch": {"fetched": 7}}

    # reader clicks a pinned document and one from friend.example: one discovery.
    reader = await sign_in("reader@example.com")
    items = (await reader.get("/feed")).json()["items"]
    by_domain = {i["document"]["domain"]: i for i in items}
    for item in (by_domain["mine.example"], by_domain["friend.example"]):
        event = {
            "kind": "click",
            "document_id": item["document"]["id"],
            "recommendation_id": item["recommendation_id"],
            "surface": "feed",
        }
        await reader.post("/events", json={"events": [event | {"kind": "impression"}, event]})
    await reader.post(
        "/feedback", json={"document_id": items[-1]["document"]["id"], "kind": "hide"}
    )

    metrics = (await admin.get("/admin/metrics")).json()
    today = metrics["daily"][-1]
    assert today["day"] == "2026-09-23"
    assert len(metrics["daily"]) == SETTINGS.metrics_days
    assert (today["impressions"], today["clicks"], today["hides"], today["discoveries"]) == (
        2,
        2,
        1,
        1,
    )
    assert metrics["hide_rate"] == 0.5
    assert metrics["documents"] == 4
    assert metrics["embedded"] == 4
    assert metrics["month_spend_usd"] == 0.5
    slices = {s["slice"]: s for s in metrics["slices"]}
    assert sum(s["clicks"] for s in slices.values()) == 2
