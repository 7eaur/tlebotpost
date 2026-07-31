"""مدير اكتشاف المجموعات والقنوات وفحص صلاحيات النشر."""
import asyncio
from datetime import datetime, timezone
from typing import Any

from telethon import TelegramClient
from telethon.errors import RPCError
from telethon.tl.types import (
    Channel,
    Chat,
    ChatForbidden,
    ChannelForbidden,
)

from config import Config
from database import Database


class GroupDiscovery:
    """يكتشف المجموعات والقنوات ويحدد إمكانية النشر فيها."""

    def __init__(self, client: TelegramClient, database: Database) -> None:
        self.client = client
        self.db = database

    async def discover(self, notify: bool = False) -> dict[str, Any]:
        """جلب كل الدردشات وتصنيفها إلى Active أو Banned وحفظها."""
        if not self.client:
            raise RuntimeError("عميل Telegram غير متصل")

        discovered_ids: list[int] = []
        added = 0
        updated = 0
        no_permission = 0

        async for dialog in self.client.iter_dialogs():
            entity = dialog.entity
            result = self._classify(entity)
            if not result:
                continue

            group_id = result["id"]
            discovered_ids.append(group_id)

            existing = await self.db.get_group(group_id)
            if existing:
                updated += 1
            else:
                added += 1

            if not result["can_post"]:
                no_permission += 1

            # الحفاظ على "excluded" السابق كـ banned؟ الآن نستخدم قائمتين فقط
            status = result["status"]
            failure_reason = result.get("failure_reason")
            if existing and existing["status"] == "excluded":
                status = "banned"
                failure_reason = failure_reason or "استبعاد يدوي"

            group_info = {
                "id": group_id,
                "title": result["title"],
                "username": result.get("username"),
                "type": result["type"],
                "status": status,
                "can_post": result["can_post"],
                "daily_posts_target": Config.DEFAULT_POSTS_PER_DAY,
                "failure_reason": failure_reason,
            }
            await self.db.upsert_group(group_info)
            await self.db.set_group_discovered(group_id)

        # وضع علامة محذوف على المجموعات التي لم تعد في القائمة
        await self.db.mark_groups_deleted(discovered_ids)

        return {
            "added": added,
            "updated": updated,
            "no_permission": no_permission,
            "total_discovered": len(discovered_ids),
        }

    def _classify(self, entity: Any) -> dict[str, Any] | None:
        """تصنيف كيان Telegram إلى Active أو Banned."""
        if isinstance(entity, (ChatForbidden, ChannelForbidden)):
            return None

        if isinstance(entity, Channel):
            is_channel = bool(getattr(entity, "broadcast", False))
            is_megagroup = bool(getattr(entity, "megagroup", False))
            if not (is_channel or is_megagroup):
                return None
            group_type = "channel" if is_channel else "group"
            title = getattr(entity, "title", "قناة/مجموعة")
            username = getattr(entity, "username", None)
        elif isinstance(entity, Chat):
            group_type = "group"
            title = getattr(entity, "title", "مجموعة")
            username = None
        else:
            return None

        can_post, status, failure_reason = self._check_post_permission(entity)

        return {
            "id": entity.id,
            "title": title,
            "username": username,
            "type": group_type,
            "can_post": can_post,
            "status": status,
            "failure_reason": failure_reason,
        }

    def _check_post_permission(
        self, entity: Any
    ) -> tuple[bool, str, str | None]:
        """التحقق من صلاحيات النشر باستخدام بيانات الكيان المحلية."""
        # القنوات: نتحقق من post_messages عبر حقوق المشرف أو صفة المنشئ
        if isinstance(entity, Channel) and getattr(entity, "broadcast", False):
            admin_rights = getattr(entity, "admin_rights", None)
            if admin_rights and getattr(admin_rights, "post_messages", False):
                return True, "active", None
            if getattr(entity, "creator", False):
                return True, "active", None
            return False, "banned", "لا توجد صلاحية نشر في القناة"

        # المجموعات: نتحقق من send_messages عبر حقوق الحظر الافتراضية
        if isinstance(entity, (Chat, Channel)):
            if getattr(entity, "left", False):
                return False, "banned", "الحساب غير عضو في المجموعة"
            banned_rights = getattr(entity, "default_banned_rights", None)
            if banned_rights and getattr(banned_rights, "send_messages", False):
                return False, "banned", "إرسال الرسائل محظور في المجموعة"
            return True, "active", None

        return False, "banned", "نوع دردشة غير مدعوم"
