"""Owner-only Telegram control surface for Runtime v2."""

from __future__ import annotations

import logging
import os
from typing import Any

from sqlalchemy import func, select
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from app.db.models import (
    Destination,
    JobStatus,
    PublishJob,
    Source,
    SystemEvent,
)
from app.telegram.session import TelegramSessionPasswordNeeded


class ControlBotV2:
    """Small operational bot; full configuration lives in the web dashboard."""

    COMMANDS = (
        ("start", "القائمة الرئيسية"),
        ("status", "حالة النظام"),
        ("sources", "المصادر"),
        ("destinations", "الأهداف"),
        ("queue", "طابور النشر"),
        ("run", "تشغيل الاستقبال"),
        ("pause", "إيقاف الاستقبال"),
        ("errors", "آخر الأخطاء"),
        ("dashboard", "فتح لوحة الويب"),
        ("login", "بدء تسجيل جلسة Telegram"),
        ("login_code", "إكمال رمز الدخول"),
        ("login_password", "إكمال التحقق بخطوتين"),
    )

    def __init__(
        self,
        *,
        token: str,
        owner_id: int,
        runtime: Any,
        public_base_url: str | None = None,
    ) -> None:
        self.token = token
        self.owner_id = owner_id
        self.runtime = runtime
        self.public_base_url = (public_base_url or os.getenv("PUBLIC_BASE_URL", "")).rstrip("/")
        self.application: Application | None = None
        self._login_phone: str | None = None
        self._login_code_hash: str | None = None
        self._logger = logging.getLogger(__name__)

    def build(self) -> Application:
        app = Application.builder().token(self.token).build()
        app.add_handler(CommandHandler("start", self.start_command))
        app.add_handler(CommandHandler("status", self.status_command))
        app.add_handler(CommandHandler("sources", self.sources_command))
        app.add_handler(CommandHandler("destinations", self.destinations_command))
        app.add_handler(CommandHandler("queue", self.queue_command))
        app.add_handler(CommandHandler("run", self.run_command))
        app.add_handler(CommandHandler("pause", self.pause_command))
        app.add_handler(CommandHandler("errors", self.errors_command))
        app.add_handler(CommandHandler("dashboard", self.dashboard_command))
        app.add_handler(CommandHandler("login", self.login_command))
        app.add_handler(CommandHandler("login_code", self.login_code_command))
        app.add_handler(CommandHandler("login_password", self.login_password_command))
        app.add_handler(CallbackQueryHandler(self.callback))
        self.application = app
        return app

    async def start(self) -> None:
        app = self.application or self.build()
        await app.initialize()
        commands = [
            BotCommand(command, description)
            for command, description in self.COMMANDS
        ]
        await app.bot.set_my_commands(commands)
        await app.start()
        if app.updater is None:
            raise RuntimeError("Telegram updater is unavailable")
        await app.updater.start_polling(drop_pending_updates=True)
        try:
            await app.bot.send_message(
                chat_id=self.owner_id,
                text=(
                    "✅ Control Bot V2 يعمل الآن.\n"
                    "استخدم /status للحالة و /dashboard للوحة الإدارة."
                ),
                reply_markup=self.keyboard(),
            )
        except Exception:
            self._logger.warning("Control Bot V2 startup notification failed", exc_info=True)
        self._logger.info("Control Bot V2 polling started")

    async def stop(self) -> None:
        app = self.application
        if app is None:
            return
        if app.updater is not None and app.updater.running:
            await app.updater.stop()
        if app.running:
            await app.stop()
        await app.shutdown()
        self._logger.info("Control Bot V2 stopped")

    def keyboard(self) -> InlineKeyboardMarkup:
        rows = [
            [
                InlineKeyboardButton("📊 الحالة", callback_data="status"),
                InlineKeyboardButton("📚 المصادر", callback_data="sources"),
            ],
            [
                InlineKeyboardButton("📤 الطابور", callback_data="queue"),
                InlineKeyboardButton("▶️ تشغيل", callback_data="run"),
                InlineKeyboardButton("⏸ إيقاف", callback_data="pause"),
            ],
        ]
        if self.public_base_url:
            rows.append([InlineKeyboardButton("🌐 لوحة الإدارة", url=self.public_base_url)])
        return InlineKeyboardMarkup(rows)

    async def _authorized(self, update: Update) -> bool:
        user = update.effective_user
        if user is None or user.id != self.owner_id:
            if update.effective_message is not None:
                await update.effective_message.reply_text("غير مصرح.")
            return False
        return True

    async def start_command(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        await update.effective_message.reply_text(
            "Telegram Relay V2\n\n"
            "البوت مخصص للتشغيل والمتابعة السريعة. الإعداد الكامل من لوحة الويب.",
            reply_markup=self.keyboard(),
        )

    async def status_command(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        await update.effective_message.reply_text(
            await self.status_text(),
            reply_markup=self.keyboard(),
        )

    async def status_text(self) -> str:
        status = await self.runtime.status_snapshot()
        return (
            "📊 حالة Runtime V2\n"
            f"• PostgreSQL: {'✅' if status['database'] else '❌'}\n"
            f"• Telegram User: {'✅' if status['telegram_connected'] else '❌'}\n"
            f"• Listener: {'✅' if status['listener_running'] else '⏸'}\n"
            f"• Worker: {'✅' if status['worker_running'] else '❌'}\n"
            f"• المصادر المراقبة: {status['source_count']}\n"
            f"• الحالة: {'متوقف مؤقتًا' if status['paused'] else 'يعمل'}"
            + (f"\n• آخر خطأ: {status['last_error']}" if status.get("last_error") else "")
        )

    async def sources_command(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        await update.effective_message.reply_text(await self.sources_text())

    async def sources_text(self) -> str:
        async with self.runtime.database.session_factory() as session:
            rows = list(
                (
                    await session.scalars(
                        select(Source)
                        .where(Source.account_id == self.runtime.settings.account_id)
                        .order_by(Source.title)
                    )
                ).all()
            )
        if not rows:
            return "📚 لا توجد مصادر."
        lines = ["📚 المصادر:"]
        for source in rows[:30]:
            lines.append(f"• {source.title} — {source.status.value}")
        return "\n".join(lines)

    async def destinations_command(
        self,
        update: Update,
        _context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not await self._authorized(update):
            return
        async with self.runtime.database.session_factory() as session:
            rows = list(
                (
                    await session.scalars(
                        select(Destination)
                        .where(Destination.account_id == self.runtime.settings.account_id)
                        .order_by(Destination.name)
                    )
                ).all()
            )
        text = "🎯 لا توجد أهداف." if not rows else "🎯 الأهداف:\n" + "\n".join(
            f"• {row.name} — {row.status.value}" for row in rows[:30]
        )
        await update.effective_message.reply_text(text)

    async def queue_command(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        await update.effective_message.reply_text(await self.queue_text())

    async def queue_text(self) -> str:
        async with self.runtime.database.session_factory() as session:
            counts = {
                status.value: int(
                    await session.scalar(
                        select(func.count(PublishJob.id)).where(
                            PublishJob.account_id == self.runtime.settings.account_id,
                            PublishJob.status == status,
                        )
                    )
                    or 0
                )
                for status in (
                    JobStatus.QUEUED,
                    JobStatus.PROCESSING,
                    JobStatus.RETRY_WAIT,
                    JobStatus.PUBLISHED,
                    JobStatus.FAILED,
                )
            }
        return (
            "📤 طابور النشر\n"
            f"• انتظار: {counts['queued']}\n"
            f"• معالجة: {counts['processing']}\n"
            f"• إعادة محاولة: {counts['retry_wait']}\n"
            f"• منشور: {counts['published']}\n"
            f"• فشل: {counts['failed']}"
        )

    async def run_command(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        try:
            await self.runtime.resume_ingestion()
        except Exception as exc:
            await update.effective_message.reply_text(
                f"تعذر تشغيل الاستقبال: {type(exc).__name__}. استخدم /status أو /login."
            )
            return
        await update.effective_message.reply_text("✅ تم تشغيل الاستقبال.")

    async def pause_command(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        await self.runtime.pause_ingestion()
        await update.effective_message.reply_text(
            "⏸ تم إيقاف الاستقبال. سيبقى Worker واللوحة يعملان."
        )

    async def errors_command(
        self,
        update: Update,
        _context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not await self._authorized(update):
            return
        async with self.runtime.database.session_factory() as session:
            rows = list(
                (
                    await session.scalars(
                        select(SystemEvent)
                        .where(
                            SystemEvent.account_id == self.runtime.settings.account_id,
                            SystemEvent.severity.in_(["warning", "error", "critical"]),
                        )
                        .order_by(SystemEvent.created_at.desc())
                        .limit(10)
                    )
                ).all()
            )
        if not rows:
            text = "✅ لا توجد أخطاء مسجلة."
        else:
            text = "⚠️ آخر الأخطاء:\n" + "\n".join(
                f"• {row.event_type}: {row.error_code or row.severity}" for row in rows
            )
        await update.effective_message.reply_text(text)

    async def dashboard_command(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        if not self.public_base_url:
            await update.effective_message.reply_text("لم يتم ضبط PUBLIC_BASE_URL بعد.")
            return
        await update.effective_message.reply_text(
            "🌐 لوحة الإدارة:",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("فتح لوحة V2", url=self.public_base_url)]]
            ),
        )

    async def login_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        if not context.args:
            await update.effective_message.reply_text("الاستخدام: /login +967XXXXXXXXX")
            return
        phone = context.args[0]
        try:
            sent = await self.runtime.client_manager.session.request_login_code(phone)
        except Exception as exc:
            await update.effective_message.reply_text(f"تعذر إرسال الرمز: {type(exc).__name__}")
            return
        self._login_phone = phone
        self._login_code_hash = sent.phone_code_hash
        await update.effective_message.reply_text(
            "✅ تم إرسال رمز Telegram. استخدم /login_code 12345"
        )

    async def login_code_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not await self._authorized(update):
            return
        if not context.args or not self._login_phone or not self._login_code_hash:
            await update.effective_message.reply_text("ابدأ أولًا بـ /login +967XXXXXXXXX")
            return
        try:
            await self.runtime.client_manager.session.complete_login(
                phone=self._login_phone,
                code=context.args[0],
                phone_code_hash=self._login_code_hash,
            )
        except TelegramSessionPasswordNeeded:
            await update.effective_message.reply_text(
                "الحساب يتطلب التحقق بخطوتين. استخدم /login_password ثم كلمة المرور."
            )
            return
        except Exception as exc:
            await update.effective_message.reply_text(f"فشل تسجيل الدخول: {type(exc).__name__}")
            return
        await self._after_login(update)

    async def login_password_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._authorized(update):
            return
        if not context.args:
            await update.effective_message.reply_text("الاستخدام: /login_password كلمة_المرور")
            return
        password = " ".join(context.args)
        try:
            await self.runtime.client_manager.session.complete_login_password(password=password)
        except Exception as exc:
            await update.effective_message.reply_text(f"فشل التحقق: {type(exc).__name__}")
            return
        finally:
            try:
                if update.effective_message is not None:
                    await update.effective_message.delete()
            except Exception:
                pass
        await self._after_login(update)

    async def _after_login(self, update: Update) -> None:
        self._login_phone = None
        self._login_code_hash = None
        try:
            await self.runtime.resume_ingestion()
        except Exception as exc:
            await update.effective_chat.send_message(
                f"✅ تم تسجيل الجلسة، لكن تشغيل Listener فشل: {type(exc).__name__}"
            )
            return
        await update.effective_chat.send_message("✅ تم تسجيل الجلسة وتشغيل Runtime V2.")

    async def callback(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        query = update.callback_query
        if query is None:
            return
        await query.answer()
        action = query.data or ""
        if action == "status":
            text = await self.status_text()
        elif action == "sources":
            text = await self.sources_text()
        elif action == "queue":
            text = await self.queue_text()
        elif action == "run":
            try:
                await self.runtime.resume_ingestion()
                text = "✅ تم تشغيل الاستقبال."
            except Exception as exc:
                text = f"تعذر التشغيل: {type(exc).__name__}"
        elif action == "pause":
            await self.runtime.pause_ingestion()
            text = "⏸ تم إيقاف الاستقبال."
        else:
            text = "أمر غير معروف."
        await query.edit_message_text(text=text, reply_markup=self.keyboard())
