"""خوارزمية حساب التأخير الذكي بين المنشورات."""
import random
from datetime import datetime
from typing import Any


class AdaptiveScheduler:
    """حساب مدة الانتظار التالية بين المنشورات بشكل ذكي ومتنوع."""

    def __init__(
        self,
        min_delay: int,
        max_delay: int,
        sleep_start: int = 23,
        sleep_end: int = 6,
    ) -> None:
        """تهيئة المجدول بالحدود الزمنية الأساسية."""
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.sleep_start = sleep_start
        self.sleep_end = sleep_end

    def _is_peak_hour(self, hour: int) -> bool:
        """تحديد ما إذا كانت الساعة ضمن أوقات الذروة."""
        return 9 <= hour <= 11 or 18 <= hour <= 21

    def _is_night_hour(self, hour: int) -> bool:
        """تحديد ما إذا كانت الساعة من أوقات الليل البطيئة."""
        return 22 <= hour or hour < 6

    def compute_delay(
        self,
        group: dict[str, Any],
        cycle_progress: float = 0.5,
        current_hour: int | None = None,
    ) -> float:
        """حساب التأخير بالثواني قبل المنشور التالي."""
        if current_hour is None:
            current_hour = datetime.now().hour

        # 1. القيمة الأساسية العشوائية بين الحد الأدنى والأقصى
        base = random.uniform(self.min_delay, self.max_delay)

        # 2. عامل وقت الذروة
        if self._is_peak_hour(current_hour):
            peak_factor = 0.70  # أسرع 30%
        elif self._is_night_hour(current_hour):
            peak_factor = 1.50  # أبطأ 50%
        else:
            peak_factor = 1.0

        # 3. عامل نجاح المجموعة
        success_rate = group.get("success_rate", 1.0) or 0.0
        if success_rate < 0.5:
            success_factor = 1.30  # أبطأ 30% للمجموعات ذات النجاح المنخفض
        elif success_rate > 0.9:
            success_factor = 0.90  # أسرع قليلاً للمجموعات الناجحة
        else:
            success_factor = 1.0

        # 4. عامل تقدم الدورة: بداية أبطأ، نهاية أسرع
        if cycle_progress < 0.2:
            progress_factor = 1.10
        elif cycle_progress > 0.8:
            progress_factor = 0.90
        else:
            progress_factor = 1.0

        # 5. عامل التشتت البشري ±20%
        human_factor = random.uniform(0.80, 1.20)

        delay = base * peak_factor * success_factor * progress_factor * human_factor
        return max(self.min_delay, delay)

    def extra_distraction(self, probability: float = 0.05) -> float:
        """توليد توقف إضافي عشوائي لمحاكاة التشتت البشري."""
        if random.random() < probability:
            return random.uniform(30, 180)
        return 0.0

    def typing_delay(self, text: str, min_seconds: int = 2, max_seconds: int = 8) -> float:
        """حساب تأخير كتابة يتناسب مع طول النص."""
        # تقدير: ثانية لكل 50 حرف مع حدود
        estimated = len(text) / 50.0
        return min(max_seconds, max(min_seconds, estimated + random.uniform(-0.5, 1.5)))

    def sleep_until_wake(self, current_hour: int | None = None) -> float:
        """حساب مدة الانتظار حتى نهاية فترة النوم."""
        if current_hour is None:
            current_hour = datetime.now().hour

        if self.sleep_start <= self.sleep_end:
            if self.sleep_start <= current_hour < self.sleep_end:
                return (self.sleep_end - current_hour) * 3600.0
        else:
            # الالتفاف حول منتصف الليل (مثلاً 23 إلى 6)
            if current_hour >= self.sleep_start or current_hour < self.sleep_end:
                if current_hour >= self.sleep_start:
                    hours = (24 - current_hour) + self.sleep_end
                else:
                    hours = self.sleep_end - current_hour
                return hours * 3600.0
        return 0.0
