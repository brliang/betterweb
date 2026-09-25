"""The crawler connects only to public addresses (PLAN.md §14 Q6)."""

from collections.abc import Iterable
from datetime import UTC, datetime

import httpcore2
import httpx2
import pytest

from app.crawl.addresses import PublicOnlyBackend, PublicOnlyTransport, Resolver, is_public
from app.crawl.http import FetchResult, Outcome, create_client, fetch
from app.crawl.robots import RobotsStatus, fetch_robots
from app.settings import Settings

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 9, 25, 12, tzinfo=UTC)
PAGE = [b"HTTP/1.1 200 OK\r\n", b"Content-Type: text/html\r\n", b"Content-Length: 2\r\n\r\n", b"hi"]
PUBLIC_V4 = "93.184.215.14"
PUBLIC_V6 = "2606:2800:21f:cb07:6820:80da:af6b:8b2c"


@pytest.mark.parametrize(
    ("address", "public"),
    [
        (PUBLIC_V4, True),
        (PUBLIC_V6, True),
        ("127.0.0.1", False),
        ("10.0.0.5", False),
        ("172.16.0.1", False),
        ("192.168.1.1", False),
        ("169.254.169.254", False),  # the cloud metadata service
        ("100.64.0.1", False),  # shared address space (CGNAT)
        ("0.0.0.0", False),  # noqa: S104 (an address to check, not one to bind)
        ("224.0.0.1", False),
        ("::1", False),
        ("fe80::1%eth0", False),
        ("fd00::1", False),
        ("::ffff:127.0.0.1", False),
        ("::ffff:10.0.0.5", False),
        (f"::ffff:{PUBLIC_V4}", True),
    ],
)
def test_is_public(address: str, *, public: bool) -> None:
    assert is_public(address) is public


class Network(httpcore2.AsyncMockBackend):
    """Serves PAGE on every connection, recording the addresses connected to."""

    def __init__(self, refuse: Iterable[str] = ()) -> None:
        super().__init__(PAGE)
        self.connected: list[str] = []
        self._refuse = set(refuse)

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 (httpcore2's backend interface)
        local_address: str | None = None,
        socket_options: Iterable[httpcore2.SOCKET_OPTION] | None = None,
    ) -> httpcore2.AsyncNetworkStream:
        self.connected.append(host)
        if host in self._refuse:
            raise httpcore2.ConnectError(f"{host} refused")
        return await super().connect_tcp(host, port, timeout, local_address, socket_options)


def dns(records: dict[str, list[str]]) -> Resolver:
    async def resolve(host: str, port: int) -> list[str]:
        return records[host]

    return resolve


async def get(url: str, network: Network, records: dict[str, list[str]]) -> FetchResult:
    backend = PublicOnlyBackend(network, dns(records))
    transport = PublicOnlyTransport(limits=httpx2.Limits(), backend=backend)
    async with create_client(Settings(_env_file=None), transport=transport) as client:
        return await fetch(client, url, max_bytes=100, now=NOW)


async def test_a_public_host_is_fetched_from_the_address_that_was_checked() -> None:
    network = Network()
    result = await get("http://example.com/", network, {"example.com": [PUBLIC_V4]})
    assert (result.outcome, result.body) == (Outcome.OK, b"hi")
    assert network.connected == [PUBLIC_V4]


@pytest.mark.parametrize(
    "addresses",
    [
        ["169.254.169.254"],
        ["127.0.0.1"],
        [PUBLIC_V4, "10.0.0.5"],  # one private address is enough
    ],
)
async def test_a_host_on_a_non_public_address_is_never_connected_to(addresses: list[str]) -> None:
    network = Network()
    result = await get("http://internal.example/", network, {"internal.example": addresses})
    assert result == FetchResult(Outcome.GONE, detail="non-public address")
    assert network.connected == []


async def test_the_next_address_is_tried_when_one_cannot_connect() -> None:
    network = Network(refuse=[PUBLIC_V6])
    result = await get("http://example.com/", network, {"example.com": [PUBLIC_V6, PUBLIC_V4]})
    assert result.outcome is Outcome.OK
    assert network.connected == [PUBLIC_V6, PUBLIC_V4]


async def test_a_host_that_cannot_connect_is_retried_later() -> None:
    network = Network(refuse=[PUBLIC_V4])
    result = await get("http://example.com/", network, {"example.com": [PUBLIC_V4]})
    assert (result.outcome, result.detail) == (Outcome.RETRY, "ConnectError")


async def test_robots_txt_on_a_non_public_address_counts_as_unreachable() -> None:
    # Unreachable robots.txt means nothing else on the host is fetched.
    backend = PublicOnlyBackend(Network(), dns({"internal.example": ["10.0.0.5"]}))
    transport = PublicOnlyTransport(limits=httpx2.Limits(), backend=backend)
    async with create_client(Settings(_env_file=None), transport=transport) as client:
        fetched = await fetch_robots(client, "http://internal.example")
    assert fetched.status is RobotsStatus.UNREACHABLE


async def test_the_crawl_client_refuses_private_addresses_by_default() -> None:
    # An address literal resolves to itself without any DNS lookup.
    async with create_client(Settings(_env_file=None)) as client:
        result = await fetch(client, "http://127.0.0.1:9/", max_bytes=100, now=NOW)
    assert result == FetchResult(Outcome.GONE, detail="non-public address")
