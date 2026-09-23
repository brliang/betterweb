"""Which domains a user's pins cover (PLAN.md §6.5, §6.6).

A pin covers its domain and the domain's `www.` twin: "example.com" usually redirects to
"www.example.com" (or back), and the crawler treats the two as one site (app.crawl.urls
`same_site`), so the documents may live under either host.
"""

import uuid

import sqlalchemy as sa
from sqlalchemy.orm import InstrumentedAttribute, aliased

from app.db.usr import Pin
from app.db.web import Domain


def _bare(host: InstrumentedAttribute[str]) -> sa.ColumnElement[str]:
    return sa.func.regexp_replace(host, r"^www\.", "")


def pinned_domains(user_id: uuid.UUID | None = None) -> sa.Select[tuple[uuid.UUID, int, str]]:
    """(user ID, domain ID, host) for every domain a pin covers; all users unless `user_id`."""
    pinned = aliased(Domain)
    statement = (
        sa.select(Pin.user_id, Domain.id, Domain.host)
        .distinct()
        .join(pinned, pinned.id == Pin.domain_id)
        .join(
            Domain,
            sa.or_(Domain.id == pinned.id, _bare(Domain.host) == _bare(pinned.host)),
        )
    )
    if user_id is not None:
        statement = statement.where(Pin.user_id == user_id)
    return statement
