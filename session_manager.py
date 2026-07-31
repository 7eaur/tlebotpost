"""إدارة جلسة Telegram مع التشفير الآمن."""
import os
import base64
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from telethon import TelegramClient
from telethon.sessions import StringSession

from config import Config


class SessionManager:
    """إنشاء جلسة مشفرة أو تحميلها من ملف مشفر سابق."""

    def __init__(self, session_path: str = "data/session.enc") -> None:
        """تهيئة مسار الجلسة ومفتاح التشفير."""
        self.session_path = session_path
        self._key = self._derive_key(Config.SESSION_ENCRYPTION_KEY)
        self._fernet = Fernet(self._key)
        os.makedirs(os.path.dirname(self.session_path), exist_ok=True)

    def _derive_key(self, raw_key: str) -> bytes:
        """اشتقاق مفتاح Fernet آمن من أي سلسلة مفتاح."""
        salt = b"telegram_smart_poster_salt_v1"
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=480000,
        )
        key_bytes = kdf.derive(raw_key.encode("utf-8"))
        return base64.urlsafe_b64encode(key_bytes)

    def _load_encrypted_session(self) -> str | None:
        """قراءة سلسلة الجلسة المشفرة من الملف."""
        if not os.path.exists(self.session_path):
            return None
        try:
            with open(self.session_path, "rb") as f:
                encrypted = f.read()
            return self._fernet.decrypt(encrypted).decode("utf-8")
        except InvalidToken:
            raise RuntimeError(
                "فشل فك تشفير الجلسة. تأكد من أن SESSION_ENCRYPTION_KEY صحيح ولم يتغير."
            )
        except Exception as exc:
            raise RuntimeError(f"خطأ أثناء قراءة ملف الجلسة: {exc}")

    def _save_encrypted_session(self, session_string: str) -> None:
        """تشفير وحفظ سلسلة الجلسة في الملف."""
        encrypted = self._fernet.encrypt(session_string.encode("utf-8"))
        with open(self.session_path, "wb") as f:
            f.write(encrypted)
        os.chmod(self.session_path, 0o600)

    def _new_client(self, session: StringSession) -> TelegramClient:
        """إنشاء عميل Telegram جديد بإعدادات متناسقة."""
        return TelegramClient(
            session,
            Config.API_ID,
            Config.API_HASH,
            device_model="Telegram Smart Poster",
            app_version="1.0",
            system_version="1.0",
            lang_code="ar",
        )

    async def get_client(self, auto_create: bool = True) -> TelegramClient:
        """إرجاع عميل Telegram جاهز للاستخدام."""
        session_string = self._load_encrypted_session()

        if session_string:
            client = self._new_client(StringSession(session_string))
            await client.connect()
            if await client.is_user_authorized():
                return client
            # الجلسة منتهية أو غير صالحة
            await client.disconnect()
            session_string = None

        if not auto_create:
            raise RuntimeError(
                "لا توجد جلسة محفوظة. يرجى إرسال /login في البوت لإنشاء جلسة جديدة."
            )

        raise RuntimeError(
            "لا توجد جلسة محفوظة. يرجى إرسال /login في البوت لإنشاء جلسة جديدة."
        )

    async def start_login(self, phone: str) -> tuple[TelegramClient, str]:
        """بدء تسجيل الدخول وإرسال رمز التحقق، وإرجاع العميل و phone_code_hash."""
        client = self._new_client(StringSession())
        await client.connect()
        sent = await client.send_code_request(phone)
        return client, sent.phone_code_hash

    async def complete_login(
        self,
        client: TelegramClient,
        phone: str,
        code: str,
        phone_code_hash: str,
        password: str | None = None,
    ) -> TelegramClient:
        """إكمال تسجيل الدخول بحفظ الجلسة المشفرة."""
        try:
            await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
        except Exception:
            if password:
                await client.sign_in(password=password)
            else:
                raise

        session_string = client.session.save()
        self._save_encrypted_session(session_string)
        return client

    async def revoke_session(self) -> None:
        """حذف ملف الجلسة المشفرة لإجبار إعادة التسجيل."""
        if os.path.exists(self.session_path):
            os.remove(self.session_path)
