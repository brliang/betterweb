"""Worker CLI: ``python -m app.worker <command>``.

- ``cycle run``: the nightly crawl cycle (PLAN.md §6.1), wired up in M10; stages land in M3-M6.
- ``taxonomy seed``: load the adapted taxonomy into web.topics (idempotent).
- ``taxonomy embed [--all]``: embed topics that lack an embedding from the configured model.
"""

import argparse
import asyncio
import logging
import sys

from app.db.session import create_engine, create_sessionmaker
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
    cycle_commands.add_parser("run", help="run all cycle stages")

    taxonomy = commands.add_parser("taxonomy", help="topic taxonomy commands")
    taxonomy_commands = taxonomy.add_subparsers(dest="action", required=True)
    taxonomy_commands.add_parser("seed", help="load the adapted taxonomy into web.topics")
    embed = taxonomy_commands.add_parser("embed", help="embed topics (needs OPENROUTER_API_KEY)")
    embed.add_argument("--all", action="store_true", help="re-embed every topic")

    args = parser.parse_args(argv)
    try:
        match (args.command, args.action):
            case ("taxonomy", "seed"):
                asyncio.run(seed_taxonomy(get_settings()))
            case ("taxonomy", "embed"):
                asyncio.run(embed_taxonomy(get_settings(), redo_all=args.all))
            case _:
                logger.error(
                    "%s %s: not implemented yet (milestone M10)", args.command, args.action
                )
                return 1
    except (ProviderError, TaxonomyError) as error:
        logger.error("%s %s failed: %s", args.command, args.action, error)
        return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    sys.exit(main())
