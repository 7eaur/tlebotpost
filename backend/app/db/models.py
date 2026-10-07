"""SQLAlchemy 2.0 models for the v2 PostgreSQL schema."""

from __future__ import annotations

import uuid
from datetime import datetime, time
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ENUM, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, CreatedAt, UpdatedAt, UuidPk


class AccountStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    ARCHIVED = "archived"


class UserStatus(StrEnum):
    ACTIVE = "active"
    INVITED = "invited"
    DISABLED = "disabled"


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class DestinationStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    ERROR = "error"
    ARCHIVED = "archived"


class SourceStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    ERROR = "error"
    ARCHIVED = "archived"


class RouteStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    ERROR = "error"
    ARCHIVED = "archived"


class RouteExecutionStatus(StrEnum):
    RECEIVED = "received"
    PROCESSING = "processing"
    FILTERED = "filtered"
    DUPLICATE = "duplicate"
    QUEUED = "queued"
    PUBLISHED = "published"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PublishingMode(StrEnum):
    DIRECT = "direct"
    QUEUED = "queued"
    MANUAL = "manual"


class RetentionMode(StrEnum):
    NONE = "none"
    SCHEDULED_ONLY = "scheduled_only"
    RETRY_ONLY = "retry_only"
    ALWAYS = "always"


class ContentType(StrEnum):
    TEXT = "text"
    PHOTO = "photo"
    VIDEO = "video"
    DOCUMENT = "document"
    AUDIO = "audio"
    VOICE = "voice"
    ALBUM = "album"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class ContentStatus(StrEnum):
    RECEIVED = "received"
    PROCESSING = "processing"
    READY = "ready"
    QUEUED = "queued"
    PUBLISHED = "published"
    SKIPPED = "skipped"
    FAILED = "failed"
    EXPIRED = "expired"


class JobStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    RETRY_WAIT = "retry_wait"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class AttemptStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    RETRYING = "retrying"
    FAILED = "failed"


class TelegramAccountStatus(StrEnum):
    ACTIVE = "active"
    DISCONNECTED = "disconnected"
    REAUTH_REQUIRED = "reauth_required"
    DISABLED = "disabled"


class MediaStatus(StrEnum):
    PENDING = "pending"
    STORED = "stored"
    DELETED = "deleted"
    FAILED = "failed"


class ScheduleKind(StrEnum):
    IMMEDIATE = "immediate"
    INTERVAL = "interval"
    DAILY_WINDOW = "daily_window"
    CRON = "cron"


def pg_enum(enum_class: type[StrEnum], name: str) -> ENUM:
    """Reference an enum created by schema.sql without recreating its type."""
    return ENUM(
        enum_class,
        name=name,
        create_type=False,
        values_callable=lambda members: [member.value for member in members],
    )

class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[UuidPk]
    name: Mapped[str] = mapped_column(String(160))
    slug: Mapped[str] = mapped_column(String(63), unique=True)
    status: Mapped[AccountStatus] = mapped_column(
        pg_enum(AccountStatus, "account_status"), default=AccountStatus.ACTIVE
    )
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]
    users: Mapped[list[AccountUser]] = relationship(back_populates="account")
    projects: Mapped[list[Project]] = relationship(back_populates="account")


class User(Base):
    __tablename__ = "users"
    id: Mapped[UuidPk]
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    email: Mapped[str | None] = mapped_column(String(320))
    display_name: Mapped[str] = mapped_column(String(160))
    status: Mapped[UserStatus] = mapped_column(
        pg_enum(UserStatus, "user_status"), default=UserStatus.ACTIVE
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]
    accounts: Mapped[list[AccountUser]] = relationship(back_populates="user")


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[UuidPk]
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[CreatedAt]


class AccountUser(Base):
    __tablename__ = "account_users"
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id", ondelete="RESTRICT"))
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[CreatedAt]
    account: Mapped[Account] = relationship(back_populates="users")
    user: Mapped[User] = relationship(back_populates="accounts")
    role: Mapped[Role] = relationship()


class UserSession(Base):
    __tablename__ = "user_sessions"
    id: Mapped[UuidPk]
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[CreatedAt]


class ApiToken(Base):
    __tablename__ = "api_tokens"
    id: Mapped[UuidPk]
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(160))
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[CreatedAt]


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[UuidPk]
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(160))
    slug: Mapped[str] = mapped_column(String(63))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ProjectStatus] = mapped_column(
        pg_enum(ProjectStatus, "project_status"), default=ProjectStatus.ACTIVE
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]
    account: Mapped[Account] = relationship(back_populates="projects")
    destinations: Mapped[list[Destination]] = relationship(back_populates="project")
    __table_args__ = (UniqueConstraint("account_id", "slug"), UniqueConstraint("account_id", "id"))


class Category(Base):
    __tablename__ = "categories"
    id: Mapped[UuidPk]
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(63))
    description: Mapped[str | None] = mapped_column(Text)
    color: Mapped[str | None] = mapped_column(String(7))
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]
    __table_args__ = (UniqueConstraint("account_id", "slug"),)


class ProjectCategory(Base):
    __tablename__ = "project_categories"
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[CreatedAt]


class ProfileBase:
    id: Mapped[UuidPk]
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]


class BrandingProfile(ProfileBase, Base):
    __tablename__ = "branding_profiles"
    footer: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(Text)
    separator: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class FilterProfile(ProfileBase, Base):
    __tablename__ = "filter_profiles"
    include_keywords: Mapped[list] = mapped_column(JSONB, default=list)
    exclude_keywords: Mapped[list] = mapped_column(JSONB, default=list)
    allowed_media_types: Mapped[list] = mapped_column(JSONB, default=list)
    remove_urls: Mapped[bool] = mapped_column(Boolean, default=True)
    remove_source_rights: Mapped[bool] = mapped_column(Boolean, default=True)
    preserve_emoji: Mapped[bool] = mapped_column(Boolean, default=True)
    custom_rules: Mapped[dict] = mapped_column(JSONB, default=dict)


class TransformProfile(ProfileBase, Base):
    __tablename__ = "transform_profiles"
    remove_link_preview: Mapped[bool] = mapped_column(Boolean, default=True)
    preserve_line_breaks: Mapped[bool] = mapped_column(Boolean, default=True)
    trim_whitespace: Mapped[bool] = mapped_column(Boolean, default=True)
    options: Mapped[dict] = mapped_column(JSONB, default=dict)


class DeduplicationProfile(ProfileBase, Base):
    __tablename__ = "deduplication_profiles"
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    window_seconds: Mapped[int] = mapped_column(Integer, default=86400)
    compare_text: Mapped[bool] = mapped_column(Boolean, default=True)
    compare_media: Mapped[bool] = mapped_column(Boolean, default=True)
    compare_telegram_id: Mapped[bool] = mapped_column(Boolean, default=True)
    options: Mapped[dict] = mapped_column(JSONB, default=dict)


class ScheduleProfile(ProfileBase, Base):
    __tablename__ = "schedule_profiles"
    kind: Mapped[ScheduleKind] = mapped_column(
        pg_enum(ScheduleKind, "schedule_kind"), default=ScheduleKind.IMMEDIATE
    )
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    interval_seconds: Mapped[int | None] = mapped_column(Integer)
    cron_expression: Mapped[str | None] = mapped_column(String(255))
    window_start: Mapped[time | None] = mapped_column(Time)
    window_end: Mapped[time | None] = mapped_column(Time)
    max_per_minute: Mapped[int | None] = mapped_column(Integer)
    min_interval_seconds: Mapped[int] = mapped_column(Integer, default=0)
    options: Mapped[dict] = mapped_column(JSONB, default=dict)


class RetentionPolicy(ProfileBase, Base):
    __tablename__ = "retention_policies"
    mode: Mapped[RetentionMode] = mapped_column(
        pg_enum(RetentionMode, "retention_mode"), default=RetentionMode.NONE
    )
    content_days: Mapped[int | None] = mapped_column(Integer)
    media_days: Mapped[int | None] = mapped_column(Integer)


class Destination(Base):
    __tablename__ = "destinations"
    id: Mapped[UuidPk]
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(160))
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger)
    telegram_username: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[DestinationStatus] = mapped_column(
        pg_enum(DestinationStatus, "destination_status"), default=DestinationStatus.PAUSED
    )
    publishing_mode: Mapped[PublishingMode] = mapped_column(
        pg_enum(PublishingMode, "publishing_mode"), default=PublishingMode.DIRECT
    )
    branding_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("branding_profiles.id", ondelete="SET NULL")
    )
    deduplication_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("deduplication_profiles.id", ondelete="SET NULL")
    )
    schedule_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("schedule_profiles.id", ondelete="SET NULL")
    )
    retention_policy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("retention_policies.id", ondelete="SET NULL")
    )
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]
    project: Mapped[Project] = relationship(back_populates="destinations")
    __table_args__ = (
        UniqueConstraint("account_id", "telegram_chat_id"),
        UniqueConstraint("account_id", "id"),
    )


class TelegramAccount(Base):
    __tablename__ = "telegram_accounts"
    id: Mapped[UuidPk]
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    label: Mapped[str] = mapped_column(String(160))
    phone_last4: Mapped[str | None] = mapped_column(String(4))
    status: Mapped[TelegramAccountStatus] = mapped_column(
        pg_enum(TelegramAccountStatus, "telegram_account_status"),
        default=TelegramAccountStatus.DISCONNECTED,
    )
    session_key: Mapped[str] = mapped_column(String(255))
    last_connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]
    __table_args__ = (
        UniqueConstraint("account_id", "label"),
        UniqueConstraint("account_id", "session_key"),
    )


class TelegramSession(Base):
    __tablename__ = "telegram_sessions"
    id: Mapped[UuidPk]
    telegram_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("telegram_accounts.id", ondelete="CASCADE")
    )
    storage_key: Mapped[str] = mapped_column(String(255), unique=True)
    encrypted: Mapped[bool] = mapped_column(Boolean, default=True)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[UuidPk]
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    telegram_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("telegram_accounts.id", ondelete="RESTRICT")
    )
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger)
    telegram_username: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(255))
    status: Mapped[SourceStatus] = mapped_column(
        pg_enum(SourceStatus, "source_status"), default=SourceStatus.ACTIVE
    )
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]
    __table_args__ = (
        UniqueConstraint("account_id", "telegram_account_id", "telegram_chat_id"),
        UniqueConstraint("account_id", "id"),
    )


class SourceCheckpoint(Base):
    __tablename__ = "source_checkpoints"
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True
    )
    last_seen_message_id: Mapped[int] = mapped_column(BigInteger, default=0)
    last_committed_message_id: Mapped[int] = mapped_column(BigInteger, default=0)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[UpdatedAt]


class ClassificationPolicy(ProfileBase, Base):
    __tablename__ = "classification_policies"
    rules: Mapped[list] = mapped_column(JSONB, default=list)
    default_category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL")
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class SourceRoute(Base):
    __tablename__ = "source_routes"
    id: Mapped[UuidPk]
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    destination_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("destinations.id", ondelete="CASCADE")
    )
    status: Mapped[RouteStatus] = mapped_column(
        pg_enum(RouteStatus, "route_status"), default=RouteStatus.PAUSED
    )
    publishing_mode: Mapped[PublishingMode | None] = mapped_column(
        pg_enum(PublishingMode, "publishing_mode")
    )
    filter_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("filter_profiles.id", ondelete="SET NULL")
    )
    transform_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("transform_profiles.id", ondelete="SET NULL")
    )
    branding_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("branding_profiles.id", ondelete="SET NULL")
    )
    schedule_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("schedule_profiles.id", ondelete="SET NULL")
    )
    deduplication_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("deduplication_profiles.id", ondelete="SET NULL")
    )
    retention_policy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("retention_policies.id", ondelete="SET NULL")
    )
    classification_policy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("classification_policies.id", ondelete="SET NULL")
    )
    priority: Mapped[int] = mapped_column(SmallInteger, default=100)
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]
    __table_args__ = (UniqueConstraint("source_id", "destination_id"),)


class RouteExecution(Base):
    __tablename__ = "route_executions"
    id: Mapped[UuidPk]
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id", ondelete="RESTRICT"))
    route_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_routes.id", ondelete="RESTRICT")
    )
    destination_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("destinations.id", ondelete="RESTRICT")
    )
    event_key: Mapped[str] = mapped_column(String(96))
    cursor_message_id: Mapped[int] = mapped_column(BigInteger)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    telegram_grouped_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[RouteExecutionStatus] = mapped_column(
        pg_enum(RouteExecutionStatus, "route_execution_status"),
        default=RouteExecutionStatus.RECEIVED,
    )
    reason_code: Mapped[str | None] = mapped_column(String(120))
    content_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("content_items.id", ondelete="SET NULL")
    )
    publish_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("publish_jobs.id", ondelete="SET NULL")
    )
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]
    __table_args__ = (
        UniqueConstraint("route_id", "event_key"),
        Index("route_executions_source_cursor_idx", "source_id", "cursor_message_id"),
        Index("route_executions_account_status_idx", "account_id", "status"),
    )


class ContentItem(Base):
    __tablename__ = "content_items"
    id: Mapped[UuidPk]
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    telegram_grouped_id: Mapped[int | None] = mapped_column(BigInteger)
    content_type: Mapped[ContentType] = mapped_column(
        pg_enum(ContentType, "content_type"), default=ContentType.UNKNOWN
    )
    text_original: Mapped[str | None] = mapped_column(Text)
    text_normalized: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[CreatedAt]
    telegram_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retention_mode: Mapped[RetentionMode] = mapped_column(
        pg_enum(RetentionMode, "retention_mode"), default=RetentionMode.NONE
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[ContentStatus] = mapped_column(
        pg_enum(ContentStatus, "content_status"), default=ContentStatus.RECEIVED
    )
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]
    __table_args__ = (UniqueConstraint("source_id", "telegram_message_id"),)


class ContentSourceRef(Base):
    __tablename__ = "content_source_refs"
    content_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE"), primary_key=True
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True
    )
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[CreatedAt]


class ContentMedia(Base):
    __tablename__ = "content_media"
    id: Mapped[UuidPk]
    content_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE")
    )
    media_type: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str | None] = mapped_column(String(1024))
    original_file_name: Mapped[str | None] = mapped_column(String(512))
    mime_type: Mapped[str | None] = mapped_column(String(255))
    byte_size: Mapped[int | None] = mapped_column(BigInteger)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[MediaStatus] = mapped_column(
        pg_enum(MediaStatus, "media_status"), default=MediaStatus.PENDING
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]


class ContentFingerprint(Base):
    __tablename__ = "content_fingerprints"
    id: Mapped[UuidPk]
    content_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE")
    )
    scope_key: Mapped[str] = mapped_column(String(255))
    fingerprint_type: Mapped[str] = mapped_column(String(32))
    fingerprint: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[CreatedAt]
    __table_args__ = (UniqueConstraint("scope_key", "fingerprint_type", "fingerprint"),)


class ContentCategory(Base):
    __tablename__ = "content_categories"
    content_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE"), primary_key=True
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True
    )
    confidence: Mapped[float | None]
    assigned_by_rule: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[CreatedAt]


class PublishJob(Base):
    __tablename__ = "publish_jobs"
    id: Mapped[UuidPk]
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    content_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("content_items.id", ondelete="RESTRICT")
    )
    destination_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("destinations.id", ondelete="CASCADE")
    )
    source_route_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source_routes.id", ondelete="SET NULL")
    )
    status: Mapped[JobStatus] = mapped_column(
        pg_enum(JobStatus, "job_status"), default=JobStatus.QUEUED
    )
    scheduled_for: Mapped[CreatedAt]
    priority: Mapped[int] = mapped_column(SmallInteger, default=100)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(255))
    last_error_code: Mapped[str | None] = mapped_column(String(120))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]
    __table_args__ = (
        UniqueConstraint("content_item_id", "destination_id"),
        Index(
            "ix_publish_jobs_due", "destination_id", "priority", "scheduled_for", "next_attempt_at"
        ),
    )


class PublicationAttempt(Base):
    __tablename__ = "publication_attempts"
    id: Mapped[UuidPk]
    publish_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("publish_jobs.id", ondelete="CASCADE")
    )
    attempt_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[AttemptStatus] = mapped_column(
        pg_enum(AttemptStatus, "attempt_status"), default=AttemptStatus.STARTED
    )
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[CreatedAt]
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (UniqueConstraint("publish_job_id", "attempt_number"),)


class PublishedMessage(Base):
    __tablename__ = "published_messages"
    id: Mapped[UuidPk]
    publish_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("publish_jobs.id", ondelete="CASCADE"), unique=True
    )
    destination_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("destinations.id", ondelete="CASCADE")
    )
    telegram_message_id: Mapped[int] = mapped_column(BigInteger)
    published_at: Mapped[CreatedAt]
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    __table_args__ = (UniqueConstraint("destination_id", "telegram_message_id"),)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE")
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(120))
    entity_type: Mapped[str] = mapped_column(String(120))
    entity_id: Mapped[uuid.UUID | None]
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[CreatedAt]


class SystemEvent(Base):
    __tablename__ = "system_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE")
    )
    event_type: Mapped[str] = mapped_column(String(120))
    severity: Mapped[str] = mapped_column(String(16), default="info")
    entity_type: Mapped[str | None] = mapped_column(String(120))
    entity_id: Mapped[uuid.UUID | None]
    error_code: Mapped[str | None] = mapped_column(String(120))
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[CreatedAt]


class WorkerLease(Base):
    __tablename__ = "worker_leases"
    id: Mapped[UuidPk]
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE")
    )
    worker_name: Mapped[str] = mapped_column(String(120))
    lease_key: Mapped[str] = mapped_column(String(255))
    owner_id: Mapped[str] = mapped_column(String(255))
    acquired_at: Mapped[CreatedAt]
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("worker_name", "lease_key"),)
