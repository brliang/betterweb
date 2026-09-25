"""Worker CLI: ``python -m app.worker <command>``.

- ``cycle run``: run (or resume) the nightly crawl cycle (PLAN.md §6.1): fetch and extract +
  dedup, repeated for up to CYCLE_FETCH_ROUNDS rounds, then embed + tag and scores. Needs
  OPENROUTER_API_KEY. The scoring stage connects with SCORE_DATABASE_URL when it is set.
- ``cycle run --stage STAGE [--stage ...]``: re-run only those stages (extract, embed, scores),
  in pipeline order, for the running cycle or else the latest one; it never fetches, starts or
  finishes a cycle. E.g. ``--stage scores`` after changing a scoring setting.
- ``cycle calendar``: print the systemd OnCalendar schedule for CYCLE_LOCAL_START in
  CYCLE_TIMEZONE (the deploy's timer uses it).
- ``alert send SUBJECT``: email SUBJECT with standard input as the body to ALERT_EMAIL_TO
  (the deploy's systemd units pipe a failed unit's log lines into it).
- ``db logins``: create or update the login users of the database roles from
  DB_LOGIN_PASSWORDS (run as the owner, after migrations).
- ``frontier seed URL [--feed FEED ...]``: enqueue a homepage as a pinned seed (for development;
  the API does this when a user pins a domain).
- ``users login-link EMAIL``: print a one-time login link for EMAIL, creating the user if needed
  (PLAN.md §8 auth; the link expires after LOGIN_TOKEN_TTL_MINUTES).
- ``users export EMAIL`` / ``users import``: write a user's preferences (settings, interests,
  pins, survey answers) as JSON to standard output / read them from standard input, to move
  them to another deployment (`app.transfer`).
- ``taxonomy seed``: load the adapted taxonomy into web.topics (idempotent).
- ``taxonomy embed [--all]``: embed topics that lack an embedding from the configured model,
  then re-tag every embedded document if any topic changed.
"""

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import AlertError, send_alert
from app.auth import create_login_link, ensure_user
from app.crawl.cycle import CYCLE_LOCK_KEY, current_cycle, finish_cycle, start_or_resume_cycle
from app.crawl.fetch_stage import run_fetch_stage, start_next_round
from app.crawl.frontier import SeedError, add_seed
from app.crawl.http import create_client
from app.db.logins import LoginError, ensure_logins
from app.db.session import create_engine, create_sessionmaker
from app.db.web import CrawlCycle
from app.embed.stage import run_embed_stage
from app.embed.tagging import tag_documents
from app.enums import SpendPurpose
from app.ingest.stage import run_extract_stage
from app.providers.embeddings import OpenRouterEmbeddings
from app.providers.openrouter import OpenRouterClient, ProviderError
from app.score.stage import run_score_stage
from app.settings import Settings, get_settings
from app.spend import open_meter, record_spend
from app.taxonomy import (
    TaxonomyError,
    embed_topics,
    load_descriptions,
    load_taxonomy,
    seed_topics,
)
from app.transfer import TransferError, UserExport, export_user, import_user

logger = logging.getLogger("app.worker")

PLACEHOLDER_CONTACT = "example.invalid"
RERUN_STAGES = ("extract", "embed", "scores")
"""Stages `cycle run --stage` can re-run, in pipeline order. Fetching has a deadline and a
budget, so it only runs as part of a whole cycle."""


class WorkerError(Exception):
    """A command can't run as configured."""


async def run_cycle(settings: Settings, stages: Sequence[str] = ()) -> None:
    """Run (or resume) a whole cycle, or re-run only `stages` for the current one."""
    if PLACEHOLDER_CONTACT in settings.user_agent:
        # PLAN.md principle 4 and §14 Q5: identify honestly, with a real contact page.
        raise WorkerError(
            "USER_AGENT still points at the placeholder contact URL; set it to a real bot "
            "contact page before crawling"
        )
    key = settings.openrouter_api_key
    if key is None or not key.get_secret_value():
        raise WorkerError("OPENROUTER_API_KEY is not set; the embed stage needs it")
    engine = create_engine(settings)
    # The scoring stage reads `usr`, so a deployment gives it its own role (PLAN.md §4).
    score_engine = (
        create_engine(settings, settings.score_database_url)
        if settings.score_database_url is not None
        else engine
    )
    try:
        async with engine.connect() as lock:
            if not await lock.scalar(sa.select(sa.func.pg_try_advisory_lock(CYCLE_LOCK_KEY))):
                raise WorkerError("another crawl cycle is already running")
            async with (
                OpenRouterClient(settings) as provider_client,
                create_sessionmaker(engine)() as session,
                create_sessionmaker(score_engine)() as score_session,
            ):
                if stages:
                    cycle = await current_cycle(session)
                    if cycle is None:
                        raise WorkerError("no crawl cycle to re-run stages of; run a cycle first")
                    logger.info("crawl cycle %d: re-running %s", cycle.id, ", ".join(stages))
                else:
                    cycle = await start_or_resume_cycle(session, settings)
                    await _fetch_rounds(session, cycle, settings)
                if "extract" in stages:
                    logger.info("crawl cycle %d: extract stage", cycle.id)
                    await run_extract_stage(session, cycle, settings)
                if not stages or "embed" in stages:
                    logger.info("crawl cycle %d: embed stage", cycle.id)
                    meter = await open_meter(session, settings)
                    provider = OpenRouterEmbeddings(provider_client, settings, meter)
                    await run_embed_stage(session, cycle, provider, meter, settings)
                if not stages or "scores" in stages:
                    logger.info("crawl cycle %d: scores stage", cycle.id)
                    scored = await score_session.get_one(CrawlCycle, cycle.id)
                    await run_score_stage(score_session, scored, settings)
                if not stages:
                    await session.refresh(cycle)  # the scores stage wrote its stats
                    await finish_cycle(session, cycle)
                    logger.info("crawl cycle %d finished", cycle.id)
    finally:
        await engine.dispose()
        await score_engine.dispose()


async def _fetch_rounds(session: AsyncSession, cycle: CrawlCycle, settings: Settings) -> None:
    """Fetch then extract, for up to CYCLE_FETCH_ROUNDS rounds (one link level each)."""
    async with create_client(settings) as client:
        while True:
            logger.info("crawl cycle %d: fetch stage", cycle.id)
            await run_fetch_stage(session, cycle, client, settings)
            logger.info("crawl cycle %d: extract stage", cycle.id)
            await run_extract_stage(session, cycle, settings)
            if not await start_next_round(session, cycle, settings):
                return


async def seed_frontier(settings: Settings, url: str, feeds: list[str]) -> None:
    engine = create_engine(settings)
    try:
        async with create_sessionmaker(engine)() as session, session.begin():
            url_id = await add_seed(session, url, settings, feeds)
    finally:
        await engine.dispose()
    logger.info("seeded %s (url %d) with %d feed(s)", url, url_id, len(feeds))


async def login_link(settings: Settings, email: str) -> None:
    engine = create_engine(settings)
    try:
        async with create_sessionmaker(engine)() as session, session.begin():
            user_id = await ensure_user(session, email)
            link = await create_login_link(session, user_id, settings, datetime.now(UTC))
    finally:
        await engine.dispose()
    logger.info(
        "login link for %s (valid %g minutes, once): %s",
        email.strip().lower(),
        settings.login_token_ttl_minutes,
        link,
    )


async def seed_taxonomy(settings: Settings) -> None:
    topics = load_taxonomy()
    engine = create_engine(settings)
    try:
        async with create_sessionmaker(engine)() as session, session.begin():
            stats = await seed_topics(session, topics, load_descriptions())
    finally:
        await engine.dispose()
    logger.info(
        "taxonomy seeded: %d topics (%d inserted, %d updated, %d deleted)",
        len(topics),
        stats.inserted,
        stats.updated,
        stats.deleted,
    )


async def embed_taxonomy(settings: Settings, *, redo_all: bool) -> None:
    engine = create_engine(settings)
    sessionmaker = create_sessionmaker(engine)
    try:
        async with OpenRouterClient(settings) as client, sessionmaker() as session:
            meter = await open_meter(session, settings)
            try:
                count = await embed_topics(
                    session,
                    OpenRouterEmbeddings(client, settings, meter),
                    settings.topic_embedding_instruction,
                    redo_all=redo_all,
                )
            except ProviderError:
                # The vectors are lost, but what the requests before the failure cost is kept.
                await session.rollback()
                await record_spend(session, meter, SpendPurpose.EMBED_TOPICS)
                await session.commit()
                raise
            spent = await record_spend(session, meter, SpendPurpose.EMBED_TOPICS)
            # New topic vectors change every document's nearest topics.
            tags = await tag_documents(session, settings.embedding_model, settings) if count else 0
            await session.commit()
    finally:
        await engine.dispose()
    logger.info(
        "embedded %d topics with %s ($%.6f); %d document tags rewritten",
        count,
        settings.embedding_model,
        spent,
        tags,
    )


def cycle_calendar(settings: Settings) -> str:
    """systemd's OnCalendar form of CYCLE_LOCAL_START in CYCLE_TIMEZONE."""
    return f"*-*-* {settings.cycle_local_start:%H:%M:%S} {settings.cycle_timezone}"


async def alert(settings: Settings, subject: str, body: str) -> None:
    if await send_alert(settings, subject, body):
        logger.info("alert sent: %s", subject)


async def create_logins(settings: Settings) -> None:
    engine = create_engine(settings)
    try:
        async with engine.begin() as conn:
            names = await ensure_logins(conn, settings.db_login_passwords)
    finally:
        await engine.dispose()
    logger.info("database login users ready: %s", ", ".join(names) or "none configured")


async def export_preferences(settings: Settings, email: str) -> str:
    engine = create_engine(settings)
    try:
        async with create_sessionmaker(engine)() as session:
            exported = await export_user(session, email)
    finally:
        await engine.dispose()
    return exported.model_dump_json(indent=2)


async def import_preferences(settings: Settings, text: str) -> None:
    try:
        data = UserExport.model_validate_json(text)
    except ValidationError as error:
        raise TransferError(f"not a user export: {error}") from error
    engine = create_engine(settings)
    try:
        async with create_sessionmaker(engine)() as session, session.begin():
            await import_user(session, data, settings)
    finally:
        await engine.dispose()
    logger.info(
        "imported %s: %d interests, %d pins, %d survey answers",
        data.email,
        len(data.interests),
        len(data.pins),
        len(data.surveys),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.worker")
    commands = parser.add_subparsers(dest="command", required=True)

    cycle = commands.add_parser("cycle", help="crawl cycle commands")
    cycle_commands = cycle.add_subparsers(dest="action", required=True)
    run = cycle_commands.add_parser("run", help="run or resume a crawl cycle")
    run.add_argument(
        "--stage",
        action="append",
        choices=RERUN_STAGES,
        default=[],
        help="re-run only this stage for the current cycle (repeatable)",
    )
    cycle_commands.add_parser("calendar", help="print the nightly schedule for systemd")

    alert_parser = commands.add_parser("alert", help="alert commands")
    alert_commands = alert_parser.add_subparsers(dest="action", required=True)
    send = alert_commands.add_parser("send", help="email an alert; the body is read from stdin")
    send.add_argument("subject")

    db = commands.add_parser("db", help="database commands")
    db_commands = db.add_subparsers(dest="action", required=True)
    db_commands.add_parser("logins", help="create the login users of the database roles")

    frontier = commands.add_parser("frontier", help="crawl frontier commands")
    frontier_commands = frontier.add_subparsers(dest="action", required=True)
    seed = frontier_commands.add_parser("seed", help="enqueue a homepage as a pinned seed")
    seed.add_argument("url")
    seed.add_argument("--feed", action="append", default=[], help="a feed of the site")

    users = commands.add_parser("users", help="user commands")
    users_commands = users.add_subparsers(dest="action", required=True)
    link = users_commands.add_parser("login-link", help="print a one-time login link")
    link.add_argument("email")
    export = users_commands.add_parser("export", help="print a user's preferences as JSON")
    export.add_argument("email")
    users_commands.add_parser("import", help="import preferences exported as JSON from stdin")

    taxonomy = commands.add_parser("taxonomy", help="topic taxonomy commands")
    taxonomy_commands = taxonomy.add_subparsers(dest="action", required=True)
    taxonomy_commands.add_parser("seed", help="load the adapted taxonomy into web.topics")
    embed = taxonomy_commands.add_parser("embed", help="embed topics (needs OPENROUTER_API_KEY)")
    embed.add_argument("--all", action="store_true", help="re-embed every topic")

    args = parser.parse_args(argv)
    try:
        match (args.command, args.action):
            case ("cycle", "run"):
                stages = [stage for stage in RERUN_STAGES if stage in args.stage]
                asyncio.run(run_cycle(get_settings(), stages))
            case ("cycle", "calendar"):
                sys.stdout.write(cycle_calendar(get_settings()) + "\n")
            case ("alert", "send"):
                asyncio.run(alert(get_settings(), args.subject, sys.stdin.read()))
            case ("db", "logins"):
                asyncio.run(create_logins(get_settings()))
            case ("users", "export"):
                sys.stdout.write(asyncio.run(export_preferences(get_settings(), args.email)) + "\n")
            case ("users", "import"):
                asyncio.run(import_preferences(get_settings(), sys.stdin.read()))
            case ("frontier", "seed"):
                asyncio.run(seed_frontier(get_settings(), args.url, args.feed))
            case ("users", "login-link"):
                asyncio.run(login_link(get_settings(), args.email))
            case ("taxonomy", "seed"):
                asyncio.run(seed_taxonomy(get_settings()))
            case ("taxonomy", "embed"):
                asyncio.run(embed_taxonomy(get_settings(), redo_all=args.all))
            case _:
                logger.error("%s %s: unknown command", args.command, args.action)
                return 1
    except (
        AlertError,
        LoginError,
        ProviderError,
        SeedError,
        TaxonomyError,
        TransferError,
        WorkerError,
    ) as error:
        logger.error("%s %s failed: %s", args.command, args.action, error)
        return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx2").setLevel(logging.WARNING)  # one line per request otherwise
    # Per-page notes ("discarding data", missing optional font tools) that the extract
    # stage's counts already cover.
    for noisy in ("trafilatura", "htmldate", "pypdf"):
        logging.getLogger(noisy).setLevel(logging.ERROR)
    sys.exit(main())
