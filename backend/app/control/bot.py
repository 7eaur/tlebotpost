"""Owner-only Telegram control bot."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from app.control.resolver import ResolvedChat, TelegramChatResolver
from app.repositories.settings import SettingsRepository
from app.repositories.sources import SourceRepository


class ControlBot:
    """Manage relay configuration through a private owner-only bot."""

    BUTTON_STATUS = "الحالة"
    BUTTON_SOURCES = "المصادر"
    BUTTON_ADD_SOURCE = "إضافة مصدر"
    BUTTON_TARGET = "القناة الهدف"
    BUTTON_FILTERS = "الفلاتر"
    BUTTON_START = "تشغيل"
    BUTTON_STOP = "إيقاف"
    BUTTON_HELP = "المساعدة"

    def __init__(
        self,
        *,
        token: str,
        owner_id: int,
        sources: SourceRepository,
        settings: SettingsRepository,
        resolver: TelegramChatResolver,
        runtime: Any,
        session: Any | None = None,
    ) -> None:
        self.token = token
        self.owner_id = owner_id
        self.sources = sources
        self.settings = settings
        self.resolver = resolver
        self.runtime = runtime
        self.session = session
        self._login_phone: str | None = None
        self._login_code_hash: str | None = None
        self.application: Application | None = None

    def build_application(self) -> Application:
        """Build handlers without starting network polling."""
        application = Application.builder().token(self.token).build()
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("help", self.help))
        application.add_handler(CommandHandler("status", self.status))
        application.add_handler(CommandHandler("sources", self.list_sources))
        application.add_handler(CommandHandler("addsource", self.add_source))
        application.add_handler(CommandHandler("removesource", self.remove_source))
        application.add_handler(CommandHandler("source_on", self.enable_source))
        application.add_handler(CommandHandler("source_off", self.disable_source))
        application.add_handler(CommandHandler("settarget", self.set_target))
        application.add_handler(CommandHandler("include", self.set_include))
        application.add_handler(CommandHandler("exclude", self.set_exclude))
        application.add_handler(CommandHandler("mediatypes", self.set_media_types))
        application.add_handler(CommandHandler("login", self.login))
        application.add_handler(CommandHandler("login_code", self.login_code))
        application.add_handler(CommandHandler("run", self.start_relay))
        application.add_handler(CommandHandler("pause", self.stop_relay))
        application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.menu_message)
        )
        self.application = application
        return application

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        await self._reply(
            update,
            "مرحبًا. هذا بوت التحكم بنظام إعادة النشر اللحظي.\n"
            "استخدم الأزرار أو /help لعرض الأوامر.",
        )

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        await self._reply(
            update,
            "الأوامر المتاحة:\n"
            "/addsource <قناة> — إضافة مصدر والبدء من أحدث رسالة\n"
            "/removesource <قناة> — إزالة مصدر\n"
            "/source_on <قناة> و /source_off <قناة>\n"
            "/settarget <قناة> — تحديد قناة الهدف\n"
            "/include <كلمات> و /exclude <كلمات>\n"
            "/mediatypes <photo,video,document>\n"
            "/login <phone> ثم /login_code <code> — تسجيل جلسة الحساب\n"
            "/run و /pause — تشغيل وإيقاف النظام\n"
            "/status و /sources",
        )

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        config = await self.settings.get()
        source_list = await self.sources.list()
        active = sum(source.enabled for source in source_list)
        runtime_state = "يعمل" if self.runtime.is_running else "متوقف"
        target = config.target_ref or "غير محددة"
        await self._reply(
            update,
            f"حالة النظام: {runtime_state}\n"
            f"المصادر: {active}/{len(source_list)} فعالة\n"
            f"القناة الهدف: {target}\n"
            f"التشغيل العام: {'مفعّل' if config.enabled else 'متوقف'}",
        )

    async def list_sources(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        source_list = await self.sources.list()
        if not source_list:
            await self._reply(update, "لا توجد مصادر مضافة حاليًا.")
            return
        lines = [
            f"{'✅' if source.enabled else '⏸️'} {source.title} — {source.input_ref}"
            for source in source_list
        ]
        await self._reply(update, "المصادر:\n" + "\n".join(lines))

    async def add_source(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        if not context.args:
            await self._reply(update, "الاستخدام: /addsource @channel أو رابط القناة")
            return
        try:
            chat = await self.resolver.resolve(context.args[0])
            source = await self.sources.upsert(
                chat_id=chat.chat_id,
                input_ref=chat.input_ref,
                title=chat.title,
                username=chat.username,
                baseline_message_id=chat.latest_message_id,
            )
        except Exception as exc:
            await self._reply(update, f"تعذر إضافة المصدر: {type(exc).__name__}")
            return
        await self._reply(
            update,
            f"تمت إضافة {source.title}. سيبدأ النظام من الرسائل الجديدة فقط "
            f"بعد الرسالة {source.baseline_message_id}.",
        )

    async def remove_source(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        chat = await self._resolve_argument(update, context, "removesource")
        if chat is None:
            return
        removed = await self.sources.delete(chat.chat_id)
        await self._reply(update, "تم حذف المصدر." if removed else "المصدر غير موجود.")

    async def enable_source(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._set_source_enabled(update, context, True)

    async def disable_source(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._set_source_enabled(update, context, False)

    async def set_target(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        if not context.args:
            await self._reply(update, "الاستخدام: /settarget @my_channel")
            return
        try:
            chat = await self.resolver.resolve(context.args[0])
            await self.settings.update_target(chat.input_ref, chat.chat_id)
        except Exception as exc:
            await self._reply(update, f"تعذر تحديد القناة الهدف: {type(exc).__name__}")
            return
        await self._reply(update, f"تم تحديد القناة الهدف: {chat.title}")

    async def set_include(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._set_keywords(update, context, include=True)

    async def set_exclude(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._set_keywords(update, context, include=False)

    async def set_media_types(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        if not context.args:
            await self._reply(update, "الاستخدام: /mediatypes photo,video أو /mediatypes all")
            return
        config = await self.settings.get()
        media_types = _csv_args(context.args)
        await self.settings.update_filters(
            include_keywords=config.include_keywords,
            exclude_keywords=config.exclude_keywords,
            allowed_media_types=() if media_types == ("all",) else media_types,
        )
        await self._reply(update, "تم تحديث فلتر أنواع الوسائط.")

    async def login(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Request a user-account login code without persisting it."""
        if not await self._authorized(update):
            return
        if self.session is None:
            await self._reply(update, "تسجيل الدخول غير مهيأ في هذا التشغيل.")
            return
        if not context.args:
            await self._reply(update, "الاستخدام: /login +967XXXXXXXXX")
            return
        try:
            sent = await self.session.request_login_code(context.args[0])
            self._login_phone = context.args[0]
            self._login_code_hash = sent.phone_code_hash
        except Exception as exc:
            await self._reply(update, f"تعذر إرسال رمز الدخول: {type(exc).__name__}")
            return
        await self._reply(update, "تم إرسال الرمز. أرسل /login_code <الرمز> [كلمة_المرور]")

    async def login_code(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Complete the pending user-account login flow."""
        if not await self._authorized(update):
            return
        if self.session is None or not self._login_phone or not self._login_code_hash:
            await self._reply(update, "لا توجد عملية تسجيل دخول معلقة.")
            return
        if not context.args:
            await self._reply(update, "الاستخدام: /login_code 12345 [كلمة_المرور]")
            return
        try:
            await self.session.complete_login(
                phone=self._login_phone,
                code=context.args[0],
                phone_code_hash=self._login_code_hash,
                password=context.args[1] if len(context.args) > 1 else None,
            )
        except Exception as exc:
            await self._reply(update, f"تعذر إكمال الدخول: {type(exc).__name__}")
            return
        self._login_phone = None
        self._login_code_hash = None
        await self._reply(update, "تم تسجيل دخول حساب Telegram بنجاح.")

    async def start_relay(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        await self.settings.set_enabled(True)
        try:
            await self.runtime.start()
        except Exception as exc:
            await self.settings.set_enabled(False)
            await self._reply(update, f"تعذر تشغيل النظام: {type(exc).__name__}")
            return
        await self._reply(update, "تم تشغيل النظام.")

    async def stop_relay(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        await self.runtime.stop()
        await self.settings.set_enabled(False)
        await self._reply(update, "تم إيقاف النظام.")

    async def menu_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        text = update.effective_message.text if update.effective_message else ""
        actions = {
            self.BUTTON_STATUS: self.status,
            self.BUTTON_SOURCES: self.list_sources,
            self.BUTTON_HELP: self.help,
            self.BUTTON_START: self.start_relay,
            self.BUTTON_STOP: self.stop_relay,
        }
        action = actions.get(text)
        if action:
            await action(update, context)
        elif text == self.BUTTON_ADD_SOURCE:
            await self._reply(update, "أرسل: /addsource @channel أو رابط القناة")
        elif text == self.BUTTON_TARGET:
            await self._reply(update, "أرسل: /settarget @my_channel")
        elif text == self.BUTTON_FILTERS:
            await self._reply(
                update,
                "الفلاتر:\n/include خبر,تقنية\n/exclude إعلان\n"
                "/mediatypes photo,video أو /mediatypes all",
            )
        else:
            await self._reply(update, "استخدم /help أو اختر زرًا من القائمة.")

    async def _set_source_enabled(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, enabled: bool
    ) -> None:
        if not await self._authorized(update):
            return
        chat = await self._resolve_argument(update, context, "source")
        if chat is None:
            return
        changed = await self.sources.set_enabled(chat.chat_id, enabled)
        state = "تفعيله" if enabled else "إيقافه"
        await self._reply(update, f"تم {state}." if changed else "المصدر غير موجود.")

    async def _set_keywords(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, include: bool
    ) -> None:
        if not await self._authorized(update):
            return
        config = await self.settings.get()
        values = _csv_args(context.args)
        await self.settings.update_filters(
            include_keywords=values if include else config.include_keywords,
            exclude_keywords=config.exclude_keywords if include else values,
            allowed_media_types=config.allowed_media_types,
        )
        await self._reply(update, "تم تحديث فلتر الكلمات.")

    async def _resolve_argument(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, command: str
    ) -> ResolvedChat | None:
        if not context.args:
            await self._reply(update, f"الاستخدام: /{command} @channel")
            return None
        try:
            return await self.resolver.resolve(context.args[0])
        except Exception as exc:
            await self._reply(update, f"تعذر حل القناة: {type(exc).__name__}")
            return None

    async def _authorized(self, update: Update) -> bool:
        user = update.effective_user
        if user is None or user.id != self.owner_id:
            if update.effective_message:
                await update.effective_message.reply_text("غير مصرح لك باستخدام هذا البوت.")
            return False
        return True

    async def _reply(self, update: Update, text: str) -> None:
        if update.effective_message:
            await update.effective_message.reply_text(text, reply_markup=self.keyboard())

    @classmethod
    def keyboard(cls) -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            [
                [cls.BUTTON_STATUS, cls.BUTTON_SOURCES],
                [cls.BUTTON_ADD_SOURCE, cls.BUTTON_TARGET],
                [cls.BUTTON_FILTERS, cls.BUTTON_START, cls.BUTTON_STOP],
                [cls.BUTTON_HELP],
            ],
            resize_keyboard=True,
        )

    async def run_polling(self) -> None:
        """Build and run polling; intended for the production entry point."""
        application = self.application or self.build_application()
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        try:
            await asyncio.Event().wait()
        finally:
            await application.updater.stop()
            await application.stop()
            await application.shutdown()


def _csv_args(args: Sequence[str]) -> tuple[str, ...]:
    return tuple(item.strip() for arg in args for item in arg.split(",") if item.strip())
