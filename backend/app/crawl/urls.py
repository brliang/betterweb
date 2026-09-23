"""URL canonicalization (PLAN.md §6.3), applied before a URL is stored in web.urls.

Two spellings of the same resource must produce the same string, and the result must still
fetch the same resource. So case, default ports, fragments, tracking parameters, dot segments,
percent-encoding and query order are normalized, but the path's case and trailing slash are
left alone and http is not upgraded to https.
"""

import re
import string
from collections.abc import Sequence
from urllib.parse import quote, unquote, urlsplit, urlunsplit

DEFAULT_PORTS = {"http": 80, "https": 443}
UNRESERVED = frozenset(string.ascii_letters + string.digits + "-._~")
PATH_SAFE = "/:@!$&'()*+,;="
"""Reserved characters that keep their meaning in a path, so they stay unescaped."""
QUERY_SAFE = "/:@!$'()*+,;=?"
"""The same for one `key=value` query piece (`&` separates pieces, so it is not listed)."""

_ESCAPE = re.compile(r"%([0-9A-Fa-f]{2})")
_STRAY_PERCENT = re.compile(r"%(?![0-9A-Fa-f]{2})")


class TrackingParams:
    """Matches query parameter names against patterns like `utm_*` (case-insensitive)."""

    def __init__(self, patterns: Sequence[str]) -> None:
        self._exact = frozenset(p.lower() for p in patterns if not p.endswith("*"))
        self._prefixes = tuple(p[:-1].lower() for p in patterns if p.endswith("*"))

    def __contains__(self, name: str) -> bool:
        name = name.lower()
        return name in self._exact or name.startswith(self._prefixes)


def _normalize_escapes(text: str, safe: str) -> str:
    """Escape what must be escaped, uppercase escapes, and unescape unreserved characters."""
    text = quote(_STRAY_PERCENT.sub("%25", text), safe=safe + "%")

    def fix(match: re.Match[str]) -> str:
        char = chr(int(match.group(1), 16))
        return char if char in UNRESERVED else f"%{match.group(1).upper()}"

    return _ESCAPE.sub(fix, text)


def _remove_dot_segments(path: str) -> str:
    """RFC 3986 §5.2.4 for an absolute path: `/a/./b/../c` becomes `/a/c`."""
    segments = path.split("/")[1:]
    output: list[str] = []
    for segment in segments:
        if segment == "..":
            if output:
                output.pop()
        elif segment != ".":
            output.append(segment)
    if segments and segments[-1] in (".", ".."):
        output.append("")
    return "/" + "/".join(output)


def _normalize_host(host: str) -> str | None:
    host = host.rstrip(".")
    if not host:
        return None
    if ":" in host:  # IPv6 literal; urlsplit strips the brackets
        return f"[{host}]"
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError:
        return None


def canonicalize(url: str, tracking: TrackingParams) -> str | None:
    """The canonical form of an absolute http(s) URL, or None if it can't be crawled.

    Returns None for other schemes, URLs with credentials in them, malformed ports and hosts.
    """
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in DEFAULT_PORTS or "@" in parts.netloc or parts.hostname is None:
        return None
    host = _normalize_host(parts.hostname)
    if host is None:
        return None
    netloc = host if port in (None, DEFAULT_PORTS[scheme]) else f"{host}:{port}"

    path = _remove_dot_segments(_normalize_escapes(parts.path or "/", PATH_SAFE))
    if not path.startswith("/"):
        path = "/" + path

    pieces = []
    for piece in parts.query.split("&"):
        if not piece:
            continue
        name = unquote(piece.split("=", 1)[0].replace("+", " "))
        if name not in tracking:
            pieces.append(_normalize_escapes(piece, QUERY_SAFE))
    # Stable, so repeated parameters (a=2&a=1) keep their relative order.
    pieces.sort(key=lambda piece: piece.split("=", 1)[0])
    return urlunsplit((scheme, netloc, path, "&".join(pieces), ""))


def host_of(canonical_url: str) -> str:
    """The domain key (web.domains.host) of a canonical URL: its authority."""
    return urlsplit(canonical_url).netloc


def origin_of(canonical_url: str) -> str:
    parts = urlsplit(canonical_url)
    return f"{parts.scheme}://{parts.netloc}"


def same_site(host_a: str, host_b: str) -> bool:
    """True when two hosts differ at most by a leading `www.`.

    Moving between them is internal navigation (depth + 1), not an external hop.
    """
    return host_a.removeprefix("www.") == host_b.removeprefix("www.")
