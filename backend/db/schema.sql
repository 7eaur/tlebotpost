-- Telegram Content Distribution Platform v2
-- Initial PostgreSQL schema for the modular monolith.
-- This file is an initial schema, not a destructive migration.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- -----------------------------------------------------------------------------
-- Shared helpers and enum types
-- -----------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

CREATE TYPE account_status AS ENUM ('active', 'suspended', 'archived');
CREATE TYPE user_status AS ENUM ('active', 'invited', 'disabled');
CREATE TYPE project_status AS ENUM ('active', 'paused', 'archived');
CREATE TYPE destination_status AS ENUM ('active', 'paused', 'error', 'archived');
CREATE TYPE source_status AS ENUM ('active', 'paused', 'error', 'archived');
CREATE TYPE route_status AS ENUM ('active', 'paused', 'error', 'archived');
CREATE TYPE publishing_mode AS ENUM ('direct', 'queued', 'manual');
CREATE TYPE retention_mode AS ENUM ('none', 'scheduled_only', 'retry_only', 'always');
CREATE TYPE content_type AS ENUM ('text', 'photo', 'video', 'document', 'audio', 'voice', 'album', 'mixed', 'unknown');
CREATE TYPE content_status AS ENUM ('received', 'processing', 'ready', 'queued', 'published', 'skipped', 'failed', 'expired');
CREATE TYPE job_status AS ENUM ('queued', 'processing', 'publishing', 'published', 'retry_wait', 'failed', 'cancelled', 'expired');
CREATE TYPE attempt_status AS ENUM ('started', 'succeeded', 'retrying', 'failed');
CREATE TYPE telegram_account_status AS ENUM ('active', 'disconnected', 'reauth_required', 'disabled');
CREATE TYPE media_status AS ENUM ('pending', 'stored', 'deleted', 'failed');
CREATE TYPE schedule_kind AS ENUM ('immediate', 'interval', 'daily_window', 'cron');

-- -----------------------------------------------------------------------------
-- Identity and tenant boundary
-- -----------------------------------------------------------------------------

CREATE TABLE accounts (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name                text NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 160),
    slug                text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
    status              account_status NOT NULL DEFAULT 'active',
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    telegram_user_id    bigint UNIQUE,
    email               text,
    display_name        text NOT NULL CHECK (length(trim(display_name)) BETWEEN 1 AND 160),
    status              user_status NOT NULL DEFAULT 'active',
    last_login_at       timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CHECK (email IS NULL OR length(trim(email)) > 3)
);

CREATE UNIQUE INDEX users_email_lower_uq
    ON users (lower(email))
    WHERE email IS NOT NULL;

CREATE TABLE roles (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code                text NOT NULL UNIQUE,
    name                text NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE account_users (
    account_id          uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_id             uuid NOT NULL REFERENCES roles(id) ON DELETE RESTRICT,
    is_owner            boolean NOT NULL DEFAULT false,
    created_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, user_id)
);

CREATE TABLE user_sessions (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash          text NOT NULL UNIQUE,
    expires_at          timestamptz NOT NULL,
    revoked_at          timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CHECK (expires_at > created_at)
);

CREATE TABLE api_tokens (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id          uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    created_by          uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    name                text NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 160),
    token_hash          text NOT NULL UNIQUE,
    last_used_at        timestamptz,
    expires_at          timestamptz,
    revoked_at          timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------------
-- Projects, categories, destinations, and reusable profiles
-- -----------------------------------------------------------------------------

CREATE TABLE projects (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id          uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    name                text NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 160),
    slug                text NOT NULL CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
    description         text,
    status              project_status NOT NULL DEFAULT 'active',
    created_by          uuid REFERENCES users(id) ON DELETE SET NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (account_id, slug),
    UNIQUE (account_id, id)
);

CREATE TABLE categories (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id          uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    name                text NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 120),
    slug                text NOT NULL CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
    description         text,
    color               text CHECK (color IS NULL OR color ~ '^#[0-9A-Fa-f]{6}$'),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (account_id, slug)
);

CREATE TABLE project_categories (
    project_id          uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    category_id         uuid NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    created_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, category_id)
);

CREATE TABLE branding_profiles (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id          uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    name                text NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 160),
    footer              text,
    link                text,
    separator           text,
    enabled             boolean NOT NULL DEFAULT true,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE filter_profiles (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id          uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    name                text NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 160),
    include_keywords    jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(include_keywords) = 'array'),
    exclude_keywords    jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(exclude_keywords) = 'array'),
    allowed_media_types jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(allowed_media_types) = 'array'),
    remove_urls         boolean NOT NULL DEFAULT true,
    remove_source_rights boolean NOT NULL DEFAULT true,
    preserve_emoji      boolean NOT NULL DEFAULT true,
    custom_rules        jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(custom_rules) = 'object'),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE transform_profiles (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id          uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    name                text NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 160),
    remove_link_preview boolean NOT NULL DEFAULT true,
    preserve_line_breaks boolean NOT NULL DEFAULT true,
    trim_whitespace     boolean NOT NULL DEFAULT true,
    options             jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(options) = 'object'),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE deduplication_profiles (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id          uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    name                text NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 160),
    enabled             boolean NOT NULL DEFAULT true,
    window_seconds      integer NOT NULL DEFAULT 86400 CHECK (window_seconds >= 0),
    compare_text        boolean NOT NULL DEFAULT true,
    compare_media       boolean NOT NULL DEFAULT true,
    compare_telegram_id boolean NOT NULL DEFAULT true,
    options             jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(options) = 'object'),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE schedule_profiles (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id          uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    name                text NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 160),
    kind                schedule_kind NOT NULL DEFAULT 'immediate',
    timezone            text NOT NULL DEFAULT 'UTC',
    interval_seconds    integer CHECK (interval_seconds IS NULL OR interval_seconds > 0),
    cron_expression     text,
    window_start        time,
    window_end          time,
    max_per_minute      integer CHECK (max_per_minute IS NULL OR max_per_minute > 0),
    min_interval_seconds integer NOT NULL DEFAULT 0 CHECK (min_interval_seconds >= 0),
    options             jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(options) = 'object'),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CHECK (kind <> 'interval' OR interval_seconds IS NOT NULL),
    CHECK (kind <> 'cron' OR cron_expression IS NOT NULL),
    CHECK (kind <> 'daily_window' OR (window_start IS NOT NULL AND window_end IS NOT NULL))
);

CREATE TABLE retention_policies (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id          uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    name                text NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 160),
    mode                retention_mode NOT NULL DEFAULT 'none',
    content_days        integer CHECK (content_days IS NULL OR content_days >= 0),
    media_days          integer CHECK (media_days IS NULL OR media_days >= 0),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE destinations (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id          uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    project_id          uuid NOT NULL,
    name                text NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 160),
    telegram_chat_id   bigint NOT NULL,
    telegram_username  text,
    status              destination_status NOT NULL DEFAULT 'paused',
    publishing_mode    publishing_mode NOT NULL DEFAULT 'direct',
    branding_profile_id uuid REFERENCES branding_profiles(id) ON DELETE SET NULL,
    deduplication_profile_id uuid REFERENCES deduplication_profiles(id) ON DELETE SET NULL,
    schedule_profile_id uuid REFERENCES schedule_profiles(id) ON DELETE SET NULL,
    retention_policy_id uuid REFERENCES retention_policies(id) ON DELETE SET NULL,
    settings           jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(settings) = 'object'),
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (account_id, project_id) REFERENCES projects(account_id, id) ON DELETE CASCADE,
    UNIQUE (account_id, telegram_chat_id),
    UNIQUE (account_id, id)
);

-- -----------------------------------------------------------------------------
-- Telegram accounts and reusable sources
-- -----------------------------------------------------------------------------

CREATE TABLE telegram_accounts (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id          uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    label               text NOT NULL CHECK (length(trim(label)) BETWEEN 1 AND 160),
    phone_last4         text,
    status              telegram_account_status NOT NULL DEFAULT 'disconnected',
    session_key         text NOT NULL,
    last_connected_at   timestamptz,
    last_error_code     text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (account_id, label),
    UNIQUE (account_id, session_key)
);

CREATE TABLE telegram_sessions (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    telegram_account_id uuid NOT NULL REFERENCES telegram_accounts(id) ON DELETE CASCADE,
    storage_key         text NOT NULL UNIQUE,
    encrypted           boolean NOT NULL DEFAULT true,
    connected_at        timestamptz,
    expires_at          timestamptz,
    last_error_code     text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sources (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id          uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    telegram_account_id uuid NOT NULL REFERENCES telegram_accounts(id) ON DELETE RESTRICT,
    telegram_chat_id   bigint NOT NULL,
    telegram_username  text,
    title               text NOT NULL CHECK (length(trim(title)) BETWEEN 1 AND 255),
    status              source_status NOT NULL DEFAULT 'active',
    metadata            jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata) = 'object'),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (account_id, telegram_account_id, telegram_chat_id),
    UNIQUE (account_id, id)
);

CREATE TABLE source_checkpoints (
    source_id           uuid PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
    last_seen_message_id bigint NOT NULL DEFAULT 0 CHECK (last_seen_message_id >= 0),
    last_event_at       timestamptz,
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE classification_policies (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id          uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    name                text NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 160),
    rules               jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(rules) = 'array'),
    default_category_id uuid REFERENCES categories(id) ON DELETE SET NULL,
    enabled             boolean NOT NULL DEFAULT true,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

-- A route owns the settings for using one source in one destination.
CREATE TABLE source_routes (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id          uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    source_id           uuid NOT NULL,
    destination_id      uuid NOT NULL,
    status              route_status NOT NULL DEFAULT 'paused',
    publishing_mode     publishing_mode,
    filter_profile_id   uuid REFERENCES filter_profiles(id) ON DELETE SET NULL,
    transform_profile_id uuid REFERENCES transform_profiles(id) ON DELETE SET NULL,
    branding_profile_id uuid REFERENCES branding_profiles(id) ON DELETE SET NULL,
    schedule_profile_id uuid REFERENCES schedule_profiles(id) ON DELETE SET NULL,
    deduplication_profile_id uuid REFERENCES deduplication_profiles(id) ON DELETE SET NULL,
    retention_policy_id uuid REFERENCES retention_policies(id) ON DELETE SET NULL,
    classification_policy_id uuid REFERENCES classification_policies(id) ON DELETE SET NULL,
    priority            integer NOT NULL DEFAULT 100,
    settings            jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(settings) = 'object'),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (account_id, source_id) REFERENCES sources(account_id, id) ON DELETE CASCADE,
    FOREIGN KEY (account_id, destination_id) REFERENCES destinations(account_id, id) ON DELETE CASCADE,
    UNIQUE (source_id, destination_id)
);

-- -----------------------------------------------------------------------------
-- Content, media, fingerprints, and classifications
-- -----------------------------------------------------------------------------

CREATE TABLE content_items (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id          uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    source_id            uuid NOT NULL,
    telegram_message_id bigint,
    telegram_grouped_id bigint,
    content_type        content_type NOT NULL DEFAULT 'unknown',
    text_original       text,
    text_normalized     text,
    received_at         timestamptz NOT NULL DEFAULT now(),
    telegram_created_at timestamptz,
    retention_mode      retention_mode NOT NULL DEFAULT 'none',
    expires_at          timestamptz,
    status              content_status NOT NULL DEFAULT 'received',
    metadata            jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata) = 'object'),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (account_id, source_id) REFERENCES sources(account_id, id) ON DELETE CASCADE,
    UNIQUE (source_id, telegram_message_id)
);

CREATE TABLE content_source_refs (
    content_item_id     uuid NOT NULL REFERENCES content_items(id) ON DELETE CASCADE,
    source_id           uuid NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    telegram_message_id bigint,
    created_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (content_item_id, source_id)
);

CREATE TABLE content_media (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    content_item_id     uuid NOT NULL REFERENCES content_items(id) ON DELETE CASCADE,
    media_type          text NOT NULL,
    storage_key         text,
    original_file_name  text,
    mime_type           text,
    byte_size           bigint CHECK (byte_size IS NULL OR byte_size >= 0),
    checksum_sha256     text CHECK (checksum_sha256 IS NULL OR checksum_sha256 ~ '^[0-9a-fA-F]{64}$'),
    status              media_status NOT NULL DEFAULT 'pending',
    expires_at          timestamptz,
    metadata            jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata) = 'object'),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE content_fingerprints (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    content_item_id     uuid NOT NULL REFERENCES content_items(id) ON DELETE CASCADE,
    scope_key           text NOT NULL,
    fingerprint_type    text NOT NULL CHECK (fingerprint_type IN ('telegram_message', 'text', 'media', 'combined')),
    fingerprint         text NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_key, fingerprint_type, fingerprint)
);

CREATE TABLE content_categories (
    content_item_id     uuid NOT NULL REFERENCES content_items(id) ON DELETE CASCADE,
    category_id         uuid NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    confidence          numeric(5,4) CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
    assigned_by_rule    boolean NOT NULL DEFAULT true,
    created_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (content_item_id, category_id)
);

-- -----------------------------------------------------------------------------
-- Publishing queue and outcomes
-- -----------------------------------------------------------------------------

CREATE TABLE publish_jobs (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id          uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    content_item_id     uuid NOT NULL REFERENCES content_items(id) ON DELETE RESTRICT,
    destination_id      uuid NOT NULL,
    source_route_id     uuid REFERENCES source_routes(id) ON DELETE SET NULL,
    status              job_status NOT NULL DEFAULT 'queued',
    scheduled_for       timestamptz NOT NULL DEFAULT now(),
    priority            integer NOT NULL DEFAULT 100,
    attempt_count       integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    next_attempt_at     timestamptz,
    locked_at           timestamptz,
    locked_by           text,
    last_error_code     text,
    last_error_message  text,
    published_at        timestamptz,
    cancelled_at        timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (account_id, destination_id) REFERENCES destinations(account_id, id) ON DELETE CASCADE,
    UNIQUE (content_item_id, destination_id)
);

CREATE TABLE publication_attempts (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    publish_job_id      uuid NOT NULL REFERENCES publish_jobs(id) ON DELETE CASCADE,
    attempt_number      integer NOT NULL CHECK (attempt_number > 0),
    status              attempt_status NOT NULL,
    telegram_message_id bigint,
    error_code          text,
    error_message       text,
    started_at          timestamptz NOT NULL DEFAULT now(),
    finished_at         timestamptz,
    latency_ms          integer CHECK (latency_ms IS NULL OR latency_ms >= 0),
    UNIQUE (publish_job_id, attempt_number)
);

CREATE TABLE published_messages (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    publish_job_id      uuid NOT NULL UNIQUE REFERENCES publish_jobs(id) ON DELETE CASCADE,
    destination_id      uuid NOT NULL REFERENCES destinations(id) ON DELETE CASCADE,
    telegram_message_id bigint NOT NULL,
    published_at        timestamptz NOT NULL DEFAULT now(),
    metadata            jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata) = 'object'),
    UNIQUE (destination_id, telegram_message_id)
);

-- -----------------------------------------------------------------------------
-- Audit and worker coordination
-- -----------------------------------------------------------------------------

CREATE TABLE audit_logs (
    id                  bigserial PRIMARY KEY,
    account_id          uuid REFERENCES accounts(id) ON DELETE CASCADE,
    actor_user_id       uuid REFERENCES users(id) ON DELETE SET NULL,
    action              text NOT NULL,
    entity_type         text NOT NULL,
    entity_id           uuid,
    details             jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(details) = 'object'),
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE system_events (
    id                  bigserial PRIMARY KEY,
    account_id          uuid REFERENCES accounts(id) ON DELETE CASCADE,
    event_type          text NOT NULL,
    severity            text NOT NULL DEFAULT 'info' CHECK (severity IN ('debug', 'info', 'warning', 'error', 'critical')),
    entity_type        text,
    entity_id          uuid,
    error_code          text,
    details             jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(details) = 'object'),
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE worker_leases (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id          uuid REFERENCES accounts(id) ON DELETE CASCADE,
    worker_name         text NOT NULL,
    lease_key           text NOT NULL,
    owner_id            text NOT NULL,
    acquired_at         timestamptz NOT NULL DEFAULT now(),
    expires_at          timestamptz NOT NULL,
    UNIQUE (worker_name, lease_key)
);

-- -----------------------------------------------------------------------------
-- Updated-at triggers
-- -----------------------------------------------------------------------------

CREATE TRIGGER accounts_set_updated_at BEFORE UPDATE ON accounts FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER users_set_updated_at BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER projects_set_updated_at BEFORE UPDATE ON projects FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER categories_set_updated_at BEFORE UPDATE ON categories FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER branding_profiles_set_updated_at BEFORE UPDATE ON branding_profiles FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER filter_profiles_set_updated_at BEFORE UPDATE ON filter_profiles FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER transform_profiles_set_updated_at BEFORE UPDATE ON transform_profiles FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER deduplication_profiles_set_updated_at BEFORE UPDATE ON deduplication_profiles FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER schedule_profiles_set_updated_at BEFORE UPDATE ON schedule_profiles FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER retention_policies_set_updated_at BEFORE UPDATE ON retention_policies FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER destinations_set_updated_at BEFORE UPDATE ON destinations FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER telegram_accounts_set_updated_at BEFORE UPDATE ON telegram_accounts FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER telegram_sessions_set_updated_at BEFORE UPDATE ON telegram_sessions FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER sources_set_updated_at BEFORE UPDATE ON sources FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER source_checkpoints_set_updated_at BEFORE UPDATE ON source_checkpoints FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER classification_policies_set_updated_at BEFORE UPDATE ON classification_policies FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER source_routes_set_updated_at BEFORE UPDATE ON source_routes FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER content_items_set_updated_at BEFORE UPDATE ON content_items FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER content_media_set_updated_at BEFORE UPDATE ON content_media FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER publish_jobs_set_updated_at BEFORE UPDATE ON publish_jobs FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- -----------------------------------------------------------------------------
-- Operational indexes
-- -----------------------------------------------------------------------------

CREATE INDEX account_users_user_idx ON account_users (user_id);
CREATE INDEX projects_account_status_idx ON projects (account_id, status);
CREATE INDEX destinations_project_status_idx ON destinations (project_id, status);
CREATE INDEX sources_account_status_idx ON sources (account_id, status);
CREATE INDEX source_routes_destination_status_idx ON source_routes (destination_id, status);
CREATE INDEX source_routes_source_status_idx ON source_routes (source_id, status);
CREATE INDEX source_checkpoints_event_idx ON source_checkpoints (last_event_at);
CREATE INDEX content_items_account_received_idx ON content_items (account_id, received_at DESC);
CREATE INDEX content_items_status_expiry_idx ON content_items (status, expires_at);
CREATE INDEX content_media_expiry_idx ON content_media (status, expires_at);
CREATE INDEX content_categories_category_idx ON content_categories (category_id);
CREATE INDEX publish_jobs_due_idx
    ON publish_jobs (destination_id, priority DESC, scheduled_for, next_attempt_at)
    WHERE status IN ('queued', 'retry_wait');
CREATE INDEX publish_jobs_account_status_idx ON publish_jobs (account_id, status, scheduled_for);
CREATE INDEX publication_attempts_job_idx ON publication_attempts (publish_job_id, attempt_number DESC);
CREATE INDEX audit_logs_account_created_idx ON audit_logs (account_id, created_at DESC);
CREATE INDEX system_events_account_created_idx ON system_events (account_id, created_at DESC);
CREATE INDEX worker_leases_expiry_idx ON worker_leases (expires_at);

-- Default roles. Account-specific permissions are handled by account_users.role_id.
INSERT INTO roles (code, name) VALUES
    ('owner', 'Owner'),
    ('admin', 'Administrator'),
    ('editor', 'Editor'),
    ('operator', 'Operator'),
    ('viewer', 'Viewer');

COMMIT;
