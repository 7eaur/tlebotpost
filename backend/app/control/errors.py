"""User-facing error messages for Telegram control flows."""

from __future__ import annotations

from telethon.errors import (
    ApiIdInvalidError,
    AuthKeyUnregisteredError,
    ChannelInvalidError,
    ChannelPrivateError,
    ChatAdminRequiredError,
    FloodWaitError,
    InviteHashExpiredError,
    InviteHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
)

from app.telegram.session import TelegramSessionError, TelegramSessionNotAuthorized


def friendly_error(error: Exception, *, action: str) -> str:
    """Return a concise Arabic error with the next useful action."""
    if isinstance(error, PhoneNumberInvalidError):
        return (
            "رقم الهاتف غير صحيح. أرسله بالصيغة الدولية الكاملة، مثل: "
            "+967700000000، بدون مسافات أو شرطات."
        )
    if isinstance(error, PhoneCodeInvalidError):
        return "رمز Telegram غير صحيح. أرسل أحدث رمز كما وصل إليك، بدون مسافات."
    if isinstance(error, PhoneCodeExpiredError):
        return "انتهت صلاحية رمز الدخول. ابدأ من جديد بإرسال /login ثم رقم الهاتف."
    if isinstance(error, SessionPasswordNeededError):
        return "الحساب محمي بالتحقق بخطوتين. أرسل: /login_code الرمز كلمة_مرور_التحقق"
    if isinstance(error, FloodWaitError):
        return f"Telegram طلب الانتظار {error.seconds} ثانية قبل إعادة المحاولة."
    if isinstance(error, ApiIdInvalidError):
        return "API_ID أو API_HASH غير صحيحين في إعدادات الاستضافة."
    if isinstance(error, AuthKeyUnregisteredError):
        return "جلسة Telegram غير صالحة. ابدأ تسجيل الدخول من جديد بواسطة /login."
    if isinstance(error, TelegramSessionNotAuthorized):
        return "جلسة الحساب غير مسجلة. اضغط «تسجيل جلسة الحساب» وأكمل الخطوات أولًا."
    if isinstance(error, TelegramSessionError):
        return f"تعذر {action}: {error}"
    if isinstance(error, (UsernameInvalidError, UsernameNotOccupiedError)):
        return "رابط أو اسم القناة غير صحيح، أو القناة غير موجودة. راجع الرابط وأعد المحاولة."
    if isinstance(error, ChannelPrivateError):
        return "القناة خاصة والحساب الحالي لا يملك وصولًا إليها. أضف الحساب إلى القناة أولًا."
    if isinstance(error, ChannelInvalidError):
        return "مرجع القناة غير صالح أو لا يشير إلى قناة/مجموعة يمكن قراءتها."
    if isinstance(error, ChatAdminRequiredError):
        return "الحساب لا يملك الصلاحية المطلوبة للوصول إلى هذه القناة."
    if isinstance(error, (InviteHashInvalidError, InviteHashExpiredError)):
        return "رابط الدعوة الخاص غير صالح أو منتهي. أرسل رابط دعوة جديدًا."
    if isinstance(error, ValueError):
        return str(error)
    if isinstance(error, OSError):
        return "تعذر الاتصال بـ Telegram حاليًا. انتظر قليلًا ثم أعد المحاولة."
    return f"تعذر {action}. الخطأ: {type(error).__name__}. راجع السجل إذا استمرت المشكلة."
