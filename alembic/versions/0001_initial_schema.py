"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-06-01 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── Enum types ────────────────────────────────────────────────────────────
    op.execute("CREATE TYPE job_source AS ENUM ('gupy', 'linkedin', 'indeed', 'remoteok')")
    op.execute(
        "CREATE TYPE contract_type AS ENUM ('clt', 'pj', 'freelance', 'internship', 'unknown')"
    )
    op.execute("CREATE TYPE seniority AS ENUM ('junior', 'mid', 'senior', 'lead', 'unknown')")

    # ── jobs ──────────────────────────────────────────────────────────────────
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column(
            "source",
            postgresql.ENUM(
                "gupy",
                "linkedin",
                "indeed",
                "remoteok",
                name="job_source",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("company", sa.String(255), nullable=False),
        sa.Column("city", sa.String(128), nullable=True),
        sa.Column("state", sa.String(64), nullable=True),
        sa.Column("remote", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "contract_type",
            postgresql.ENUM(
                "clt",
                "pj",
                "freelance",
                "internship",
                "unknown",
                name="contract_type",
                create_type=False,
            ),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column(
            "seniority",
            postgresql.ENUM(
                "junior",
                "mid",
                "senior",
                "lead",
                "unknown",
                name="seniority",
                create_type=False,
            ),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("salary_min", sa.Integer(), nullable=True),
        sa.Column("salary_max", sa.Integer(), nullable=True),
        sa.Column(
            "technologies",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default=sa.text("ARRAY[]::varchar[]"),
        ),
        sa.Column("raw_description", sa.Text(), nullable=True),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url", name="uq_jobs_url"),
    )
    op.create_index("ix_jobs_external_id", "jobs", ["external_id"])
    op.create_index("ix_jobs_source", "jobs", ["source"])
    op.create_index("ix_jobs_content_hash", "jobs", ["content_hash"])
    # Composite index for upsert lookups by (source, external_id)
    op.create_index("ix_jobs_source_external_id", "jobs", ["source", "external_id"])

    # ── daily_snapshots ───────────────────────────────────────────────────────
    op.create_table(
        "daily_snapshots",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("total_jobs", sa.Integer(), nullable=False),
        sa.Column("new_jobs", sa.Integer(), nullable=False),
        sa.Column("expired_jobs", sa.Integer(), nullable=False),
        sa.Column(
            "top_technologies",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "top_cities",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("avg_salary_junior", sa.Integer(), nullable=True),
        sa.Column("avg_salary_mid", sa.Integer(), nullable=True),
        sa.Column("avg_salary_senior", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("date", name="uq_daily_snapshots_date"),
    )
    op.create_index("ix_daily_snapshots_date", "daily_snapshots", ["date"])


def downgrade() -> None:
    op.drop_index("ix_daily_snapshots_date", table_name="daily_snapshots")
    op.drop_table("daily_snapshots")

    op.drop_index("ix_jobs_source_external_id", table_name="jobs")
    op.drop_index("ix_jobs_content_hash", table_name="jobs")
    op.drop_index("ix_jobs_source", table_name="jobs")
    op.drop_index("ix_jobs_external_id", table_name="jobs")
    op.drop_table("jobs")

    op.execute("DROP TYPE IF EXISTS seniority")
    op.execute("DROP TYPE IF EXISTS contract_type")
    op.execute("DROP TYPE IF EXISTS job_source")
