# Telegram Channel Relay — Engineering Rules

- `main` هو مصدر النظام الجديد.
- النظام السابق محفوظ في فرع `legacy/smart-poster` ولا يُخلط مع النظام الجديد.
- اقرأ `docs/architecture.md` و`docs/implementation-plan.md` قبل التنفيذ.
- لا تخزن نصوص المنشورات أو الوسائط في SQLite أو logs.
- النظام live-only: لا يستورد المحتوى القديم عند الإضافة أو بعد الانقطاع.
- التزم بفصل المسؤوليات: Telegram, database, transformation, publishing, control bot.
- لا تضع أسرارًا أو ملفات جلسة في Git.
- اختبر كل مرحلة قبل الانتقال للمرحلة التالية.
