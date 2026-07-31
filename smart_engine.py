"""المحرك الذكي: إدارة دورة النشر الكاملة مع محاكاة بشرية ومعالجة شاملة للأخطاء."""
import asyncio
import hashlib
import random
import re
import string
from datetime import datetime, timedelta, timezone
from typing import Any

from telethon import TelegramClient
from telethon.errors import (
    AuthKeyDuplicatedError,
    ChannelInvalidError,
    ChatAdminRequiredError,
    ChatWriteForbiddenError,
    FloodWaitError,
    PeerIdInvalidError,
    RPCError,
    SlowModeWaitError,
    UserBannedInChannelError,
    UserDeactivatedBanError,
)
from telethon.errors.rpcerrorlist import UserNotParticipantError
from telethon.tl.types import InputPeerChat, InputPeerChannel

from config import Config
from database import Database
from group_discovery import GroupDiscovery
from session_manager import SessionManager
from algorithms.distributor import SmartDistributor
from algorithms.rotator import SmartRotator
from algorithms.scheduler import AdaptiveScheduler


class SmartEngine:
    """قلب النظام المسؤول عن النشر التلقائي والتعلم الذاتي."""

    def __init__(
        self,
        database: Database,
        session_manager: SessionManager,
        on_status_change: Any | None = None,
    ) -> None:
        """تهيئة المحرك بالمكونات الأساسية."""
        self.db = database
        self.session_manager = session_manager
        self.client: TelegramClient | None = None

        self.distributor = SmartDistributor()
        self.rotator = SmartRotator()
        self.scheduler = AdaptiveScheduler(
            min_delay=Config.MIN_DELAY,
            max_delay=Config.MAX_DELAY,
            sleep_start=Config.SLEEP_START,
            sleep_end=Config.SLEEP_END,
        )

        self.state: str = "stopped"  # stopped / running / paused
        self.current_cycle_id: int | None = None
        self._task: asyncio.Task[Any] | None = None
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._stop_event = asyncio.Event()
        self.on_status_change = on_status_change
        self._last_learning_at: datetime | None = None
        self._last_discovery_at: datetime | None = None
        self._consecutive_errors = 0
        self._smart_pause_count = 0

    # ───────────────────────────────────────────────
    # تهيئة الجلسة والاتصال
    # ───────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        """هل المحرك يعمل حالياً؟"""
        return self.state == "running"

    @property
    def is_paused(self) -> bool:
        """هل المحرك متوقف مؤقتاً؟"""
        return self.state == "paused"

    async def init(self) -> None:
        """تهيئة الجلسة والاتصال بـ Telegram."""
        if self.client is None:
            self.client = await self.session_manager.get_client(auto_create=False)

    async def _ensure_client(self) -> TelegramClient:
        """التأكد من أن العميل متصل ومصرح له، مع إعادة الاتصال عند الحاجة."""
        if not self.client:
            await self.init()

        assert self.client is not None

        for attempt, wait in enumerate([2, 5, 10, 15, 30, 60], start=1):
            try:
                if not self.client.is_connected():
                    await self.client.connect()
                if await self.client.is_user_authorized():
                    return self.client
                # الجلسة لم تعد صالحة
                raise RuntimeError(
                    "الجلسة غير مصرح بها. أرسل /login في البوت لإعادة تسجيل الدخول."
                )
            except Exception as exc:
                if attempt == 6:
                    raise RuntimeError(f"تعذر الاتصال بـ Telegram بعد عدة محاولات: {exc}")
                if self.on_status_change:
                    await self.on_status_change(
                        f"🌐 محاولة إعادة اتصال {attempt}/6 بعد {wait} ثانية..."
                    )
                await self._interruptible_sleep(wait)

        return self.client

    # ───────────────────────────────────────────────
    # اكتشاف المجموعات
    # ───────────────────────────────────────────────

    async def discover_groups(self, notify: bool = True) -> dict[str, Any]:
        """اكتشاف المجموعات والقنوات المتاحة للنشر وتخزينها."""
        client = await self._ensure_client()
        discovery = GroupDiscovery(client, self.db)
        summary = await discovery.discover(notify=notify)
        self._last_discovery_at = datetime.now(timezone.utc)

        # التكيف التلقائي مع العدد الجديد للمجموعات
        await self._adapt_daily_targets()

        if notify and self.on_status_change:
            text = (
                f"✅ تم اكتشاف {summary['total_discovered']} مجموعة/قناة.\n"
                f"جديدة: {summary['added']} | محدثة: {summary['updated']} | "
                f"بدون صلاحية: {summary['no_permission']}"
            )
            await self.on_status_change(text)
        return summary

    async def _adapt_daily_targets(self) -> None:
        """إعادة حساب عدد المنشورات اليومية لكل مجموعة حسب العدد الإجمالي."""
        groups = await self.db.get_groups()
        total = len(groups)
        if total <= 50:
            target = 4
        elif total <= 300:
            target = 2
        else:
            target = 1

        if total <= 50:
            min_delay, max_delay = 60, 300
        elif total <= 300:
            min_delay, max_delay = 180, 600
        else:
            min_delay, max_delay = 300, 900

        Config.MIN_DELAY = min_delay
        Config.MAX_DELAY = max_delay
        self.scheduler.min_delay = min_delay
        self.scheduler.max_delay = max_delay

        for group in groups:
            await self.db.update_group_target(group["id"], target)

        if self.on_status_change:
            await self.on_status_change(
                f"⚙️ تم تكيف الإعدادات: {target} منشور/يوم لكل مجموعة، "
                f"تأخير {min_delay}-{max_delay} ثانية."
            )

    # ───────────────────────────────────────────────
    # التحكم في المحرك
    # ───────────────────────────────────────────────

    async def start(self) -> None:
        """بدء المحرك الذكي ودورة النشر."""
        await self.init()

        if self.state == "running":
            return

        # اكتشاف المجموعات عند التشغيل إذا لم تكن موجودة
        existing = await self.db.get_groups()
        if not existing:
            if self.on_status_change:
                await self.on_status_change("🔍 جاري اكتشاف المجموعات...")
            await self.discover_groups()
        else:
            # اكتشاف دوري خفيف للتحقق من أي تغييرات
            await self._maybe_discover_groups()

        # تحميل أو إنشاء دورة
        state = await self.db.get_state()
        if state["state"] == "paused" and state["current_cycle_id"]:
            self.current_cycle_id = state["current_cycle_id"]
            self.state = "running"
        else:
            self.current_cycle_id = await self.db.start_cycle()
            self.state = "running"

        await self.db.set_state("running", self.current_cycle_id)

        self._consecutive_errors = 0
        self._smart_pause_count = 0

        if self.on_status_change:
            await self.on_status_change("▶️ بدأ المحرك الذكي في العمل.")

        self._stop_event.clear()
        self._pause_event.set()
        self._task = asyncio.create_task(self._worker())

    async def pause(self) -> None:
        """إيقاف مؤقت مع حفظ الحالة."""
        if self.state != "running":
            return
        self.state = "paused"
        self._pause_event.clear()
        await self.db.set_state("paused", self.current_cycle_id)
        if self.on_status_change:
            await self.on_status_change("⏸️ تم إيقاف المحرك مؤقتاً.")

    async def resume(self) -> None:
        """استئناف المحرك من نفس النقطة."""
        if self.state != "paused":
            return
        self.state = "running"
        self._pause_event.set()
        await self.db.set_state("running", self.current_cycle_id)
        if self.on_status_change:
            await self.on_status_change("🔄 تم استئناف المحرك.")

    async def stop(self) -> None:
        """إيقاف كامل وإعادة ضبط الحالة."""
        self.state = "stopped"
        self._stop_event.set()
        self._pause_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self.current_cycle_id:
            await self.db.end_cycle(self.current_cycle_id, status="stopped")
        await self.db.reset_state()
        self.current_cycle_id = None
        if self.on_status_change:
            await self.on_status_change("🛑 تم إيقاف المحرك بالكامل.")

    async def reset(self) -> None:
        """إعادة ضبط النظام دون حذف الجلسة أو المجموعات."""
        await self.stop()
        await self.db.reset_state()
        await self.db.reset_daily_counts()
        groups = await self.db.get_groups()
        for group in groups:
            if group["status"] in ("restricted", "slowmode"):
                await self.db.update_group_status(group["id"], "active")
        if self.on_status_change:
            await self.on_status_change("🔄 تم إعادة ضبط النظام لبدء عمل جديد.")

    # ───────────────────────────────────────────────
    # العامل الداخلي
    # ───────────────────────────────────────────────

    async def _worker(self) -> None:
        """الحلقة الرئيسية للنشر المستمر مع الحماية من التجمد."""
        while self.state != "stopped" and not self._stop_event.is_set():
            try:
                await self._pause_event.wait()
                if self._stop_event.is_set():
                    break

                # فحص صحة الحساب
                await self._check_account_health()

                # احترام أوقات النوم
                sleep_delay = self.scheduler.sleep_until_wake()
                if sleep_delay > 0:
                    if self.on_status_change:
                        hours = int(sleep_delay / 3600)
                        await self.on_status_change(
                            f"😴 وقت النوم. سيتوقف النشر لمدة {hours} ساعة."
                        )
                    await self._interruptible_sleep(sleep_delay)
                    await self.db.reset_daily_counts()
                    continue

                await self._maybe_reset_daily_counts()
                await self._unrestrict_due_groups()
                await self._maybe_run_learning()
                await self._maybe_discover_groups()

                # التحقق من معدل الأخطاء وتفعيل الوقفة الذكية
                should_pause = await self._smart_pause_on_errors()
                if should_pause:
                    await self._interruptible_sleep(1800)  # 30 دقيقة
                    continue

                # تحميل الرسالة الحالية
                message_record = await self.db.get_message()
                message_text = message_record["text"]
                use_html = bool(message_record["use_html"])

                if not message_text.strip():
                    if self.on_status_change:
                        await self.on_status_change(
                            "⚠️ لا يوجد منشور محدد. أرسل /setmsg لتعيين النص."
                        )
                    await self._interruptible_sleep(60)
                    continue

                # جلب المجموعات النشطة
                groups = await self.db.get_groups(["active"])
                if not groups:
                    if self.on_status_change:
                        await self.on_status_change(
                            "⚠️ لا توجد مجموعات نشطة للنشر. سأحاول الاكتشاف مجدداً."
                        )
                    await self.discover_groups(notify=False)
                    await self._interruptible_sleep(300)
                    continue

                # اختيار المجموعة التالية
                current_hour = datetime.now().hour
                group = self.rotator.select_next(groups, current_hour=current_hour)
                if not group:
                    await self._interruptible_sleep(60)
                    continue

                # الوقاية من النشر المكرر
                message_hash = hashlib.sha256(message_text.encode("utf-8")).hexdigest()
                if await self.db.has_recent_post(group["id"], message_hash, hours=1):
                    if self.on_status_change:
                        await self.on_status_change(
                            f"⚠️ تم تخطي {group['title']} لتجنب التكرار."
                        )
                    await self._interruptible_sleep(10)
                    continue

                # محاكاة التفكير
                think_delay = random.uniform(0.5, 3.0)
                await self._interruptible_sleep(think_delay)

                # محاكاة الكتابة
                typing_delay = self.scheduler.typing_delay(message_text)
                await self._interruptible_sleep(typing_delay)

                # محاكاة الأخطاء الكتابية (8%)
                final_text = message_text
                if random.random() < 0.08:
                    final_text = self._introduce_human_typo(message_text, use_html)

                # النشر
                await self._post_to_group(group, final_text, use_html, message_hash)
                self._consecutive_errors = 0  # إعادة تعيين عند نجاح أي نشر

                # حساب التأخير التكيفي
                cycle_progress = await self._cycle_progress(groups)
                delay = self.scheduler.compute_delay(group, cycle_progress, current_hour)
                delay += self.scheduler.extra_distraction(probability=0.05)

                if self.on_status_change:
                    await self.on_status_change(
                        f"⏳ الانتظار {int(delay)} ثانية قبل المنشور التالي."
                    )
                await self._interruptible_sleep(delay)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._consecutive_errors += 1
                if self.on_status_change:
                    await self.on_status_change(f"❌ خطأ داخلي: {exc}")
                await self.db.log_error("worker", type(exc).__name__, str(exc))
                await self._interruptible_sleep(60)

    # ───────────────────────────────────────────────
    # النشر ومعالجة الأخطاء
    # ───────────────────────────────────────────────

    async def _post_to_group(
        self,
        group: dict[str, Any],
        text: str,
        use_html: bool,
        message_hash: str,
    ) -> None:
        """إرسال رسالة إلى مجموعة مع معالجة شاملة للأخطاء."""
        client = await self._ensure_client()
        parse_mode = "html" if use_html else None

        try:
            # استخدام مهلة لمنع التجمد
            await asyncio.wait_for(
                client.send_message(
                    group["id"],
                    text,
                    parse_mode=parse_mode,
                    link_preview=False,
                ),
                timeout=60,
            )
            await self.db.log_post(
                group_id=group["id"],
                group_title=group["title"],
                message_text=text[:500],
                status="success",
                cycle_id=self.current_cycle_id,
            )
            await self.db.update_group_after_post(group["id"], success=True)
            await self.db.record_post_hash(group["id"], message_hash)
            if self.on_status_change:
                await self.on_status_change(
                    f"✅ نُشرت الرسالة في: {group['title']}"
                )
            self._consecutive_errors = 0
        except asyncio.TimeoutError:
            await self._handle_post_error(
                group, text, "TIMEOUT", "انتهت المهلة أثناء إرسال الرسالة",
                temporary=True, wait_seconds=60,
            )
        except Exception as exc:
            await self._classify_and_handle_error(group, text, exc)

    async def _classify_and_handle_error(
        self, group: dict[str, Any], text: str, exc: Exception
    ) -> None:
        """تصنيف الخطأ واتخاذ الإجراء المناسب."""
        error_code = type(exc).__name__
        error_message = str(exc)

        # أخطاء تتطلب إيقاف المحرك فوراً (حظر الحساب)
        if isinstance(exc, (UserDeactivatedBanError, AuthKeyDuplicatedError)):
            if self.on_status_change:
                await self.on_status_change(
                    f"🚨 تم حظر الحساب أو الجلسة: {error_message}\n"
                    "تم إيقاف المحرك. أرسل /login لإعادة التسجيل."
                )
            await self.stop()
            return

        # FloodWait: خطأ مؤقت — الانتظار دون نقل المجموعة إلى الممنوعات
        if isinstance(exc, FloodWaitError):
            wait = int(exc.seconds)
            await self._log_failed_post(group, text, "FLOOD_WAIT", error_message, wait)
            if self.on_status_change:
                await self.on_status_change(
                    f"⏳ FloodWait في {group['title']}: انتظار {wait} ثانية."
                )
            await self._interruptible_sleep(wait)
            return

        # SlowMode: خطأ مؤقت — الانتظار دون نقل المجموعة إلى الممنوعات
        if isinstance(exc, SlowModeWaitError):
            wait = int(getattr(exc, "seconds", 60))
            await self._log_failed_post(group, text, "SLOW_MODE", error_message, wait)
            if self.on_status_change:
                await self.on_status_change(
                    f"🐢 SlowMode في {group['title']}: انتظار {wait} ثانية."
                )
            await self._interruptible_sleep(wait)
            return

        # أخطاء عدم صلاحية النشر أو الانضمام — نقل المجموعة فوراً إلى الممنوعات
        if isinstance(
            exc,
            (
                ChatWriteForbiddenError,
                UserBannedInChannelError,
                UserNotParticipantError,
                ChatAdminRequiredError,
                PeerIdInvalidError,
                ChannelInvalidError,
            ),
        ):
            await self.db.update_group_status(
                group_id=group["id"],
                status="banned",
                failure_reason=error_code,
            )
            await self._log_failed_post(group, text, error_code.upper(), error_message)
            if self.on_status_change:
                await self.on_status_change(
                    f"🚫 لا يمكن النشر في {group['title']} - نُقلت إلى الممنوعات."
                )
            return

        # أي خطأ RPC آخر: نقل إلى الممنوعات لتجنب المحاولات الفاشلة
        if isinstance(exc, RPCError):
            await self.db.update_group_status(
                group_id=group["id"],
                status="banned",
                failure_reason=f"{error_code}: {error_message}",
            )
            await self._log_failed_post(group, text, error_code.upper(), error_message)
            if self.on_status_change:
                await self.on_status_change(
                    f"🚫 خطأ غير متوقع في {group['title']} - نُقلت إلى الممنوعات."
                )
            return

        # خطأ عام مؤقت غير RPC
        await self._handle_post_error(
            group, text, error_code.upper(), error_message, temporary=True, wait_seconds=30,
        )

    async def _handle_post_error(
        self,
        group: dict[str, Any],
        text: str,
        error_code: str,
        error_message: str,
        temporary: bool = False,
        wait_seconds: int = 30,
    ) -> None:
        """معالجة الأخطاء المؤقتة أو غير المعروفة."""
        await self._log_failed_post(group, text, error_code, error_message, wait_seconds)
        if temporary:
            if self.on_status_change:
                await self.on_status_change(
                    f"⚠️ خطأ مؤقت في {group['title']}: {error_code}. سأعيد المحاولة لاحقاً."
                )
            await self._interruptible_sleep(wait_seconds)
        else:
            if self.on_status_change:
                await self.on_status_change(
                    f"❌ فشل في {group['title']}: {error_code}"
                )

    async def _log_failed_post(
        self,
        group: dict[str, Any],
        text: str,
        error_code: str,
        error_message: str,
        wait_seconds: int | None = None,
    ) -> None:
        """تسجيل محاولة نشر فاشلة."""
        await self.db.log_post(
            group_id=group["id"],
            group_title=group["title"],
            message_text=text[:500],
            status="failed",
            error_code=error_code,
            error_message=error_message,
            wait_seconds=wait_seconds,
            cycle_id=self.current_cycle_id,
        )
        await self.db.log_error(
            source="post",
            error_code=error_code,
            error_message=error_message,
            context=f"group_id={group['id']}, title={group['title']}",
        )
        self._consecutive_errors += 1

    # ───────────────────────────────────────────────
    # أدوات مساعدة
    # ───────────────────────────────────────────────

    async def _check_account_health(self) -> None:
        """فحص سريع للتأكد من أن الحساب لم يُحظر."""
        try:
            client = await self._ensure_client()
            me = await asyncio.wait_for(client.get_me(), timeout=30)
            if not me:
                raise RuntimeError("لم يتم العثور على معلومات الحساب")
        except (UserDeactivatedBanError, AuthKeyDuplicatedError):
            if self.on_status_change:
                await self.on_status_change(
                    "🚨 الحساب محظور أو الجلسة مكررة. سيتم إيقاف المحرك."
                )
            await self.stop()
        except Exception:
            # سيتم إعادة المحاولة لاحقاً
            pass

    async def _smart_pause_on_errors(self) -> bool:
        """الوقفة الذكية عند ارتفاع معدل الأخطاء."""
        failed, total = await self.db.get_recent_error_rate(minutes=30)
        rate = failed / total

        if rate >= 0.7:
            self._smart_pause_count += 1
            if self._smart_pause_count >= 3:
                if self.on_status_change:
                    await self.on_status_change(
                        "🛑 معدل الأخطاء مرتفع بشكل استثنائي. تم إيقاف المحرك."
                    )
                await self.stop()
                return False
            if self.on_status_change:
                await self.on_status_change(
                    f"⏸️ الوقفة الذكية {self._smart_pause_count}/3: "
                    f"معدل الأخطاء {int(rate * 100)}%. انتظار 30 دقيقة."
                )
            return True
        return False

    async def _maybe_discover_groups(self) -> None:
        """اكتشاف دوري للمجموعات كل 6 ساعات."""
        now = datetime.now(timezone.utc)
        if (
            self._last_discovery_at is None
            or (now - self._last_discovery_at).total_seconds() >= 6 * 3600
        ):
            if self.on_status_change:
                await self.on_status_change("🔍 جاري الاكتشاف الدوري للمجموعات...")
            await self.discover_groups(notify=False)

    async def _interruptible_sleep(self, seconds: float) -> None:
        """نوم قابل للمقاطعة عند الإيقاف المؤقت أو التوقف."""
        remaining = seconds
        while remaining > 0 and not self._stop_event.is_set():
            await self._pause_event.wait()
            if self._stop_event.is_set():
                break
            step = min(remaining, 1.0)
            await asyncio.sleep(step)
            remaining -= step

    async def _maybe_reset_daily_counts(self) -> None:
        """إعادة تعيين عداد اليومية عند منتصف الليل تقريباً."""
        now = datetime.now()
        if now.hour == Config.SLEEP_END and now.minute < 5:
            await self.db.reset_daily_counts()

    async def _unrestrict_due_groups(self) -> None:
        """إعادة تفعيل المجموعات التي انتهى وقت انتظارها."""
        groups = await self.db.get_groups(["restricted", "slowmode"])
        now = datetime.now(timezone.utc)
        for group in groups:
            restricted_until = group.get("restricted_until")
            if not restricted_until:
                continue
            try:
                due = datetime.fromisoformat(restricted_until)
                if due.tzinfo is None:
                    due = due.replace(tzinfo=timezone.utc)
                if now >= due:
                    await self.db.update_group_status(group["id"], "active")
            except ValueError:
                continue

    async def _maybe_run_learning(self) -> None:
        """تشغيل محرك التعلم كل 6 ساعات."""
        now = datetime.now(timezone.utc)
        if (
            self._last_learning_at is None
            or (now - self._last_learning_at).total_seconds() >= 6 * 3600
        ):
            insights = await self.run_learning()
            self._last_learning_at = now
            if insights and self.on_status_change:
                await self.on_status_change(
                    f"💡 اكتمل تحليل الأداء: {len(insights.get('best_hours', []))} ساعة مثالية."
                )

    async def _cycle_progress(self, groups: list[dict[str, Any]]) -> float:
        """حساب نسبة تقدم الدورة الحالية."""
        if not groups:
            return 0.5
        total_target = sum(g.get("daily_posts_target", 2) for g in groups)
        total_done = sum(g.get("daily_posts_count", 0) for g in groups)
        if total_target <= 0:
            return 0.5
        return min(1.0, max(0.0, total_done / total_target))

    def _introduce_human_typo(self, text: str, use_html: bool) -> str:
        """إحداث خطأ كتابي بسيط لمحاكاة الإنسان."""
        if not text:
            return text

        if use_html and text.count("<") > 0:
            return text

        index = random.randint(0, max(0, len(text) - 2))
        char = text[index]

        if char in string.whitespace or char in string.punctuation:
            return text

        typo_type = random.choice(["duplicate", "swap"])
        if typo_type == "duplicate":
            return text[: index + 1] + char + text[index + 1 :]

        adjacent = {
            "ا": "أ",
            "أ": "ا",
            "ب": "ت",
            "ت": "ب",
            "ي": "ى",
            "ى": "ي",
            "s": "a",
            "a": "s",
        }
        replacement = adjacent.get(char, char)
        return text[:index] + replacement + text[index + 1 :]

    # ───────────────────────────────────────────────
    # التعلم الذاتي
    # ───────────────────────────────────────────────

    async def run_learning(self) -> dict[str, Any]:
        """تحليل الأداء واستخلاص الأنماط."""
        logs = await self._get_recent_logs()
        if not logs:
            return {}

        hour_success: dict[int, list[bool]] = {}
        for log in logs:
            posted = datetime.fromisoformat(log["posted_at"])
            hour = posted.hour
            hour_success.setdefault(hour, []).append(log["status"] == "success")

        best_hours = []
        for hour, results in hour_success.items():
            if len(results) >= 3 and (sum(results) / len(results)) > 0.7:
                best_hours.append(hour)

        period_adjustments = {}
        period_names = {
            "dawn": list(range(6, 9)),
            "morning": list(range(9, 12)),
            "noon": list(range(12, 15)),
            "evening": list(range(15, 21)),
            "night": list(range(21, 23)),
        }
        for period, hours in period_names.items():
            results = []
            for h in hours:
                if h in hour_success:
                    results.extend(hour_success[h])
            if results:
                rate = sum(results) / len(results)
                factor = 0.9 + (rate * 0.4)
                period_adjustments[period] = round(factor, 2)

        model = {
            "best_hours": best_hours,
            "period_adjustments": period_adjustments,
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
        }
        await self.db.save_learning_model("distributor", model)
        return model

    async def _get_recent_logs(self) -> list[dict[str, Any]]:
        """جلب السجلات الأخيرة للتحليل."""
        import aiosqlite
        async with aiosqlite.connect(self.db.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM post_logs ORDER BY posted_at DESC LIMIT 500"
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
