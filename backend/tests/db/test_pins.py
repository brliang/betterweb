"""A pin covers its domain and the domain's `www.` twin (app.pins), nothing more."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.pins import pinned_domains
from tests.db.builders import add_user, domain_of, pin

pytestmark = pytest.mark.anyio


async def test_a_pin_covers_its_www_twin(session: AsyncSession) -> None:
    for host in ("www.example.org", "wwwexample.org", "example.org.evil", "blog.example.org"):
        await domain_of(session, host)
    alice = await add_user(session, "alice@example.com")
    bob = await add_user(session, "bob@example.com")
    await pin(session, alice, "example.org")
    await pin(session, bob, "www.other.example")
    await domain_of(session, "other.example")

    covered = {(user, host) for user, _, host in await session.execute(pinned_domains())}
    assert covered == {
        (alice, "example.org"),
        (alice, "www.example.org"),
        (bob, "www.other.example"),
        (bob, "other.example"),
    }
    only_alice = {host for _, _, host in await session.execute(pinned_domains(alice))}
    assert only_alice == {"example.org", "www.example.org"}
