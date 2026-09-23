import pytest

from app.taxonomy import Description, TopicSpec
from scripts.describe_topics import build_prompt, clean, describe
from tests.fakes import FakeLLM

TOPICS = [
    TopicSpec("1", "Tech", None, 1),
    TopicSpec("2", "Computing", "1", 2),
    TopicSpec("3", "Programming", "1", 2),
    TopicSpec("4", "Science", None, 1),
]
BY_ID = {topic.external_id: topic for topic in TOPICS}


def test_prompt_gives_path_children_and_siblings() -> None:
    assert build_prompt(BY_ID["2"], BY_ID) == (
        "Topic: Tech > Computing\nSibling topics, for contrast: Programming"
    )
    assert build_prompt(BY_ID["1"], BY_ID) == (
        "Topic: Tech\nIts subtopics: Computing, Programming\nSibling topics, for contrast: Science"
    )


@pytest.mark.parametrize(
    ("reply", "expected"),
    [('"Quoted  sentence\nacross lines."', "Quoted sentence across lines."), ("Plain.", "Plain.")],
)
def test_clean(reply: str, expected: str) -> None:
    assert clean(reply) == expected


@pytest.mark.anyio
async def test_describe_records_the_name_it_described() -> None:
    llm = FakeLLM("Code and languages.")
    written = await describe(llm, [BY_ID["3"]], BY_ID, concurrency=2)
    assert written == {"3": Description(name="Programming", description="Code and languages.")}
    assert llm.prompts == [build_prompt(BY_ID["3"], BY_ID)]
