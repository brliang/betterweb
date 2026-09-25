"""Keep the crawler off non-public addresses (PLAN.md §14 Q6).

A crawled page can link to any host, including ones that resolve to loopback, private or
link-local addresses (`127.0.0.1`, `10.0.0.5`, the cloud metadata service at
`169.254.169.254`), which would make the bot request services on its own machine or network.
So the crawler's connections resolve each host themselves and connect only to the addresses
they checked; a name can't pass the check and then resolve somewhere else. Every request,
robots.txt and redirects included, opens its connection here, because redirects are never
followed inline (robots.txt redirects are, but each hop connects anew).
"""

import ipaddress
import socket
from collections.abc import Awaitable, Callable, Iterable

import anyio
import httpcore2
import httpx2

Resolver = Callable[[str, int], Awaitable[list[str]]]


class NonPublicAddressError(httpcore2.ConnectError):
    """A host resolved to an address the crawler must not connect to. A ConnectError, so
    httpx2 raises it as one (with this as its cause) and every caller already handles it:
    robots.txt counts as unreachable, so nothing else on the host is fetched."""


def is_public(address: str) -> bool:
    """Whether `address` is on the public internet: not loopback, private, link-local,
    shared (CGNAT), reserved, multicast or unspecified."""
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped  # ::ffff:127.0.0.1 is 127.0.0.1
    return ip.is_global and not ip.is_multicast


async def resolve(host: str, port: int) -> list[str]:
    """The host's addresses, in the order the system resolver prefers them."""
    try:
        infos = await anyio.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as error:  # socket.gaierror: no such name, or DNS unreachable
        raise httpcore2.ConnectError(f"cannot resolve {host}: {error}") from error
    return list(dict.fromkeys(str(sockaddr[0]) for *_, sockaddr in infos))


class PublicOnlyBackend(httpcore2.AsyncNetworkBackend):
    """Opens TCP connections only to public addresses (TLS still verifies the host name)."""

    def __init__(
        self,
        backend: httpcore2.AsyncNetworkBackend | None = None,
        resolver: Resolver = resolve,
    ) -> None:
        self._backend = backend or httpcore2.AnyIOBackend()
        self._resolve = resolver

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 (httpcore2's backend interface)
        local_address: str | None = None,
        socket_options: Iterable[httpcore2.SOCKET_OPTION] | None = None,
    ) -> httpcore2.AsyncNetworkStream:
        with anyio.move_on_after(timeout) as scope:
            addresses = await self._resolve(host, port)
        if scope.cancelled_caught:
            raise httpcore2.ConnectTimeout(f"resolving {host} timed out")
        if not addresses:
            raise httpcore2.ConnectError(f"{host} has no addresses")
        if blocked := [address for address in addresses if not is_public(address)]:
            # One non-public address is enough: the host is pointed into a private network.
            raise NonPublicAddressError(f"{host} resolves to non-public {', '.join(blocked)}")
        error: httpcore2.ConnectError | httpcore2.ConnectTimeout | None = None
        for address in addresses:  # e.g. IPv6 first, then IPv4 where there's no IPv6 route
            try:
                return await self._backend.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore2.ConnectError, httpcore2.ConnectTimeout) as failed:
                error = failed
        assert error is not None  # noqa: S101 (addresses is not empty)
        raise error

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


class PublicOnlyTransport(httpx2.AsyncHTTPTransport):
    """httpx2's transport, connecting through a PublicOnlyBackend and ignoring proxy
    environment variables (a proxy would do the connecting instead)."""

    def __init__(self, *, limits: httpx2.Limits, backend: PublicOnlyBackend | None = None) -> None:
        super().__init__(limits=limits, trust_env=False)
        # AsyncHTTPTransport takes no network backend, so its pool is replaced by one that
        # has ours and the same settings; handle_async_request only uses the pool.
        self._pool = httpcore2.AsyncConnectionPool(
            ssl_context=httpx2.create_ssl_context(trust_env=False),
            max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry,
            network_backend=backend or PublicOnlyBackend(),
        )


def blocked_address(error: BaseException) -> NonPublicAddressError | None:
    """The NonPublicAddressError behind an httpx2 error, if that's why the request failed."""
    cause = error.__cause__
    return cause if isinstance(cause, NonPublicAddressError) else None
