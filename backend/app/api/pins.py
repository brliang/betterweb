"""Pinned sites (PLAN.md §7 step 2, §8). Pinning enqueues the site for the next crawl cycle."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel

from app.api.deps import CurrentUser, DbSession, SettingsDep
from app.db.usr import Pin
from app.db.web import Domain
from app.enums import PinSource
from app.preferences import PreferenceError, parse_site, pin_site, suggested_source_for

router = APIRouter(tags=["pins"])


class PinIn(BaseModel):
    site: str
    """A domain ("example.com") or any URL on the site."""


class PinOut(BaseModel):
    domain_id: int
    host: str
    source: PinSource
    name: str | None
    """The suggested source's name, if it is one."""
    created_at: datetime


async def _pins(
    session: DbSession, user_id: uuid.UUID, domain_id: int | None = None
) -> list[PinOut]:
    statement = (
        sa.select(Pin.domain_id, Domain.host, Pin.source, Pin.created_at)
        .join(Domain, Domain.id == Pin.domain_id)
        .where(Pin.user_id == user_id)
        .order_by(Domain.host)
    )
    if domain_id is not None:
        statement = statement.where(Pin.domain_id == domain_id)
    return [
        PinOut(
            domain_id=pinned,
            host=host,
            source=source,
            name=suggested.name if (suggested := suggested_source_for(host)) else None,
            created_at=created_at,
        )
        for pinned, host, source, created_at in await session.execute(statement)
    ]


@router.get("/pins")
async def list_pins(user: CurrentUser, session: DbSession) -> list[PinOut]:
    return await _pins(session, user.id)


@router.post("/pins", status_code=status.HTTP_201_CREATED)
async def add_pin(
    pin: PinIn, user: CurrentUser, session: DbSession, settings: SettingsDep
) -> PinOut:
    """Pin a site (idempotent); its homepage and feeds join the crawl frontier."""
    try:
        site = parse_site(pin.site, settings)
    except PreferenceError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    domain_id = await pin_site(session, user.id, site, settings)
    await session.commit()
    [pinned] = await _pins(session, user.id, domain_id)
    return pinned


@router.delete("/pins/{domain_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_pin(domain_id: int, user: CurrentUser, session: DbSession) -> Response:
    """Unpin a site. What was crawled stays in the shared graph; the user's scores follow at
    the next cycle."""
    deleted = await session.scalar(
        sa.delete(Pin)
        .where(Pin.user_id == user.id, Pin.domain_id == domain_id)
        .returning(Pin.domain_id)
    )
    if deleted is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not pinned")
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
