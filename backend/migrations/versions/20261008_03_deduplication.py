"""add v3 deduplication handoff and route fingerprints

Revision ID: 20261008_03
Revises: 20261008_02
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261008_03"
down_revision: str | None = "20261008_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_VALUES = (
    "received",
    "processing",
    "ready_for_dedup",
    "filtered",
    "duplicate",
    "queued",
    "published",
    "failed",
    "cancelled",
)


def upgrade() -> None:
    op.execute(
        "ALTER TYPE route_execution_status "
        "ADD VALUE IF NOT EXISTS 'ready_for_queue' AFTER 'ready_for_dedup'"
    )
    op.create_table(
        "route_fingerprints",
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
        sa.Column("scope_key", sa.String(length=255), nullable=False),
        sa.Column("fingerprint_type", sa.String(length=32), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "fingerprint_type IN ('telegram_identity', 'text', 'media', 'combined')",
            name="route_fingerprints_type_valid",
        ),
        sa.UniqueConstraint(
            "route_execution_id",
            "fingerprint_type",
            name="route_fingerprints_execution_type_uq",
        ),
    )
    op.create_index(
        "route_fingerprints_lookup_idx",
        "route_fingerprints",
        ["account_id", "scope_key", "fingerprint_type", "fingerprint", "observed_at"],
    )


def downgrade() -> None:
    op.drop_index("route_fingerprints_lookup_idx", table_name="route_fingerprints")
    op.drop_table("route_fingerprints")

    op.execute(
        "UPDATE route_executions "
        "SET status = 'ready_for_dedup'::route_execution_status "
        "WHERE status = 'ready_for_queue'::route_execution_status"
    )
    op.execute("ALTER TABLE route_executions ALTER COLUMN status DROP DEFAULT")
    op.execute("ALTER TYPE route_execution_status RENAME TO route_execution_status_v3_phase5")
    op.execute(
        "CREATE TYPE route_execution_status AS ENUM ("
        + ", ".join(f"'{value}'" for value in _OLD_VALUES)
        + ")"
    )
    op.execute(
        "ALTER TABLE route_executions ALTER COLUMN status "
        "TYPE route_execution_status USING status::text::route_execution_status"
    )
    op.execute("DROP TYPE route_execution_status_v3_phase5")
    op.execute(
        "ALTER TABLE route_executions ALTER COLUMN status "
        "SET DEFAULT 'received'::route_execution_status"
    )
