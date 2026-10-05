"""Owner-only Telegram control bot with guided configuration flows."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from app.control.errors import friendly_error
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
    BUTTON_LOGIN = "تسجيل جلسة الحساب"
    BUTTON_START = "تشغيل"
    BUTTON_STOP = "إيقاف"
    BUTTON_HELP = "المساعدة"
    BUTTON_CANCEL = "إلغاء"

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
        self._pending_action: str | None = None
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
        application.add_handler(CommandHandler("cancel", self.cancel))
        application.add_handler(CommandHandler("run", self.start_relay))
        application.add_handler(CommandHandler("pause", self.stop_relay))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.menu_message))
        self.application = application
        return application

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        await self._reply(
            update,
            "مرحبًا بك في نظام إعادة النشر اللحظي.\n\n"
            "ابدأ بالترتيب التالي:\n"
            "1) سجّل جلسة الحساب\n"
            "2) أضف قناة مصدر\n"
            "3) حدّد القناة الهدف\n"
            "4) اضغط تشغيل\n\n"
            "استخدم الأزرار أو /help للمساعدة.",
        )

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        await self._reply(
            update,
            "دليل الاستخدام المتسلسل:\n\n"
            "1️⃣ /login ثم أرسل رقم الهاتف الدولي\n"
            "2️⃣ أرسل رمز Telegram عبر /login_code\n"
            "3️⃣ /addsource ثم أرسل @channel أو رابط t.me\n"
            "4️⃣ /settarget ثم أرسل @channel أو رابطها\n"
            "5️⃣ /run لبدء المراقبة\n\n"
            "أوامر الإدارة:\n"
            "/status — الحالة الحالية\n"
            "/sources — قائمة المصادر\n"
            "/source_on و /source_off — تفعيل/إيقاف مصدر\n"
            "/removesource — حذف مصدر\n"
            "/include و /exclude — فلاتر الكلمات\n"
            "/mediatypes — أنواع الوسائط\n"
            "/pause — إيقاف آمن\n"
            "/cancel — إلغاء الخطوة الحالية\n\n"
            "يمكنك إرسال الرابط كاملًا مثل https://t.me/example، "
            "وليس مطلوبًا حفظ الأوامر في كل مرة؛ الأزرار تفتح الخطوة التالية.",
        )

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        config = await self.settings.get()
        source_list = await self.sources.list()
        active = sum(source.enabled for source in source_list)
        runtime_state = "يعمل" if self.runtime.is_running else "متوقف"
        target = config.target_ref or "غير محددة"
        session_state = (
            "جاهزة" if self.session and self.session.client.is_connected() else "غير متصلة"
        )
        await self._reply(
            update,
            "📊 الحالة الحالية\n"
            f"• التشغيل: {runtime_state}\n"
            f"• جلسة الحساب: {session_state}\n"
            f"• المصادر: {active}/{len(source_list)} فعالة\n"
            f"• القناة الهدف: {target}\n"
            f"• الإعداد العام: {'مفعّل' if config.enabled else 'متوقف'}",
        )

    async def list_sources(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        source_list = await self.sources.list()
        if not source_list:
            await self._reply(update, "📭 لا توجد مصادر مضافة. اضغط «إضافة مصدر» للبدء.")
            return
        lines = [
            f"{'✅' if source.enabled else '⏸️'} {source.title}\n   {source.input_ref}"
            for source in source_list
        ]
        await self._reply(update, "📚 المصادر الحالية:\n\n" + "\n".join(lines))

    async def add_source(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        if not context.args:
            self._pending_action = "add_source"
            await self._reply(
                update,
                "أرسل الآن رابط المصدر أو اسم المستخدم، مثل:\n"
                "• @channel\n• https://t.me/channel\n• رابط القناة الخاصة بعد انضمام الحساب\n\n"
                "أرسل /cancel للإلغاء.",
            )
            return
        await self._process_add_source(update, " ".join(context.args))

    async def _process_add_source(self, update: Update, input_ref: str) -> None:
        try:
            chat = await self.resolver.resolve(input_ref)
            source = await self.sources.upsert(
                chat_id=chat.chat_id,
                input_ref=chat.input_ref,
                title=chat.title,
                username=chat.username,
                baseline_message_id=chat.latest_message_id,
            )
        except Exception as exc:
            await self._reply(
                update, "❌ لم تتم إضافة المصدر.\n" + friendly_error(exc, action="إضافة المصدر")
            )
            return
        self._pending_action = None
        await self._reply(
            update,
            "✅ تمت إضافة المصدر بنجاح\n"
            f"• الاسم: {source.title}\n"
            f"• المرجع: {source.input_ref}\n"
            f"• البداية من الرسالة: {source.baseline_message_id}\n\n"
            "لن يتم نشر الرسائل القديمة. أضف مصدرًا آخر أو حدّد القناة الهدف.",
        )

    async def remove_source(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        chat = await self._resolve_argument(update, context, "removesource")
        if chat is None:
            return
        removed = await self.sources.delete(chat.chat_id)
        await self._reply(update, "✅ تم حذف المصدر." if removed else "⚠️ المصدر غير موجود.")

    async def enable_source(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._set_source_enabled(update, context, True)

    async def disable_source(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._set_source_enabled(update, context, False)

    async def set_target(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        if not context.args:
            self._pending_action = "set_target"
            await self._reply(
                update,
                "أرسل الآن رابط أو اسم قناة الهدف، مثل @my_channel أو https://t.me/my_channel.\n"
                "تأكد أن البوت مشرف في القناة مع صلاحية «نشر الرسائل».\n\n"
                "أرسل /cancel للإلغاء.",
            )
            return
        await self._process_set_target(update, " ".join(context.args))

    async def _process_set_target(self, update: Update, input_ref: str) -> None:
        try:
            chat = await self.resolver.resolve(input_ref)
            await self.settings.update_target(chat.input_ref, chat.chat_id)
        except Exception as exc:
            await self._reply(
                update,
                "❌ لم يتم تحديد القناة الهدف.\n"
                + friendly_error(exc, action="تحديد القناة الهدف"),
            )
            return
        self._pending_action = None
        await self._reply(
            update,
            "✅ تم تحديد القناة الهدف بنجاح\n"
            f"• القناة: {chat.title}\n"
            f"• المرجع: {chat.input_ref}\n\n"
            "تأكد من إضافة البوت مشرفًا قبل الضغط على «تشغيل».",
        )

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
        await self._reply(update, "✅ تم تحديث فلتر أنواع الوسائط.")

    async def login(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Start a guided user-account login flow."""
        if not await self._authorized(update):
            return
        if self.session is None:
            await self._reply(update, "❌ تسجيل الدخول غير مهيأ في هذا التشغيل.")
            return
        if not context.args:
            self._pending_action = "login_phone"
            await self._reply(
                update,
                "🔐 أرسل رقم الهاتف بصيغة دولية كاملة، مثل:\n"
                "+967700000000\n\n"
                "يمكنك أيضًا كتابة 00967700000000. أرسل /cancel للإلغاء.",
            )
            return
        await self._process_login_phone(update, " ".join(context.args))

    async def _process_login_phone(self, update: Update, phone: str) -> None:
        try:
            sent = await self.session.request_login_code(phone)
        except Exception as exc:
            await self._reply(
                update,
                "❌ لم يتم إرسال رمز الدخول.\n" + friendly_error(exc, action="إرسال رمز الدخول"),
            )
            return
        self._login_phone = phone
        self._login_code_hash = sent.phone_code_hash
        self._pending_action = "login_code"
        await self._reply(
            update,
            "✅ تم إرسال رمز الدخول إلى Telegram.\n"
            "أرسل الرمز الآن فقط، مثل: 12345\n"
            "إذا كان الحساب محميًا بخطوتين، أرسل: الرمز كلمة_المرور\n\n"
            "أرسل /cancel للإلغاء.",
        )

    async def login_code(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        if not context.args:
            self._pending_action = "login_code"
            await self._reply(update, "أرسل رمز الدخول الذي وصلك من Telegram، أو /cancel للإلغاء.")
            return
        await self._process_login_code(update, " ".join(context.args))

    async def _process_login_code(self, update: Update, value: str) -> None:
        if self.session is None or not self._login_phone or not self._login_code_hash:
            await self._reply(update, "⚠️ لا توجد عملية تسجيل دخول معلقة. ابدأ بإرسال /login.")
            return
        parts = value.split()
        try:
            await self.session.complete_login(
                phone=self._login_phone,
                code=parts[0],
                phone_code_hash=self._login_code_hash,
                password=parts[1] if len(parts) > 1 else None,
            )
        except Exception as exc:
            await self._reply(
                update,
                "❌ لم يكتمل تسجيل الدخول.\n" + friendly_error(exc, action="إكمال تسجيل الدخول"),
            )
            return
        self._clear_login_state()
        await self._reply(
            update,
            "✅ تم تسجيل جلسة حساب Telegram بنجاح.\n"
            "يمكنك الآن إضافة المصادر وتحديد القناة الهدف ثم الضغط على «تشغيل».",
        )

    async def start_relay(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        config = await self.settings.get()
        source_list = await self.sources.list(enabled_only=True)
        if not config.target_chat_id:
            await self._reply(
                update, "❌ لا يمكن التشغيل قبل تحديد القناة الهدف. اضغط «القناة الهدف»."
            )
            return
        if not source_list:
            await self._reply(update, "❌ لا يمكن التشغيل قبل إضافة مصدر فعّال واحد على الأقل.")
            return
        await self.settings.set_enabled(True)
        try:
            await self.runtime.start()
        except Exception as exc:
            await self.settings.set_enabled(False)
            await self._reply(
                update, "❌ تعذر تشغيل النظام.\n" + friendly_error(exc, action="تشغيل النظام")
            )
            return
        await self._reply(update, "✅ تم تشغيل النظام. سيتم نشر الرسائل الجديدة فقط.")

    async def stop_relay(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        try:
            await self.runtime.stop()
            await self.settings.set_enabled(False)
        except Exception as exc:
            await self._reply(
                update, "❌ تعذر إيقاف النظام بأمان.\n" + friendly_error(exc, action="إيقاف النظام")
            )
            return
        await self._reply(update, "✅ تم إيقاف النظام بأمان.")

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        self._pending_action = None
        self._clear_login_state()
        await self._reply(update, "✅ أُلغيت الخطوة الحالية. اختر أمرًا من القائمة.")

    async def menu_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        text = (update.effective_message.text if update.effective_message else "").strip()
        if self._pending_action == "add_source":
            await self._process_add_source(update, text)
            return
        if self._pending_action == "set_target":
            await self._process_set_target(update, text)
            return
        if self._pending_action == "login_phone":
            await self._process_login_phone(update, text)
            return
        if self._pending_action == "login_code":
            await self._process_login_code(update, text)
            return

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
            await self.add_source(update, SimpleNamespace(args=[]))
        elif text == self.BUTTON_TARGET:
            await self.set_target(update, SimpleNamespace(args=[]))
        elif text == self.BUTTON_LOGIN:
            await self.login(update, SimpleNamespace(args=[]))
        elif text == self.BUTTON_FILTERS:
            await self._reply(
                update,
                "⚙️ الفلاتر:\n/include خبر,تقنية\n/exclude إعلان\n"
                "/mediatypes photo,video أو /mediatypes all",
            )
        elif text == self.BUTTON_CANCEL:
            await self.cancel(update, context)
        else:
            await self._reply(
                update, "استخدم الأزرار أو /help. إذا كنت داخل خطوة، أرسل /cancel أولًا."
            )

    async def _set_source_enabled(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, enabled: bool
    ) -> None:
        if not await self._authorized(update):
            return
        chat = await self._resolve_argument(update, context, "source")
        if chat is None:
            return
        changed = await self.sources.set_enabled(chat.chat_id, enabled)
        state = "تفعيل" if enabled else "إيقاف"
        await self._reply(update, f"✅ تم {state} المصدر." if changed else "⚠️ المصدر غير موجود.")

    async def _set_keywords(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, include: bool
    ) -> None:
        if not await self._authorized(update):
            return
        values = _csv_args(context.args)
        if not values:
            await self._reply(update, "⚠️ أرسل كلمة واحدة على الأقل، مفصولة بفواصل.")
            return
        config = await self.settings.get()
        await self.settings.update_filters(
            include_keywords=values if include else config.include_keywords,
            exclude_keywords=config.exclude_keywords if include else values,
            allowed_media_types=config.allowed_media_types,
        )
        await self._reply(update, "✅ تم تحديث فلتر الكلمات.")

    async def _resolve_argument(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, command: str
    ) -> ResolvedChat | None:
        if not context.args:
            await self._reply(update, f"الاستخدام: /{command} @channel أو رابط القناة")
            return None
        try:
            return await self.resolver.resolve(" ".join(context.args))
        except Exception as exc:
            await self._reply(
                update,
                "❌ تعذر الوصول إلى القناة.\n" + friendly_error(exc, action="الوصول إلى القناة"),
            )
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

    def _clear_login_state(self) -> None:
        self._login_phone = None
        self._login_code_hash = None
        self._pending_action = None

    async def notify_owner(self, status: str) -> None:
        """Send operational status to the owner when the bot is already running."""
        if self.application is None:
            return
        messages = {
            "telegram_disconnected": "⚠️ انقطع اتصال حساب Telegram. ستتم محاولة الاستعادة تلقائيًا.",
            "telegram_reconnected": "✅ تمت استعادة اتصال Telegram وإعادة ضبط نقطة البداية.",
            "telegram_reconnect_retrying": (
                "⚠️ ما زال اتصال Telegram متوقفًا؛ ستستمر محاولات الاستعادة."
            ),
        }
        text = messages.get(status)
        if text:
            await self.application.bot.send_message(chat_id=self.owner_id, text=text)

    async def _notify_started(self) -> None:
        """Notify the owner after the control bot has successfully started."""
        if self.application is None:
            return
        try:
            await self.application.bot.send_message(
                chat_id=self.owner_id,
                text="✅ البوت يعمل الآن وجاهز لاستقبال الأوامر.",
                reply_markup=self.keyboard(),
            )
        except Exception:
            logging.getLogger(__name__).warning(
                "startup notification could not be delivered to owner",
                exc_info=True,
            )

    @classmethod
    def keyboard(cls) -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            [
                [cls.BUTTON_STATUS, cls.BUTTON_SOURCES],
                [cls.BUTTON_ADD_SOURCE, cls.BUTTON_TARGET],
                [cls.BUTTON_LOGIN, cls.BUTTON_FILTERS],
                [cls.BUTTON_START, cls.BUTTON_STOP],
                [cls.BUTTON_HELP, cls.BUTTON_CANCEL],
            ],
            resize_keyboard=True,
        )

    async def run_polling(self) -> None:
        """Build and run polling; intended for the production entry point."""
        application = self.application or self.build_application()
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        await self._notify_started()
        try:
            await asyncio.Event().wait()
        finally:
            await application.updater.stop()
            await application.stop()
            await application.shutdown()


def _csv_args(args: Sequence[str]) -> tuple[str, ...]:
    return tuple(item.strip() for arg in args for item in arg.split(",") if item.strip())
