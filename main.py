"""نقطة الدخول الرئيسية لنظام Telegram Smart Poster."""
import asyncio
import signal
import sys

from config import Config
from control_bot import ControlBot
from database import Database
from session_manager import SessionManager
from smart_engine import SmartEngine


async def main() -> int:
    """إعداد المكونات وتشغيل النظام."""
    # التحقق من الإعدادات
    errors = Config.validate()
    if errors:
        print("❌ أخطاء في الإعدادات:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print("🚀 جاري تهيئة Telegram Smart Poster...")

    # تهيئة قاعدة البيانات
    db = Database(Config.DATABASE_PATH)
    await db.init()

    # تهيئة مدير الجلسة
    session_manager = SessionManager(Config.SESSION_PATH)

    # تهيئة المحرك الذكي
    engine = SmartEngine(db, session_manager)

    # محاولة تحميل الجلسة
    try:
        await engine.init()
        print("✅ تم تحميل الجلسة.")
    except Exception as exc:
        print(f"⚠️ لا يوجد جلسة صالحة بعد: {exc}")

    async def _background_discovery() -> None:
        """اكتشاف أولي للمجموعات دون حظر تشغيل البوت."""
        if not engine.client:
            return
        print("🔍 جاري اكتشاف المجموعات في الخلفية...")
        try:
            summary = await engine.discover_groups(notify=False)
            print(
                f"✅ تم اكتشاف {summary.get('total_discovered', 0)} مجموعة/قناة "
                f"({summary.get('no_permission', 0)} بدون صلاحية)."
            )
        except Exception as exc:
            print(f"⚠️ فشل الاكتشاف التلقائي: {exc}")

    async def _periodic_discovery() -> None:
        """اكتشاف دوري للمجموعات كل 6 ساعات بشكل مستقل عن حالة المحرك."""
        while True:
            try:
                await asyncio.sleep(6 * 60 * 60)  # 6 ساعات
                if not engine.client:
                    continue
                print("🔄 جاري الاكتشاف الدوري للمجموعات...")
                summary = await engine.discover_groups(notify=False)
                print(
                    f"✅ اكتشاف دوري: {summary.get('total_discovered', 0)} مجموعة/قناة "
                    f"({summary.get('no_permission', 0)} بدون صلاحية)."
                )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                print(f"⚠️ فشل الاكتشاف الدوري: {exc}")

    # تهيئة بوت التحكم
    bot = ControlBot(db, engine)

    # استئناف الحالة السابقة إذا كانت متوقفة مؤقتاً
    state = await db.get_state()
    if state["state"] == "paused":
        await bot._notify_owner("🔔 النظام يعمل. كان متوقفاً مؤقتاً، يمكنك الضغط على استئناف.")

    # تشغيل البوت
    await bot.run()

    # إرسال رسالة جاهزية للمالك مع لوحة التحكم
    await bot.send_welcome()

    # بدء اكتشاف المجموعات في الخلفية (بعد تشغيل البوت)
    asyncio.create_task(_background_discovery())

    # بدء الاكتشاف الدوري المستقل عن حالة المحرك
    asyncio.create_task(_periodic_discovery())

    # انتظار إشارة الإيقاف
    stop_event = asyncio.Event()

    def _signal_handler(_sig: int, _frame: object) -> None:
        print("\n🛑 تم استلام إشارة الإيقاف...")
        stop_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        await stop_event.wait()
    finally:
        await engine.stop()
        await bot.stop()
        print("👋 تم إيقاف النظام بأمان.")

    return 0


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 تم إيقاف النظام يدوياً.")
        sys.exit(0)
    else:
        sys.exit(exit_code)
