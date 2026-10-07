"""FastAPI administration surface for Telegram Relay Runtime v2."""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, select

from app.control.resolver import TelegramChatResolver
from app.db.models import (
    ContentItem,
    Destination,
    DestinationStatus,
    JobStatus,
    Project,
    PublishJob,
    RouteStatus,
    Source,
    SourceCheckpoint,
    SourceRoute,
    SourceStatus,
    SystemEvent,
    TelegramAccount,
)


COOKIE_NAME = "relay_v2_session"


@dataclass(frozen=True, slots=True)
class WebSettings:
    username: str
    password: str
    session_secret: str
    admin_token: str
    session_ttl_seconds: int = 43200

    @classmethod
    def from_env(cls) -> "WebSettings":
        return cls(
            username=os.getenv("WEB_ADMIN_USER", "admin").strip() or "admin",
            password=os.getenv("WEB_ADMIN_PASSWORD", ""),
            session_secret=os.getenv("WEB_SESSION_SECRET", ""),
            admin_token=os.getenv("WEB_ADMIN_TOKEN", ""),
            session_ttl_seconds=int(os.getenv("WEB_SESSION_TTL_SECONDS", "43200")),
        )

    @property
    def configured(self) -> bool:
        return bool(self.password and self.session_secret)


def create_web_app(runtime: Any) -> FastAPI:
    settings = WebSettings.from_env()
    app = FastAPI(
        title="Telegram Relay V2",
        version="2.0.0",
        docs_url="/api/docs",
        redoc_url=None,
    )

    def signed_cookie() -> str:
        expires = int(time.time()) + settings.session_ttl_seconds
        payload = f"{settings.username}:{expires}".encode()
        encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        signature = hmac.new(
            settings.session_secret.encode(),
            encoded.encode(),
            hashlib.sha256,
        ).hexdigest()
        return f"{encoded}.{signature}"

    def session_valid(request: Request) -> bool:
        if not settings.configured:
            return False
        value = request.cookies.get(COOKIE_NAME, "")
        if "." not in value:
            return False
        encoded, signature = value.rsplit(".", 1)
        expected = hmac.new(
            settings.session_secret.encode(),
            encoded.encode(),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return False
        try:
            padding = "=" * (-len(encoded) % 4)
            decoded = base64.urlsafe_b64decode(encoded + padding).decode()
            username, expires_raw = decoded.rsplit(":", 1)
            expires = int(expires_raw)
        except (ValueError, UnicodeDecodeError):
            return False
        return username == settings.username and expires > int(time.time())

    async def require_api_auth(request: Request) -> None:
        if session_valid(request):
            return
        authorization = request.headers.get("authorization", "")
        if settings.admin_token and authorization.startswith("Bearer "):
            token = authorization.removeprefix("Bearer ").strip()
            if hmac.compare_digest(token, settings.admin_token):
                return
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    async def get_dashboard_data() -> dict[str, Any]:
        account_id = runtime.settings.account_id
        async with runtime.database.session_factory() as session:
            sources = list(
                (
                    await session.scalars(
                        select(Source)
                        .where(Source.account_id == account_id)
                        .order_by(Source.title)
                    )
                ).all()
            )
            destinations = list(
                (
                    await session.scalars(
                        select(Destination)
                        .where(Destination.account_id == account_id)
                        .order_by(Destination.name)
                    )
                ).all()
            )
            routes = list(
                (
                    await session.scalars(
                        select(SourceRoute)
                        .where(SourceRoute.account_id == account_id)
                        .order_by(SourceRoute.created_at)
                    )
                ).all()
            )
            projects = list(
                (
                    await session.scalars(
                        select(Project)
                        .where(Project.account_id == account_id)
                        .order_by(Project.created_at)
                    )
                ).all()
            )
            jobs = list(
                (
                    await session.scalars(
                        select(PublishJob)
                        .where(PublishJob.account_id == account_id)
                        .order_by(PublishJob.created_at.desc())
                        .limit(40)
                    )
                ).all()
            )
            events = list(
                (
                    await session.scalars(
                        select(SystemEvent)
                        .where(SystemEvent.account_id == account_id)
                        .order_by(SystemEvent.created_at.desc())
                        .limit(30)
                    )
                ).all()
            )
            content_count = int(
                await session.scalar(
                    select(func.count(ContentItem.id)).where(ContentItem.account_id == account_id)
                )
                or 0
            )
        source_by_id = {source.id: source for source in sources}
        destination_by_id = {destination.id: destination for destination in destinations}
        return {
            "sources": sources,
            "destinations": destinations,
            "routes": routes,
            "projects": projects,
            "jobs": jobs,
            "events": events,
            "content_count": content_count,
            "source_by_id": source_by_id,
            "destination_by_id": destination_by_id,
        }

    @app.get("/livez")
    async def livez() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        snapshot = await runtime.status_snapshot()
        ready = bool(snapshot["database"] and snapshot["worker_running"])
        return JSONResponse(
            {"status": "ok" if ready else "degraded", **snapshot},
            status_code=200 if ready else 503,
        )

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        snapshot = await runtime.status_snapshot()
        ready = bool(
            snapshot["database"]
            and snapshot["worker_running"]
            and snapshot["telegram_connected"]
            and snapshot["listener_running"]
            and not snapshot["paused"]
        )
        return JSONResponse(
            {"status": "ready" if ready else "not_ready", **snapshot},
            status_code=200 if ready else 503,
        )

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request) -> HTMLResponse | RedirectResponse:
        if session_valid(request):
            return RedirectResponse("/", status_code=303)
        return HTMLResponse(_login_html(settings.configured))

    @app.post("/login")
    async def login(
        username: str = Form(...),
        password: str = Form(...),
    ) -> HTMLResponse | RedirectResponse:
        if not settings.configured:
            return HTMLResponse(_login_html(False, "إعداد دخول الويب غير مكتمل."), status_code=503)
        valid_user = hmac.compare_digest(username.strip(), settings.username)
        valid_password = hmac.compare_digest(password, settings.password)
        if not (valid_user and valid_password):
            return HTMLResponse(_login_html(True, "بيانات الدخول غير صحيحة."), status_code=401)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            COOKIE_NAME,
            signed_cookie(),
            httponly=True,
            secure=os.getenv("WEB_COOKIE_SECURE", "true").lower() not in {"0", "false", "no"},
            samesite="strict",
            max_age=settings.session_ttl_seconds,
        )
        return response

    @app.post("/logout")
    async def logout() -> RedirectResponse:
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(COOKIE_NAME)
        return response

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> HTMLResponse | RedirectResponse:
        if not session_valid(request):
            return RedirectResponse("/login", status_code=303)
        data = await get_dashboard_data()
        snapshot = await runtime.status_snapshot()
        return HTMLResponse(_dashboard_html(data, snapshot))

    @app.post("/actions/runtime/{action}")
    async def runtime_action(request: Request, action: str) -> RedirectResponse:
        if not session_valid(request):
            return RedirectResponse("/login", status_code=303)
        if action == "run":
            await runtime.resume_ingestion()
        elif action == "pause":
            await runtime.pause_ingestion()
        elif action == "reload":
            await runtime.reload_ingestion()
        else:
            raise HTTPException(404)
        return RedirectResponse("/", status_code=303)

    @app.post("/actions/source/{source_id}/{action}")
    async def source_action(request: Request, source_id: uuid.UUID, action: str) -> RedirectResponse:
        if not session_valid(request):
            return RedirectResponse("/login", status_code=303)
        status_value = _source_status(action)
        async with runtime.database.session_factory() as session:
            async with session.begin():
                source = await session.scalar(
                    select(Source).where(
                        Source.id == source_id,
                        Source.account_id == runtime.settings.account_id,
                    )
                )
                if source is None:
                    raise HTTPException(404)
                source.status = status_value
        await runtime.reload_ingestion()
        return RedirectResponse("/", status_code=303)

    @app.post("/actions/destination/{destination_id}/{action}")
    async def destination_action(
        request: Request, destination_id: uuid.UUID, action: str
    ) -> RedirectResponse:
        if not session_valid(request):
            return RedirectResponse("/login", status_code=303)
        status_value = _destination_status(action)
        async with runtime.database.session_factory() as session:
            async with session.begin():
                destination = await session.scalar(
                    select(Destination).where(
                        Destination.id == destination_id,
                        Destination.account_id == runtime.settings.account_id,
                    )
                )
                if destination is None:
                    raise HTTPException(404)
                destination.status = status_value
        await runtime.reload_ingestion()
        return RedirectResponse("/", status_code=303)

    @app.post("/actions/route/{route_id}/{action}")
    async def route_action(request: Request, route_id: uuid.UUID, action: str) -> RedirectResponse:
        if not session_valid(request):
            return RedirectResponse("/login", status_code=303)
        status_value = _route_status(action)
        async with runtime.database.session_factory() as session:
            async with session.begin():
                route = await session.scalar(
                    select(SourceRoute).where(
                        SourceRoute.id == route_id,
                        SourceRoute.account_id == runtime.settings.account_id,
                    )
                )
                if route is None:
                    raise HTTPException(404)
                route.status = status_value
        await runtime.reload_ingestion()
        return RedirectResponse("/", status_code=303)

    @app.post("/actions/job/{job_id}/{action}")
    async def job_action(request: Request, job_id: uuid.UUID, action: str) -> RedirectResponse:
        if not session_valid(request):
            return RedirectResponse("/login", status_code=303)
        if action == "retry":
            await runtime.queue.requeue(job_id)
        elif action == "cancel":
            await runtime.queue.cancel(job_id)
        else:
            raise HTTPException(404)
        return RedirectResponse("/", status_code=303)

    @app.post("/actions/source/add")
    async def add_source(
        request: Request,
        reference: str = Form(...),
        destination_id: str = Form(""),
    ) -> RedirectResponse:
        if not session_valid(request):
            return RedirectResponse("/login", status_code=303)
        client = await runtime.client_manager.ensure_connected()
        resolved = await TelegramChatResolver(client).resolve(reference)
        async with runtime.database.session_factory() as session:
            async with session.begin():
                telegram_account = await session.scalar(
                    select(TelegramAccount)
                    .where(TelegramAccount.account_id == runtime.settings.account_id)
                    .order_by(TelegramAccount.created_at)
                    .limit(1)
                )
                if telegram_account is None:
                    raise HTTPException(409, "Telegram account is not configured")
                source = await session.scalar(
                    select(Source).where(
                        Source.account_id == runtime.settings.account_id,
                        Source.telegram_account_id == telegram_account.id,
                        Source.telegram_chat_id == resolved.chat_id,
                    )
                )
                if source is None:
                    source = Source(
                        account_id=runtime.settings.account_id,
                        telegram_account_id=telegram_account.id,
                        telegram_chat_id=resolved.chat_id,
                        telegram_username=resolved.username,
                        title=resolved.title,
                        status=SourceStatus.ACTIVE,
                        metadata_json={"input_ref": resolved.input_ref},
                    )
                    session.add(source)
                    await session.flush()
                    session.add(
                        SourceCheckpoint(
                            source_id=source.id,
                            last_seen_message_id=resolved.latest_message_id,
                        )
                    )
                else:
                    source.title = resolved.title
                    source.telegram_username = resolved.username
                    source.status = SourceStatus.ACTIVE

                if destination_id:
                    destination_uuid = uuid.UUID(destination_id)
                    destination = await session.scalar(
                        select(Destination).where(
                            Destination.id == destination_uuid,
                            Destination.account_id == runtime.settings.account_id,
                        )
                    )
                    if destination is None:
                        raise HTTPException(404, "Destination not found")
                    route = await session.scalar(
                        select(SourceRoute).where(
                            SourceRoute.source_id == source.id,
                            SourceRoute.destination_id == destination.id,
                        )
                    )
                    if route is None:
                        template = await session.scalar(
                            select(SourceRoute)
                            .where(SourceRoute.account_id == runtime.settings.account_id)
                            .order_by(SourceRoute.created_at)
                            .limit(1)
                        )
                        route = SourceRoute(
                            account_id=runtime.settings.account_id,
                            source_id=source.id,
                            destination_id=destination.id,
                            status=RouteStatus.ACTIVE,
                            filter_profile_id=getattr(template, "filter_profile_id", None),
                            transform_profile_id=getattr(template, "transform_profile_id", None),
                            branding_profile_id=getattr(template, "branding_profile_id", None),
                            schedule_profile_id=getattr(template, "schedule_profile_id", None),
                            deduplication_profile_id=getattr(template, "deduplication_profile_id", None),
                            retention_policy_id=getattr(template, "retention_policy_id", None),
                        )
                        session.add(route)
                    else:
                        route.status = RouteStatus.ACTIVE
        await runtime.reload_ingestion()
        return RedirectResponse("/", status_code=303)

    @app.post("/actions/destination/add")
    async def add_destination(
        request: Request,
        reference: str = Form(...),
        name: str = Form(""),
    ) -> RedirectResponse:
        if not session_valid(request):
            return RedirectResponse("/login", status_code=303)
        client = await runtime.client_manager.ensure_connected()
        resolved = await TelegramChatResolver(client).resolve(reference)
        async with runtime.database.session_factory() as session:
            async with session.begin():
                project = await session.scalar(
                    select(Project)
                    .where(Project.account_id == runtime.settings.account_id)
                    .order_by(Project.created_at)
                    .limit(1)
                )
                if project is None:
                    raise HTTPException(409, "Project is not configured")
                destination = await session.scalar(
                    select(Destination).where(
                        Destination.account_id == runtime.settings.account_id,
                        Destination.telegram_chat_id == resolved.chat_id,
                    )
                )
                if destination is None:
                    template = await session.scalar(
                        select(Destination)
                        .where(Destination.account_id == runtime.settings.account_id)
                        .order_by(Destination.created_at)
                        .limit(1)
                    )
                    destination = Destination(
                        account_id=runtime.settings.account_id,
                        project_id=project.id,
                        name=(name.strip() or resolved.title),
                        telegram_chat_id=resolved.chat_id,
                        telegram_username=resolved.username,
                        status=DestinationStatus.ACTIVE,
                        publishing_mode=getattr(template, "publishing_mode", None) or "direct",
                        branding_profile_id=getattr(template, "branding_profile_id", None),
                        deduplication_profile_id=getattr(template, "deduplication_profile_id", None),
                        schedule_profile_id=getattr(template, "schedule_profile_id", None),
                        retention_policy_id=getattr(template, "retention_policy_id", None),
                        settings={},
                    )
                    session.add(destination)
                else:
                    destination.name = name.strip() or resolved.title
                    destination.telegram_username = resolved.username
                    destination.status = DestinationStatus.ACTIVE
        await runtime.reload_ingestion()
        return RedirectResponse("/", status_code=303)

    @app.post("/actions/route/add")
    async def add_route(
        request: Request,
        source_id: uuid.UUID = Form(...),
        destination_id: uuid.UUID = Form(...),
    ) -> RedirectResponse:
        if not session_valid(request):
            return RedirectResponse("/login", status_code=303)
        async with runtime.database.session_factory() as session:
            async with session.begin():
                source = await session.scalar(
                    select(Source).where(
                        Source.id == source_id,
                        Source.account_id == runtime.settings.account_id,
                    )
                )
                destination = await session.scalar(
                    select(Destination).where(
                        Destination.id == destination_id,
                        Destination.account_id == runtime.settings.account_id,
                    )
                )
                if source is None or destination is None:
                    raise HTTPException(404)
                existing = await session.scalar(
                    select(SourceRoute).where(
                        SourceRoute.source_id == source.id,
                        SourceRoute.destination_id == destination.id,
                    )
                )
                if existing is None:
                    template = await session.scalar(
                        select(SourceRoute)
                        .where(SourceRoute.account_id == runtime.settings.account_id)
                        .order_by(SourceRoute.created_at)
                        .limit(1)
                    )
                    session.add(
                        SourceRoute(
                            account_id=runtime.settings.account_id,
                            source_id=source.id,
                            destination_id=destination.id,
                            status=RouteStatus.ACTIVE,
                            filter_profile_id=getattr(template, "filter_profile_id", None),
                            transform_profile_id=getattr(template, "transform_profile_id", None),
                            branding_profile_id=getattr(template, "branding_profile_id", None),
                            schedule_profile_id=getattr(template, "schedule_profile_id", None),
                            deduplication_profile_id=(
                                getattr(template, "deduplication_profile_id", None)
                                or destination.deduplication_profile_id
                            ),
                            retention_policy_id=(
                                getattr(template, "retention_policy_id", None)
                                or destination.retention_policy_id
                            ),
                        )
                    )
                else:
                    existing.status = RouteStatus.ACTIVE
        await runtime.reload_ingestion()
        return RedirectResponse("/", status_code=303)

    @app.get("/api/v1/status", dependencies=[Depends(require_api_auth)])
    async def api_status() -> dict[str, Any]:
        return await runtime.status_snapshot()

    @app.get("/api/v1/sources", dependencies=[Depends(require_api_auth)])
    async def api_sources() -> list[dict[str, Any]]:
        async with runtime.database.session_factory() as session:
            rows = (
                await session.scalars(
                    select(Source).where(Source.account_id == runtime.settings.account_id)
                )
            ).all()
        return [
            {
                "id": str(row.id),
                "title": row.title,
                "chat_id": row.telegram_chat_id,
                "username": row.telegram_username,
                "status": row.status.value,
            }
            for row in rows
        ]

    @app.get("/api/v1/destinations", dependencies=[Depends(require_api_auth)])
    async def api_destinations() -> list[dict[str, Any]]:
        async with runtime.database.session_factory() as session:
            rows = (
                await session.scalars(
                    select(Destination).where(
                        Destination.account_id == runtime.settings.account_id
                    )
                )
            ).all()
        return [
            {
                "id": str(row.id),
                "name": row.name,
                "chat_id": row.telegram_chat_id,
                "username": row.telegram_username,
                "status": row.status.value,
                "publishing_mode": row.publishing_mode.value,
            }
            for row in rows
        ]

    @app.get("/api/v1/routes", dependencies=[Depends(require_api_auth)])
    async def api_routes() -> list[dict[str, Any]]:
        async with runtime.database.session_factory() as session:
            rows = (
                await session.scalars(
                    select(SourceRoute).where(
                        SourceRoute.account_id == runtime.settings.account_id
                    )
                )
            ).all()
        return [
            {
                "id": str(row.id),
                "source_id": str(row.source_id),
                "destination_id": str(row.destination_id),
                "status": row.status.value,
                "priority": row.priority,
            }
            for row in rows
        ]

    @app.get("/api/v1/queue", dependencies=[Depends(require_api_auth)])
    async def api_queue(limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))
        async with runtime.database.session_factory() as session:
            rows = (
                await session.scalars(
                    select(PublishJob)
                    .where(PublishJob.account_id == runtime.settings.account_id)
                    .order_by(PublishJob.created_at.desc())
                    .limit(limit)
                )
            ).all()
        return [
            {
                "id": str(row.id),
                "content_item_id": str(row.content_item_id),
                "destination_id": str(row.destination_id),
                "status": row.status.value,
                "attempt_count": row.attempt_count,
                "scheduled_for": row.scheduled_for.isoformat(),
                "published_at": row.published_at.isoformat() if row.published_at else None,
                "last_error_code": row.last_error_code,
            }
            for row in rows
        ]

    @app.get("/api/v1/events", dependencies=[Depends(require_api_auth)])
    async def api_events(limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))
        async with runtime.database.session_factory() as session:
            rows = (
                await session.scalars(
                    select(SystemEvent)
                    .where(SystemEvent.account_id == runtime.settings.account_id)
                    .order_by(SystemEvent.created_at.desc())
                    .limit(limit)
                )
            ).all()
        return [
            {
                "id": row.id,
                "event_type": row.event_type,
                "severity": row.severity,
                "error_code": row.error_code,
                "created_at": row.created_at.isoformat(),
                "details": row.details,
            }
            for row in rows
        ]

    return app


def _source_status(action: str) -> SourceStatus:
    mapping = {
        "activate": SourceStatus.ACTIVE,
        "pause": SourceStatus.PAUSED,
        "archive": SourceStatus.ARCHIVED,
    }
    if action not in mapping:
        raise HTTPException(404)
    return mapping[action]


def _destination_status(action: str) -> DestinationStatus:
    mapping = {
        "activate": DestinationStatus.ACTIVE,
        "pause": DestinationStatus.PAUSED,
        "archive": DestinationStatus.ARCHIVED,
    }
    if action not in mapping:
        raise HTTPException(404)
    return mapping[action]


def _route_status(action: str) -> RouteStatus:
    mapping = {
        "activate": RouteStatus.ACTIVE,
        "pause": RouteStatus.PAUSED,
        "archive": RouteStatus.ARCHIVED,
    }
    if action not in mapping:
        raise HTTPException(404)
    return mapping[action]


def _login_html(configured: bool, error: str = "") -> str:
    disabled = "" if configured else "disabled"
    message = error or ("" if configured else "اضبط WEB_ADMIN_PASSWORD و WEB_SESSION_SECRET أولًا.")
    return f"""<!doctype html>
<html lang="ar" dir="rtl">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>تسجيل الدخول — Telegram Relay V2</title>
<style>
body{{font-family:system-ui,-apple-system,sans-serif;background:#0b1020;color:#e8edf7;margin:0;display:grid;place-items:center;min-height:100vh}}
.card{{width:min(92vw,420px);background:#121a2e;border:1px solid #24314f;border-radius:18px;padding:28px;box-shadow:0 20px 60px #0005}}
h1{{font-size:1.35rem;margin:0 0 8px}}p{{color:#9caac7;line-height:1.7}}
label{{display:block;margin-top:16px;font-size:.9rem}}input{{box-sizing:border-box;width:100%;margin-top:7px;padding:12px;border-radius:10px;border:1px solid #31405f;background:#0c1325;color:#fff}}
button{{width:100%;margin-top:20px;padding:12px;border:0;border-radius:10px;background:#2e7cf6;color:#fff;font-weight:700;cursor:pointer}}
.error{{color:#ff9a9a}}
</style></head>
<body><form class="card" method="post" action="/login">
<h1>Telegram Relay V2</h1><p>لوحة الإدارة الخاصة بالنظام.</p>
{f'<p class="error">{html.escape(message)}</p>' if message else ''}
<label>المستخدم<input name="username" autocomplete="username" required></label>
<label>كلمة المرور<input name="password" type="password" autocomplete="current-password" required></label>
<button {disabled}>دخول</button></form></body></html>"""


def _dashboard_html(data: dict[str, Any], snapshot: dict[str, Any]) -> str:
    sources: list[Source] = data["sources"]
    destinations: list[Destination] = data["destinations"]
    routes: list[SourceRoute] = data["routes"]
    jobs: list[PublishJob] = data["jobs"]
    events: list[SystemEvent] = data["events"]
    source_by_id = data["source_by_id"]
    destination_by_id = data["destination_by_id"]

    def badge(ok: bool, yes: str = "يعمل", no: str = "متوقف") -> str:
        cls = "ok" if ok else "bad"
        return f'<span class="badge {cls}">{yes if ok else no}</span>'

    source_rows = "".join(
        f"""<tr><td>{html.escape(row.title)}</td><td>{html.escape(row.status.value)}</td>
<td><code>{row.telegram_chat_id}</code></td><td class="actions">
<form method="post" action="/actions/source/{row.id}/activate"><button>تفعيل</button></form>
<form method="post" action="/actions/source/{row.id}/pause"><button class="ghost">إيقاف</button></form>
</td></tr>"""
        for row in sources
    ) or '<tr><td colspan="4">لا توجد مصادر.</td></tr>'

    destination_rows = "".join(
        f"""<tr><td>{html.escape(row.name)}</td><td>{html.escape(row.status.value)}</td>
<td><code>{row.telegram_chat_id}</code></td><td class="actions">
<form method="post" action="/actions/destination/{row.id}/activate"><button>تفعيل</button></form>
<form method="post" action="/actions/destination/{row.id}/pause"><button class="ghost">إيقاف</button></form>
</td></tr>"""
        for row in destinations
    ) or '<tr><td colspan="4">لا توجد أهداف.</td></tr>'

    route_rows = "".join(
        f"""<tr><td>{html.escape(getattr(source_by_id.get(row.source_id), 'title', str(row.source_id)))}</td>
<td>{html.escape(getattr(destination_by_id.get(row.destination_id), 'name', str(row.destination_id)))}</td>
<td>{html.escape(row.status.value)}</td><td class="actions">
<form method="post" action="/actions/route/{row.id}/activate"><button>تفعيل</button></form>
<form method="post" action="/actions/route/{row.id}/pause"><button class="ghost">إيقاف</button></form>
</td></tr>"""
        for row in routes
    ) or '<tr><td colspan="4">لا توجد مسارات.</td></tr>'

    job_rows = "".join(
        f"""<tr><td><code>{str(row.id)[:8]}</code></td><td>{html.escape(row.status.value)}</td>
<td>{row.attempt_count}</td><td>{html.escape(row.last_error_code or '-')}</td><td class="actions">
{f'<form method="post" action="/actions/job/{row.id}/retry"><button>إعادة</button></form>' if row.status in {JobStatus.FAILED, JobStatus.RETRY_WAIT, JobStatus.CANCELLED} else ''}
{f'<form method="post" action="/actions/job/{row.id}/cancel"><button class="ghost">إلغاء</button></form>' if row.status in {JobStatus.QUEUED, JobStatus.RETRY_WAIT} else ''}
</td></tr>"""
        for row in jobs
    ) or '<tr><td colspan="5">الطابور فارغ.</td></tr>'

    event_rows = "".join(
        f"""<tr><td>{html.escape(row.event_type)}</td><td>{html.escape(row.severity)}</td>
<td>{html.escape(row.error_code or '-')}</td><td>{html.escape(row.created_at.isoformat(timespec='seconds'))}</td></tr>"""
        for row in events
    ) or '<tr><td colspan="4">لا توجد أحداث.</td></tr>'

    destination_options = "".join(
        f'<option value="{row.id}">{html.escape(row.name)}</option>' for row in destinations
    )
    source_options = "".join(
        f'<option value="{row.id}">{html.escape(row.title)}</option>' for row in sources
    )

    return f"""<!doctype html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="30">
<title>Telegram Relay V2</title>
<style>
:root{{--bg:#080d18;--panel:#101827;--panel2:#0c1423;--border:#22304a;--text:#edf3ff;--muted:#91a0bb;--accent:#3b82f6;--green:#43d17c;--red:#ff6b72}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,sans-serif}}
.wrap{{width:min(1460px,94vw);margin:auto;padding:24px 0 50px}}header{{display:flex;justify-content:space-between;gap:16px;align-items:center;margin-bottom:22px;flex-wrap:wrap}}
h1{{font-size:1.55rem;margin:0}}h2{{font-size:1.05rem;margin:0 0 16px}}p{{color:var(--muted)}}
.grid{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px}}.metric,.card{{background:var(--panel);border:1px solid var(--border);border-radius:16px}}
.metric{{padding:16px}}.metric b{{display:block;font-size:1.4rem;margin-top:8px}}.card{{padding:18px;margin-top:16px;overflow:auto}}
.badge{{display:inline-flex;padding:4px 9px;border-radius:999px;font-size:.8rem;background:#25304a}}.badge.ok{{color:var(--green)}}.badge.bad{{color:var(--red)}}
button{{background:var(--accent);color:white;border:0;border-radius:8px;padding:8px 11px;cursor:pointer;font-weight:650}}button.ghost{{background:#27324a}}
.actions{{display:flex;gap:6px;flex-wrap:wrap}}.actions form{{margin:0}}table{{width:100%;border-collapse:collapse;min-width:680px}}th,td{{padding:11px;border-bottom:1px solid var(--border);text-align:right;font-size:.9rem}}th{{color:var(--muted)}}
.forms{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}.mini{{background:var(--panel2);border:1px solid var(--border);padding:14px;border-radius:12px}}input,select{{width:100%;padding:9px;margin:6px 0 10px;border-radius:8px;border:1px solid var(--border);background:#08101f;color:white}}
code{{direction:ltr;display:inline-block}}.top-actions{{display:flex;gap:8px;flex-wrap:wrap}}
@media(max-width:1000px){{.grid{{grid-template-columns:repeat(2,1fr)}}.forms{{grid-template-columns:1fr}}}}@media(max-width:600px){{.grid{{grid-template-columns:1fr}}}}
</style></head><body><div class="wrap">
<header><div><h1>Telegram Relay V2</h1><p>لوحة التشغيل والإدارة الموحدة</p></div>
<div class="top-actions">
<form method="post" action="/actions/runtime/run"><button>▶ تشغيل</button></form>
<form method="post" action="/actions/runtime/pause"><button class="ghost">⏸ إيقاف</button></form>
<form method="post" action="/actions/runtime/reload"><button class="ghost">↻ إعادة تحميل</button></form>
<form method="post" action="/logout"><button class="ghost">خروج</button></form>
</div></header>
<div class="grid">
<div class="metric">PostgreSQL<b>{badge(bool(snapshot['database']))}</b></div>
<div class="metric">Telegram<b>{badge(bool(snapshot['telegram_connected']))}</b></div>
<div class="metric">Listener<b>{badge(bool(snapshot['listener_running']))}</b></div>
<div class="metric">Worker<b>{badge(bool(snapshot['worker_running']))}</b></div>
<div class="metric">المحتوى<b>{data['content_count']}</b></div>
</div>
<div class="card"><h2>إضافة وربط</h2><div class="forms">
<form class="mini" method="post" action="/actions/source/add"><b>مصدر جديد</b><input name="reference" placeholder="@channel أو رابط Telegram" required>
<select name="destination_id"><option value="">بدون ربط</option>{destination_options}</select><button>إضافة المصدر</button></form>
<form class="mini" method="post" action="/actions/destination/add"><b>هدف جديد</b><input name="reference" placeholder="@target" required><input name="name" placeholder="اسم اختياري"><button>إضافة الهدف</button></form>
<form class="mini" method="post" action="/actions/route/add"><b>ربط مصدر بهدف</b><select name="source_id" required>{source_options}</select><select name="destination_id" required>{destination_options}</select><button>إنشاء المسار</button></form>
</div></div>
<div class="card"><h2>المصادر</h2><table><thead><tr><th>الاسم</th><th>الحالة</th><th>Chat ID</th><th>إجراء</th></tr></thead><tbody>{source_rows}</tbody></table></div>
<div class="card"><h2>الأهداف</h2><table><thead><tr><th>الاسم</th><th>الحالة</th><th>Chat ID</th><th>إجراء</th></tr></thead><tbody>{destination_rows}</tbody></table></div>
<div class="card"><h2>المسارات</h2><table><thead><tr><th>المصدر</th><th>الهدف</th><th>الحالة</th><th>إجراء</th></tr></thead><tbody>{route_rows}</tbody></table></div>
<div class="card"><h2>طابور النشر</h2><table><thead><tr><th>ID</th><th>الحالة</th><th>المحاولات</th><th>الخطأ</th><th>إجراء</th></tr></thead><tbody>{job_rows}</tbody></table></div>
<div class="card"><h2>System Events</h2><table><thead><tr><th>الحدث</th><th>المستوى</th><th>الرمز</th><th>الوقت</th></tr></thead><tbody>{event_rows}</tbody></table></div>
</div></body></html>"""
