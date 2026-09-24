import pytest

from app.crawl.urls import (
    TrackingParams,
    canonicalize,
    has_path_segment,
    host_of,
    origin_of,
    registrable_domain,
    same_site,
)
from app.settings import Settings

TRACKING = TrackingParams(Settings(_env_file=None).tracking_params)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # Scheme and host are case-insensitive; the path is not.
        ("HTTPS://Example.COM/Path", "https://example.com/Path"),
        # An empty path is "/"; a trailing slash is kept (it can name another resource).
        ("https://example.com", "https://example.com/"),
        ("https://example.com/a/", "https://example.com/a/"),
        ("https://example.com/a", "https://example.com/a"),
        # Default ports go; others stay.
        ("http://example.com:80/", "http://example.com/"),
        ("https://example.com:443/", "https://example.com/"),
        ("https://example.com:8443/", "https://example.com:8443/"),
        ("http://example.com:443/", "http://example.com:443/"),
        # http is not upgraded: it may be a different resource.
        ("http://example.com/", "http://example.com/"),
        # Fragments go.
        ("https://example.com/a#section", "https://example.com/a"),
        ("https://example.com/#", "https://example.com/"),
        # Dot segments are resolved.
        ("https://example.com/a/./b/../c", "https://example.com/a/c"),
        ("https://example.com/a/b/..", "https://example.com/a/"),
        ("https://example.com/../../a", "https://example.com/a"),
        ("https://example.com/%2E%2E/a", "https://example.com/a"),
        ("https://example.com/a//b", "https://example.com/a//b"),
        # Escapes: uppercase hex, unreserved characters decoded, reserved ones kept escaped.
        ("https://example.com/%7euser", "https://example.com/~user"),
        ("https://example.com/a%2fb", "https://example.com/a%2Fb"),
        ("https://example.com/caf%c3%a9", "https://example.com/caf%C3%A9"),
        ("https://example.com/café", "https://example.com/caf%C3%A9"),
        ("https://example.com/a b", "https://example.com/a%20b"),
        ("https://example.com/100%", "https://example.com/100%25"),
        # Tracking parameters go, case-insensitively, by name or prefix.
        ("https://example.com/?utm_source=x&utm_medium=y", "https://example.com/"),
        ("https://example.com/?UTM_Campaign=x&id=1", "https://example.com/?id=1"),
        ("https://example.com/?fbclid=1&gclid=2&mc_eid=3&ref=4", "https://example.com/"),
        ("https://example.com/?reference=1", "https://example.com/?reference=1"),
        # Remaining parameters are sorted by name; repeated names keep their order.
        ("https://example.com/?b=2&a=1", "https://example.com/?a=1&b=2"),
        ("https://example.com/?a=2&b=1&a=1", "https://example.com/?a=2&a=1&b=1"),
        # Empty pieces go; empty values stay.
        ("https://example.com/?&&a=&b", "https://example.com/?a=&b"),
        ("https://example.com/?", "https://example.com/"),
        ("https://example.com/?q=a%20b&q=%e2%82%ac", "https://example.com/?q=a%20b&q=%E2%82%AC"),
        ("https://example.com/?q=a+b", "https://example.com/?q=a+b"),
        # Hosts: trailing dot, internationalized names, IPv6 literals.
        ("https://example.com./a", "https://example.com/a"),
        ("https://Bücher.de/", "https://xn--bcher-kva.de/"),
        ("https://[::1]:8080/a", "https://[::1]:8080/a"),
        ("  https://example.com/a  ", "https://example.com/a"),
    ],
)
def test_canonicalize(url: str, expected: str) -> None:
    assert canonicalize(url, TRACKING) == expected


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/",
        "mailto:someone@example.com",
        "javascript:alert(1)",
        "/relative/path",
        "https://user:password@example.com/",
        "https://example.com:99999/",
        "https://example.com:port/",
        "https:///path-without-host",
        "https://./",
    ],
)
def test_uncrawlable_urls(url: str) -> None:
    assert canonicalize(url, TRACKING) is None


@pytest.mark.parametrize(
    "url",
    [
        "HTTP://Example.COM:80/a/./b/../c?b=2&utm_source=x&a=1#frag",
        "https://example.com/café?q=a b&z=%7e",
        "https://Bücher.de./%2E/x?",
    ],
)
def test_canonicalize_is_idempotent(url: str) -> None:
    once = canonicalize(url, TRACKING)
    assert once is not None
    assert canonicalize(once, TRACKING) == once


def test_tracking_params_are_configurable() -> None:
    tracking = TrackingParams(["session*", "src"])
    assert canonicalize("https://example.com/?sessionid=1&src=2&utm_source=3", tracking) == (
        "https://example.com/?utm_source=3"
    )


def test_hosts_and_origins() -> None:
    assert host_of("https://example.com:8443/a?b") == "example.com:8443"
    assert origin_of("https://example.com:8443/a?b") == "https://example.com:8443"


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("example.com", "example.com", True),
        ("www.example.com", "example.com", True),
        ("example.com", "www.example.com", True),
        ("blog.example.com", "example.com", False),
        ("example.org", "example.com", False),
    ],
)
def test_same_site(a: str, b: str, expected: bool) -> None:
    assert same_site(a, b) is expected


ACCOUNT = frozenset({"login", "subscription"})


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com/login", True),
        ("https://example.com/athletic/login2/?redirect_uri=x", True),
        ("https://example.com/Login/", True),
        ("https://example.com/subscription/athletic?onboarded=false", True),
        ("https://example.com/2026/09/why-i-cancelled-my-subscription/", False),
        ("https://example.com/blog/?next=/login", False),
        ("https://example.com/logins", False),
    ],
)
def test_has_path_segment(url: str, expected: bool) -> None:
    assert has_path_segment(url, ACCOUNT) is expected


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("herman.bearblog.dev", "bearblog.dev"),
        ("bearblog.dev", "bearblog.dev"),
        ("someone.substack.com", "substack.com"),
        ("cooking.nytimes.com", "nytimes.com"),
        ("www.bbc.co.uk", "bbc.co.uk"),
        # Only ICANN suffixes count: blogs on a platform's own suffix share its servers.
        ("someone.github.io", "github.io"),
        ("blog.example.com:8443", "example.com"),
        # IP addresses and bare suffixes are their own.
        ("127.0.0.1:8000", "127.0.0.1"),
        ("[::1]:8000", "::1"),
        ("localhost", "localhost"),
    ],
)
def test_registrable_domain(host: str, expected: str) -> None:
    assert registrable_domain(host) == expected
