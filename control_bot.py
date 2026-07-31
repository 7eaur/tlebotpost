"""بوت التحكم التفاعلي في النظام مع قائمة لوحة مفاتيح هرمية."""
import asyncio
import re
from datetime import datetime, timezone
from typing import Any

from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.error import BadRequest
import re
from telethon.errors import (
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)

from config import Config
from database import Database
from smart_engine import SmartEngine


class _WaitMessage:
    """إدارة رسائل الانتظار: حذف القديم وإرسال جديد كل 20 ثانية."""

    def __init__(self, bot: Any, chat_id: int, total_seconds: int, label: str = "انتظار") -> None:
        self.bot = bot
        self.chat_id = chat_id
        self.total_seconds = total_seconds
        self.remaining = total_seconds
        self.label = label
        self.message_id: int | None = None
        self._task: asyncio.Task[Any] | None = None
        self._done = False

    async def start(self) -> None:
        """بدء عداد الانتظار."""
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """إيقاف العداد وحذف الرسالة الأخيرة."""
        self._done = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._delete_current()

    async def _loop(self) -> None:
        """حلقة التحديث كل 20 ثانية."""
        while not self._done and self.remaining > 0:
            await self._delete_current()
            if self._done:
                break
            self.message_id = await self._send_current()
            step = min(20, self.remaining)
            await asyncio.sleep(step)
            self.remaining -= step
        await self._delete_current()

    async def _send_current(self) -> int | None:
        """إرسال رسالة الانتظار الحالية."""
        try:
            msg = await self.bot.send_message(
                chat_id=self.chat_id,
                text=f"⏳ {self.label}: باقي {self.remaining} ثانية...",
            )
            return msg.message_id
        except Exception:
            return None

    async def _delete_current(self) -> None:
        """حذف رسالة الانتظار الحالية."""
        if not self.message_id:
            return
        try:
            await self.bot.delete_message(self.chat_id, self.message_id)
        except Exception:
            pass
        self.message_id = None


class ControlBot:
    """بوت التحكم المقيد بالمالك مع قائمة لوحة مفاتيح هرمية."""

    # الأزرار الرئيسية
    BTN_START = "▶️ تشغيل النشر"
    BTN_STOP = "⏹️ إيقاف النشر"
    BTN_TOGGLE = "toggle"  # يُستخدم داخلياً
    BTN_GROUPS = "📋 المجموعات"
    BTN_SETTINGS = "⚙️ الإعدادات"
    BTN_SERVER = "🖥️ السيرفر"
    BTN_SESSION = "🔐 الجلسة"
    BTN_DISCOVER = "🔍 اكتشاف"
    BTN_STATS = "📊 إحصائيات"
    BTN_INSIGHTS = "💡 الأفكار"
    BTN_HELP = "❓ المساعدة"
    BTN_BACK = "🔙 رجوع"

    # أزرار فرعية
    BTN_ACTIVE = "✅ القابلة للنشر"
    BTN_BANNED = "🚫 الممنوعات"
    BTN_EXCLUDE = "⛔️ استبعاد مجموعة"
    BTN_INCLUDE = "🔄 إلغاء استثناء"
    BTN_EDIT_MSG = "✏️ تعديل المنشور"
    BTN_EDIT_DELAY = "⏱️ تعديل التأخير"
    BTN_RESET = "🔄 إعادة الضبط"
    BTN_PAUSE = "⏸️ إيقاف مؤقت"
    BTN_RESUME = "🔄 استئناف"
    BTN_STOP_FULL = "🛑 إيقاف كامل"
    BTN_LOGIN = "🔑 تسجيل الدخول"
    BTN_DELETE_SESSION = "🗑️ حذف الجلسة"

    # حالات القوائم
    MENU_MAIN = "main"
    MENU_GROUPS = "groups"
    MENU_SETTINGS = "settings"
    MENU_SERVER = "server"
    MENU_SESSION = "session"

    def __init__(self, database: Database, engine: SmartEngine) -> None:
        self.db = database
        self.engine = engine
        self._app: Application | None = None
        self._menu_state = self.MENU_MAIN
        self._prompt_state: str | None = None
        self._auth_state: str | None = None
        self._auth_phone: str | None = None
        self._auth_phone_code_hash: str | None = None
        self._auth_client: Any | None = None
        self._active_wait: _WaitMessage | None = None

    # ───────────────────────────────────────────────
    # لوحات المفاتيح
    # ───────────────────────────────────────────────

    def _keyboard(self, buttons: list[list[str]]) -> ReplyKeyboardMarkup:
        """إنشاء لوحة مفاتيح ثابتة."""
        return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

    def _toggle_label(self) -> str:
        """نص زر التشغيل/الإيقاف حسب حالة المحرك."""
        return self.BTN_STOP if self.engine.is_running else self.BTN_START

    def _keyboard_main(self) -> ReplyKeyboardMarkup:
        toggle = self._toggle_label()
        return self._keyboard(
            [
                [toggle, self.BTN_GROUPS, self.BTN_SETTINGS],
                [self.BTN_SERVER, self.BTN_SESSION, self.BTN_DISCOVER],
                [self.BTN_STATS, self.BTN_INSIGHTS, self.BTN_HELP],
            ]
        )

    def _keyboard_groups(self) -> ReplyKeyboardMarkup:
        return self._keyboard(
            [
                [self.BTN_ACTIVE, self.BTN_BANNED],
                [self.BTN_EXCLUDE, self.BTN_INCLUDE],
                [self.BTN_BACK],
            ]
        )

    def _keyboard_settings(self) -> ReplyKeyboardMarkup:
        return self._keyboard(
            [
                [self.BTN_EDIT_MSG, self.BTN_EDIT_DELAY],
                [self.BTN_RESET, self.BTN_BACK],
            ]
        )

    def _keyboard_server(self) -> ReplyKeyboardMarkup:
        toggle = self._toggle_label()
        return self._keyboard(
            [
                [toggle, self.BTN_PAUSE],
                [self.BTN_RESUME],
                [self.BTN_BACK],
            ]
        )

    def _keyboard_session(self) -> ReplyKeyboardMarkup:
        return self._keyboard(
            [
                [self.BTN_LOGIN, self.BTN_DELETE_SESSION],
                [self.BTN_BACK],
            ]
        )

    def _current_keyboard(self) -> ReplyKeyboardMarkup:
        if self._menu_state == self.MENU_GROUPS:
            return self._keyboard_groups()
        if self._menu_state == self.MENU_SETTINGS:
            return self._keyboard_settings()
        if self._menu_state == self.MENU_SERVER:
            return self._keyboard_server()
        if self._menu_state == self.MENU_SESSION:
            return self._keyboard_session()
        return self._keyboard_main()

    async def _reply(
        self,
        update: Update,
        text: str,
        menu: str | None = None,
    ) -> None:
        """إرسال رد مع لوحة المفاتيح المناسبة."""
        if menu:
            self._menu_state = menu
        await update.message.reply_text(text, reply_markup=self._current_keyboard())

    # ───────────────────────────────────────────────
    # التشغيل والإيقاف
    # ───────────────────────────────────────────────

    async def _info_text(self) -> str:
        """نص معلومات المدير والبوت."""
        bot_info = "غير معروف"
        try:
            if self._app:
                me = await self._app.bot.get_me()
                bot_info = f"@{me.username}" if me.username else me.first_name
        except Exception:
            pass
        return (
            f"👤 المدير المعتمد: {Config.OWNER_ID}\n"
            f"🤖 البوت: {bot_info}"
        )

    async def send_welcome(self) -> None:
        """إرسال رسالة الترحيب مع القائمة الرئيسية للمالك."""
        if not self._app:
            return
        self._menu_state = self.MENU_MAIN
        info = await self._info_text()
        text = (
            "مرحباً بك في Telegram Smart Poster 🚀\n\n"
            f"{info}\n\n"
            f"حالة النشر: {'يعمل ✅' if self.engine.is_running else 'متوقف ⏹️'}\n"
            "السيرفر: يعمل على مدار 24 ساعة 🖥️\n\n"
            "اختر فئة من القائمة أدناه:\n"
            f"{self._toggle_label()} — تشغيل/إيقاف النشر فقط\n"
            "📋 المجموعات — إدارة المجموعات\n"
            "⚙️ الإعدادات — تعديل المنشور والتأخير\n"
            "🖥️ السيرفر — تحكم مفصل في المحرك\n"
            "🔐 الجلسة — تسجيل الدخول أو الحذف\n"
            "🔍 اكتشاف — تحديث قوائم المجموعات"
        )
        try:
            await self._app.bot.send_message(
                chat_id=Config.OWNER_ID,
                text=text,
                reply_markup=self._keyboard_main(),
            )
        except Exception as exc:
            print(f"⚠️ لم يتم إرسال رسالة الترحيب للمالك: {exc}")

    async def _notify_owner(self, text: str) -> None:
        """إرسال إشعار للمالك."""
        if not self._app:
            return
        try:
            await self._app.bot.send_message(
                chat_id=Config.OWNER_ID,
                text=text,
                reply_markup=self._current_keyboard(),
            )
        except Exception:
            pass

    async def run(self) -> None:
        """تهيئة وتشغيل بوت التحكم."""
        self._app = (
            Application.builder()
            .token(Config.BOT_TOKEN)
            .concurrent_updates(True)
            .build()
        )

        self.engine.on_status_change = self._on_engine_status

        # معالجات الأوامر
        self._app.add_handler(CommandHandler("start", self._cmd_start))
        self._app.add_handler(CommandHandler("help", self._cmd_start))
        self._app.add_handler(CommandHandler("login", self._cmd_login))
        self._app.add_handler(CommandHandler("logout", self._cmd_logout))
        self._app.add_handler(CommandHandler("setmsg", self._cmd_setmsg))
        self._app.add_handler(CommandHandler("setdelay", self._cmd_setdelay))
        self._app.add_handler(CommandHandler("exclude", self._cmd_exclude))
        self._app.add_handler(CommandHandler("include", self._cmd_include))
        self._app.add_handler(CommandHandler("groups", self._cmd_groups))

        # معالج النصوص (الأزرار والأوامر العامة)
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._text_handler)
        )

        # معالج الأخطاء
        self._app.add_error_handler(self._error_handler)

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)  # type: ignore

    async def stop(self) -> None:
        """إيقاف بوت التحكم."""
        await self._stop_wait()
        if self._app:
            await self._app.updater.stop()  # type: ignore
            await self._app.stop()
            await self._app.shutdown()

    # ───────────────────────────────────────────────
    # استقبال حالة المحرك
    # ───────────────────────────────────────────────

    async def _on_engine_status(self, text: str) -> None:
        """استقبال تحديثات حالة المحرك وإرسالها للمالك."""
        if not self._app:
            return
        try:
            match = re.search(r"⏳\s*الانتظار\s*(\d+)\s*ثانية", text)
            if match:
                seconds = int(match.group(1))
                await self._start_wait(seconds, label="انتظار المنشور التالي")
                return

            await self._stop_wait()
            await self._app.bot.send_message(
                chat_id=Config.OWNER_ID,
                text=text,
                reply_markup=self._current_keyboard(),
            )
        except Exception:
            pass

    async def _start_wait(self, total_seconds: int, label: str = "انتظار") -> None:
        """بدء رسالة انتظار جديدة."""
        await self._stop_wait()
        if not self._app:
            return
        self._active_wait = _WaitMessage(
            bot=self._app.bot,
            chat_id=Config.OWNER_ID,
            total_seconds=total_seconds,
            label=label,
        )
        await self._active_wait.start()

    async def _stop_wait(self) -> None:
        """إيقاف رسالة الانتظار النشطة."""
        if self._active_wait:
            await self._active_wait.stop()
            self._active_wait = None

    # ───────────────────────────────────────────────
    # التحقق من الملكية
    # ───────────────────────────────────────────────

    async def _is_owner(self, update: Update) -> bool:
        """التحقق من أن المرسل هو المالك."""
        user_id = update.effective_user.id if update.effective_user else 0
        return user_id == Config.OWNER_ID

    async def _reject(self, update: Update) -> None:
        """رفض الوصول للمستخدمين غير المصرح لهم."""
        await update.message.reply_text("🚫 ليس لديك صلاحية استخدام هذا البوت.")

    # ───────────────────────────────────────────────
    # معالجات الأوامر
    # ───────────────────────────────────────────────

    async def _cmd_start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """أمر /start و /help."""
        if not await self._is_owner(update):
            await self._reject(update)
            return
        info = await self._info_text()
        await self._reply(update, (
            "مرحباً بك في Telegram Smart Poster 🚀\n\n"
            f"{info}\n\n"
            f"حالة النشر: {'يعمل ✅' if self.engine.is_running else 'متوقف ⏹️'}\n"
            "السيرفر: يعمل على مدار 24 ساعة 🖥️\n\n"
            "اختر فئة من القائمة أدناه:\n"
            f"{self._toggle_label()} — تشغيل/إيقاف النشر فقط\n"
            "📋 المجموعات — إدارة المجموعات\n"
            "⚙️ الإعدادات — تعديل المنشور والتأخير\n"
            "🖥️ السيرفر — تحكم مفصل في المحرك\n"
            "🔐 الجلسة — تسجيل الدخول أو الحذف\n"
            "🔍 اكتشاف — تحديث قوائم المجموعات"
        ), menu=self.MENU_MAIN)

    async def _cmd_login(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """بدء تدفق تسجيل الدخول التفاعلي."""
        if not await self._is_owner(update):
            await self._reject(update)
            return
        await self._start_login(update)

    async def _start_login(self, update: Update) -> None:
        # عند إضافة جلسة جديدة نمسح كل البيانات القديمة لنبدأ بشكل نظيف
        await self._clear_account_data(silent=True)
        self._reset_auth()
        self._auth_state = "awaiting_phone"
        await self._reply(
            update,
            "🧹 تم مسح البيانات القديمة.\n"
            "🔑 بدء تسجيل الدخول. أرسل رقم الهاتف بالصيغة الدولية (مثلاً +9665XXXXXXXX):",
            menu=self.MENU_SESSION,
        )

    async def _cmd_logout(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """حذف الجلسة الحالية."""
        if not await self._is_owner(update):
            await self._reject(update)
            return
        await self._delete_session(update)

    async def _cmd_setmsg(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """تحديث نص المنشور."""
        if not await self._is_owner(update):
            await self._reject(update)
            return
        text = update.message.text.partition(" ")[2].strip()
        if not text:
            await self._reply(update, "⚠️ استخدم: /setmsg <النص الجديد>")
            return
        await self.db.set_message(text, use_html=True)
        await self._reply(update, "✅ تم تحديث نص المنشور.")

    async def _cmd_setdelay(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """تعديل نطاق التأخير."""
        if not await self._is_owner(update):
            await self._reject(update)
            return
        parts = update.message.text.split()
        if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
            await self._reply(update, "⚠️ استخدم: /setdelay <ثواني_أدنى> <ثواني_أقصى>")
            return
        min_delay = int(parts[1])
        max_delay = int(parts[2])
        if min_delay >= max_delay:
            await self._reply(update, "⚠️ يجب أن يكون الحد الأقصى أكبر من الحد الأدنى.")
            return
        Config.MIN_DELAY = min_delay
        Config.MAX_DELAY = max_delay
        self.engine.scheduler.min_delay = min_delay
        self.engine.scheduler.max_delay = max_delay
        await self._reply(update, f"✅ تم تحديث التأخير: {min_delay}-{max_delay} ثانية.")

    async def _cmd_exclude(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """استبعاد مجموعة من النشر."""
        if not await self._is_owner(update):
            await self._reject(update)
            return
        identifier = update.message.text.partition(" ")[2].strip()
        if not identifier:
            self._prompt_state = "awaiting_exclude"
            await self._reply(
                update,
                "⛔️ أرسل يوزر المجموعة أو الرابط لاستبعادها:",
                menu=self.MENU_GROUPS,
            )
            return
        await self._do_exclude(update, identifier)

    async def _cmd_include(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """إعادة مجموعة مستبعدة للنشر."""
        if not await self._is_owner(update):
            await self._reject(update)
            return
        identifier = update.message.text.partition(" ")[2].strip()
        if not identifier:
            self._prompt_state = "awaiting_include"
            await self._reply(
                update,
                "🔄 أرسل يوزر المجموعة أو الرابط لإلغاء الاستثناء:",
                menu=self.MENU_GROUPS,
            )
            return
        await self._do_include(update, identifier)

    async def _cmd_groups(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """عرض قائمة المجموعات."""
        if not await self._is_owner(update):
            await self._reject(update)
            return
        await self._send_groups_list(update, statuses=["active"])

    # ───────────────────────────────────────────────
    # معالج النصوص (الأزرار والإدخال العام)
    # ───────────────────────────────────────────────

    async def _text_handler(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """معالجة النصوص العامة بما فيها أزرار لوحة المفاتيح."""
        if not await self._is_owner(update):
            await self._reject(update)
            return

        text = update.message.text.strip()

        # حالة تسجيل الدخول لها الأولوية
        if self._auth_state:
            await self._handle_auth_text(update, text)
            return

        # حالات الاستقبال
        if self._prompt_state:
            await self._handle_prompt(update, text)
            return

        # زر الرجوع
        if text == self.BTN_BACK:
            await self._reply(update, "القائمة الرئيسية:", menu=self.MENU_MAIN)
            return

        # زر التشغيل/الإيقاف الديناميكي
        if text in (self.BTN_START, self.BTN_STOP):
            if self.engine.is_running:
                await self.engine.stop()
                await self._reply(update, "⏹️ تم إيقاف المحرك.")
            else:
                await self._run_long_action(
                    update, self.engine.start(), "جاري تشغيل المحرك..."
                )
            return

        # القائمة الرئيسية
        if text == self.BTN_GROUPS:
            await self._reply(update, "إدارة المجموعات:", menu=self.MENU_GROUPS)
            return
        if text == self.BTN_SETTINGS:
            await self._reply(update, "الإعدادات:", menu=self.MENU_SETTINGS)
            return
        if text == self.BTN_SERVER:
            await self._reply(update, "التحكم في السيرفر:", menu=self.MENU_SERVER)
            return
        if text == self.BTN_SESSION:
            await self._reply(update, "إدارة الجلسة:", menu=self.MENU_SESSION)
            return
        if text == self.BTN_DISCOVER:
            await self._run_long_action(
                update, self.engine.discover_groups(notify=False), "جاري اكتشاف المجموعات..."
            )
            return
        if text == self.BTN_STATS:
            await self._send_stats(update)
            return
        if text == self.BTN_INSIGHTS:
            await self._send_insights(update)
            return
        if text == self.BTN_HELP:
            await self._cmd_start(update, context)
            return

        # قائمة المجموعات
        if text == self.BTN_ACTIVE:
            await self._send_groups_list(update, statuses=["active"])
            return
        if text == self.BTN_BANNED:
            await self._send_groups_list(update, statuses=["banned", "excluded"])
            return
        if text == self.BTN_EXCLUDE:
            self._prompt_state = "awaiting_exclude"
            await self._reply(update, "⛔️ أرسل يوزر المجموعة أو الرابط لاستبعادها:")
            return
        if text == self.BTN_INCLUDE:
            self._prompt_state = "awaiting_include"
            await self._reply(update, "🔄 أرسل يوزر المجموعة أو الرابط لإلغاء الاستثناء:")
            return

        # قائمة الإعدادات
        if text == self.BTN_EDIT_MSG:
            self._prompt_state = "awaiting_msg"
            await self._reply(update, "✏️ أرسل النص الجديد للمنشور (يمكنك استخدام HTML):")
            return
        if text == self.BTN_EDIT_DELAY:
            self._prompt_state = "awaiting_delay"
            await self._reply(update, "⏱️ أرسل التأخير بالصيغة: أدنى أقصى (مثلاً 30 120):")
            return
        if text == self.BTN_RESET:
            await self.engine.reset()
            await self._reply(update, "🔄 تم إعادة ضبط النظام.")
            return

        # قائمة السيرفر
        if text == self.BTN_PAUSE:
            if not self.engine.is_running:
                await self._reply(update, "⚠️ المحرك متوقف بالفعل.")
                return
            await self.engine.pause()
            await self._reply(update, "⏸️ تم إيقاف المحرك مؤقتاً.")
            return
        if text == self.BTN_RESUME:
            if not self.engine.is_paused:
                await self._reply(update, "⚠️ المحرك غير متوقف مؤقتاً.")
                return
            await self.engine.resume()
            await self._reply(update, "🔄 تم استئناف المحرك.")
            return
        if text == self.BTN_STOP_FULL:
            await self.engine.stop()
            await self._reply(update, "🛑 تم إيقاف المحرك بالكامل.")
            return

        # قائمة الجلسة
        if text == self.BTN_LOGIN:
            await self._start_login(update)
            return
        if text == self.BTN_DELETE_SESSION:
            await self._delete_session(update)
            return

        # أمر غير معروف
        await self._reply(update, "⚠️ لم أفهم الأمر. اختر أحد الخيارات من القائمة أدناه.")

    async def _handle_prompt(self, update: Update, text: str) -> None:
        """معالجة حالات الاستقبال المؤقتة."""
        state = self._prompt_state
        self._prompt_state = None

        if state == "awaiting_exclude":
            await self._do_exclude(update, text)
            return
        if state == "awaiting_include":
            await self._do_include(update, text)
            return
        if state == "awaiting_msg":
            await self.db.set_message(text, use_html=True)
            await self._reply(update, "✅ تم تحديث نص المنشور.")
            return
        if state == "awaiting_delay":
            parts = text.split()
            if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
                self._prompt_state = "awaiting_delay"
                await self._reply(update, "⚠️ الصيغة غير صحيحة. أرسل: أدنى أقصى (مثلاً 30 120):")
                return
            min_delay = int(parts[0])
            max_delay = int(parts[1])
            if min_delay >= max_delay:
                self._prompt_state = "awaiting_delay"
                await self._reply(update, "⚠️ يجب أن يكون الحد الأقصى أكبر من الحد الأدنى.")
                return
            Config.MIN_DELAY = min_delay
            Config.MAX_DELAY = max_delay
            self.engine.scheduler.min_delay = min_delay
            self.engine.scheduler.max_delay = max_delay
            await self._reply(update, f"✅ تم تحديث التأخير: {min_delay}-{max_delay} ثانية.")
            return

    async def _do_exclude(self, update: Update, identifier: str) -> None:
        """تنفيذ استبعاد مجموعة (نقلها إلى الممنوعات)."""
        group = await self.db.find_group_by_username_or_id(identifier)
        if not group:
            await self._reply(
                update,
                "❌ لم أجد المجموعة. تأكد من اليوزر أو الرابط، أو اضغط 🔍 اكتشاف أولاً.",
            )
            return
        await self.db.update_group_status(
            group["id"], "banned", failure_reason="استبعاد يدوي"
        )
        await self._reply(update, f"✅ تم استبعاد {group['title']} ونقلها إلى الممنوعات.")

    async def _do_include(self, update: Update, identifier: str) -> None:
        """تنفيذ إلغاء استثناء مجموعة (إعادتها إلى القابلة للنشر)."""
        group = await self.db.find_group_by_username_or_id(identifier)
        if not group:
            await self._reply(update, "❌ لم أجد المجموعة.")
            return
        await self.db.update_group_status(group["id"], "active", failure_reason=None)
        await self._reply(update, f"✅ تمت إعادة {group['title']} إلى القابلة للنشر.")

    async def _send_groups_list(
        self, update: Update, statuses: list[str] | None = None
    ) -> None:
        """إرسال قائمة المجموعات للمالك."""
        statuses = statuses or ["active"]
        groups, total = await self.db.get_groups_paginated(
            page=1, page_size=50, statuses=statuses
        )
        if not groups:
            await self._reply(update, "📋 لا توجد مجموعات في هذه القائمة.")
            return

        status_icon = {
            "active": "✅",
            "banned": "🚫",
            "excluded": "🚫",
        }
        header = "✅ القابلة للنشر" if statuses == ["active"] else "🚫 الممنوعات"
        lines = [f"{header} (إجمالي: {total}):\n"]
        for idx, group in enumerate(groups, start=1):
            icon = status_icon.get(group["status"], "⚪")
            username = f"@{group['username']}" if group["username"] else group["id"]
            lines.append(f"{idx}. {icon} {group['title']} — {username}")
        if total > len(groups):
            lines.append(f"\n... و {total - len(groups)} مجموعة أخرى.")
        await self._reply(update, "\n".join(lines))

    async def _delete_session(self, update: Update) -> None:
        """حذف الجلسة الحالية وإعادة ضبطها."""
        await self._clear_account_data(silent=False)
        await self._reply(
            update,
            "🗑️ تم حذف الجلسة ومسح جميع البيانات. أرسل 🔑 تسجيل الدخول لإضافة جلسة جديدة.",
            menu=self.MENU_SESSION,
        )

    async def _clear_account_data(self, silent: bool = False) -> None:
        """إيقاف المحرك، حذف الجلسة، ومسح كل بيانات الحساب القديمة."""
        try:
            await self.engine.stop()
        except Exception:
            pass
        try:
            if self.engine.client and self.engine.client.is_connected():
                await self.engine.client.disconnect()
        except Exception:
            pass
        self.engine.client = None
        try:
            await self.engine.session_manager.revoke_session()
        except Exception:
            pass
        try:
            await self.db.clear_all_data()
        except Exception:
            pass
        try:
            await self.engine.reset()
        except Exception:
            pass
        if not silent:
            print("🧹 تم مسح جميع بيانات الحساب القديمة.")

    async def _run_long_action(
        self,
        update: Update,
        coro: Any,
        label: str,
    ) -> None:
        """تشغيل عملية طويلة مع رسالة انتظار."""
        wait_msg = await update.message.reply_text(
            f"⏳ {label}", reply_markup=self._current_keyboard()
        )
        try:
            result = await coro
            await self._safe_delete(update.effective_chat.id, wait_msg.message_id)
            suffix = ""
            if isinstance(result, dict) and "total_discovered" in result:
                suffix = (
                    f"\nاكتمل الاكتشاف. عدد المجموعات/القنوات المكتشفة: "
                    f"{result['total_discovered']}"
                )
            await update.message.reply_text(
                f"✅ تمت العملية بنجاح.{suffix}",
                reply_markup=self._current_keyboard(),
            )
        except Exception as exc:
            await self._safe_delete(update.effective_chat.id, wait_msg.message_id)
            await update.message.reply_text(
                f"❌ فشلت العملية: {exc}\nأرسل 🔑 تسجيل الدخول لإضافة الجلسة.",
                reply_markup=self._current_keyboard(),
            )

    async def _safe_delete(self, chat_id: int, message_id: int) -> None:
        """حذف رسالة مع تجاهل الأخطاء."""
        try:
            await self._app.bot.delete_message(chat_id, message_id)
        except Exception:
            pass

    async def _background_discovery(self, update: Update) -> None:
        """اكتشاف المجموعات في الخلفية بعد تسجيل الدخول."""
        try:
            summary = await self.engine.discover_groups(notify=False)
            text = (
                f"✅ تم الاكتشاف التلقائي. عدد المجموعات/القنوات المكتشفة: "
                f"{summary.get('total_discovered', 0)}"
            )
        except Exception as exc:
            text = f"⚠️ فشل الاكتشاف التلقائي: {exc}"
        try:
            await update.message.reply_text(text, reply_markup=self._current_keyboard())
        except Exception:
            pass

    # ───────────────────────────────────────────────
    # تسجيل الدخول
    # ───────────────────────────────────────────────

    async def _handle_auth_text(self, update: Update, text: str) -> None:
        """معالجة خطوات تسجيل الدخول."""
        if self._auth_state == "awaiting_phone":
            if not text.startswith("+"):
                await self._reply(update, "⚠️ الرجاء إرسال رقم الهاتف بالصيغة الدولية (يبدأ بـ +).")
                return
            self._auth_phone = text
            try:
                self._auth_client, self._auth_phone_code_hash = (
                    await self.engine.session_manager.start_login(text)
                )
                self._auth_state = "awaiting_code"
                await self._reply(
                    update,
                    "📩 تم إرسال رمز التحقق إلى تطبيق Telegram.\n"
                    "أرسل الرمز الآن (الأرقام فقط):",
                )
            except Exception as exc:
                self._reset_auth()
                await self._reply(update, f"❌ فشل إرسال رمز التحقق: {exc}")
            return

        if self._auth_state == "awaiting_code":
            # استخراج الأرقام فقط من أي نص يرسله المستخدم
            code = re.sub(r"\D", "", text)
            if not code:
                await self._reply(
                    update,
                    "⚠️ لم أجد رمز التحقق في رسالتك. أرسل الرمز (الأرقام فقط):",
                )
                return
            try:
                await self.engine.session_manager.complete_login(
                    self._auth_client,
                    self._auth_phone,
                    code,
                    self._auth_phone_code_hash,
                )
                self.engine.client = self._auth_client
                self._reset_auth()
                await self._reply(
                    update,
                    "✅ تم تسجيل الدخول وحفظ الجلسة مشفرة بنجاح.\n"
                    "جاري اكتشاف المجموعات في الخلفية...",
                    menu=self.MENU_MAIN,
                )
                asyncio.create_task(self._background_discovery(update))
            except SessionPasswordNeededError:
                self._auth_state = "awaiting_password"
                await self._reply(
                    update,
                    "🔐 الحساب محمي بكلمة مرور ثنائية. أرسل كلمة المرور:",
                )
            except (PhoneCodeExpiredError, PhoneCodeInvalidError) as exc:
                # لا نعيد الإرسال تلقائياً لتجنب حظر الرقم؛ نطلب من المستخدم البدء من جديد
                self._reset_auth()
                await self._reply(
                    update,
                    f"⚠️ {exc}.\n"
                    "أرسل 🔑 تسجيل الدخول للمحاولة من جديد.",
                    menu=self.MENU_SESSION,
                )
            except Exception as exc:
                self._reset_auth()
                await self._reply(
                    update,
                    f"❌ فشل تسجيل الدخول: {exc}\n"
                    "أرسل 🔑 تسجيل الدخول للمحاولة من جديد.",
                    menu=self.MENU_SESSION,
                )
            return

        if self._auth_state == "awaiting_password":
            try:
                await self._auth_client.sign_in(password=text)
                session_string = self._auth_client.session.save()
                self.engine.session_manager._save_encrypted_session(session_string)
                self.engine.client = self._auth_client
                self._reset_auth()
                await self._reply(
                    update,
                    "✅ تم تسجيل الدخول وحفظ الجلسة مشفرة بنجاح.\n"
                    "جاري اكتشاف المجموعات في الخلفية...",
                    menu=self.MENU_MAIN,
                )
                asyncio.create_task(self._background_discovery(update))
            except Exception as exc:
                await self._reply(update, f"❌ فشلت كلمة المرور: {exc}")
            return

    def _reset_auth(self) -> None:
        """إعادة ضبط حالة المصادقة."""
        self._auth_state = None
        self._auth_phone = None
        self._auth_phone_code_hash = None
        self._auth_client = None

    # ───────────────────────────────────────────────
    # الردود المشتركة
    # ───────────────────────────────────────────────

    async def _send_stats(self, update: Update) -> None:
        """إرسال الإحصائيات."""
        stats = await self.db.get_post_stats()
        failed, total = await self.db.get_recent_error_rate(minutes=30)
        text = (
            "📊 إحصائيات النظام\n\n"
            f"إجمالي المنشورات: {stats['total']}\n"
            f"المنشورات الناجحة: {stats['success']}\n"
            f"المنشورات الفاشلة: {stats['failed']}\n"
            f"نسبة النجاح: {stats['success_rate']}%\n"
            f"المجموعات النشطة: {stats['active_groups']}\n"
            f"الدورات المكتملة: {stats['completed_cycles']}\n\n"
            f"معدل الأخطاء (30 د): {int(failed / total * 100)}%"
        )
        await self._reply(update, text)

    async def _send_insights(self, update: Update) -> None:
        """إرسال رؤى التعلم الذاتي."""
        model = await self.db.get_learning_model("distributor")
        if not model:
            text = "💡 لم تُستخلص أي أفكار بعد. انتظر اكتمال دورة واحدة على الأقل."
        else:
            import json
            data = json.loads(model["model_data"])
            best_hours = data.get("best_hours", [])
            adjustments = data.get("period_adjustments", {})
            text = (
                "💡 رؤى محرك التعلم الذاتي\n\n"
                f"أفضل الساعات: {', '.join(map(str, best_hours)) or 'غير محدد بعد'}\n"
                f"تعديلات التوزيع: {adjustments}"
            )
        await self._reply(update, text)

    async def _error_handler(
        self, update: object, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """معالج الأخطاء العام لمنع تعطل البوت."""
        error = context.error
        if isinstance(error, BadRequest) and "Message is not modified" in str(error):
            return
        import logging
        logging.getLogger("control_bot").error(
            "حدث خطأ: %s", error, exc_info=context.error
        )
