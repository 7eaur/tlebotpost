"""Fresh V3 identity bootstrap with no legacy application-data import."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Account, Project, ProjectStatus, TelegramAccount


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    account_id: uuid.UUID
    project_id: uuid.UUID
    telegram_account_id: uuid.UUID
    created_account: bool
    created_project: bool
    created_telegram_account: bool


async def bootstrap_v3_identity(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    account_name: str = "Telegram Relay V3",
    account_slug: str = "runtime-v3",
    project_name: str = "Telegram Relay",
    project_slug: str = "relay",
    telegram_label: str = "primary",
    session_key: str = "v3-primary",
    account_id: uuid.UUID | None = None,
    telegram_account_id: uuid.UUID | None = None,
) -> BootstrapResult:
    """Create only the minimum V3 identity rows, idempotently."""

    async with session_factory() as session:
        async with session.begin():
            account = await session.scalar(
                select(Account).where(Account.slug == account_slug).with_for_update()
            )
            created_account = account is None
            if account is None:
                account = Account(id=account_id, name=account_name, slug=account_slug)
                session.add(account)
                await session.flush()
            else:
                if account_id is not None and account.id != account_id:
                    raise ValueError("configured V3 account id does not match existing bootstrap account")
                account.name = account_name

            project = await session.scalar(
                select(Project)
                .where(
                    Project.account_id == account.id,
                    Project.slug == project_slug,
                )
                .with_for_update()
            )
            created_project = project is None
            if project is None:
                project = Project(
                    account_id=account.id,
                    name=project_name,
                    slug=project_slug,
                    status=ProjectStatus.ACTIVE,
                )
                session.add(project)
                await session.flush()
            else:
                project.name = project_name
                project.status = ProjectStatus.ACTIVE

            telegram_account = await session.scalar(
                select(TelegramAccount)
                .where(
                    TelegramAccount.account_id == account.id,
                    TelegramAccount.label == telegram_label,
                )
                .with_for_update()
            )
            created_telegram_account = telegram_account is None
            if telegram_account is None:
                telegram_account = TelegramAccount(
                    id=telegram_account_id,
                    account_id=account.id,
                    label=telegram_label,
                    session_key=session_key,
                )
                session.add(telegram_account)
                await session.flush()
            else:
                if (
                    telegram_account_id is not None
                    and telegram_account.id != telegram_account_id
                ):
                    raise ValueError(
                        "configured V3 Telegram account id does not match existing bootstrap account"
                    )
                telegram_account.session_key = session_key

            return BootstrapResult(
                account_id=account.id,
                project_id=project.id,
                telegram_account_id=telegram_account.id,
                created_account=created_account,
                created_project=created_project,
                created_telegram_account=created_telegram_account,
            )
