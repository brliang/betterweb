"""Database roles that enforce the web/usr split (PLAN.md §4).

- discovery_crawl: crawl stages. Reads and writes `web`; no access to `usr` at all.
- discovery_score: scoring stage. Everything crawl can do, reads `usr`, and writes only the
  scoring outputs (user_ppr, user_profile_vectors).
- discovery_api: the API. Everything crawl can do (adding a pin enqueues URLs), plus reads
  and writes `usr`.

These are NOLOGIN group roles; each deployment creates login users that are members of them.
Default privileges extend the grants to tables later migrations create, as long as the same
role runs the migrations; tests/db/test_roles.py checks the grants on every table.

Roles belong to the whole Postgres cluster, not one database, and other databases on the same
server (e.g. the test database) may use them. So upgrade creates them only if missing, and
downgrade revokes this database's grants but leaves the roles in place.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-22 17:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CRAWL = "discovery_crawl"
SCORE = "discovery_score"
API = "discovery_api"
DML = "SELECT, INSERT, UPDATE, DELETE"
SCORE_WRITES_USR = ("usr.user_ppr", "usr.user_profile_vectors")


def upgrade() -> None:
    for role in (CRAWL, SCORE, API):
        op.execute(
            f"DO $$ BEGIN "
            f"IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') "
            f"THEN CREATE ROLE {role} NOLOGIN; END IF; END $$"
        )
    op.execute(f"GRANT {CRAWL} TO {SCORE}, {API}")

    # web: read/write for all three (score and api inherit it from crawl).
    op.execute(f"GRANT USAGE ON SCHEMA web TO {CRAWL}")
    op.execute(f"GRANT {DML} ON ALL TABLES IN SCHEMA web TO {CRAWL}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA web TO {CRAWL}")
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA web GRANT {DML} ON TABLES TO {CRAWL}")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA web GRANT USAGE, SELECT ON SEQUENCES TO {CRAWL}"
    )

    # usr: nothing for crawl; read-only for score (plus its outputs); read/write for api.
    op.execute(f"GRANT USAGE ON SCHEMA usr TO {SCORE}, {API}")
    op.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA usr TO {SCORE}")
    op.execute(f"GRANT {DML} ON {', '.join(SCORE_WRITES_USR)} TO {SCORE}")
    op.execute(f"GRANT {DML} ON ALL TABLES IN SCHEMA usr TO {API}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA usr TO {API}")
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA usr GRANT SELECT ON TABLES TO {SCORE}")
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA usr GRANT {DML} ON TABLES TO {API}")
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA usr GRANT USAGE, SELECT ON SEQUENCES TO {API}")


def downgrade() -> None:
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA usr REVOKE ALL ON SEQUENCES FROM {API}")
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA usr REVOKE ALL ON TABLES FROM {SCORE}, {API}")
    op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA usr FROM {API}")
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA usr FROM {SCORE}, {API}")
    op.execute(f"REVOKE ALL ON SCHEMA usr FROM {SCORE}, {API}")

    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA web REVOKE ALL ON SEQUENCES FROM {CRAWL}")
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA web REVOKE ALL ON TABLES FROM {CRAWL}")
    op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA web FROM {CRAWL}")
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA web FROM {CRAWL}")
    op.execute(f"REVOKE ALL ON SCHEMA web FROM {CRAWL}")
    # Role memberships and the roles themselves are cluster-wide; see the module docstring.
