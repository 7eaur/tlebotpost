"""طبقة قاعدة البيانات SQLite باستخدام aiosqlite."""
import os
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

import aiosqlite


class Database:
    """إدارة جميع جداول النظام والعمليات عليها بشكل غير متزامن."""

    def __init__(self, db_path: str = "data/telegram_smart_poster.db") -> None:
        """تهيئة المسار والتأكد من وجود المجلد."""
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

    async def init(self) -> None:
        """إنشاء الجداول المطلوبة عند تشغيل النظام لأول مرة أو ترقيتها."""
        async with aiosqlite.connect(self.db_path) as db:
            # ترقية جدول المجموعات إذا كان موجوداً بنسخة قديمة
            await self._migrate_groups_table(db)

            # جدول المجموعات والقنوات
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS groups (
                    id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    username TEXT,
                    type TEXT NOT NULL,
                    status TEXT DEFAULT 'active' CHECK(status IN ('active', 'restricted', 'banned', 'no_permission', 'slowmode', 'deleted', 'excluded')),
                    can_post INTEGER DEFAULT 1,
                    last_posted_at TEXT,
                    last_discovered_at TEXT,
                    daily_posts_count INTEGER DEFAULT 0,
                    daily_posts_target INTEGER DEFAULT 2,
                    success_rate REAL DEFAULT 1.0,
                    priority INTEGER DEFAULT 0,
                    peak_hours TEXT DEFAULT '9,10,11,18,19,20,21',
                    failure_reason TEXT,
                    restricted_until TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

            # جدول سجل النشر
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS post_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER NOT NULL,
                    group_title TEXT,
                    message_text TEXT,
                    status TEXT NOT NULL CHECK(status IN ('success', 'failed')),
                    error_code TEXT,
                    error_message TEXT,
                    wait_seconds INTEGER,
                    posted_at TEXT NOT NULL,
                    cycle_id INTEGER
                )
                """
            )

            # جدول الدورات
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS cycles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    total_groups INTEGER DEFAULT 0,
                    successful_posts INTEGER DEFAULT 0,
                    failed_posts INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'running' CHECK(status IN ('running', 'paused', 'completed', 'stopped')),
                    queue_snapshot TEXT
                )
                """
            )

            # جدول النص الحالي للمنشور
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS current_message (
                    id INTEGER PRIMARY KEY CHECK(id = 1),
                    text TEXT NOT NULL DEFAULT '',
                    use_html INTEGER DEFAULT 1,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                "INSERT OR IGNORE INTO current_message (id, text, use_html, updated_at) VALUES (1, '', 1, ?)",
                (self.now(),),
            )

            # جدول نماذج التعلم
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS learning_models (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    model_type TEXT NOT NULL,
                    model_data TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

            # جدول حالة النظام (للاستئناف من نفس النقطة)
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS system_state (
                    id INTEGER PRIMARY KEY CHECK(id = 1),
                    state TEXT NOT NULL DEFAULT 'stopped',
                    current_cycle_id INTEGER,
                    queue TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                "INSERT OR IGNORE INTO system_state (id, state, current_cycle_id, queue, updated_at) VALUES (1, 'stopped', NULL, NULL, ?)",
                (self.now(),),
            )

            # جدول الوقاية من النشر المكرر
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS post_hashes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER NOT NULL,
                    message_hash TEXT NOT NULL,
                    posted_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_post_hashes ON post_hashes(group_id, message_hash, posted_at)"
            )

            # جدول سجل الأخطاء التفصيلي
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS error_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    context TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_error_logs_code ON error_logs(error_code, created_at)"
            )

            await db.commit()

    async def _migrate_groups_table(self, db: aiosqlite.Connection) -> None:
        """ترقية جدول groups لدعم حالات جديدة وعمود last_discovered_at."""
        try:
            cursor = await db.execute("PRAGMA table_info(groups)")
            columns = {row[1] for row in await cursor.fetchall()}
        except Exception:
            columns = set()

        if "status" in columns:
            # إعادة إنشاء الجدول لإزالة القيد القديم وإضافة الأعمدة الجديدة
            await db.execute("ALTER TABLE groups RENAME TO groups_old")
            await db.execute(
                """
                CREATE TABLE groups (
                    id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    username TEXT,
                    type TEXT NOT NULL,
                    status TEXT DEFAULT 'active' CHECK(status IN ('active', 'restricted', 'banned', 'no_permission', 'slowmode', 'deleted', 'excluded')),
                    can_post INTEGER DEFAULT 1,
                    last_posted_at TEXT,
                    last_discovered_at TEXT,
                    daily_posts_count INTEGER DEFAULT 0,
                    daily_posts_target INTEGER DEFAULT 2,
                    success_rate REAL DEFAULT 1.0,
                    priority INTEGER DEFAULT 0,
                    peak_hours TEXT DEFAULT '9,10,11,18,19,20,21',
                    failure_reason TEXT,
                    restricted_until TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                INSERT INTO groups
                SELECT id, title, username, type,
                       CASE WHEN status IN ('active','restricted','banned') THEN status ELSE 'active' END,
                       can_post, last_posted_at, NULL, daily_posts_count, daily_posts_target,
                       success_rate, priority, peak_hours, failure_reason, restricted_until,
                       created_at, updated_at
                FROM groups_old
                """
            )
            await db.execute("DROP TABLE groups_old")

    def now(self) -> str:
        """إرجاع الوقت الحالي بصيغة ISO."""
        return datetime.now(timezone.utc).isoformat()

    # ───────────────────────────────────────────────
    # عمليات المجموعات
    # ───────────────────────────────────────────────

    async def upsert_group(self, group: dict[str, Any]) -> None:
        """إضافة مجموعة جديدة أو تحديثها إذا كانت موجودة."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO groups (
                    id, title, username, type, status, can_post, last_posted_at,
                    daily_posts_count, daily_posts_target, success_rate, priority,
                    peak_hours, failure_reason, restricted_until, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    username=excluded.username,
                    type=excluded.type,
                    can_post=excluded.can_post,
                    updated_at=excluded.updated_at
                """,
                (
                    group["id"],
                    group["title"],
                    group.get("username"),
                    group["type"],
                    group.get("status", "active"),
                    1 if group.get("can_post", True) else 0,
                    group.get("last_posted_at"),
                    group.get("daily_posts_count", 0),
                    group.get("daily_posts_target", 2),
                    group.get("success_rate", 1.0),
                    group.get("priority", 0),
                    group.get("peak_hours", "9,10,11,18,19,20,21"),
                    group.get("failure_reason"),
                    group.get("restricted_until"),
                    self.now(),
                    self.now(),
                ),
            )
            await db.commit()

    async def get_groups(self, statuses: list[str] | None = None) -> list[dict[str, Any]]:
        """جلب المجموعات حسب الحالة أو جميعها."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if statuses:
                placeholders = ",".join("?" * len(statuses))
                cursor = await db.execute(
                    f"SELECT * FROM groups WHERE status IN ({placeholders}) ORDER BY updated_at DESC",
                    statuses,
                )
            else:
                cursor = await db.execute("SELECT * FROM groups ORDER BY updated_at DESC")
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_group(self, group_id: int) -> dict[str, Any] | None:
        """جلب مجموعة واحدة بواسطة المعرف."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM groups WHERE id = ?", (group_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def update_group_status(
        self,
        group_id: int,
        status: str,
        failure_reason: str | None = None,
        restricted_until: str | None = None,
    ) -> None:
        """تحديث حالة مجموعة (نشطة / مقيدة / محظورة / لا صلاحية / slowmode / محذوفة)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE groups
                SET status = ?, failure_reason = ?, restricted_until = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, failure_reason, restricted_until, self.now(), group_id),
            )
            await db.commit()

    async def mark_groups_deleted(self, known_ids: list[int]) -> None:
        """وضع علامة محذوف على المجموعات التي لم تعد موجودة في الحساب."""
        if not known_ids:
            return
        async with aiosqlite.connect(self.db_path) as db:
            placeholders = ",".join("?" * len(known_ids))
            await db.execute(
                f"""
                UPDATE groups
                SET status = 'deleted', failure_reason = 'غير موجود في قائمة الحساب', updated_at = ?
                WHERE id NOT IN ({placeholders}) AND status != 'deleted'
                """,
                (self.now(), *known_ids),
            )
            await db.commit()

    async def set_group_discovered(self, group_id: int) -> None:
        """تحديث وقت آخر اكتشاف لمجموعة."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE groups SET last_discovered_at = ?, updated_at = ? WHERE id = ?",
                (self.now(), self.now(), group_id),
            )
            await db.commit()

    async def find_group_by_username_or_id(self, identifier: str) -> dict[str, Any] | None:
        """البحث عن مجموعة بواسطة يوزر أو رابط أو معرف."""
        identifier = identifier.strip().lower()
        # إزالة بادئة t.me/
        if identifier.startswith("https://t.me/"):
            identifier = identifier.split("/")[-1]
        elif identifier.startswith("t.me/"):
            identifier = identifier[5:]
        # إزالة @ إن وجدت
        if identifier.startswith("@"):
            identifier = identifier[1:]

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            # البحث باليوزر
            cursor = await db.execute(
                "SELECT * FROM groups WHERE LOWER(username) = ? LIMIT 1",
                (identifier,),
            )
            row = await cursor.fetchone()
            if row:
                return dict(row)
            # البحث بالمعرف
            if identifier.isdigit() or (identifier.startswith("-") and identifier[1:].isdigit()):
                group_id = int(identifier)
                cursor = await db.execute(
                    "SELECT * FROM groups WHERE id = ? LIMIT 1", (group_id,)
                )
                row = await cursor.fetchone()
                if row:
                    return dict(row)
            return None

    async def get_groups_paginated(
        self,
        page: int = 1,
        page_size: int = 30,
        statuses: list[str] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """جلب مجموعات مرقمة مع العدد الكلي، اختيارياً حسب الحالة."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            offset = (page - 1) * page_size
            if statuses:
                placeholders = ",".join("?" * len(statuses))
                cursor = await db.execute(
                    f"""
                    SELECT * FROM groups
                    WHERE status IN ({placeholders})
                    ORDER BY title
                    LIMIT ? OFFSET ?
                    """,
                    (*statuses, page_size, offset),
                )
                count_cursor = await db.execute(
                    f"SELECT COUNT(*) FROM groups WHERE status IN ({placeholders})",
                    statuses,
                )
            else:
                cursor = await db.execute(
                    "SELECT * FROM groups ORDER BY title LIMIT ? OFFSET ?",
                    (page_size, offset),
                )
                count_cursor = await db.execute("SELECT COUNT(*) FROM groups")
            rows = await cursor.fetchall()
            total = (await count_cursor.fetchone())[0]
            return [dict(row) for row in rows], total

    async def reset_group_stats_for_rediscovery(self) -> None:
        """إعادة فحص المجموعات التي كانت بدون صلاحية أو slowmode (للفحص الدوري)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE groups
                SET status = 'active', failure_reason = NULL, restricted_until = NULL, updated_at = ?
                WHERE status IN ('no_permission', 'slowmode')
                """,
                (self.now(),),
            )
            await db.commit()

    async def update_group_after_post(self, group_id: int, success: bool) -> None:
        """تحديث إحصائيات المجموعة بعد كل عملية نشر."""
        async with aiosqlite.connect(self.db_path) as db:
            # إعادة حساب معدل النجاح باستخدام السجلات الأخيرة
            cursor = await db.execute(
                "SELECT status FROM post_logs WHERE group_id = ? ORDER BY posted_at DESC LIMIT 50",
                (group_id,),
            )
            rows = await cursor.fetchall()
            if rows:
                successes = sum(1 for r in rows if r[0] == "success")
                success_rate = successes / len(rows)
            else:
                success_rate = 1.0 if success else 0.0

            await db.execute(
                """
                UPDATE groups
                SET last_posted_at = ?,
                    daily_posts_count = daily_posts_count + 1,
                    success_rate = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (self.now(), success_rate, self.now(), group_id),
            )
            await db.commit()

    async def reset_daily_counts(self) -> None:
        """إعادة تعيين عداد المنشورات اليومية لجميع المجموعات."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE groups SET daily_posts_count = 0, updated_at = ?",
                (self.now(),),
            )
            await db.commit()

    async def update_group_target(self, group_id: int, target: int) -> None:
        """تحديث الهدف اليومي لمجموعة محددة."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE groups SET daily_posts_target = ?, updated_at = ? WHERE id = ?",
                (target, self.now(), group_id),
            )
            await db.commit()

    # ───────────────────────────────────────────────
    # عمليات سجل النشر
    # ───────────────────────────────────────────────

    async def log_post(
        self,
        group_id: int,
        group_title: str,
        message_text: str,
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
        wait_seconds: int | None = None,
        cycle_id: int | None = None,
    ) -> None:
        """تسجيل عملية نشر ناجحة أو فاشلة."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO post_logs (
                    group_id, group_title, message_text, status, error_code,
                    error_message, wait_seconds, posted_at, cycle_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    group_id,
                    group_title,
                    message_text,
                    status,
                    error_code,
                    error_message,
                    wait_seconds,
                    self.now(),
                    cycle_id,
                ),
            )
            await db.commit()

    async def get_post_stats(self) -> dict[str, Any]:
        """جلب إحصائيات عامة عن عمليات النشر."""
        async with aiosqlite.connect(self.db_path) as db:
            total = await db.execute("SELECT COUNT(*) FROM post_logs")
            total_count = (await total.fetchone())[0]

            success = await db.execute(
                "SELECT COUNT(*) FROM post_logs WHERE status = 'success'"
            )
            success_count = (await success.fetchone())[0]

            failed = await db.execute(
                "SELECT COUNT(*) FROM post_logs WHERE status = 'failed'"
            )
            failed_count = (await failed.fetchone())[0]

            cycles = await db.execute(
                "SELECT COUNT(*) FROM cycles WHERE status = 'completed'"
            )
            cycles_count = (await cycles.fetchone())[0]

            active_groups = await db.execute(
                "SELECT COUNT(*) FROM groups WHERE status = 'active'"
            )
            active_groups_count = (await active_groups.fetchone())[0]

            success_rate = (success_count / total_count * 100) if total_count else 0.0
            return {
                "total": total_count,
                "success": success_count,
                "failed": failed_count,
                "success_rate": round(success_rate, 2),
                "completed_cycles": cycles_count,
                "active_groups": active_groups_count,
            }

    async def has_recent_post(self, group_id: int, message_hash: str, hours: int = 1) -> bool:
        """التحقق من وجود نشر مكرر لنفس المنشور في نفس المجموعة خلال الفترة الماضية."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT 1 FROM post_hashes
                WHERE group_id = ? AND message_hash = ? AND posted_at > datetime('now', ?)
                LIMIT 1
                """,
                (group_id, message_hash, f"-{hours} hours"),
            )
            return await cursor.fetchone() is not None

    async def record_post_hash(self, group_id: int, message_hash: str) -> None:
        """تسجيل hash المنشور للتحقق من التكرار."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO post_hashes (group_id, message_hash, posted_at) VALUES (?, ?, ?)",
                (group_id, message_hash, self.now()),
            )
            await db.commit()

    async def prune_post_hashes(self, hours: int = 24) -> None:
        """حذف سجلات hash القديمة."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM post_hashes WHERE posted_at < datetime('now', ?)",
                (f"-{hours} hours",),
            )
            await db.commit()

    async def log_error(
        self,
        source: str,
        error_code: str,
        error_message: str,
        context: str | None = None,
    ) -> None:
        """تسجيل خطأ تفصيلي في قاعدة البيانات."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO error_logs (source, error_code, error_message, context, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (source, error_code, error_message, context, self.now()),
            )
            await db.commit()

    async def get_recent_error_rate(self, minutes: int = 30) -> tuple[int, int]:
        """إرجاع (عدد الأخطاء، العدد الكلي) للمحاولات الأخيرة."""
        async with aiosqlite.connect(self.db_path) as db:
            total = await db.execute(
                "SELECT COUNT(*) FROM post_logs WHERE posted_at > datetime('now', ?)",
                (f"-{minutes} minutes",),
            )
            total_count = (await total.fetchone())[0]

            failed = await db.execute(
                "SELECT COUNT(*) FROM post_logs WHERE status = 'failed' AND posted_at > datetime('now', ?)",
                (f"-{minutes} minutes",),
            )
            failed_count = (await failed.fetchone())[0]
            return failed_count, max(1, total_count)

    async def get_last_errors(self, limit: int = 5) -> list[dict[str, Any]]:
        """جلب آخر الأخطاء المسجلة."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM error_logs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    # ───────────────────────────────────────────────
    # عمليات الدورات
    # ───────────────────────────────────────────────

    async def start_cycle(self) -> int:
        """بدء دورة جديدة وإرجاع معرفها."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "INSERT INTO cycles (started_at, status) VALUES (?, ?)",
                (self.now(), "running"),
            )
            await db.commit()
            return cursor.lastrowid  # type: ignore[return-value]

    async def end_cycle(self, cycle_id: int, status: str = "completed") -> None:
        """إنهاء دورة موجودة وتحديث إحصائياتها."""
        async with aiosqlite.connect(self.db_path) as db:
            stats = await db.execute(
                "SELECT COUNT(*), SUM(CASE WHEN status='success' THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) FROM post_logs WHERE cycle_id = ?",
                (cycle_id,),
            )
            total, success, failed = await stats.fetchone()
            await db.execute(
                """
                UPDATE cycles
                SET ended_at = ?, status = ?, total_groups = ?, successful_posts = ?, failed_posts = ?
                WHERE id = ?
                """,
                (self.now(), status, total or 0, success or 0, failed or 0, cycle_id),
            )
            await db.commit()

    async def get_current_cycle(self) -> dict[str, Any] | None:
        """جلب آخر دورة غير مكتملة."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM cycles WHERE status = 'running' ORDER BY id DESC LIMIT 1"
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    # ───────────────────────────────────────────────
    # عمليات الرسالة الحالية
    # ───────────────────────────────────────────────

    async def get_message(self) -> dict[str, Any]:
        """جلب المنشور الحالي."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM current_message WHERE id = 1")
            row = await cursor.fetchone()
            return dict(row) if row else {"text": "", "use_html": 1}

    async def set_message(self, text: str, use_html: bool = True) -> None:
        """تحديث المنشور الحالي."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE current_message SET text = ?, use_html = ?, updated_at = ? WHERE id = 1",
                (text, 1 if use_html else 0, self.now()),
            )
            await db.commit()

    # ───────────────────────────────────────────────
    # عمليات نماذج التعلم
    # ───────────────────────────────────────────────

    async def get_learning_model(self, model_type: str) -> dict[str, Any] | None:
        """جلب نموذج تعلم محدد."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM learning_models WHERE model_type = ? ORDER BY id DESC LIMIT 1",
                (model_type,),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def save_learning_model(self, model_type: str, model_data: dict[str, Any]) -> None:
        """حفظ أو تحديث نموذج تعلم."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO learning_models (model_type, model_data, updated_at) VALUES (?, ?, ?)",
                (model_type, json.dumps(model_data, ensure_ascii=False), self.now()),
            )
            await db.commit()

    # ───────────────────────────────────────────────
    # عمليات حالة النظام
    # ───────────────────────────────────────────────

    async def get_state(self) -> dict[str, Any]:
        """جلب حالة النظام الحالية."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM system_state WHERE id = 1")
            row = await cursor.fetchone()
            if row:
                data = dict(row)
                data["queue"] = json.loads(data["queue"]) if data["queue"] else []
                return data
            return {"state": "stopped", "current_cycle_id": None, "queue": []}

    async def set_state(
        self,
        state: str,
        current_cycle_id: int | None = None,
        queue: list[int] | None = None,
    ) -> None:
        """تحديث حالة النظام."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE system_state SET state = ?, current_cycle_id = ?, queue = ?, updated_at = ? WHERE id = 1",
                (state, current_cycle_id, json.dumps(queue or []), self.now()),
            )
            await db.commit()

    async def reset_state(self) -> None:
        """إعادة ضبط حالة النظام ومسح قائمة الانتظار."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE system_state SET state = ?, current_cycle_id = ?, queue = ?, updated_at = ? WHERE id = 1",
                ("stopped", None, json.dumps([]), self.now()),
            )
            await db.commit()

    async def clear_all_data(self) -> None:
        """مسح جميع بيانات المجموعات والنشر والدورات عند تغيير الحساب."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM groups")
            await db.execute("DELETE FROM post_logs")
            await db.execute("DELETE FROM cycles")
            await db.execute("DELETE FROM post_hashes")
            await db.execute("DELETE FROM error_logs")
            await db.execute(
                "UPDATE system_state SET state = ?, current_cycle_id = ?, queue = ?, updated_at = ? WHERE id = 1",
                ("stopped", None, json.dumps([]), self.now()),
            )
            await db.commit()
