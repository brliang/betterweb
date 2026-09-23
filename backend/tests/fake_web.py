"""An in-memory web for crawler tests, served through httpx2.MockTransport."""

from collections import Counter
from collections.abc import Awaitable, Callable, Mapping

import httpx2

Handler = Callable[[httpx2.Request], Awaitable[httpx2.Response]]
Route = httpx2.Response | list[httpx2.Response] | Handler


def html(body: str = "<html><body>page</body></html>", **headers: str) -> httpx2.Response:
    return httpx2.Response(200, text=body, headers={"content-type": "text/html", **headers})


def xml(body: str, content_type: str = "application/xml") -> httpx2.Response:
    return httpx2.Response(200, text=body, headers={"content-type": content_type})


def redirect(location: str, status: int = 301) -> httpx2.Response:
    return httpx2.Response(status, headers={"location": location})


class FakeWeb:
    """Serves routes by exact URL; anything else is a 404.

    A route is a response, a list of responses served in turn (the last one repeats), or an
    async handler.
    """

    def __init__(self, routes: Mapping[str, Route] | None = None) -> None:
        self.routes: dict[str, Route] = dict(routes or {})
        self.requests: list[httpx2.Request] = []
        self._served: Counter[str] = Counter()

    @property
    def urls(self) -> list[str]:
        return [str(request.url) for request in self.requests]

    def count(self, url: str) -> int:
        return self.urls.count(url)

    async def _handle(self, request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        self.requests.append(request)
        route = self.routes.get(url)
        if route is None:
            return httpx2.Response(404)
        if isinstance(route, httpx2.Response):
            return route
        if isinstance(route, list):
            index = min(self._served[url], len(route) - 1)
            self._served[url] += 1
            return route[index]
        return await route(request)

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self._handle)
