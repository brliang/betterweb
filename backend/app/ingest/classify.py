"""Document type classification (PLAN.md §6.3): a registry of heuristic classifiers, asked in
order; the first that recognizes the page decides, and anything unrecognized is a `page`.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.enums import DocumentType
from app.ingest.html import HtmlMeta

PDF_TYPES = frozenset({"application/pdf"})
HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})


@dataclass(frozen=True)
class PageFacts:
    """What the classifiers see: where the page came from and what it says about itself."""

    url: str
    content_type: str
    meta: HtmlMeta | None
    """None for anything but HTML."""


class Classifier(Protocol):
    @property
    def name(self) -> str:
        """Recorded in the extract stage's stats, so misclassifications can be traced."""
        ...

    def classify(self, page: PageFacts) -> DocumentType | None:
        """The page's type, or None if this heuristic can't tell."""
        ...


@dataclass(frozen=True)
class Classification:
    type: DocumentType
    classifier: str


class UrlPatternClassifier:
    """Sites and paths whose URLs say what they hold, whatever the page's markup claims."""

    name = "url"
    PATTERNS: tuple[tuple[re.Pattern[str], DocumentType], ...] = tuple(
        (re.compile(pattern, re.IGNORECASE), type_)
        for pattern, type_ in [
            # Preprint servers and paper repositories.
            (r"^https?://(www\.|export\.)?arxiv\.org/(abs|pdf|html)/", DocumentType.PAPER),
            (r"^https?://(www\.)?(biorxiv|medrxiv)\.org/content/", DocumentType.PAPER),
            (r"^https?://(www\.)?openreview\.net/(forum|pdf)\?", DocumentType.PAPER),
            (r"^https?://(www\.)?aclanthology\.org/[^/]+\.\d+/?$", DocumentType.PAPER),
            # Government publications.
            (r"^https?://(www\.)?govinfo\.gov/(content/pkg|app/details)/", DocumentType.PAPER),
            (r"^https?://[^/]+\.gov/(.+/)?(publications?|reports?)/.", DocumentType.PAPER),
            # Discussion threads.
            (r"^https?://news\.ycombinator\.com/item\?", DocumentType.THREAD),
            (r"^https?://lobste\.rs/s/", DocumentType.THREAD),
            (r"^https?://[^/]+/r/[^/]+/comments/", DocumentType.THREAD),
            (r"^https?://[^/]+/t/[^/]+/\d+", DocumentType.THREAD),  # Discourse
            (r"^https?://[^/]+/(.+/)?threads?/.", DocumentType.THREAD),
            (r"^https?://[^/]+/wiki/([a-z_]+_)?talk:", DocumentType.THREAD),  # MediaWiki
            # Video hosts.
            (r"^https?://(www\.|m\.)?youtube\.com/(watch\?|shorts/)", DocumentType.VIDEO),
            (r"^https?://youtu\.be/.", DocumentType.VIDEO),
            (r"^https?://(www\.)?vimeo\.com/\d+", DocumentType.VIDEO),
        ]
    )

    def classify(self, page: PageFacts) -> DocumentType | None:
        return next((type_ for pattern, type_ in self.PATTERNS if pattern.search(page.url)), None)


class ContentTypeClassifier:
    name = "content_type"

    def classify(self, page: PageFacts) -> DocumentType | None:
        return DocumentType.PDF if page.content_type in PDF_TYPES else None


class ScholarlyMetaClassifier:
    """`citation_title` meta tags mark papers for Google Scholar (journals, repositories)."""

    name = "citation_meta"

    def classify(self, page: PageFacts) -> DocumentType | None:
        return DocumentType.PAPER if page.meta is not None and page.meta.scholarly else None


class SchemaOrgClassifier:
    """schema.org types from JSON-LD and microdata. When a page declares several, the most
    specific kind of content wins (the order below)."""

    name = "schema_org"
    TYPES: tuple[tuple[frozenset[str], DocumentType], ...] = (
        (frozenset({"ScholarlyArticle", "MedicalScholarlyArticle"}), DocumentType.PAPER),
        (frozenset({"DiscussionForumPosting", "QAPage", "Question"}), DocumentType.THREAD),
        (frozenset({"VideoObject", "Movie", "Episode", "Clip"}), DocumentType.VIDEO),
        (frozenset({"BlogPosting", "SocialMediaPosting", "LiveBlogPosting"}), DocumentType.POST),
        (
            frozenset(
                {
                    "Article",
                    "NewsArticle",
                    "AnalysisNewsArticle",
                    "OpinionNewsArticle",
                    "ReportageNewsArticle",
                    "ReviewNewsArticle",
                    "BackgroundNewsArticle",
                    "Report",
                    "TechArticle",
                }
            ),
            DocumentType.ARTICLE,
        ),
    )

    def classify(self, page: PageFacts) -> DocumentType | None:
        if page.meta is None:
            return None
        return next((type_ for types, type_ in self.TYPES if types & page.meta.schema_types), None)


class OpenGraphClassifier:
    name = "og_type"

    def classify(self, page: PageFacts) -> DocumentType | None:
        og_type = (page.meta.og_type or "").lower() if page.meta is not None else ""
        if og_type == "article":
            return DocumentType.ARTICLE
        if og_type.startswith("video"):
            return DocumentType.VIDEO
        return None


class DatedPathClassifier:
    """`/2026/08/some-title/`: the permalink shape of most blog engines. A weak signal, so it
    is asked last; news sites with the same shape declare `article` in their markup."""

    name = "dated_path"
    PATTERN = re.compile(
        r"^https?://[^/]+/(.+/)?(19|20)\d\d/[01]\d/([0-3]\d/)?[^/?]*[a-z][^/?]*/?$"
    )

    def classify(self, page: PageFacts) -> DocumentType | None:
        if page.content_type in HTML_TYPES and self.PATTERN.search(page.url):
            return DocumentType.POST
        return None


DEFAULT_CLASSIFIERS: tuple[Classifier, ...] = (
    UrlPatternClassifier(),
    ContentTypeClassifier(),
    ScholarlyMetaClassifier(),
    SchemaOrgClassifier(),
    OpenGraphClassifier(),
    DatedPathClassifier(),
)
FALLBACK = "fallback"


def classify(
    page: PageFacts, classifiers: Sequence[Classifier] = DEFAULT_CLASSIFIERS
) -> Classification:
    for classifier in classifiers:
        if (type_ := classifier.classify(page)) is not None:
            return Classification(type_, classifier.name)
    return Classification(DocumentType.PAGE, FALLBACK)
