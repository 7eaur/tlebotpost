"""Owner-only Telegram control surface for V3."""

from __future__ import annotations

import logging
from typing import Any

from telegram import BotCommand, Update
from telegram.constants import ChatType
from telegram.ext import Application, CommandHandler, ContextTypes

from .errors import ControlServiceError
from .service import ControlServiceV3


_COMMANDS = (
    ("start", "القائمة والمساعدة"),
    ("status", "حالة V3 والطابور"),
    ("health", "حالة الاتصال وآخر خطأ"),
    ("projects", "عرض المشاريع"),
    ("sources", "عرض المصادر"),
    ("destinations", "عرض الوجهات"),
    ("routes", "عرض المسارات"),
    ("addsource", "إضافة مصدر: /addsource @channel"),
    ("source_on", "تفعيل مصدر بواسطة UUID"),
    ("source_off", "إيقاف مصدر بواسطة UUID"),
    ("adddestination", "إضافة وجهة: /adddestination @channel [project]"),
    ("destination_on", "تفعيل وجهة بواسطة UUID"),
    ("destination_off", "إيقاف وجهة بواسطة UUID"),
    ("addroute", "ربط مصدر بوجهة بواسطة UUID"),
    ("route_on", "تفعيل مسار بواسطة UUID"),
    ("route_off", "إيقاف مسار بواسطة UUID"),
    ("run", "تشغيل Project"),
    ("pause", "إيقاف Project مؤقتًا"),
    ("release", "تحرير manual PublishJob"),
    ("reload", "إعادة تحميل الاشتراكات بأمان"),
)


class ControlBotV3:
    """Thin Telegram command adapter over ControlServiceV3."""

    name = "control-bot-v3"

    def __init__(
        self,
        *,
        token: str,
        owner_id: int,
        service: ControlServiceV3,
    ) -> None:
        if not token.strip():
            raise ValueError("control bot token is required")
        if owner_id <= 0:
            raise ValueError("owner_id must be positive")
        self.token = token
        self.owner_id = owner_id
        self.service = service
        self.application: Application | None = None
        self._logger = logging.getLogger(__name__)

    def build_application(self) -> Application:
        application = Application.builder().token(self.token).build()
        handlers = {
            "start": self.start_command,
            "help": self.start_command,
            "status": self.status_command,
            "health": self.status_command,
            "projects": self.projects_command,
            "sources": self.sources_command,
            "destinations": self.destinations_command,
            "routes": self.routes_command,
            "addsource": self.add_source_command,
            "source_on": self.source_on_command,
            "source_off": self.source_off_command,
            "adddestination": self.add_destination_command,
            "destination_on": self.destination_on_command,
            "destination_off": self.destination_off_command,
            "addroute": self.add_route_command,
            "route_on": self.route_on_command,
            "route_off": self.route_off_command,
            "run": self.run_command,
            "pause": self.pause_command,
            "release": self.release_command,
            "reload": self.reload_command,
        }
        for name, callback in handlers.items():
            application.add_handler(CommandHandler(name, callback))
        self.application = application
        return application

    async def start(self) -> None:
        if self.application is not None:
            return
        application = self.build_application()
        await application.initialize()
        await application.bot.set_my_commands(
            [BotCommand(command=name, description=description) for name, description in _COMMANDS]
        )
        await application.start()
        if application.updater is None:
            await application.stop()
            await application.shutdown()
            self.application = None
            raise RuntimeError("control bot polling updater is unavailable")
        await application.updater.start_polling(drop_pending_updates=False)
        self._logger.info("V3 control bot started")

    async def stop(self) -> None:
        application = self.application
        if application is None:
            return
        try:
            if application.updater is not None and application.updater.running:
                await application.updater.stop()
            if application.running:
                await application.stop()
        finally:
            await application.shutdown()
            self.application = None
        self._logger.info("V3 control bot stopped")

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        await self._reply(
            update,
            "🛠 Telegram Relay V3\n\n"
            "الحالة: /status\n"
            "المشاريع: /projects\n"
            "المصادر: /sources\n"
            "الوجهات: /destinations\n"
            "المسارات: /routes\n\n"
            "إضافة مصدر: /addsource @channel\n"
            "إضافة وجهة: /adddestination @channel [project-slug]\n"
            "إنشاء مسار: /addroute SOURCE_UUID DESTINATION_UUID\n\n"
            "إيقاف/تشغيل مشروع: /pause [project] | /run [project]\n"
            "تفعيل/إيقاف العناصر: source_on/source_off، "
            "destination_on/destination_off، route_on/route_off\n"
            "تحرير Manual Job: /release JOB_UUID\n"
            "Reload آمن: /reload",
        )

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        try:
            status = await self.service.status()
        except Exception as exc:
            await self._operation_error(update, exc)
            return
        await self._reply(
            update,
            "📊 حالة V3\n"
            f"• Telegram account: {status.telegram_status}\n"
            f"• User session: {'متصلة' if status.telegram_connected else 'غير متصلة'}\n"
            f"• Projects: {status.projects_active}/{status.projects_total} فعالة\n"
            f"• Sources: {status.sources_active}/{status.sources_total} فعالة\n"
            f"• Destinations: {status.destinations_active}/{status.destinations_total} فعالة\n"
            f"• Routes: {status.routes_active}/{status.routes_total} فعالة\n"
            f"• Queue pending: {status.queue_pending}\n"
            f"• Queue failed: {status.queue_failed}\n"
            f"• Telegram last error: {status.telegram_last_error or 'لا يوجد'}\n"
            f"• Publish last error: {status.last_publish_error or 'لا يوجد'}",
        )

    async def projects_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        try:
            rows = await self.service.list_projects()
        except Exception as exc:
            await self._operation_error(update, exc)
            return
        if not rows:
            await self._reply(update, "لا توجد Projects في الحساب.")
            return
        await self._reply(
            update,
            "📁 Projects\n\n"
            + "\n\n".join(
                f"{_status_icon(row.status)} {row.name} ({row.slug})\n{row.id}\nstatus={row.status}"
                for row in rows
            ),
        )

    async def sources_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        try:
            rows = await self.service.list_sources()
        except Exception as exc:
            await self._operation_error(update, exc)
            return
        if not rows:
            await self._reply(update, "لا توجد مصادر. استخدم /addsource.")
            return
        await self._reply(
            update,
            "📚 Sources\n\n"
            + "\n\n".join(
                f"{_status_icon(row.status)} {row.title}\n"
                f"{row.id}\nchat={row.chat_id}\n"
                f"seen={row.last_seen_message_id}\nstatus={row.status}"
                for row in rows
            ),
        )

    async def destinations_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not await self._authorized(update):
            return
        try:
            rows = await self.service.list_destinations()
        except Exception as exc:
            await self._operation_error(update, exc)
            return
        if not rows:
            await self._reply(update, "لا توجد وجهات. استخدم /adddestination.")
            return
        await self._reply(
            update,
            "🎯 Destinations\n\n"
            + "\n\n".join(
                f"{_status_icon(row.status)} {row.name}\n"
                f"{row.id}\nproject={row.project_id}\nchat={row.chat_id}\n"
                f"mode={row.publishing_mode}\nstatus={row.status}"
                for row in rows
            ),
        )

    async def routes_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        try:
            rows = await self.service.list_routes()
        except Exception as exc:
            await self._operation_error(update, exc)
            return
        if not rows:
            await self._reply(update, "لا توجد Routes. استخدم /addroute.")
            return
        await self._reply(
            update,
            "🔀 Routes\n\n"
            + "\n\n".join(
                f"{_status_icon(row.status)} {row.id}\n"
                f"source={row.source_id}\ndestination={row.destination_id}\n"
                f"priority={row.priority}\nstatus={row.status}"
                for row in rows
            ),
        )

    async def add_source_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not await self._authorized(update):
            return
        if not context.args:
            await self._reply(update, "الاستخدام: /addsource @channel")
            return
        try:
            row = await self.service.add_source(" ".join(context.args))
        except Exception as exc:
            await self._operation_error(update, exc)
            return
        await self._reply(
            update,
            "✅ تم حفظ المصدر وBaseline الحالي\n"
            f"{row.title}\n{row.id}\n"
            f"last_seen={row.last_seen_message_id}",
        )

    async def source_on_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._set_source(update, context, True)

    async def source_off_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._set_source(update, context, False)

    async def _set_source(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        active: bool,
    ) -> None:
        if not await self._authorized(update):
            return
        if len(context.args) != 1:
            await self._reply(update, "الاستخدام: /source_on UUID أو /source_off UUID")
            return
        try:
            row = await self.service.set_source_active(context.args[0], active=active)
        except Exception as exc:
            await self._operation_error(update, exc)
            return
        await self._reply(update, f"✅ Source {row.status}: {row.title}\n{row.id}")

    async def add_destination_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not await self._authorized(update):
            return
        if not context.args or len(context.args) > 2:
            await self._reply(
                update,
                "الاستخدام: /adddestination @channel [project-slug-or-UUID]",
            )
            return
        try:
            row = await self.service.add_destination(
                context.args[0],
                project_selector=context.args[1] if len(context.args) == 2 else None,
            )
        except Exception as exc:
            await self._operation_error(update, exc)
            return
        await self._reply(
            update,
            "✅ تم حفظ الوجهة\n"
            f"{row.name}\n{row.id}\nproject={row.project_id}",
        )

    async def destination_on_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        await self._set_destination(update, context, True)

    async def destination_off_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        await self._set_destination(update, context, False)

    async def _set_destination(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        active: bool,
    ) -> None:
        if not await self._authorized(update):
            return
        if len(context.args) != 1:
            await self._reply(
                update,
                "الاستخدام: /destination_on UUID أو /destination_off UUID",
            )
            return
        try:
            row = await self.service.set_destination_active(context.args[0], active=active)
        except Exception as exc:
            await self._operation_error(update, exc)
            return
        await self._reply(update, f"✅ Destination {row.status}: {row.name}\n{row.id}")

    async def add_route_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not await self._authorized(update):
            return
        if len(context.args) != 2:
            await self._reply(update, "الاستخدام: /addroute SOURCE_UUID DESTINATION_UUID")
            return
        try:
            row = await self.service.add_route(context.args[0], context.args[1])
        except Exception as exc:
            await self._operation_error(update, exc)
            return
        await self._reply(
            update,
            "✅ Route active\n"
            f"{row.id}\nsource={row.source_id}\ndestination={row.destination_id}",
        )

    async def route_on_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._set_route(update, context, True)

    async def route_off_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._set_route(update, context, False)

    async def _set_route(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        active: bool,
    ) -> None:
        if not await self._authorized(update):
            return
        if len(context.args) != 1:
            await self._reply(update, "الاستخدام: /route_on UUID أو /route_off UUID")
            return
        try:
            row = await self.service.set_route_active(context.args[0], active=active)
        except Exception as exc:
            await self._operation_error(update, exc)
            return
        await self._reply(update, f"✅ Route {row.status}: {row.id}")

    async def run_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._set_project(update, context, True)

    async def pause_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._set_project(update, context, False)

    async def _set_project(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        active: bool,
    ) -> None:
        if not await self._authorized(update):
            return
        if len(context.args) > 1:
            await self._reply(update, "الاستخدام: /run [project] أو /pause [project]")
            return
        try:
            row = await self.service.set_project_active(
                project_selector=context.args[0] if context.args else None,
                active=active,
            )
        except Exception as exc:
            await self._operation_error(update, exc)
            return
        await self._reply(update, f"✅ Project {row.status}: {row.name} ({row.slug})")

    async def release_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        if len(context.args) != 1:
            await self._reply(update, "الاستخدام: /release JOB_UUID")
            return
        try:
            job_id = await self.service.release_manual_job(context.args[0])
        except Exception as exc:
            await self._operation_error(update, exc)
            return
        await self._reply(update, f"✅ تم تحرير Manual Job: {job_id}")

    async def reload_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        try:
            await self.service.reload_runtime()
        except Exception as exc:
            await self._operation_error(update, exc)
            return
        await self._reply(update, "✅ تم Reload آمن لاشتراكات V3.")

    async def _authorized(self, update: Update) -> bool:
        user = update.effective_user
        chat = update.effective_chat
        allowed = (
            user is not None
            and int(user.id) == self.owner_id
            and chat is not None
            and chat.type == ChatType.PRIVATE
        )
        if not allowed:
            if update.effective_message is not None:
                await update.effective_message.reply_text("غير مصرح.")
            return False
        return True

    async def _operation_error(self, update: Update, exc: Exception) -> None:
        if isinstance(exc, ControlServiceError):
            message = _friendly_control_error(str(exc))
        else:
            self._logger.exception("V3 control command failed")
            message = "حدث خطأ داخلي. راجع السجل وحالة النظام."
        await self._reply(update, f"❌ {message}")

    @staticmethod
    async def _reply(update: Update, text: str) -> None:
        if update.effective_message is not None:
            await update.effective_message.reply_text(text)


def _status_icon(status: str) -> str:
    return "✅" if status == "active" else "⏸️"


def _friendly_control_error(code: str) -> str:
    return {
        "telegram_account_unavailable": "حساب Telegram غير متاح لهذا الحساب.",
        "telegram_reference_required": "أرسل مرجع القناة أو المجموعة.",
        "telegram_reference_unavailable": "تعذر الوصول إلى مرجع Telegram بواسطة جلسة المستخدم.",
        "source_id_invalid": "معرف Source غير صالح.",
        "source_not_found": "Source غير موجود.",
        "destination_id_invalid": "معرف Destination غير صالح.",
        "destination_not_found": "Destination غير موجود.",
        "route_id_invalid": "معرف Route غير صالح.",
        "route_not_found": "Route غير موجود.",
        "project_not_found": "Project غير موجود.",
        "active_project_not_found": "لا يوجد Project فعال.",
        "project_selector_required": "يوجد أكثر من Project؛ حدد slug أو UUID.",
        "job_id_invalid": "معرف PublishJob غير صالح.",
        "runtime_reload_unavailable": "Reload غير متاح في هذا التشغيل.",
        "configuration_saved_runtime_reload_failed": (
            "تم حفظ الإعداد، لكن Reload فشل. استخدم /reload بعد فحص /status."
        ),
    }.get(code, code)
