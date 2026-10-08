"""add v3 durable publish payload and queue reliability fields

Revision ID: 20261008_04
Revises: 20261008_03
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261008_04"
down_revision: str | None = "20261008_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_JOB_STATUS_VALUES = (
    "queued",
    "processing",
    "publishing",
    "published",
    "retry_wait",
    "failed",
    "cancelled",
    "expired",
)


def upgrade() -> None:
    op.execute(
        "ALTER TYPE job_status "
        "ADD VALUE IF NOT EXISTS 'manual_hold' AFTER 'queued'"
    )

    op.create_table(
        "source_event_snapshots",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_key", sa.String(length=96), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("cursor_message_id", sa.BigInteger(), nullable=False),
        sa.Column("primary_message_id", sa.BigInteger(), nullable=False),
        sa.Column("grouped_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "messages",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "source_id",
            "event_key",
            name="source_event_snapshots_source_event_uq",
        ),
    )
    op.create_index(
        "source_event_snapshots_cursor_idx",
        "source_event_snapshots",
        ["source_id", "cursor_message_id"],
    )

    op.create_table(
        "route_publish_payloads",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "route_execution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("route_executions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "content_type",
            postgresql.ENUM(name="content_type", create_type=False),
            nullable=False,
        ),
        sa.Column("normalized_text", sa.Text(), nullable=True),
        sa.Column("rendered_text", sa.Text(), nullable=True),
        sa.Column(
            "media",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "route_execution_id",
            name="route_publish_payloads_execution_uq",
        ),
    )

    op.alter_column(
        "publish_jobs",
        "content_item_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.add_column(
        "publish_jobs",
        sa.Column("route_execution_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "publish_jobs",
        sa.Column("route_payload_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "publish_jobs",
        sa.Column(
            "max_attempts",
            sa.Integer(),
            nullable=False,
            server_default="5",
        ),
    )
    op.add_column(
        "publish_jobs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "publish_jobs_route_execution_fk",
        "publish_jobs",
        "route_executions",
        ["route_execution_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "publish_jobs_route_payload_fk",
        "publish_jobs",
        "route_publish_payloads",
        ["route_payload_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "publish_jobs_route_execution_uq",
        "publish_jobs",
        ["route_execution_id"],
    )
    op.create_check_constraint(
        "publish_jobs_max_attempts_positive",
        "publish_jobs",
        "max_attempts > 0",
    )
    op.create_index(
        "publish_jobs_v3_due_idx",
        "publish_jobs",
        [
            "account_id",
            "status",
            "scheduled_for",
            "next_attempt_at",
            "lease_expires_at",
        ],
    )


def downgrade() -> None:
    op.execute(
        "UPDATE route_executions "
        "SET status = 'ready_for_queue'::route_execution_status, publish_job_id = NULL "
        "WHERE publish_job_id IN ("
        "SELECT id FROM publish_jobs WHERE route_execution_id IS NOT NULL"
        ")"
    )
    op.execute("DELETE FROM publish_jobs WHERE route_execution_id IS NOT NULL")

    op.drop_index("publish_jobs_v3_due_idx", table_name="publish_jobs")
    op.drop_constraint("publish_jobs_max_attempts_positive", "publish_jobs", type_="check")
    op.drop_constraint("publish_jobs_route_execution_uq", "publish_jobs", type_="unique")
    op.drop_constraint("publish_jobs_route_payload_fk", "publish_jobs", type_="foreignkey")
    op.drop_constraint("publish_jobs_route_execution_fk", "publish_jobs", type_="foreignkey")
    op.drop_column("publish_jobs", "lease_expires_at")
    op.drop_column("publish_jobs", "max_attempts")
    op.drop_column("publish_jobs", "route_payload_id")
    op.drop_column("publish_jobs", "route_execution_id")
    op.alter_column(
        "publish_jobs",
        "content_item_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.drop_table("route_publish_payloads")
    op.drop_index("source_event_snapshots_cursor_idx", table_name="source_event_snapshots")
    op.drop_table("source_event_snapshots")

    op.execute(
        "UPDATE publish_jobs "
        "SET status = 'queued'::job_status "
        "WHERE status = 'manual_hold'::job_status"
    )
    op.execute("ALTER TABLE publish_jobs ALTER COLUMN status DROP DEFAULT")
    op.execute("ALTER TYPE job_status RENAME TO job_status_v3_phase6")
    op.execute(
        "CREATE TYPE job_status AS ENUM ("
        + ", ".join(f"'{value}'" for value in _OLD_JOB_STATUS_VALUES)
        + ")"
    )
    op.execute(
        "ALTER TABLE publish_jobs ALTER COLUMN status "
        "TYPE job_status USING status::text::job_status"
    )
    op.execute("DROP TYPE job_status_v3_phase6")
    op.execute(
        "ALTER TABLE publish_jobs ALTER COLUMN status "
        "SET DEFAULT 'queued'::job_status"
    )
