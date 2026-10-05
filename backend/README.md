# Backend

هذه الحزمة هي أساس Telegram Channel Relay. في Phase 1 تحتوي على إعدادات البيئة، SQLite خفيفة، واختبارات أولية فقط. لم يتم ربط Listener أو Control Bot بعد.

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
