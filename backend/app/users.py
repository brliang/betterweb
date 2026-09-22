import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.usr import User


async def delete_user(session: AsyncSession, user_id: uuid.UUID) -> bool:
    """Delete a user and all of their data. Returns False if the user didn't exist.

    One statement: every `usr` table cascades from `usr.users` (PLAN.md §4.2). The shared
    `web` graph holds no user data, so nothing there changes. The caller commits.
    """
    deleted = await session.scalar(sa.delete(User).where(User.id == user_id).returning(User.id))
    return deleted is not None
