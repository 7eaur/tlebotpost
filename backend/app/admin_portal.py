"""Extended administration pages and API routes for Runtime v2."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.admin_pages import escape, page, post_button, slugify, table
from app.audit import AuditRecorder
from app.db.models import (
    AuditLog,
    Destination,
    JobStatus,
    Project,
    ProjectStatus,
    PublishedMessage,
    PublishJob,
)
from app.profile_admin import load_profiles


def register_admin_routes(
    app: FastAPI,
    runtime: Any,
    *,
    session_valid: Callable[[Request], bool],
    require_api_auth: Callable[[Request], Any],
) -> None:
    audit = AuditRecorder(runtime.database.session_factory, runtime.settings.account_id)

    def require_web(request: Request) -> RedirectResponse | None:
        if session_valid(request):
            return None
        return RedirectResponse("/login", status_code=303)

    @app.get("/projects", response_class=HTMLResponse)
    async def projects_page(request: Request) -> HTMLResponse | RedirectResponse:
        redirect = require_web(request)
        if redirect:
            return redirect
        async with runtime.database.session_factory() as session:
            rows = list(
                (
                    await session.scalars(
                        select(Project)
                        .where(Project.account_id == runtime.settings.account_id)
                        .order_by(Project.created_at.desc())
                    )
                ).all()
            )
        body = "".join(
            "<tr>"
            f"<td>{escape(row.name)}</td>"
            f"<td><code>{escape(row.slug)}</code></td>"
            f"<td>{escape(row.status.value)}</td>"
            '<td class="actions">'
            + post_button(f"/projects/{row.id}/status/active", "تفعيل")
            + post_button(f"/projects/{row.id}/status/paused", "إيقاف", ghost=True)
            + post_button(f"/projects/{row.id}/status/archived", "أرشفة", ghost=True)
            + "</td></tr>"
            for row in rows
        ) or '<tr><td colspan="4">لا توجد مشاريع.</td></tr>'
        form = """
        <section><h2>مشروع جديد</h2>
        <form class="form-grid" method="post" action="/projects">
          <input name="name" placeholder="اسم المشروع" required>
          <input name="slug" placeholder="project-slug" required>
          <input name="description" placeholder="وصف مختصر">
          <button>إنشاء مشروع</button>
        </form></section>
        """
        return HTMLResponse(
            page(
                "المشاريع",
                form + table(["الاسم", "Slug", "الحالة", "الإجراءات"], body),
            )
        )

    @app.post("/projects")
    async def create_project(
        request: Request,
        name: str = Form(...),
        slug: str = Form(...),
        description: str = Form(""),
    ) -> RedirectResponse:
        redirect = require_web(request)
        if redirect:
            return redirect
        normalized_slug = slugify(slug)
        if not normalized_slug:
            raise HTTPException(400, "Invalid project slug")
        async with runtime.database.session_factory() as session:
            async with session.begin():
                project = Project(
                    account_id=runtime.settings.account_id,
                    name=name.strip(),
                    slug=normalized_slug,
                    description=description.strip() or None,
                    status=ProjectStatus.ACTIVE,
                    settings={},
                )
                session.add(project)
                await session.flush()
                project_id = project.id
        await audit.record(
            "project.create",
            "project",
            entity_id=project_id,
            actor="web_owner",
            details={"name": name.strip(), "slug": normalized_slug},
        )
        return RedirectResponse("/projects", status_code=303)

    @app.post("/projects/{project_id}/status/{value}")
    async def project_status(
        request: Request,
        project_id: uuid.UUID,
        value: str,
    ) -> RedirectResponse:
        redirect = require_web(request)
        if redirect:
            return redirect
        statuses = {
            "active": ProjectStatus.ACTIVE,
            "paused": ProjectStatus.PAUSED,
            "archived": ProjectStatus.ARCHIVED,
        }
        status_value = statuses.get(value)
        if status_value is None:
            raise HTTPException(404)
        async with runtime.database.session_factory() as session:
            async with session.begin():
                project = await session.scalar(
                    select(Project).where(
                        Project.id == project_id,
                        Project.account_id == runtime.settings.account_id,
                    )
                )
                if project is None:
                    raise HTTPException(404)
                project.status = status_value
        await audit.record(
            "project.status",
            "project",
            entity_id=project_id,
            actor="web_owner",
            details={"status": value},
        )
        return RedirectResponse("/projects", status_code=303)

    @app.get("/scheduled", response_class=HTMLResponse)
    async def scheduled_page(request: Request) -> HTMLResponse | RedirectResponse:
        redirect = require_web(request)
        if redirect:
            return redirect
        async with runtime.database.session_factory() as session:
            rows = list(
                (
                    await session.scalars(
                        select(PublishJob)
                        .where(
                            PublishJob.account_id == runtime.settings.account_id,
                            PublishJob.status.in_(
                                [
                                    JobStatus.QUEUED,
                                    JobStatus.RETRY_WAIT,
                                    JobStatus.PROCESSING,
                                ]
                            ),
                        )
                        .order_by(PublishJob.scheduled_for)
                        .limit(200)
                    )
                ).all()
            )
        body = "".join(
            "<tr>"
            f"<td><code>{row.id}</code></td>"
            f"<td>{escape(row.status.value)}</td>"
            f"<td>{escape(row.scheduled_for.isoformat())}</td>"
            f"<td>{row.attempt_count}</td>"
            f"<td>{escape(row.last_error_code or '-')}</td>"
            "</tr>"
            for row in rows
        ) or '<tr><td colspan="5">لا توجد مهام معلقة.</td></tr>'
        return HTMLResponse(
            page(
                "المنشورات المجدولة والطابور",
                table(["Job", "الحالة", "الموعد", "المحاولات", "آخر خطأ"], body),
            )
        )

    @app.get("/published", response_class=HTMLResponse)
    async def published_page(request: Request) -> HTMLResponse | RedirectResponse:
        redirect = require_web(request)
        if redirect:
            return redirect
        async with runtime.database.session_factory() as session:
            rows = (
                await session.execute(
                    select(PublishedMessage, PublishJob, Destination)
                    .join(PublishJob, PublishJob.id == PublishedMessage.publish_job_id)
                    .join(Destination, Destination.id == PublishedMessage.destination_id)
                    .where(PublishJob.account_id == runtime.settings.account_id)
                    .order_by(PublishedMessage.published_at.desc())
                    .limit(200)
                )
            ).all()
        body = "".join(
            "<tr>"
            f"<td>{escape(destination.name)}</td>"
            f"<td>{message.telegram_message_id}</td>"
            f"<td>{escape(message.published_at.isoformat())}</td>"
            f"<td>{job.attempt_count}</td>"
            "</tr>"
            for message, job, destination in rows
        ) or '<tr><td colspan="4">لا توجد منشورات ناجحة بعد.</td></tr>'
        return HTMLResponse(
            page(
                "المنشورات المنشورة",
                table(["الهدف", "Telegram ID", "وقت النشر", "المحاولات"], body),
            )
        )

    @app.get("/telegram", response_class=HTMLResponse)
    async def telegram_page(request: Request) -> HTMLResponse | RedirectResponse:
        redirect = require_web(request)
        if redirect:
            return redirect
        snapshot = await runtime.status_snapshot()
        body = f"""
        <div class="metric-grid">
          <div class="metric">الاتصال
            <b>{'متصل' if snapshot['telegram_connected'] else 'غير متصل'}</b>
          </div>
          <div class="metric">Listener
            <b>{'يعمل' if snapshot['listener_running'] else 'متوقف'}</b>
          </div>
          <div class="metric">المصادر<b>{snapshot['source_count']}</b></div>
        </div>
        <section>
          <h2>إدارة الجلسة</h2>
          <p>لحماية بيانات Telegram الحساسة، تسجيل الدخول و2FA يتمان
          فقط داخل محادثة Control Bot الخاصة بالمالك.</p>
          <p><code>/login +967XXXXXXXXX</code> ثم <code>/login_code</code>
          وعند الحاجة <code>/login_password</code>.</p>
        </section>
        """
        return HTMLResponse(page("جلسة Telegram", body))

    @app.get("/audit", response_class=HTMLResponse)
    async def audit_page(request: Request) -> HTMLResponse | RedirectResponse:
        redirect = require_web(request)
        if redirect:
            return redirect
        async with runtime.database.session_factory() as session:
            rows = list(
                (
                    await session.scalars(
                        select(AuditLog)
                        .where(AuditLog.account_id == runtime.settings.account_id)
                        .order_by(AuditLog.created_at.desc())
                        .limit(300)
                    )
                ).all()
            )
        body = "".join(
            "<tr>"
            f"<td>{escape(row.action)}</td>"
            f"<td>{escape(row.entity_type)}</td>"
            f"<td>{escape(str(row.entity_id or '-'))}</td>"
            f"<td>{escape(row.created_at.isoformat())}</td>"
            "</tr>"
            for row in rows
        ) or '<tr><td colspan="4">لا توجد سجلات تدقيق.</td></tr>'
        return HTMLResponse(
            page("سجل التدقيق", table(["الإجراء", "النوع", "المعرف", "الوقت"], body))
        )

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request) -> HTMLResponse | RedirectResponse:
        redirect = require_web(request)
        if redirect:
            return redirect
        snapshot = await runtime.status_snapshot()
        values = {
            "Account ID": runtime.settings.account_id,
            "Worker ID": runtime.settings.worker_id,
            "Queue batch": runtime.settings.queue_batch_size,
            "Poll interval": runtime.settings.poll_interval_seconds,
            "Retry delay": runtime.settings.retry_delay_seconds,
            "Max attempts": runtime.settings.max_retry_attempts,
            "Reconnect delay": runtime.settings.reconnect_delay_seconds,
            "Cleanup interval": runtime.settings.cleanup_interval_seconds,
            "Media root": runtime.settings.media_root,
            "Telegram connected": snapshot["telegram_connected"],
            "Web/API secrets": "configured (values hidden)",
        }
        body = "".join(
            f"<tr><td>{escape(key)}</td><td><code>{escape(value)}</code></td></tr>"
            for key, value in values.items()
        )
        return HTMLResponse(page("الإعدادات", table(["الإعداد", "القيمة"], body)))

    @app.get("/api/v1/projects")
    async def api_projects(request: Request) -> list[dict[str, Any]]:
        await require_api_auth(request)
        async with runtime.database.session_factory() as session:
            rows = (
                await session.scalars(
                    select(Project).where(Project.account_id == runtime.settings.account_id)
                )
            ).all()
        return [
            {
                "id": str(row.id),
                "name": row.name,
                "slug": row.slug,
                "status": row.status.value,
            }
            for row in rows
        ]

    @app.get("/api/v1/profiles")
    async def api_profiles(request: Request) -> dict[str, list[dict[str, Any]]]:
        await require_api_auth(request)
        values = await load_profiles(runtime)
        response: dict[str, list[dict[str, Any]]] = {}
        for key, rows in values.items():
            response[key] = [
                {
                    "id": str(item.id),
                    "name": getattr(item, "name", getattr(item, "slug", "")),
                }
                for item in rows
            ]
        return response

    @app.get("/api/v1/audit")
    async def api_audit(request: Request, limit: int = 100) -> list[dict[str, Any]]:
        await require_api_auth(request)
        limit = max(1, min(limit, 500))
        async with runtime.database.session_factory() as session:
            rows = (
                await session.scalars(
                    select(AuditLog)
                    .where(AuditLog.account_id == runtime.settings.account_id)
                    .order_by(AuditLog.created_at.desc())
                    .limit(limit)
                )
            ).all()
        return [
            {
                "id": row.id,
                "action": row.action,
                "entity_type": row.entity_type,
                "entity_id": str(row.entity_id) if row.entity_id else None,
                "details": row.details,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]
