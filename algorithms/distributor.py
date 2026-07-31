"""خوارزمية توزيع المنشورات على فترات اليوم الخمس."""
import random
from datetime import datetime
from typing import Any


class SmartDistributor:
    """توزيع المنشورات على فترات اليوم مع عشوائية وتكيف ذكي."""

    # التوزيع الأساسي: فجر، صباح، ظهر، مساء، ليل
    BASE_DISTRIBUTION = {
        "dawn": 0.10,
        "morning": 0.30,
        "noon": 0.10,
        "evening": 0.35,
        "night": 0.15,
    }

    # الساعات المقترحة لكل فترة
    PERIOD_HOURS = {
        "dawn": list(range(6, 9)),
        "morning": list(range(9, 12)),
        "noon": list(range(12, 15)),
        "evening": list(range(15, 21)),
        "night": list(range(21, 23)),
    }

    def __init__(self, learning_model: dict[str, Any] | None = None) -> None:
        """تهيئة الموزع باستخدام نموذج التعلم إن وجد."""
        self.distribution = self._apply_learning(learning_model)

    def _apply_learning(self, learning_model: dict[str, Any] | None) -> dict[str, float]:
        """تعديل التوزيع بناءً على أنماط النجاح السابقة."""
        base = self.BASE_DISTRIBUTION.copy()
        if learning_model and "period_adjustments" in learning_model:
            adjustments = learning_model["period_adjustments"]
            for period, factor in adjustments.items():
                if period in base:
                    base[period] *= max(0.5, min(2.0, factor))

            # إعادة التطبيع بحيث يكون المجموع 1
            total = sum(base.values())
            if total > 0:
                base = {k: v / total for k, v in base.items()}
        return base

    def build_plan(
        self,
        total_posts: int,
        current_hour: int | None = None,
    ) -> dict[str, int]:
        """بناء خطة المنشورات لكل فترة."""
        if current_hour is None:
            current_hour = datetime.now().hour

        plan: dict[str, int] = {}
        allocated = 0

        # توزيع النسب مع عشوائية ±5%
        for period, ratio in self.distribution.items():
            jitter = random.uniform(-0.05, 0.05)
            adjusted_ratio = max(0.01, ratio + jitter)
            count = int(total_posts * adjusted_ratio)
            plan[period] = count
            allocated += count

        # معالجة الفرق بسبب التقريب
        diff = total_posts - allocated
        if diff != 0:
            # نضيف الفرق إلى الفترة ذات النسبة الأعلى
            target = max(plan, key=plan.get) if diff > 0 else min(plan, key=plan.get)
            plan[target] += diff

        # إذا بدأنا بعد بداية اليوم، نعيد توزيع المنشورات المتبقية
        return plan

    def get_current_period(self, hour: int | None = None) -> str:
        """تحديد الفترة الحالية بناءً على الساعة."""
        if hour is None:
            hour = datetime.now().hour
        for period, hours in self.PERIOD_HOURS.items():
            if hour in hours:
                return period
        # ساعات الليل المتأخرة تعتبر من فترة النوم
        return "night"

    def calculate_total_posts(self, group_count: int, posts_per_day: int) -> int:
        """حساب إجمالي المنشورات اليومية."""
        return group_count * posts_per_day
