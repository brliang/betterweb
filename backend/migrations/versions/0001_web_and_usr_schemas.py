"""web and usr schemas: every PLAN.md §4 table.

Enum columns are text with CHECK constraints listing their values as of this revision.
Embedding columns have no fixed dimension yet; M5 sets it once the model is chosen.

Revision ID: 0001
Revises:
Create Date: 2026-09-22 16:47:45.425282
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE SCHEMA web")
    op.execute("CREATE SCHEMA usr")
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("timezone", sa.Text(), server_default="UTC", nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("email = lower(email)", name=op.f("ck_users_email_lowercase")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
        schema="usr",
    )
    op.create_table(
        "crawl_cycles",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "started_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "running",
                "succeeded",
                "failed",
                name="cyclestatus",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="running",
            nullable=False,
        ),
        sa.Column("page_budget", sa.Integer(), nullable=False),
        sa.Column("pages_fetched", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "stats",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crawl_cycles")),
        schema="web",
    )
    op.create_table(
        "domains",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("host", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "active",
                "blocked",
                "unreachable",
                name="domainstatus",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="active",
            nullable=False,
        ),
        sa.Column("robots_txt", sa.Text(), nullable=True),
        sa.Column("robots_fetched_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("crawl_delay_s", sa.Float(), nullable=True),
        sa.Column(
            "feed_urls",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "sitemap_urls",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "first_seen_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_crawled_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("verified_owner_id", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_domains")),
        sa.UniqueConstraint("host", name=op.f("uq_domains_host")),
        schema="web",
    )
    op.create_table(
        "topics",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column("tier", sa.SmallInteger(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("embedding", VECTOR(), nullable=True),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["web.topics.id"], name=op.f("fk_topics_parent_id_topics")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_topics")),
        sa.UniqueConstraint("external_id", name=op.f("uq_topics_external_id")),
        schema="web",
    )
    op.create_index(
        op.f("ix_topics_parent_id"), "topics", ["parent_id"], unique=False, schema="web"
    )
    op.create_table(
        "pins",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("domain_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "source",
            sa.Enum(
                "survey", "suggested", name="pinsource", native_enum=False, create_constraint=True
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["domain_id"],
            ["web.domains.id"],
            name=op.f("fk_pins_domain_id_domains"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["usr.users.id"], name=op.f("fk_pins_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", "domain_id", name=op.f("pk_pins")),
        schema="usr",
    )
    op.create_index(op.f("ix_pins_domain_id"), "pins", ["domain_id"], unique=False, schema="usr")
    op.create_table(
        "survey_responses",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("survey_version", sa.Integer(), nullable=False),
        sa.Column("answers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["usr.users.id"],
            name=op.f("fk_survey_responses_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_survey_responses")),
        schema="usr",
    )
    op.create_index(
        op.f("ix_survey_responses_user_id"),
        "survey_responses",
        ["user_id"],
        unique=False,
        schema="usr",
    )
    op.create_table(
        "user_interests",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.BigInteger(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column(
            "source",
            sa.Enum(
                "survey",
                "learned",
                name="interestsource",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["topic_id"],
            ["web.topics.id"],
            name=op.f("fk_user_interests_topic_id_topics"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["usr.users.id"],
            name=op.f("fk_user_interests_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "topic_id", name=op.f("pk_user_interests")),
        schema="usr",
    )
    op.create_table(
        "user_profile_vectors",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "interest",
                "liked",
                "hidden",
                name="profilevectorkind",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("vector", VECTOR(), nullable=False),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["usr.users.id"],
            name=op.f("fk_user_profile_vectors_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "kind", name=op.f("pk_user_profile_vectors")),
        schema="usr",
    )
    op.create_table(
        "user_settings",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("weights", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("exploration_pct", sa.Float(), nullable=False),
        sa.Column("exploration_split", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_types", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column(
            "summaries_opt_in", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "exploration_pct >= 0 AND exploration_pct <= 1",
            name=op.f("ck_user_settings_exploration_pct_range"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["usr.users.id"],
            name=op.f("fk_user_settings_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_user_settings")),
        schema="usr",
    )
    op.create_table(
        "domain_metadata_overrides",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("domain_id", sa.BigInteger(), nullable=False),
        sa.Column("url_pattern", sa.Text(), nullable=False),
        sa.Column("field", sa.Text(), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["domain_id"],
            ["web.domains.id"],
            name=op.f("fk_domain_metadata_overrides_domain_id_domains"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_domain_metadata_overrides")),
        schema="web",
    )
    op.create_index(
        op.f("ix_domain_metadata_overrides_domain_id"),
        "domain_metadata_overrides",
        ["domain_id"],
        unique=False,
        schema="web",
    )
    op.create_table(
        "domain_scores",
        sa.Column("domain_id", sa.BigInteger(), nullable=False),
        sa.Column("cycle_id", sa.BigInteger(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["cycle_id"],
            ["web.crawl_cycles.id"],
            name=op.f("fk_domain_scores_cycle_id_crawl_cycles"),
        ),
        sa.ForeignKeyConstraint(
            ["domain_id"],
            ["web.domains.id"],
            name=op.f("fk_domain_scores_domain_id_domains"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("domain_id", name=op.f("pk_domain_scores")),
        schema="web",
    )
    op.create_index(
        op.f("ix_domain_scores_cycle_id"), "domain_scores", ["cycle_id"], unique=False, schema="web"
    )
    op.create_table(
        "urls",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("domain_id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=True),
        sa.Column("http_status", sa.SmallInteger(), nullable=True),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("last_modified", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column(
            "first_seen_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_fetched_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("fetch_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("change_count", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(
            ["domain_id"], ["web.domains.id"], name=op.f("fk_urls_domain_id_domains")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_urls")),
        sa.UniqueConstraint("url", name=op.f("uq_urls_url")),
        schema="web",
    )
    op.create_index(
        op.f("ix_urls_document_id"), "urls", ["document_id"], unique=False, schema="web"
    )
    op.create_index(op.f("ix_urls_domain_id"), "urls", ["domain_id"], unique=False, schema="web")
    op.create_table(
        "documents",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("canonical_url_id", sa.BigInteger(), nullable=False),
        sa.Column("domain_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "article",
                "post",
                "thread",
                "paper",
                "pdf",
                "video",
                "page",
                name="documenttype",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("author", sa.Text(), nullable=True),
        sa.Column("published_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("language", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("word_count", sa.Integer(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column("simhash", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["canonical_url_id"], ["web.urls.id"], name=op.f("fk_documents_canonical_url_id_urls")
        ),
        sa.ForeignKeyConstraint(
            ["domain_id"], ["web.domains.id"], name=op.f("fk_documents_domain_id_domains")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        sa.UniqueConstraint("canonical_url_id", name=op.f("uq_documents_canonical_url_id")),
        schema="web",
    )
    op.create_index(
        op.f("ix_documents_content_hash"), "documents", ["content_hash"], unique=False, schema="web"
    )
    op.create_index(
        op.f("ix_documents_domain_id"), "documents", ["domain_id"], unique=False, schema="web"
    )
    # urls and documents reference each other, so this FK is added after both tables exist.
    op.create_foreign_key(
        op.f("fk_urls_document_id_documents"),
        "urls",
        "documents",
        ["document_id"],
        ["id"],
        source_schema="web",
        referent_schema="web",
        ondelete="SET NULL",
    )
    op.create_table(
        "frontier",
        sa.Column("url_id", sa.BigInteger(), nullable=False),
        sa.Column("internal_depth", sa.SmallInteger(), nullable=False),
        sa.Column("external_hops", sa.SmallInteger(), nullable=False),
        sa.Column("priority", sa.Float(), server_default="0", nullable=False),
        sa.Column(
            "reason",
            sa.Enum(
                "new", "recrawl", name="frontierreason", native_enum=False, create_constraint=True
            ),
            nullable=False,
        ),
        sa.Column(
            "next_fetch_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "enqueued_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "external_hops >= 0", name=op.f("ck_frontier_external_hops_non_negative")
        ),
        sa.CheckConstraint(
            "internal_depth >= 0", name=op.f("ck_frontier_internal_depth_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["url_id"], ["web.urls.id"], name=op.f("fk_frontier_url_id_urls"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("url_id", name=op.f("pk_frontier")),
        schema="web",
    )
    op.create_index(
        op.f("ix_frontier_priority"), "frontier", ["priority"], unique=False, schema="web"
    )
    op.create_table(
        "feedback",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "like",
                "hide",
                "block_domain",
                name="feedbackkind",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("reason_text", sa.Text(), nullable=True),
        sa.Column(
            "visibility",
            sa.Enum("private", name="visibility", native_enum=False, create_constraint=True),
            server_default="private",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["web.documents.id"],
            name=op.f("fk_feedback_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["usr.users.id"],
            name=op.f("fk_feedback_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_feedback")),
        schema="usr",
    )
    op.create_index(
        op.f("ix_feedback_document_id"), "feedback", ["document_id"], unique=False, schema="usr"
    )
    op.create_index(
        op.f("ix_feedback_user_id_document_id"),
        "feedback",
        ["user_id", "document_id"],
        unique=False,
        schema="usr",
    )
    op.create_table(
        "recommendations",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "surface",
            sa.Enum("feed", "search", name="surface", native_enum=False, create_constraint=True),
            nullable=False,
        ),
        sa.Column("query", sa.Text(), nullable=True),
        sa.Column(
            "slice",
            sa.Enum(
                "main",
                "adjacent_semantic",
                "adjacent_graph",
                name="slice",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("components", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("cycle_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["cycle_id"],
            ["web.crawl_cycles.id"],
            name=op.f("fk_recommendations_cycle_id_crawl_cycles"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["web.documents.id"],
            name=op.f("fk_recommendations_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["usr.users.id"],
            name=op.f("fk_recommendations_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recommendations")),
        schema="usr",
    )
    op.create_index(
        op.f("ix_recommendations_cycle_id"),
        "recommendations",
        ["cycle_id"],
        unique=False,
        schema="usr",
    )
    op.create_index(
        op.f("ix_recommendations_document_id"),
        "recommendations",
        ["document_id"],
        unique=False,
        schema="usr",
    )
    op.create_index(
        op.f("ix_recommendations_user_id_created_at"),
        "recommendations",
        ["user_id", "created_at"],
        unique=False,
        schema="usr",
    )
    op.create_table(
        "summaries",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["web.documents.id"],
            name=op.f("fk_summaries_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["usr.users.id"],
            name=op.f("fk_summaries_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "document_id", "model", name=op.f("pk_summaries")),
        schema="usr",
    )
    op.create_index(
        op.f("ix_summaries_document_id"), "summaries", ["document_id"], unique=False, schema="usr"
    )
    op.create_table(
        "user_ppr",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("cycle_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["cycle_id"], ["web.crawl_cycles.id"], name=op.f("fk_user_ppr_cycle_id_crawl_cycles")
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["web.documents.id"],
            name=op.f("fk_user_ppr_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["usr.users.id"],
            name=op.f("fk_user_ppr_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "document_id", name=op.f("pk_user_ppr")),
        schema="usr",
    )
    op.create_index(
        op.f("ix_user_ppr_cycle_id"), "user_ppr", ["cycle_id"], unique=False, schema="usr"
    )
    op.create_index(
        op.f("ix_user_ppr_document_id"), "user_ppr", ["document_id"], unique=False, schema="usr"
    )
    op.create_index(
        op.f("ix_user_ppr_user_id_score"),
        "user_ppr",
        ["user_id", "score"],
        unique=False,
        schema="usr",
    )
    op.create_table(
        "dedup_decisions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("url_id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["web.documents.id"],
            name=op.f("fk_dedup_decisions_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["url_id"],
            ["web.urls.id"],
            name=op.f("fk_dedup_decisions_url_id_urls"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dedup_decisions")),
        schema="web",
    )
    op.create_index(
        op.f("ix_dedup_decisions_document_id"),
        "dedup_decisions",
        ["document_id"],
        unique=False,
        schema="web",
    )
    op.create_index(
        op.f("ix_dedup_decisions_url_id"), "dedup_decisions", ["url_id"], unique=False, schema="web"
    )
    op.create_table(
        "document_embeddings",
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("vector", VECTOR(), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["web.documents.id"],
            name=op.f("fk_document_embeddings_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("document_id", "model", name=op.f("pk_document_embeddings")),
        schema="web",
    )
    op.create_table(
        "document_topics",
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("topic_id", sa.BigInteger(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["web.documents.id"],
            name=op.f("fk_document_topics_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["topic_id"],
            ["web.topics.id"],
            name=op.f("fk_document_topics_topic_id_topics"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("document_id", "topic_id", name=op.f("pk_document_topics")),
        schema="web",
    )
    op.create_index(
        op.f("ix_document_topics_topic_id"),
        "document_topics",
        ["topic_id"],
        unique=False,
        schema="web",
    )
    op.create_table(
        "global_scores",
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("cycle_id", sa.BigInteger(), nullable=False),
        sa.Column("pagerank", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["cycle_id"],
            ["web.crawl_cycles.id"],
            name=op.f("fk_global_scores_cycle_id_crawl_cycles"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["web.documents.id"],
            name=op.f("fk_global_scores_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("document_id", name=op.f("pk_global_scores")),
        schema="web",
    )
    op.create_index(
        op.f("ix_global_scores_cycle_id"), "global_scores", ["cycle_id"], unique=False, schema="web"
    )
    op.create_table(
        "links",
        sa.Column("src_document_id", sa.BigInteger(), nullable=False),
        sa.Column("dst_url_id", sa.BigInteger(), nullable=False),
        sa.Column("anchor_text", sa.Text(), nullable=True),
        sa.Column("is_internal", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["dst_url_id"],
            ["web.urls.id"],
            name=op.f("fk_links_dst_url_id_urls"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["src_document_id"],
            ["web.documents.id"],
            name=op.f("fk_links_src_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("src_document_id", "dst_url_id", name=op.f("pk_links")),
        schema="web",
    )
    op.create_index(
        op.f("ix_links_dst_url_id"), "links", ["dst_url_id"], unique=False, schema="web"
    )
    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("recommendation_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "kind",
            sa.Enum(
                "impression",
                "click",
                "like",
                "hide",
                "why_open",
                "summary_view",
                name="eventkind",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "surface",
            sa.Enum("feed", "search", name="surface", native_enum=False, create_constraint=True),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["web.documents.id"],
            name=op.f("fk_events_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["recommendation_id"],
            ["usr.recommendations.id"],
            name=op.f("fk_events_recommendation_id_recommendations"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["usr.users.id"], name=op.f("fk_events_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_events")),
        schema="usr",
    )
    op.create_index(
        op.f("ix_events_document_id"), "events", ["document_id"], unique=False, schema="usr"
    )
    op.create_index(
        op.f("ix_events_recommendation_id"),
        "events",
        ["recommendation_id"],
        unique=False,
        schema="usr",
    )
    op.create_index(
        op.f("ix_events_user_id_created_at"),
        "events",
        ["user_id", "created_at"],
        unique=False,
        schema="usr",
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_events_user_id_created_at"), table_name="events", schema="usr")
    op.drop_index(op.f("ix_events_recommendation_id"), table_name="events", schema="usr")
    op.drop_index(op.f("ix_events_document_id"), table_name="events", schema="usr")
    op.drop_table("events", schema="usr")
    op.drop_index(op.f("ix_links_dst_url_id"), table_name="links", schema="web")
    op.drop_table("links", schema="web")
    op.drop_index(op.f("ix_global_scores_cycle_id"), table_name="global_scores", schema="web")
    op.drop_table("global_scores", schema="web")
    op.drop_index(op.f("ix_document_topics_topic_id"), table_name="document_topics", schema="web")
    op.drop_table("document_topics", schema="web")
    op.drop_table("document_embeddings", schema="web")
    op.drop_index(op.f("ix_dedup_decisions_url_id"), table_name="dedup_decisions", schema="web")
    op.drop_index(
        op.f("ix_dedup_decisions_document_id"), table_name="dedup_decisions", schema="web"
    )
    op.drop_table("dedup_decisions", schema="web")
    op.drop_index(op.f("ix_user_ppr_user_id_score"), table_name="user_ppr", schema="usr")
    op.drop_index(op.f("ix_user_ppr_document_id"), table_name="user_ppr", schema="usr")
    op.drop_index(op.f("ix_user_ppr_cycle_id"), table_name="user_ppr", schema="usr")
    op.drop_table("user_ppr", schema="usr")
    op.drop_index(op.f("ix_summaries_document_id"), table_name="summaries", schema="usr")
    op.drop_table("summaries", schema="usr")
    op.drop_index(
        op.f("ix_recommendations_user_id_created_at"), table_name="recommendations", schema="usr"
    )
    op.drop_index(
        op.f("ix_recommendations_document_id"), table_name="recommendations", schema="usr"
    )
    op.drop_index(op.f("ix_recommendations_cycle_id"), table_name="recommendations", schema="usr")
    op.drop_table("recommendations", schema="usr")
    op.drop_index(op.f("ix_feedback_user_id_document_id"), table_name="feedback", schema="usr")
    op.drop_index(op.f("ix_feedback_document_id"), table_name="feedback", schema="usr")
    op.drop_table("feedback", schema="usr")
    op.drop_index(op.f("ix_frontier_priority"), table_name="frontier", schema="web")
    op.drop_table("frontier", schema="web")
    op.drop_index(op.f("ix_documents_domain_id"), table_name="documents", schema="web")
    op.drop_index(op.f("ix_documents_content_hash"), table_name="documents", schema="web")
    op.drop_constraint(
        op.f("fk_urls_document_id_documents"), "urls", schema="web", type_="foreignkey"
    )
    op.drop_table("documents", schema="web")
    op.drop_index(op.f("ix_urls_domain_id"), table_name="urls", schema="web")
    op.drop_index(op.f("ix_urls_document_id"), table_name="urls", schema="web")
    op.drop_table("urls", schema="web")
    op.drop_index(op.f("ix_domain_scores_cycle_id"), table_name="domain_scores", schema="web")
    op.drop_table("domain_scores", schema="web")
    op.drop_index(
        op.f("ix_domain_metadata_overrides_domain_id"),
        table_name="domain_metadata_overrides",
        schema="web",
    )
    op.drop_table("domain_metadata_overrides", schema="web")
    op.drop_table("user_settings", schema="usr")
    op.drop_table("user_profile_vectors", schema="usr")
    op.drop_table("user_interests", schema="usr")
    op.drop_index(op.f("ix_survey_responses_user_id"), table_name="survey_responses", schema="usr")
    op.drop_table("survey_responses", schema="usr")
    op.drop_index(op.f("ix_pins_domain_id"), table_name="pins", schema="usr")
    op.drop_table("pins", schema="usr")
    op.drop_index(op.f("ix_topics_parent_id"), table_name="topics", schema="web")
    op.drop_table("topics", schema="web")
    op.drop_table("domains", schema="web")
    op.drop_table("crawl_cycles", schema="web")
    op.drop_table("users", schema="usr")
    op.execute("DROP SCHEMA usr")
    op.execute("DROP SCHEMA web")
    op.execute("DROP EXTENSION IF EXISTS vector")
