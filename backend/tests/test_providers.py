import json
import math

import httpx2
import pytest
from pydantic import SecretStr

from app.providers.embeddings import OpenRouterEmbeddings, fit
from app.providers.llm import OpenRouterLLM
from app.providers.openrouter import OpenRouterClient, ProviderError
from app.providers.spend import Charge, SpendCapReached, SpendMeter
from app.settings import Settings

pytestmark = pytest.mark.anyio

Handler = httpx2.MockTransport


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "openrouter_api_key": SecretStr("test-key"),
        "provider_retry_base_delay_s": 0,
        "embedding_batch_size": 2,
    }
    return Settings.model_validate({**values, **overrides})


def meter(budget_usd: float = 1.0) -> SpendMeter:
    return SpendMeter(budget_usd)


def embedding_response(request: httpx2.Request, size: int = 4096) -> httpx2.Response:
    texts = json.loads(request.content)["input"]
    # Reversed, to check results are put back in input order.
    data = [{"index": i, "embedding": [float(i + 1)] * size} for i in reversed(range(len(texts)))]
    return httpx2.Response(200, json={"data": data, "usage": {"prompt_tokens": 3}})


def test_fit_truncates_and_normalizes() -> None:
    vector = fit([3.0, 4.0, 100.0], 2)
    assert vector == pytest.approx([0.6, 0.8])


@pytest.mark.parametrize(("vector", "message"), [([1.0], "at least 2"), ([0.0, 0.0], "zero")])
def test_fit_rejects_unusable_vectors(vector: list[float], message: str) -> None:
    with pytest.raises(ProviderError, match=message):
        fit(vector, 2)


def test_client_needs_an_api_key() -> None:
    with pytest.raises(ProviderError, match="OPENROUTER_API_KEY"):
        OpenRouterClient(Settings(_env_file=None))


async def test_embed_documents_batches_and_keeps_order() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return embedding_response(request)

    async with OpenRouterClient(settings(), transport=Handler(handler)) as client:
        provider = OpenRouterEmbeddings(client, settings(), meter(), dimensions=8)
        vectors = await provider.embed_documents(["a", "b", "c"])

    assert [json.loads(r.content)["input"] for r in requests] == [["a", "b"], ["c"]]
    body = json.loads(requests[0].content)
    assert body["model"] == "qwen/qwen3-embedding-8b"
    assert requests[0].url.path == "/api/v1/embeddings"
    assert requests[0].headers["Authorization"] == "Bearer test-key"
    assert len(vectors) == 3
    assert all(len(v) == 8 and math.isclose(sum(x * x for x in v), 1) for v in vectors)


async def test_embed_queries_adds_the_instruction() -> None:
    inputs: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        inputs.extend(json.loads(request.content)["input"])
        return embedding_response(request)

    async with OpenRouterClient(settings(), transport=Handler(handler)) as client:
        provider = OpenRouterEmbeddings(client, settings(), meter())
        await provider.embed_queries(["pagerank", "hits"], "Find pages")

    assert inputs == ["Instruct: Find pages\nQuery:pagerank", "Instruct: Find pages\nQuery:hits"]


async def test_retries_transient_failures() -> None:
    statuses = iter([429, 503])

    def handler(request: httpx2.Request) -> httpx2.Response:
        status = next(statuses, 200)
        return embedding_response(request) if status == 200 else httpx2.Response(status)

    async with OpenRouterClient(settings(), transport=Handler(handler)) as client:
        vectors = await OpenRouterEmbeddings(client, settings(), meter()).embed_documents(["a"])
    assert len(vectors) == 1


async def test_gives_up_after_max_retries() -> None:
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(503)

    async with OpenRouterClient(settings(provider_max_retries=2), transport=Handler(handler)) as c:
        with pytest.raises(ProviderError, match="503"):
            await OpenRouterEmbeddings(c, settings(), meter()).embed_documents(["a"])
    assert calls == 3


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(401, json={"error": {"message": "bad key"}}),
        httpx2.Response(200, json={"error": {"message": "upstream failed"}}),
    ],
)
async def test_errors_are_not_retried(response: httpx2.Response) -> None:
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return response

    async with OpenRouterClient(settings(), transport=Handler(handler)) as client:
        with pytest.raises(ProviderError):
            await OpenRouterEmbeddings(client, settings(), meter()).embed_documents(["a"])
    assert calls == 1


async def test_rejects_short_vectors() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return embedding_response(request, size=512)

    async with OpenRouterClient(settings(), transport=Handler(handler)) as client:
        with pytest.raises(ProviderError, match="at least 1024"):
            await OpenRouterEmbeddings(client, settings(), meter()).embed_documents(["a"])


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        # Reported cost.
        ({"prompt_tokens": 7, "cost": 0.5}, Charge("qwen/qwen3-embedding-8b", 7, 0.5, False)),
        # Tokens but no cost: priced at EMBEDDING_USD_PER_MTOK.
        ({"prompt_tokens": 7}, Charge("qwen/qwen3-embedding-8b", 7, 7e-6, True)),
        # Nothing reported: 6 characters at 3 per token, priced.
        (None, Charge("qwen/qwen3-embedding-8b", 2, 2e-6, True)),
    ],
)
async def test_embeddings_are_charged(usage: dict[str, object] | None, expected: Charge) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        response = embedding_response(request)
        body = json.loads(response.content)
        body["usage"] = usage
        return httpx2.Response(200, json=body)

    spend = meter()
    config = settings(embedding_usd_per_mtok=1.0, provider_chars_per_token=3)
    async with OpenRouterClient(config, transport=Handler(handler)) as client:
        await OpenRouterEmbeddings(client, config, spend).embed_documents(["abc", "def"])
    assert spend.take() == [expected]
    assert spend.spent_usd == pytest.approx(expected.cost_usd)
    assert spend.take() == []


async def test_embeddings_over_budget_are_never_sent() -> None:
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return embedding_response(request)

    # Two batches of 2 texts of 3 characters: 2 estimated tokens each, $2 at $1M per Mtok.
    config = settings(embedding_usd_per_mtok=1_000_000, provider_chars_per_token=3)
    spend = meter(budget_usd=3)
    async with OpenRouterClient(config, transport=Handler(handler)) as client:
        provider = OpenRouterEmbeddings(client, config, spend)
        with pytest.raises(SpendCapReached, match="PROVIDER_MONTHLY_SPEND_CAP_USD"):
            await provider.embed_documents(["abc", "def", "ghi", "jkl"])
    # The first batch was sent and charged its reported (usage without cost: priced) tokens.
    assert calls == 1
    assert [charge.tokens for charge in spend.take()] == [3]


def chat_response(content: str | None, finish_reason: str = "stop") -> httpx2.Response:
    return httpx2.Response(
        200,
        json={
            "choices": [
                {
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        },
    )


async def test_llm_complete() -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(request.content))
        return chat_response("  A sentence.\n")

    async with OpenRouterClient(settings(), transport=Handler(handler)) as client:
        text = await OpenRouterLLM(client, "some/model").complete(
            system="Be brief.", prompt="Describe.", max_tokens=50
        )

    assert text == "A sentence."
    assert bodies == [
        {
            "model": "some/model",
            "messages": [
                {"role": "system", "content": "Be brief."},
                {"role": "user", "content": "Describe."},
            ],
            "max_tokens": 50,
        }
    ]


@pytest.mark.parametrize(
    ("response", "message"),
    [(chat_response(None), "no text"), (chat_response("Cut off", "length"), "max_tokens")],
)
async def test_llm_rejects_unusable_replies(response: httpx2.Response, message: str) -> None:
    async with OpenRouterClient(settings(), transport=Handler(lambda _: response)) as client:
        with pytest.raises(ProviderError, match=message):
            await OpenRouterLLM(client, "some/model").complete(system="s", prompt="p", max_tokens=5)
