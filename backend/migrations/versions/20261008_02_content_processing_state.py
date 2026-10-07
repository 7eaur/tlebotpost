"""add v3 content-processing handoff state

Revision ID: 20261008_02
Revises: 20261007_01
Create Date: 2026-10-08
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261008_02"
down_revision: str | None = "20261007_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_VALUES = (
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
        "ALTER TYPE route_execution_status "
        "ADD VALUE IF NOT EXISTS 'ready_for_dedup' AFTER 'processing'"
    )


def downgrade() -> None:
    # A one-step downgrade maps unfinished Phase-4 handoffs back to processing.
    op.execute(
        "UPDATE route_executions "
        "SET status = 'processing'::route_execution_status "
        "WHERE status = 'ready_for_dedup'::route_execution_status"
    )
    op.execute("ALTER TABLE route_executions ALTER COLUMN status DROP DEFAULT")
    op.execute("ALTER TYPE route_execution_status RENAME TO route_execution_status_v3_phase4")
    op.execute(
        "CREATE TYPE route_execution_status AS ENUM ("
        + ", ".join(f"'{value}'" for value in _OLD_VALUES)
        + ")"
    )
    op.execute(
        "ALTER TABLE route_executions ALTER COLUMN status "
        "TYPE route_execution_status USING status::text::route_execution_status"
    )
    op.execute("DROP TYPE route_execution_status_v3_phase4")
    op.execute(
        "ALTER TABLE route_executions ALTER COLUMN status "
        "SET DEFAULT 'received'::route_execution_status"
    )
