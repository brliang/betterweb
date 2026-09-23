"""Extraction (PLAN.md §6.3): turn a fetched body into document fields, links and a declared
canonical URL.

`analyze` is pure (no database, no network) and CPU-bound, so the extract stage runs it in a
worker thread. Extractors form a registry keyed by document type; a type without its own
extractor, or one whose extractor can't read the body's media type, falls back to the
default extractor for that media type (`page` for HTML, `pdf` for PDFs).
"""

import io
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlsplit

import trafilatura
from lxml import html
from pypdf import PdfReader

from app.crawl.urls import TrackingParams, canonicalize, host_of, same_site
from app.enums import DocumentType
from app.ingest.classify import (
    DEFAULT_CLASSIFIERS,
    HTML_TYPES,
    PDF_TYPES,
    Classifier,
    PageFacts,
    classify,
)
from app.ingest.html import HtmlMeta, extract_meta, parse_document, parse_robots_directives
from app.ingest.text import content_hash, excerpt, normalize_text, word_count
from app.settings import Settings

logger = logging.getLogger(__name__)

DATE_PARAMS = {"original_date": True, "extensive_search": False}
"""htmldate options: the publication date, not the last edit; skip the slow deep search
(dates in the page's markup come first anyway)."""


@dataclass(frozen=True)
class ParsedPage:
    url: str
    content_type: str
    body: bytes
    tree: html.HtmlElement | None
    """Parsed HTML; extractors may modify it. None for anything but HTML."""
    meta: HtmlMeta | None


@dataclass(frozen=True)
class Extracted:
    title: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    language: str | None = None
    text: str | None = None
    """Raw extracted text; `analyze` normalizes it."""
    description: str | None = None
    """The page's own summary, used as the excerpt when present."""


class Extractor(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def media_types(self) -> frozenset[str]:
        """Content types this extractor can read."""
        ...

    def extract(self, page: ParsedPage, settings: Settings) -> Extracted: ...


class ExtractError(ValueError):
    """The body can't be read as its content type claims."""


class TrafilaturaExtractor:
    """Main text via trafilatura, which drops navigation, ads and other boilerplate."""

    media_types = HTML_TYPES

    def __init__(self, name: str, *, include_comments: bool) -> None:
        self.name = name
        self._include_comments = include_comments

    def extract(self, page: ParsedPage, settings: Settings) -> Extracted:
        meta = page.meta or HtmlMeta()
        result = trafilatura.bare_extraction(
            page.tree,
            url=page.url,
            with_metadata=True,
            include_comments=self._include_comments,
            date_extraction_params=DATE_PARAMS,
        )
        # None: too little text to count as a main body (a homepage, an index page).
        document = result if isinstance(result, trafilatura.settings.Document) else None
        text = document.text if document else None
        date = document.date if document else None
        return Extracted(
            title=meta.title or (document.title if document else None),
            author=meta.author or (document.author if document else None),
            published_at=meta.published_at or _date(date),
            language=meta.language,
            text=text,
            description=meta.description or (document.description if document else None),
        )


class MetadataExtractor:
    """Metadata only (PLAN.md §6.3 `video`): no text body to extract."""

    name = "metadata"
    media_types = HTML_TYPES

    def extract(self, page: ParsedPage, settings: Settings) -> Extracted:
        meta = page.meta or HtmlMeta()
        return Extracted(
            title=meta.title,
            author=meta.author,
            published_at=meta.published_at,
            language=meta.language,
            description=meta.description,
        )


class PdfExtractor:
    name = "pdf"
    media_types = PDF_TYPES

    def extract(self, page: ParsedPage, settings: Settings) -> Extracted:
        try:
            reader = PdfReader(io.BytesIO(page.body), strict=False)
            if reader.is_encrypted and not reader.decrypt(""):
                raise ExtractError("encrypted PDF")
            texts = [pdf_page.extract_text() for pdf_page in reader.pages[: settings.pdf_max_pages]]
            info = reader.metadata
        except ExtractError:
            raise
        except Exception as error:  # pypdf raises many kinds of errors on broken files
            raise ExtractError(f"unreadable PDF: {error}") from error
        text = "\n\n".join(texts)
        created = info.creation_date if info else None
        title = (info.title if info else None) or next(
            (line.strip() for line in text.splitlines() if line.strip()), None
        )
        return Extracted(
            title=title,
            author=info.author if info else None,
            published_at=created.replace(tzinfo=created.tzinfo or UTC) if created else None,
            text=text,
        )


ARTICLE = TrafilaturaExtractor("article", include_comments=False)
PAGE = TrafilaturaExtractor("page", include_comments=True)
PDF = PdfExtractor()
VIDEO = MetadataExtractor()

EXTRACTORS: Mapping[DocumentType, Extractor] = {
    DocumentType.ARTICLE: ARTICLE,
    DocumentType.POST: ARTICLE,
    DocumentType.PAGE: PAGE,
    DocumentType.PDF: PDF,
    DocumentType.VIDEO: VIDEO,
}
"""PLAN.md §6.3's V0 set. Threads and papers use the fallback until V1 adds their own."""
FALLBACKS: tuple[Extractor, ...] = (PAGE, PDF)
"""By media type, for types without an extractor that can read the body."""


def extractor_for(
    type_: DocumentType, content_type: str, registry: Mapping[DocumentType, Extractor] = EXTRACTORS
) -> Extractor | None:
    """The extractor for a type and body; None if nothing can read the body (a feed)."""
    chosen = registry.get(type_)
    if chosen is not None and content_type in chosen.media_types:
        return chosen
    return next((fallback for fallback in FALLBACKS if content_type in fallback.media_types), None)


@dataclass(frozen=True)
class OutLink:
    url: str
    """Canonical."""
    anchor_text: str
    internal: bool


@dataclass(frozen=True)
class Analysis:
    """Everything the extract stage needs from one page."""

    type: DocumentType
    classifier: str
    extractor: str
    title: str | None
    author: str | None
    published_at: datetime | None
    language: str | None
    text: str | None
    excerpt: str | None
    word_count: int
    content_hash: str | None
    declared_canonical: str | None
    """The page's own rel=canonical or og:url, when it names another URL on the same site."""
    links: tuple[OutLink, ...]
    """Links the site vouches for (not rel=nofollow/ugc/sponsored), in page order."""
    noindex: bool
    nofollow: bool


def analyze(
    url: str,
    content_type: str,
    charset: str | None,
    body: bytes,
    robots_tag: str | None,
    settings: Settings,
    *,
    product: str,
    classifiers: tuple[Classifier, ...] = DEFAULT_CLASSIFIERS,
) -> Analysis | None:
    """Classify and extract one fetched page; None if it isn't a document at all (a feed or
    another XML file). Raises ValueError if the body can't be read."""
    if content_type not in HTML_TYPES | PDF_TYPES:
        return None
    tree = meta = None
    if content_type in HTML_TYPES:
        tree = parse_document(body, charset)
        meta = extract_meta(tree, url, product)
    classification = classify(PageFacts(url, content_type, meta), classifiers)
    extractor = extractor_for(classification.type, content_type)
    if extractor is None:
        return None
    extracted = extractor.extract(ParsedPage(url, content_type, body, tree, meta), settings)

    text = normalize_text(extracted.text or "", settings.extract_max_text_chars) or None
    summary = _clip(extracted.description, settings.extract_max_text_chars) or text
    directives = parse_robots_directives((robots_tag or "").splitlines(), product)
    if meta is not None:
        directives |= meta.robots
    tracking = TrackingParams(settings.tracking_params)
    return Analysis(
        type=classification.type,
        classifier=classification.classifier,
        extractor=extractor.name,
        title=_clip(extracted.title, settings.extract_field_max_chars),
        author=_clip(extracted.author, settings.extract_field_max_chars),
        published_at=extracted.published_at,
        language=extracted.language,
        text=text,
        excerpt=excerpt(summary, settings.extract_excerpt_chars) if summary else None,
        word_count=word_count(text),
        content_hash=content_hash(text) if text else None,
        declared_canonical=declared_canonical(meta, url, tracking) if meta else None,
        links=_out_links(meta, url, tracking, settings) if meta else (),
        noindex="noindex" in directives,
        nofollow="nofollow" in directives,
    )


def declared_canonical(meta: HtmlMeta, url: str, tracking: TrackingParams) -> str | None:
    """The URL the page says it is (rel=canonical, else og:url), if it's another URL on the
    same site (PLAN.md §6.3). A canonical pointing at the homepage from any other page is
    ignored: it's the classic misconfiguration that would fold a whole site into one page."""
    for candidate in (meta.canonical, meta.og_url):
        canonical = canonicalize(candidate, tracking) if candidate else None
        if canonical is None or not same_site(host_of(canonical), host_of(url)):
            continue
        if canonical == url:
            return None
        if _is_homepage(canonical) and not _is_homepage(url):
            continue
        return canonical
    return None


def _is_homepage(url: str) -> bool:
    parts = urlsplit(url)
    return parts.path in ("", "/") and not parts.query


def _out_links(
    meta: HtmlMeta, url: str, tracking: TrackingParams, settings: Settings
) -> tuple[OutLink, ...]:
    links: dict[str, OutLink] = {}
    host = host_of(url)
    for link in meta.links:
        if len(links) >= settings.extract_max_links_per_page:
            break
        if link.nofollow:
            continue
        target = canonicalize(link.url, tracking)
        if target is None or target == url or target in links:
            continue
        anchor = link.text[: settings.extract_anchor_max_chars]
        links[target] = OutLink(target, anchor, same_site(host_of(target), host))
    return tuple(links.values())


def _date(value: str | None) -> datetime | None:
    """trafilatura's `YYYY-MM-DD` date, as midnight UTC."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return None


def _clip(value: str | None, max_chars: int) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split())[:max_chars]
    return value or None
