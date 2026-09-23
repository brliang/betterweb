"""Metadata and links from an HTML page, parsed once and shared by the classifiers and
extractors (PLAN.md §6.3).

Everything here comes from the page's own markup: `<title>`, `<html lang>`, OpenGraph and
`citation_*` meta tags, `<link rel=canonical>`, schema.org JSON-LD and microdata, robots meta
tags, and `<a href>` links.
"""

import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from urllib.parse import urljoin, urlsplit

from lxml import etree, html

LANGUAGE = re.compile(r"[a-z]{2,3}")
"""An ISO 639 language code: the primary subtag of a BCP 47 tag such as `en-US`."""
XML_DECLARATION = re.compile(r"^\s*<\?xml[^>]*\?>")
META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([a-zA-Z0-9_.:-]+)""", re.IGNORECASE)
SNIFF_BYTES = 4096
"""How far into the body to look for a `<meta charset>` when the header names none."""
ROBOTS_VALUED_DIRECTIVES = frozenset(
    {"max-snippet", "max-image-preview", "max-video-preview", "unavailable_after"}
)
"""Robots directives written `name: value`, so not a user-agent prefix."""
NOFOLLOW_RELS = frozenset({"nofollow", "ugc", "sponsored"})
"""Link rel values that say the site doesn't vouch for the target: not followed, not an edge."""
TITLE_SEPARATORS = re.compile("\\s+(?:[-\u2013\u2014|\u00b7:]|::)\\s+")
"""Between a page's title and the site name that many sites append to it."""
DASHES = str.maketrans("\u2013\u2014", "--")
"""En and em dashes read as hyphens when comparing a title's tail with the site name."""
MIN_HOST_LABEL = 3
"""Host labels shorter than this (`en`, `m`) are too ambiguous to recognize in a title."""


@dataclass(frozen=True)
class HtmlLink:
    url: str
    """Absolute, resolved against the page's base URL; not yet canonicalized."""
    text: str
    nofollow: bool


@dataclass(frozen=True)
class HtmlMeta:
    title: str | None = None
    """`og:title`, else `citation_title`, else `<title>`."""
    description: str | None = None
    language: str | None = None
    canonical: str | None = None
    """`<link rel=canonical>`, absolute."""
    og_url: str | None = None
    og_type: str | None = None
    schema_types: frozenset[str] = frozenset()
    """schema.org types of the page's top-level JSON-LD entities and microdata items."""
    scholarly: bool = False
    """The page carries `citation_title` (Google Scholar / Highwire) tags: a paper."""
    author: str | None = None
    published_at: datetime | None = None
    robots: frozenset[str] = frozenset()
    """Directives from `<meta name="robots">` and `<meta name="<our bot>">`."""
    links: tuple[HtmlLink, ...] = field(default=(), repr=False)


def decode(body: bytes, charset: str | None) -> str:
    """The page's text: the header's charset, else a `<meta charset>`, else UTF-8."""
    if charset is None and (match := META_CHARSET.search(body[:SNIFF_BYTES])):
        charset = match.group(1).decode("ascii").lower()
    try:
        return body.decode(charset or "utf-8", errors="replace")
    except LookupError:  # an unknown charset name
        return body.decode("utf-8", errors="replace")


def parse_document(body: bytes, charset: str | None) -> html.HtmlElement:
    """Parse a page leniently; raises ValueError if there's nothing to parse."""
    text = XML_DECLARATION.sub("", decode(body, charset), count=1)
    if not text.strip():
        raise ValueError("empty page")
    try:
        return html.document_fromstring(text)
    except etree.ParserError as error:
        raise ValueError(f"unparseable page: {error}") from error


def parse_robots_directives(values: Iterable[str], product: str) -> frozenset[str]:
    """Directives from robots meta tags or X-Robots-Tag headers that apply to us.

    A value may address one user agent first (`googlebot: noindex`); values addressed to
    another agent are ignored. `none` means `noindex, nofollow`.
    """
    directives: set[str] = set()
    for value in values:
        head, sep, rest = value.partition(":")
        agent = head.strip().lower()
        if sep and agent not in ROBOTS_VALUED_DIRECTIVES and "," not in head:
            if agent != product:
                continue
            value = rest
        for part in value.split(","):
            directive = part.strip().lower()
            if directive == "none":
                directives |= {"noindex", "nofollow"}
            elif directive:
                directives.add(directive)
    return frozenset(directives)


def parse_datetime(value: str | None) -> datetime | None:
    """An ISO 8601 or `YYYY/MM/DD` date; a date alone is midnight UTC, and a time without a
    zone is taken as UTC."""
    if not value:
        return None
    value = value.strip().replace("/", "-")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        try:
            parsed = datetime.combine(date.fromisoformat(value[:10]), datetime.min.time())
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def strip_site_name(title: str, site_name: str | None, url: str) -> str:
    """`PageRank - Wikipedia` -> `PageRank`: drop a trailing segment that is the site's name
    (`og:site_name`) or starts with a label of its host name."""
    labels = {
        label
        for label in (urlsplit(url).hostname or "").split(".")[:-1]
        if len(label) >= MIN_HOST_LABEL and label != "www"
    }
    site = _fold(site_name) if site_name else None
    for separator in TITLE_SEPARATORS.finditer(title):
        head, tail = title[: separator.start()].strip(), _fold(title[separator.end() :])
        words = re.findall(r"[a-z0-9]+", tail)
        if head and (tail == site or (words and words[0] in labels)):
            return head
    return title


def _fold(text: str) -> str:
    return " ".join(text.translate(DASHES).casefold().split())


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split())
    return value or None


def _meta(tree: html.HtmlElement) -> dict[str, list[str]]:
    """Meta tag contents by lowercase `property` or `name`, in page order."""
    values: dict[str, list[str]] = {}
    for element in tree.iterfind(".//meta"):
        key = element.get("property") or element.get("name")
        content = element.get("content")
        if key and content is not None:
            values.setdefault(key.strip().lower(), []).append(content)
    return values


def _json_ld(tree: html.HtmlElement) -> Iterator[dict[str, object]]:
    """Top-level JSON-LD entities, including those in an `@graph`."""
    for script in tree.iterfind(".//script"):
        if (script.get("type") or "").strip().lower() != "application/ld+json":
            continue
        try:
            data = json.loads(script.text or "")
        except ValueError:
            continue
        stack = data if isinstance(data, list) else [data]
        for item in stack:
            if not isinstance(item, dict):
                continue
            yield item
            graph = item.get("@graph")
            if isinstance(graph, list):
                yield from (entity for entity in graph if isinstance(entity, dict))


def _types(value: object) -> set[str]:
    values = value if isinstance(value, list) else [value]
    # "https://schema.org/Article" and "Article" are the same type.
    return {item.rsplit("/", 1)[-1] for item in values if isinstance(item, str) and item}


def _name(value: object) -> str | None:
    """A JSON-LD author: a name, a Person or Organization, or a list of them (first wins)."""
    if isinstance(value, list):
        return next((name for item in value if (name := _name(item))), None)
    if isinstance(value, dict):
        value = value.get("name")
    return _clean(value) if isinstance(value, str) else None


def _microdata_types(tree: html.HtmlElement) -> set[str]:
    """Types of top-level microdata items (nested items describe parts of the page)."""
    types = set()
    for element in tree.xpath("//*[@itemscope and @itemtype][not(ancestor::*[@itemscope])]"):
        types |= _types(str(element.get("itemtype", "")).split())
    return types


def _links(tree: html.HtmlElement, base_url: str) -> tuple[HtmlLink, ...]:
    links = []
    for anchor in tree.iterfind(".//a"):
        href = anchor.get("href")
        if not href or href.startswith("#"):
            continue
        try:
            url = urljoin(base_url, href.strip())
        except ValueError:  # e.g. a malformed IPv6 host
            continue
        rels = set((anchor.get("rel") or "").lower().split())
        links.append(HtmlLink(url, _clean(anchor.text_content()) or "", bool(rels & NOFOLLOW_RELS)))
    return tuple(links)


def extract_meta(tree: html.HtmlElement, url: str, product: str) -> HtmlMeta:
    """Metadata of a parsed page fetched from `url`; `product` is our robots name."""
    meta = _meta(tree)

    def first(*keys: str) -> str | None:
        return next((value for key in keys for value in meta.get(key, []) if _clean(value)), None)

    base = tree.find(".//base[@href]")
    base_url = urljoin(url, base.get("href", "")) if base is not None else url
    canonical = next(
        (
            link.get("href")
            for link in tree.iterfind(".//link[@href]")
            if "canonical" in (link.get("rel") or "").lower().split()
        ),
        None,
    )
    title_element = tree.find(".//title")
    title = _clean(
        first("og:title", "citation_title")
        or (title_element.text_content() if title_element is not None else None)
    )
    language = (tree.get("lang") or tree.get("xml:lang") or "").split("-")[0].strip().lower()

    entities = list(_json_ld(tree))
    schema_types = _microdata_types(tree)
    author = published = None
    for entity in entities:
        schema_types |= _types(entity.get("@type"))
        author = author or _name(entity.get("author"))
        published = published or parse_datetime(_str(entity.get("datePublished")))

    robots = [*meta.get("robots", []), *meta.get(product, [])]
    return HtmlMeta(
        title=strip_site_name(title, first("og:site_name"), url) if title else None,
        description=_clean(first("og:description", "description")),
        language=language if LANGUAGE.fullmatch(language) else None,
        canonical=urljoin(base_url, canonical.strip()) if canonical else None,
        og_url=_absolute(base_url, first("og:url")),
        og_type=_clean(first("og:type")),
        schema_types=frozenset(schema_types),
        scholarly="citation_title" in meta,
        author=author or _clean(first("citation_author", "author", "article:author")),
        published_at=published
        or parse_datetime(
            first("article:published_time", "citation_publication_date", "citation_date")
        ),
        robots=parse_robots_directives(robots, product),
        links=_links(tree, base_url),
    )


def _str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _absolute(base_url: str, value: str | None) -> str | None:
    return urljoin(base_url, value.strip()) if value else None
