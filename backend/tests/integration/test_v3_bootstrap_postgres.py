from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import delete, select

from app.db import Account, Database, Project, TelegramAccount
from app.v3.bootstrap import bootstrap_v3_identity

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_v3_identity_bootstrap_is_idempotent_and_minimal():
    if not (os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")):
        pytest.skip("PostgreSQL test URL is required")

    database = Database.from_env()
    suffix = uuid.uuid4().hex[:10]
    account_slug = f"bootstrap-{suffix}"
    account_id = None
    configured_account_id = uuid.uuid4()
    configured_telegram_account_id = uuid.uuid4()

    try:
        first = await bootstrap_v3_identity(
            database.session_factory,
            account_name="Bootstrap Test",
            account_slug=account_slug,
            project_name="Relay",
            project_slug="relay",
            telegram_label="primary",
            session_key=f"session-{suffix}",
            account_id=configured_account_id,
            telegram_account_id=configured_telegram_account_id,
        )
        account_id = first.account_id

        second = await bootstrap_v3_identity(
            database.session_factory,
            account_name="Bootstrap Test Updated",
            account_slug=account_slug,
            project_name="Relay Updated",
            project_slug="relay",
            telegram_label="primary",
            session_key=f"session-{suffix}",
            account_id=configured_account_id,
            telegram_account_id=configured_telegram_account_id,
        )

        assert first.account_id == configured_account_id
        assert first.telegram_account_id == configured_telegram_account_id
        assert second.account_id == first.account_id
        assert second.project_id == first.project_id
        assert second.telegram_account_id == first.telegram_account_id
        assert first.created_account is True
        assert first.created_project is True
        assert first.created_telegram_account is True
        assert second.created_account is False
        assert second.created_project is False
        assert second.created_telegram_account is False

        async with database.session() as session:
            accounts = list(
                (
                    await session.scalars(
                        select(Account).where(Account.id == first.account_id)
                    )
                ).all()
            )
            projects = list(
                (
                    await session.scalars(
                        select(Project).where(Project.account_id == first.account_id)
                    )
                ).all()
            )
            telegram_accounts = list(
                (
                    await session.scalars(
                        select(TelegramAccount).where(
                            TelegramAccount.account_id == first.account_id
                        )
                    )
                ).all()
            )

        assert len(accounts) == 1
        assert accounts[0].name == "Bootstrap Test Updated"
        assert len(projects) == 1
        assert projects[0].name == "Relay Updated"
        assert len(telegram_accounts) == 1
    finally:
        if account_id is not None:
            async with database.transaction() as session:
                await session.execute(delete(Account).where(Account.id == account_id))
        await database.close()
