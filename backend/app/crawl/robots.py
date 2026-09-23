"""robots.txt per RFC 9309 (PLAN.md §1 principle 4, §6.2).

Parsing uses protego: the longest matching rule wins, `*` and `$` patterns work, and Allow
beats Disallow on a tie. The standard library's parser does none of that.

What a fetch of /robots.txt means (RFC 9309 §2.3.1):
- 2xx: the file's rules apply.
- 4xx: there are no rules; everything is allowed.
- 5xx or no answer: everything is disallowed, unless an earlier copy is cached.
"""

from dataclasses import dataclass
from enum import StrEnum

import httpx2
from protego import Protego

MAX_ROBOTS_BYTES = 500 * 1024
"""RFC 9309 §2.5: parse at least the first 500 KiB; anything after it is ignored."""
MAX_ROBOTS_REDIRECTS = 5
"""RFC 9309 §2.3.1.2: follow at least five redirects. Set as the crawl client's
`max_redirects`; page fetches don't follow redirects at all."""


def product_token(user_agent: str) -> str:
    """The name robots.txt and robots meta tags address: `bribot/0.1 (+...)` -> "bribot"."""
    return user_agent.split("/", 1)[0].split(" ", 1)[0].strip().lower()


class RobotsStatus(StrEnum):
    FOUND = "found"
    MISSING = "missing"
    """A 4xx: no rules apply."""
    UNREACHABLE = "unreachable"
    """A 5xx or a network error: nothing may be fetched."""


@dataclass(frozen=True)
class RobotsFetch:
    status: RobotsStatus
    text: str = ""


class Robots:
    """The rules for one host, as they apply to our user agent."""

    def __init__(self, text: str | None, user_agent: str) -> None:
        """`text` None means the host is unreachable: everything is disallowed."""
        self.text = text
        self._user_agent = user_agent
        self._rules = Protego.parse(text) if text is not None else None

    def allows(self, url: str) -> bool:
        return self._rules is not None and self._rules.can_fetch(url, self._user_agent)

    @property
    def crawl_delay_s(self) -> float | None:
        return self._rules.crawl_delay(self._user_agent) if self._rules else None

    @property
    def sitemaps(self) -> list[str]:
        return list(self._rules.sitemaps) if self._rules else []


async def fetch_robots(client: httpx2.AsyncClient, origin: str) -> RobotsFetch:
    try:
        response = await client.get(f"{origin}/robots.txt", follow_redirects=True)
    except httpx2.TooManyRedirects:
        # RFC 9309 §2.3.1.2: past the redirect limit, the file counts as unavailable (4xx).
        return RobotsFetch(RobotsStatus.MISSING)
    except httpx2.RequestError:
        return RobotsFetch(RobotsStatus.UNREACHABLE)
    if response.is_success:
        text = response.content[:MAX_ROBOTS_BYTES].decode("utf-8", errors="replace")
        return RobotsFetch(RobotsStatus.FOUND, text)
    if 400 <= response.status_code < 500:
        return RobotsFetch(RobotsStatus.MISSING)
    return RobotsFetch(RobotsStatus.UNREACHABLE)
