"""Worker CLI: ``python -m app.worker <command>``.

- ``cycle run``: run (or resume) the nightly crawl cycle (PLAN.md §6.1). Stages so far: fetch
  (M3), then extract + dedup (M4); embed and scores land in M5-M6, and M10 adds scheduling
  and alerts.
- ``frontier seed URL [--feed FEED ...]``: enqueue a homepage as a pinned seed (for development;
  the API does this when a user pins a domain).
- ``taxonomy seed``: load the adapted taxonomy into web.topics (idempotent).
- ``taxonomy embed [--all]``: embed topics that lack an embedding from the configured model.
"""

import argparse
import asyncio
import logging
import sys

import sqlalchemy as sa

from app.crawl.cycle import CYCLE_LOCK_KEY, finish_cycle, start_or_resume_cycle
from app.crawl.fetch_stage import run_fetch_stage
from app.crawl.frontier import SeedError, add_seed
from app.crawl.http import create_client
from app.db.session import create_engine, create_sessionmaker
from app.ingest.stage import run_extract_stage
from app.providers.embeddings import OpenRouterEmbeddings
from app.providers.openrouter import OpenRouterClient, ProviderError
from app.settings import Settings, get_settings
from app.taxonomy import (
    TaxonomyError,
    embed_topics,
    load_descriptions,
    load_taxonomy,
    seed_topics,
)

logger = logging.getLogger("app.worker")

PLACEHOLDER_CONTACT = "example.invalid"


class WorkerError(Exception):
    """A command can't run as configured."""


async def run_cycle(settings: Settings) -> None:
    if PLACEHOLDER_CONTACT in settings.user_agent:
        # PLAN.md principle 4 and §14 Q5: identify honestly, with a real contact page.
        raise WorkerError(
            "USER_AGENT still points at the placeholder contact URL; set it to a real bot "
            "contact page before crawling"
        )
    engine = create_engine(settings)
    try:
        async with engine.connect() as lock:
            if not await lock.scalar(sa.select(sa.func.pg_try_advisory_lock(CYCLE_LOCK_KEY))):
                raise WorkerError("another crawl cycle is already running")
            async with create_sessionmaker(engine)() as session, create_client(settings) as client:
                cycle = await start_or_resume_cycle(session, settings)
                logger.info("crawl cycle %d: fetch stage", cycle.id)
                await run_fetch_stage(session, cycle, client, settings)
                logger.info("crawl cycle %d: extract stage", cycle.id)
                await run_extract_stage(session, cycle, settings)
                await finish_cycle(session, cycle)
                logger.info("crawl cycle %d finished", cycle.id)
    finally:
        await engine.dispose()


async def seed_frontier(settings: Settings, url: str, feeds: list[str]) -> None:
    engine = create_engine(settings)
    try:
        async with create_sessionmaker(engine)() as session, session.begin():
            url_id = await add_seed(session, url, settings, feeds)
    finally:
        await engine.dispose()
    logger.info("seeded %s (url %d) with %d feed(s)", url, url_id, len(feeds))


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
    try:
        async with (
            OpenRouterClient(settings) as client,
            create_sessionmaker(engine)() as session,
            session.begin(),
        ):
            count = await embed_topics(
                session,
                OpenRouterEmbeddings(client, settings),
                settings.topic_embedding_instruction,
                redo_all=redo_all,
            )
    finally:
        await engine.dispose()
    logger.info("embedded %d topics with %s", count, settings.embedding_model)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.worker")
    commands = parser.add_subparsers(dest="command", required=True)

    cycle = commands.add_parser("cycle", help="crawl cycle commands")
    cycle_commands = cycle.add_subparsers(dest="action", required=True)
    cycle_commands.add_parser("run", help="run or resume a crawl cycle")

    frontier = commands.add_parser("frontier", help="crawl frontier commands")
    frontier_commands = frontier.add_subparsers(dest="action", required=True)
    seed = frontier_commands.add_parser("seed", help="enqueue a homepage as a pinned seed")
    seed.add_argument("url")
    seed.add_argument("--feed", action="append", default=[], help="a feed of the site")

    taxonomy = commands.add_parser("taxonomy", help="topic taxonomy commands")
    taxonomy_commands = taxonomy.add_subparsers(dest="action", required=True)
    taxonomy_commands.add_parser("seed", help="load the adapted taxonomy into web.topics")
    embed = taxonomy_commands.add_parser("embed", help="embed topics (needs OPENROUTER_API_KEY)")
    embed.add_argument("--all", action="store_true", help="re-embed every topic")

    args = parser.parse_args(argv)
    try:
        match (args.command, args.action):
            case ("cycle", "run"):
                asyncio.run(run_cycle(get_settings()))
            case ("frontier", "seed"):
                asyncio.run(seed_frontier(get_settings(), args.url, args.feed))
            case ("taxonomy", "seed"):
                asyncio.run(seed_taxonomy(get_settings()))
            case ("taxonomy", "embed"):
                asyncio.run(embed_taxonomy(get_settings(), redo_all=args.all))
            case _:
                logger.error("%s %s: unknown command", args.command, args.action)
                return 1
    except (ProviderError, SeedError, TaxonomyError, WorkerError) as error:
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
