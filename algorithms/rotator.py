"""خوارزمية اختيار المجموعة التالية للنشر."""
import random
from datetime import datetime, timezone
from typing import Any


class SmartRotator:
    """حساب نقاط الأولوية لكل مجموعة واختيار الهدف التالي بشكل ذكي."""

    def __init__(self, peak_hours: list[int] | None = None) -> None:
        """تهيئة المدوّر بساعات الذروة الافتراضية."""
        self.peak_hours = peak_hours or [9, 10, 11, 18, 19, 20, 21]

    def _parse_last_posted(self, last_posted_at: str | None) -> datetime | None:
        """تحويل سلسلة الوقت إلى كائن datetime."""
        if not last_posted_at:
            return None
        try:
            return datetime.fromisoformat(last_posted_at)
        except ValueError:
            return None

    def _hours_since_last_post(self, group: dict[str, Any]) -> float:
        """حساب عدد الساعات منذ آخر نشر."""
        last = self._parse_last_posted(group.get("last_posted_at"))
        if not last:
            # إذا لم يُنشر من قبل، نمنحها قيمة عالية لضمان الاختيار
            return 168.0  # أسبوع افتراضي
        now = datetime.now(timezone.utc)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return max(0.0, (now - last).total_seconds() / 3600.0)

    def calculate_score(self, group: dict[str, Any], current_hour: int | None = None) -> float:
        """حساب نقاط الأولوية لمجموعة واحدة."""
        if current_hour is None:
            current_hour = datetime.now().hour

        score = 0.0

        # 1. الوقت منذ آخر نشر: 10 نقاط لكل ساعة
        hours = self._hours_since_last_post(group)
        score += hours * 10.0

        # 2. المنشورات المتبقية اليومية: 20 نقطة لكل منشور
        remaining = max(
            0,
            group.get("daily_posts_target", 2) - group.get("daily_posts_count", 0),
        )
        score += remaining * 20.0

        # 3. معدل النجاح: ±30 نقطة
        success_rate = group.get("success_rate", 1.0) or 0.0
        score += (success_rate - 0.5) * 60.0  # يتراوح بين -30 و +30 تقريباً

        # 4. النشاط الحالي (ساعات الذروة): 25 نقطة
        if current_hour in self.peak_hours:
            score += 25.0

        # 5. الأولوية اليدوية: 40 نقطة
        if group.get("priority", 0) > 0:
            score += 40.0

        # 6. العشوائية البشرية: من -10 إلى +10
        score += random.uniform(-10.0, 10.0)

        return score

    def select_next(
        self,
        groups: list[dict[str, Any]],
        top_percent: float = 0.20,
        current_hour: int | None = None,
    ) -> dict[str, Any] | None:
        """اختيار المجموعة التالية من أعلى نسبة مئوية من المجموعات."""
        if not groups:
            return None

        # حساب النقاط لكل مجموعة
        scored = []
        for group in groups:
            score = self.calculate_score(group, current_hour)
            scored.append((score, group))

        # ترتيب تنازلي
        scored.sort(key=lambda x: x[0], reverse=True)

        # اختيار أعلى نسبة
        top_count = max(1, int(len(scored) * top_percent))
        top_pool = scored[:top_count]

        # اختيار عشوائي موزون من داخل المجموعة المختارة
        weights = [max(0.1, score) for score, _ in top_pool]
        chosen = random.choices(top_pool, weights=weights, k=1)[0]
        return chosen[1]
