"""Write a one-sentence description for each taxonomy topic that lacks a current one.

    uv run python -m scripts.describe_topics         # only missing or renamed topics
    uv run python -m scripts.describe_topics --all   # rewrite every description

Run after changing data/taxonomy/adaptation.toml, then review and commit
data/taxonomy/descriptions.json. IAB topics have no descriptions, and embedding
`path: description` tags documents far better than a bare name (PLAN.md §6.4). Needs
OPENROUTER_API_KEY; the model is TAXONOMY_DESCRIPTION_MODEL.
"""

import argparse
import asyncio
import logging
import sys
from collections.abc import Mapping, Sequence

from app.providers.llm import LLMProvider, OpenRouterLLM
from app.providers.openrouter import OpenRouterClient
from app.settings import get_settings
from app.taxonomy import (
    Description,
    TopicSpec,
    load_descriptions,
    load_taxonomy,
    save_descriptions,
    stale_descriptions,
    topic_path,
)

logger = logging.getLogger("scripts.describe_topics")

MAX_TOKENS = 200
"""Room for one sentence; a longer reply is an error rather than a truncated description."""

SYSTEM_PROMPT = """\
You write descriptions for the topics of a content taxonomy. Each description is embedded and \
compared with embeddings of web articles to decide which topics an article is about, so it \
should name the concrete subjects, activities and vocabulary that articles on the topic \
contain, and should distinguish the topic from its siblings.

Write exactly one sentence of 20 to 40 words. Don't begin with the topic's name or with \
"This topic", don't use marketing language, and don't mention advertising. Reply with the \
sentence only."""


def build_prompt(topic: TopicSpec, by_id: Mapping[str, TopicSpec]) -> str:
    parent = by_id[topic.parent_id] if topic.parent_id is not None else None
    siblings = sorted(
        other.name
        for other in by_id.values()
        if other.parent_id == topic.parent_id and other.external_id != topic.external_id
    )
    children = sorted(
        other.name for other in by_id.values() if other.parent_id == topic.external_id
    )
    lines = [f"Topic: {topic_path(topic.name, parent.name if parent else None)}"]
    if children:
        lines.append(f"Its subtopics: {', '.join(children)}")
    if siblings:
        lines.append(f"Sibling topics, for contrast: {', '.join(siblings)}")
    return "\n".join(lines)


def clean(text: str) -> str:
    sentence = " ".join(text.split()).strip("\"'“”")
    if not sentence:
        raise ValueError("empty description")
    return sentence


async def describe(
    llm: LLMProvider, topics: Sequence[TopicSpec], by_id: Mapping[str, TopicSpec], concurrency: int
) -> dict[str, Description]:
    limit = asyncio.Semaphore(concurrency)

    async def one(topic: TopicSpec) -> tuple[str, Description]:
        async with limit:
            text = await llm.complete(
                system=SYSTEM_PROMPT, prompt=build_prompt(topic, by_id), max_tokens=MAX_TOKENS
            )
        logger.info("%s: %s", topic.name, text)
        return topic.external_id, Description(name=topic.name, description=clean(text))

    return dict(await asyncio.gather(*(one(topic) for topic in topics)))


async def run(rewrite_all: bool) -> int:
    settings = get_settings()
    topics = load_taxonomy()
    by_id = {topic.external_id: topic for topic in topics}
    existing = load_descriptions()
    todo = topics if rewrite_all else stale_descriptions(topics, existing)
    if todo:
        async with OpenRouterClient(settings) as client:
            llm = OpenRouterLLM(client, settings.taxonomy_description_model)
            written = await describe(llm, todo, by_id, settings.llm_concurrency)
    else:
        written = {}
    # Taxonomy order, dropping topics that no longer exist.
    merged = {**existing, **written}
    save_descriptions({topic.external_id: merged[topic.external_id] for topic in topics})
    logger.info(
        "wrote %d descriptions with %s; %d topics in total",
        len(written),
        settings.taxonomy_description_model,
        len(topics),
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.describe_topics")
    parser.add_argument("--all", action="store_true", help="rewrite every description")
    args = parser.parse_args(argv)
    return asyncio.run(run(rewrite_all=args.all))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(main())
