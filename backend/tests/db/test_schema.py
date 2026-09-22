"""Structural rules from PLAN.md §4, checked against the migrated database."""

from sqlalchemy import Connection, text


def test_web_never_references_usr(conn: Connection) -> None:
    references = conn.scalars(
        text(
            "SELECT conname FROM pg_constraint "
            "WHERE contype = 'f' "
            "AND conrelid::regclass::text LIKE 'web.%' "
            "AND confrelid::regclass::text LIKE 'usr.%'"
        )
    ).all()
    assert references == []


def test_every_user_table_cascades_from_users(conn: Connection) -> None:
    # So deleting a user from usr.users deletes all of their data in one statement.
    missing = conn.scalars(
        text(
            "SELECT c.relname FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'usr' AND c.relkind = 'r' AND c.relname <> 'users' "
            "AND NOT EXISTS ("
            "  SELECT FROM pg_constraint k "
            "  JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = ANY(k.conkey) "
            "  WHERE k.conrelid = c.oid AND k.contype = 'f' "
            "  AND k.confrelid = 'usr.users'::regclass AND k.confdeltype = 'c' "
            "  AND a.attname = 'user_id'"
            ")"
        )
    ).all()
    assert missing == []
