"""إدارة إعدادات النظام من متغيرات البيئة."""
import os
from dotenv import load_dotenv

# تحميل متغيرات البيئة من ملف .env
load_dotenv()


class Config:
    """تهيئة وقراءة جميع الإعدادات اللازمة لتشغيل النظام."""

    API_ID: int = int(os.getenv("API_ID", "0"))
    API_HASH: str = os.getenv("API_HASH", "")
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    OWNER_ID: int = int(os.getenv("OWNER_ID", "0"))
    SESSION_ENCRYPTION_KEY: str = os.getenv("SESSION_ENCRYPTION_KEY", "")

    # الإعدادات الافتراضية القابلة للتخصيص
    MIN_DELAY: int = int(os.getenv("MIN_DELAY", "300"))  # ثوانٍ
    MAX_DELAY: int = int(os.getenv("MAX_DELAY", "900"))  # ثوانٍ
    SLEEP_START: int = int(os.getenv("SLEEP_START", "23"))  # ساعة
    SLEEP_END: int = int(os.getenv("SLEEP_END", "6"))  # ساعة
    DEFAULT_POSTS_PER_DAY: int = int(os.getenv("DEFAULT_POSTS_PER_DAY", "2"))

    # أسماء الملفات الثابتة
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "data/telegram_smart_poster.db")
    SESSION_PATH: str = os.getenv("SESSION_PATH", "data/session.enc")

    @classmethod
    def validate(cls) -> list[str]:
        """التحقق من وجود الإعدادات الأساسية وإرجاع قائمة الأخطاء."""
        errors = []
        if cls.API_ID == 0:
            errors.append("API_ID غير مضبوط في ملف .env")
        if not cls.API_HASH:
            errors.append("API_HASH غير مضبوط في ملف .env")
        if not cls.BOT_TOKEN:
            errors.append("BOT_TOKEN غير مضبوط في ملف .env")
        if cls.OWNER_ID == 0:
            errors.append("OWNER_ID غير مضبوط في ملف .env")
        if not cls.SESSION_ENCRYPTION_KEY:
            errors.append("SESSION_ENCRYPTION_KEY غير مضبوط في ملف .env")
        if len(cls.SESSION_ENCRYPTION_KEY.encode()) < 32:
            errors.append("SESSION_ENCRYPTION_KEY يجب أن يكون 32 بايت على الأقل")
        if cls.MIN_DELAY >= cls.MAX_DELAY:
            errors.append("MIN_DELAY يجب أن يكون أصغر من MAX_DELAY")
        return errors
