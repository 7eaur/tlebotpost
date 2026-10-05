# Backend

هذه الحزمة هي أساس Telegram Channel Relay. تحتوي حاليًا على إعدادات البيئة، SQLite خفيفة، وRepositories منفصلة للمصادر والإعدادات وسجل الأحداث، إضافة إلى جلسة المستخدم وListener live-only وTransformer وPublisher ودعم الألبومات. لم يتم ربط Control Bot بعد.

## تشغيل محلي

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp ../.env.example .env
# أضف القيم المطلوبة إلى .env
python -m app.main
pytest
ruff check .
```

لا تضع `.env` أو جلسة Telegram أو مجلد `data/` داخل Git.
