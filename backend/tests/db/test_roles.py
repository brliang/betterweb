"""The DB roles enforce the web/usr split (PLAN.md §4; migration 0002)."""

import psycopg.errors
import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import ProgrammingError

CRAWL = "discovery_crawl"
SCORE = "discovery_score"
API = "discovery_api"

ALL_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
READ = frozenset({"SELECT"})
READ_WRITE = frozenset({"SELECT", "INSERT", "UPDATE", "DELETE"})
NONE: frozenset[str] = frozenset()
SCORE_WRITES_USR = {"user_ppr", "user_profile_vectors"}


def expected_privileges(role: str, schema: str, table: str) -> frozenset[str]:
    if schema == "web":
        return READ_WRITE
    if role == CRAWL:
        return NONE
    if role == SCORE:
        return READ_WRITE if table in SCORE_WRITES_USR else READ
    return READ_WRITE


def tables(conn: Connection, *schemas: str) -> list[tuple[str, str]]:
    rows = conn.execute(
        text(
            "SELECT schemaname, tablename FROM pg_tables "
            "WHERE schemaname = ANY(:schemas) ORDER BY 1, 2"
        ),
        {"schemas": list(schemas)},
    )
    return [(schema, table) for schema, table in rows]


@pytest.mark.parametrize("role", [CRAWL, SCORE, API])
def test_every_table_grants_exactly_the_policy(conn: Connection, role: str) -> None:
    mismatches = {}
    for schema, table in tables(conn, "web", "usr"):
        actual = frozenset(
            privilege
            for privilege in ALL_PRIVILEGES
            if conn.scalar(
                text("SELECT has_table_privilege(:role, :table, :privilege)"),
                {"role": role, "table": f"{schema}.{table}", "privilege": privilege},
            )
        )
        expected = expected_privileges(role, schema, table)
        if actual != expected:
            mismatches[f"{schema}.{table}"] = {"actual": actual, "expected": expected}
    assert mismatches == {}


def test_crawl_role_cannot_read_the_user_store(conn: Connection) -> None:
    usr_tables = tables(conn, "usr")
    assert usr_tables, "expected usr tables to exist"

    conn.execute(text(f"SET LOCAL ROLE {CRAWL}"))
    for schema, table in usr_tables:
        with pytest.raises(ProgrammingError) as error, conn.begin_nested():
            conn.execute(text(f"SELECT * FROM {schema}.{table} LIMIT 1"))  # noqa: S608 (catalog names)
        assert isinstance(error.value.orig, psycopg.errors.InsufficientPrivilege)


def test_crawl_role_can_write_the_web_graph(conn: Connection) -> None:
    conn.execute(text(f"SET LOCAL ROLE {CRAWL}"))
    domain_id = conn.scalar(
        text("INSERT INTO web.domains (host) VALUES ('example.com') RETURNING id")
    )
    url_id = conn.scalar(
        text(
            "INSERT INTO web.urls (url, domain_id) VALUES ('https://example.com/', :d) RETURNING id"
        ),
        {"d": domain_id},
    )
    conn.execute(
        text(
            "INSERT INTO web.frontier (url_id, internal_depth, external_hops, reason) "
            "VALUES (:u, 0, 0, 'new')"
        ),
        {"u": url_id},
    )
    assert conn.scalar(text("SELECT count(*) FROM web.frontier")) == 1
