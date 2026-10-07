"""Profile, category, and route-policy administration for V2."""

from __future__ import annotations

import json
import uuid
from datetime import time
from typing import Any, Callable

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.admin_pages import csv_values, escape, page
from app.audit import AuditRecorder
from app.db.models import (
    BrandingProfile,
    Category,
    ClassificationPolicy,
    DeduplicationProfile,
    Destination,
    FilterProfile,
    RetentionMode,
    RetentionPolicy,
    ScheduleKind,
    ScheduleProfile,
    Source,
    SourceRoute,
    TransformProfile,
)


def register_profile_routes(
    app: FastAPI,
    runtime: Any,
    *,
    session_valid: Callable[[Request], bool],
) -> None:
    audit = AuditRecorder(runtime.database.session_factory, runtime.settings.account_id)

    def require_web(request: Request) -> RedirectResponse | None:
        if session_valid(request):
            return None
        return RedirectResponse("/login", status_code=303)

    @app.get("/profiles", response_class=HTMLResponse)
    async def profiles_page(request: Request) -> HTMLResponse | RedirectResponse:
        redirect = require_web(request)
        if redirect:
            return redirect
        return HTMLResponse(render_profiles(await load_profiles(runtime)))

    @app.post("/profiles/branding")
    async def create_branding(
        request: Request,
        name: str = Form(...),
        footer: str = Form(""),
        link: str = Form(""),
        separator: str = Form(""),
    ) -> RedirectResponse:
        redirect = require_web(request)
        if redirect:
            return redirect
        profile = BrandingProfile(
            account_id=runtime.settings.account_id,
            name=name.strip(),
            footer=footer.strip() or None,
            link=link.strip() or None,
            separator=separator or None,
            enabled=True,
        )
        profile_id = await insert(runtime, profile)
        await audit_profile(audit, "branding", profile_id, name)
        return RedirectResponse("/profiles", status_code=303)

    @app.post("/profiles/filter")
    async def create_filter(
        request: Request,
        name: str = Form(...),
        include_keywords: str = Form(""),
        exclude_keywords: str = Form(""),
        allowed_media_types: str = Form(""),
        min_text_length: str = Form(""),
        max_text_length: str = Form(""),
        required_hashtags: str = Form(""),
        remove_urls: str | None = Form(None),
        remove_source_rights: str | None = Form(None),
        preserve_emoji: str | None = Form(None),
    ) -> RedirectResponse:
        redirect = require_web(request)
        if redirect:
            return redirect
        custom_rules: dict[str, Any] = {}
        put_int(custom_rules, "min_text_length", min_text_length)
        put_int(custom_rules, "max_text_length", max_text_length)
        tags = csv_values(required_hashtags)
        if tags:
            custom_rules["required_hashtags"] = tags
        profile = FilterProfile(
            account_id=runtime.settings.account_id,
            name=name.strip(),
            include_keywords=csv_values(include_keywords),
            exclude_keywords=csv_values(exclude_keywords),
            allowed_media_types=csv_values(allowed_media_types),
            remove_urls=remove_urls is not None,
            remove_source_rights=remove_source_rights is not None,
            preserve_emoji=preserve_emoji is not None,
            custom_rules=custom_rules,
        )
        profile_id = await insert(runtime, profile)
        await audit_profile(audit, "filter", profile_id, name)
        return RedirectResponse("/profiles", status_code=303)

    @app.post("/profiles/transform")
    async def create_transform(
        request: Request,
        name: str = Form(...),
        remove_link_preview: str | None = Form(None),
        preserve_line_breaks: str | None = Form(None),
        trim_whitespace: str | None = Form(None),
    ) -> RedirectResponse:
        redirect = require_web(request)
        if redirect:
            return redirect
        profile = TransformProfile(
            account_id=runtime.settings.account_id,
            name=name.strip(),
            remove_link_preview=remove_link_preview is not None,
            preserve_line_breaks=preserve_line_breaks is not None,
            trim_whitespace=trim_whitespace is not None,
            options={},
        )
        profile_id = await insert(runtime, profile)
        await audit_profile(audit, "transform", profile_id, name)
        return RedirectResponse("/profiles", status_code=303)

    @app.post("/profiles/dedup")
    async def create_dedup(
        request: Request,
        name: str = Form(...),
        window_seconds: int = Form(86400),
        compare_text: str | None = Form(None),
        compare_media: str | None = Form(None),
        compare_telegram_id: str | None = Form(None),
        match_text_only: str | None = Form(None),
        match_media_only: str | None = Form(None),
    ) -> RedirectResponse:
        redirect = require_web(request)
        if redirect:
            return redirect
        profile = DeduplicationProfile(
            account_id=runtime.settings.account_id,
            name=name.strip(),
            enabled=True,
            window_seconds=max(0, window_seconds),
            compare_text=compare_text is not None,
            compare_media=compare_media is not None,
            compare_telegram_id=compare_telegram_id is not None,
            options={
                "match_text_only": match_text_only is not None,
                "match_media_only": match_media_only is not None,
            },
        )
        profile_id = await insert(runtime, profile)
        await audit_profile(audit, "deduplication", profile_id, name)
        return RedirectResponse("/profiles", status_code=303)

    @app.post("/profiles/schedule")
    async def create_schedule(
        request: Request,
        name: str = Form(...),
        kind: str = Form("immediate"),
        timezone: str = Form("UTC"),
        interval_seconds: str = Form(""),
        cron_expression: str = Form(""),
        window_start: str = Form(""),
        window_end: str = Form(""),
        max_per_minute: str = Form(""),
        min_interval_seconds: int = Form(0),
    ) -> RedirectResponse:
        redirect = require_web(request)
        if redirect:
            return redirect
        try:
            schedule_kind = ScheduleKind(kind)
        except ValueError as exc:
            raise HTTPException(400, "Invalid schedule kind") from exc
        profile = ScheduleProfile(
            account_id=runtime.settings.account_id,
            name=name.strip(),
            kind=schedule_kind,
            timezone=timezone.strip() or "UTC",
            interval_seconds=int_or_none(interval_seconds),
            cron_expression=cron_expression.strip() or None,
            window_start=time_or_none(window_start),
            window_end=time_or_none(window_end),
            max_per_minute=int_or_none(max_per_minute),
            min_interval_seconds=max(0, min_interval_seconds),
            options={},
        )
        profile_id = await insert(runtime, profile)
        await audit_profile(audit, "schedule", profile_id, name)
        return RedirectResponse("/profiles", status_code=303)

    @app.post("/profiles/retention")
    async def create_retention(
        request: Request,
        name: str = Form(...),
        mode: str = Form("none"),
        content_days: str = Form(""),
        media_days: str = Form(""),
    ) -> RedirectResponse:
        redirect = require_web(request)
        if redirect:
            return redirect
        try:
            retention_mode = RetentionMode(mode)
        except ValueError as exc:
            raise HTTPException(400, "Invalid retention mode") from exc
        profile = RetentionPolicy(
            account_id=runtime.settings.account_id,
            name=name.strip(),
            mode=retention_mode,
            content_days=int_or_none(content_days),
            media_days=int_or_none(media_days),
        )
        profile_id = await insert(runtime, profile)
        await audit_profile(audit, "retention", profile_id, name)
        return RedirectResponse("/profiles", status_code=303)

    @app.post("/profiles/category")
    async def create_category(
        request: Request,
        name: str = Form(...),
        slug: str = Form(...),
        description: str = Form(""),
        color: str = Form(""),
    ) -> RedirectResponse:
        redirect = require_web(request)
        if redirect:
            return redirect
        category = Category(
            account_id=runtime.settings.account_id,
            name=name.strip(),
            slug=slug.strip().lower(),
            description=description.strip() or None,
            color=color.strip() or None,
        )
        category_id = await insert(runtime, category)
        await audit.record(
            "category.create",
            "category",
            entity_id=category_id,
            actor="web_owner",
            details={"name": name.strip()},
        )
        return RedirectResponse("/profiles", status_code=303)

    @app.post("/profiles/classification")
    async def create_classification(
        request: Request,
        name: str = Form(...),
        rules_json: str = Form("[]"),
        default_category_id: str = Form(""),
    ) -> RedirectResponse:
        redirect = require_web(request)
        if redirect:
            return redirect
        try:
            rules = json.loads(rules_json or "[]")
        except json.JSONDecodeError as exc:
            raise HTTPException(400, "Invalid classification JSON") from exc
        if not isinstance(rules, list):
            raise HTTPException(400, "Classification rules must be an array")
        profile = ClassificationPolicy(
            account_id=runtime.settings.account_id,
            name=name.strip(),
            rules=rules,
            default_category_id=uuid_or_none(default_category_id),
            enabled=True,
        )
        profile_id = await insert(runtime, profile)
        await audit_profile(audit, "classification", profile_id, name)
        return RedirectResponse("/profiles", status_code=303)

    @app.post("/profiles/assign")
    async def assign_profile(
        request: Request,
        entity_type: str = Form(...),
        entity_id: uuid.UUID = Form(...),
        profile_kind: str = Form(...),
        profile_id: str = Form(""),
    ) -> RedirectResponse:
        redirect = require_web(request)
        if redirect:
            return redirect
        common = {
            "branding": "branding_profile_id",
            "deduplication": "deduplication_profile_id",
            "schedule": "schedule_profile_id",
            "retention": "retention_policy_id",
        }
        route_only = {
            "filter": "filter_profile_id",
            "transform": "transform_profile_id",
            "classification": "classification_policy_id",
        }
        fields = {**common, **route_only} if entity_type == "route" else common
        field = fields.get(profile_kind)
        if field is None:
            raise HTTPException(400, "Profile cannot be assigned to this entity")
        model: type[SourceRoute] | type[Destination]
        model = SourceRoute if entity_type == "route" else Destination
        async with runtime.database.session_factory() as session:
            async with session.begin():
                entity = await session.scalar(
                    select(model).where(
                        model.id == entity_id,
                        model.account_id == runtime.settings.account_id,
                    )
                )
                if entity is None:
                    raise HTTPException(404)
                setattr(entity, field, uuid_or_none(profile_id))
        await audit.record(
            "profile.assign",
            entity_type,
            entity_id=entity_id,
            actor="web_owner",
            details={"profile_kind": profile_kind, "profile_id": profile_id or None},
        )
        await runtime.reload_ingestion()
        return RedirectResponse("/profiles", status_code=303)


async def load_profiles(runtime: Any) -> dict[str, list[Any]]:
    models = {
        "branding": BrandingProfile,
        "filter": FilterProfile,
        "transform": TransformProfile,
        "deduplication": DeduplicationProfile,
        "schedule": ScheduleProfile,
        "retention": RetentionPolicy,
        "classification": ClassificationPolicy,
        "category": Category,
    }
    result: dict[str, list[Any]] = {}
    async with runtime.database.session_factory() as session:
        for key, model in models.items():
            result[key] = list(
                (
                    await session.scalars(
                        select(model)
                        .where(model.account_id == runtime.settings.account_id)
                        .order_by(model.created_at.desc())
                    )
                ).all()
            )
        for key, model in (
            ("routes", SourceRoute),
            ("destinations", Destination),
            ("sources", Source),
        ):
            result[key] = list(
                (
                    await session.scalars(
                        select(model)
                        .where(model.account_id == runtime.settings.account_id)
                        .order_by(model.created_at.desc())
                    )
                ).all()
            )
    return result


async def insert(runtime: Any, entity: Any) -> uuid.UUID:
    async with runtime.database.session_factory() as session:
        async with session.begin():
            session.add(entity)
            await session.flush()
            return entity.id


async def audit_profile(
    audit: AuditRecorder,
    kind: str,
    profile_id: uuid.UUID,
    name: str,
) -> None:
    await audit.record(
        "profile.create",
        f"{kind}_profile",
        entity_id=profile_id,
        actor="web_owner",
        details={"name": name.strip()},
    )


def render_profiles(values: dict[str, list[Any]]) -> str:
    categories = values["category"]
    category_options = '<option value="">بدون افتراضي</option>' + "".join(
        f'<option value="{item.id}">{escape(item.name)}</option>'
        for item in categories
    )
    cards = [
        profile_card(
            "Branding",
            "/profiles/branding",
            """
            <input name="name" placeholder="اسم الملف" required>
            <input name="footer" placeholder="التوقيع">
            <input name="link" placeholder="الرابط">
            <input name="separator" placeholder="الفاصل">
            """,
            values["branding"],
        ),
        profile_card(
            "Filters",
            "/profiles/filter",
            """
            <input name="name" placeholder="اسم الملف" required>
            <input name="include_keywords" placeholder="تضمين: كلمة,كلمة">
            <input name="exclude_keywords" placeholder="استبعاد: كلمة,كلمة">
            <input name="allowed_media_types" placeholder="photo,video,document">
            <input name="min_text_length" placeholder="أقل طول">
            <input name="max_text_length" placeholder="أقصى طول">
            <input name="required_hashtags" placeholder="#وسم,#وسم">
            <label><input type="checkbox" name="remove_urls" checked> إزالة الروابط</label>
            <label><input type="checkbox" name="remove_source_rights" checked> إزالة الحقوق</label>
            <label><input type="checkbox" name="preserve_emoji" checked> إبقاء Emoji</label>
            """,
            values["filter"],
        ),
        profile_card(
            "Transforms",
            "/profiles/transform",
            """
            <input name="name" placeholder="اسم الملف" required>
            <label><input type="checkbox" name="remove_link_preview" checked> تعطيل المعاينة</label>
            <label><input type="checkbox" name="preserve_line_breaks" checked> إبقاء الأسطر</label>
            <label><input type="checkbox" name="trim_whitespace" checked> تنظيف الفراغات</label>
            """,
            values["transform"],
        ),
        profile_card(
            "Deduplication",
            "/profiles/dedup",
            """
            <input name="name" placeholder="اسم الملف" required>
            <input name="window_seconds" value="86400" type="number" min="0">
            <label><input type="checkbox" name="compare_text" checked> النص</label>
            <label><input type="checkbox" name="compare_media" checked> الوسائط</label>
            <label><input type="checkbox" name="compare_telegram_id" checked> Telegram ID</label>
            <label><input type="checkbox" name="match_text_only"> النص وحده تكرار</label>
            <label><input type="checkbox" name="match_media_only"> الوسيط وحده تكرار</label>
            """,
            values["deduplication"],
        ),
        profile_card(
            "Schedules",
            "/profiles/schedule",
            """
            <input name="name" placeholder="اسم الملف" required>
            <select name="kind">
              <option value="immediate">immediate</option>
              <option value="interval">interval</option>
              <option value="daily_window">daily_window</option>
              <option value="cron">cron</option>
            </select>
            <input name="timezone" value="Asia/Aden">
            <input name="interval_seconds" placeholder="interval seconds">
            <input name="cron_expression" placeholder="0 * * * *">
            <input name="window_start" type="time">
            <input name="window_end" type="time">
            <input name="max_per_minute" placeholder="max/min">
            <input name="min_interval_seconds" value="0" type="number">
            """,
            values["schedule"],
        ),
        profile_card(
            "Retention",
            "/profiles/retention",
            """
            <input name="name" placeholder="اسم الملف" required>
            <select name="mode">
              <option value="none">none</option>
              <option value="retry_only">retry_only</option>
              <option value="scheduled">scheduled</option>
              <option value="archive">archive</option>
            </select>
            <input name="content_days" placeholder="أيام النص">
            <input name="media_days" placeholder="أيام الوسائط">
            """,
            values["retention"],
        ),
        profile_card(
            "Categories",
            "/profiles/category",
            """
            <input name="name" placeholder="اسم الفئة" required>
            <input name="slug" placeholder="slug" required>
            <input name="description" placeholder="الوصف">
            <input name="color" placeholder="#14305F">
            """,
            values["category"],
        ),
        profile_card(
            "Classification",
            "/profiles/classification",
            (
                '<input name="name" placeholder="اسم السياسة" required>'
                f'<select name="default_category_id">{category_options}</select>'
                '<textarea name="rules_json" rows="8">[]</textarea>'
            ),
            values["classification"],
        ),
    ]
    return page(
        "Profiles والقواعد",
        '<div class="cards">' + "".join(cards) + "</div>" + assignment_form(values),
    )


def profile_card(
    title: str,
    action: str,
    fields: str,
    rows: list[Any],
) -> str:
    listing = "".join(
        (
            f"<li><code>{item.id}</code> — "
            f"{escape(getattr(item, 'name', getattr(item, 'slug', '')))}</li>"
        )
        for item in rows[:20]
    ) or "<li>لا يوجد.</li>"
    return (
        '<section class="profile-card">'
        f"<h2>{escape(title)}</h2>"
        f'<form method="post" action="{action}">{fields}<button>إنشاء</button></form>'
        f"<ul>{listing}</ul></section>"
    )


def assignment_form(values: dict[str, list[Any]]) -> str:
    entity_options = "".join(
        f'<option value="{item.id}">Route {str(item.id)[:8]}</option>'
        for item in values["routes"]
    ) + "".join(
        f'<option value="{item.id}">Destination {escape(item.name)}</option>'
        for item in values["destinations"]
    )
    profile_options = ""
    for kind in (
        "branding",
        "filter",
        "transform",
        "deduplication",
        "schedule",
        "retention",
        "classification",
    ):
        for item in values[kind]:
            profile_options += (
                f'<option value="{item.id}">{escape(kind)} — '
                f"{escape(item.name)}</option>"
            )
    return f"""
    <section>
      <h2>ربط Profile</h2>
      <form class="form-grid" method="post" action="/profiles/assign">
        <select name="entity_type">
          <option value="route">route</option>
          <option value="destination">destination</option>
        </select>
        <select name="entity_id">{entity_options}</select>
        <select name="profile_kind">
          <option value="branding">branding</option>
          <option value="filter">filter</option>
          <option value="transform">transform</option>
          <option value="deduplication">deduplication</option>
          <option value="schedule">schedule</option>
          <option value="retention">retention</option>
          <option value="classification">classification</option>
        </select>
        <select name="profile_id">
          <option value="">إلغاء الربط</option>{profile_options}
        </select>
        <button>حفظ الربط</button>
      </form>
    </section>
    """


def int_or_none(value: str) -> int | None:
    if not value.strip():
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise HTTPException(400, "Expected integer value") from exc


def put_int(target: dict[str, Any], key: str, value: str) -> None:
    parsed = int_or_none(value)
    if parsed is not None:
        target[key] = parsed


def time_or_none(value: str) -> time | None:
    if not value.strip():
        return None
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(400, "Invalid time") from exc


def uuid_or_none(value: str) -> uuid.UUID | None:
    if not value.strip():
        return None
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(400, "Invalid UUID") from exc
