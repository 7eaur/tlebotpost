"""add v3 route execution durability

Revision ID: 20261007_01
Revises:
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261007_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROUTE_EXECUTION_VALUES = (
    "received",
    "processing",
    "filtered",
    "duplicate",
    "queued",
    "published",
    "failed",
    "cancelled",
)


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_type WHERE typname = 'route_execution_status'
            ) THEN
                CREATE TYPE route_execution_status AS ENUM (
                    'received',
                    'processing',
                    'filtered',
                    'duplicate',
                    'queued',
                    'published',
                    'failed',
                    'cancelled'
                );
            END IF;
        END
        $$;
        """
    )

    op.add_column(
        "source_checkpoints",
        sa.Column(
            "last_committed_message_id",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.execute(
        """
        UPDATE source_checkpoints
        SET last_committed_message_id = last_seen_message_id
        WHERE last_seen_message_id > last_committed_message_id
        """
    )

    execution_status = postgresql.ENUM(
        *_ROUTE_EXECUTION_VALUES,
        name="route_execution_status",
        create_type=False,
    )
    op.create_table(
        "route_executions",
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
        sa.Column(
            "route_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("source_routes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "destination_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("destinations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_key", sa.String(length=96), nullable=False),
        sa.Column("cursor_message_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("telegram_grouped_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "status",
            execution_status,
            nullable=False,
            server_default=sa.text("'received'::route_execution_status"),
        ),
        sa.Column("reason_code", sa.String(length=120), nullable=True),
        sa.Column(
            "content_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "publish_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("publish_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("cursor_message_id > 0", name="route_executions_cursor_positive"),
        sa.UniqueConstraint("route_id", "event_key", name="route_executions_route_event_uq"),
    )
    op.create_index(
        "route_executions_source_cursor_idx",
        "route_executions",
        ["source_id", "cursor_message_id"],
    )
    op.create_index(
        "route_executions_account_status_idx",
        "route_executions",
        ["account_id", "status"],
    )
    op.execute(
        """
        CREATE TRIGGER route_executions_set_updated_at
        BEFORE UPDATE ON route_executions
        FOR EACH ROW EXECUTE FUNCTION set_updated_at()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS route_executions_set_updated_at ON route_executions")
    op.drop_index("route_executions_account_status_idx", table_name="route_executions")
    op.drop_index("route_executions_source_cursor_idx", table_name="route_executions")
    op.drop_table("route_executions")
    op.drop_column("source_checkpoints", "last_committed_message_id")
    op.execute("DROP TYPE IF EXISTS route_execution_status")
