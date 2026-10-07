# Runtime v2 المتكامل

## الحالة

تم دمج المكونات على الفرع:

```text
feature/v2-postgres-schema
```

## المكونات

```text
TelegramClientManager
        ↓
V2TelegramListener
        ↓
ContentPipeline
        ↓
PublishQueue
        ↓
PublishWorker + BotPublisher
```

الملف المنسق هو:

- `backend/app/runtime_v2.py`

ونقطة التشغيل:

- `backend/scripts/run_v2.py`

## lifecycle

عند `start()`:

1. يفحص PostgreSQL عبر `SELECT 1`.
2. يتصل بجلسة Telegram user المصادق عليها.
3. يحمّل Source Routes النشطة.
4. يثبت baseline لكل مصدر.
5. يسجل Telegram handlers.
6. يبدأ Worker دوريًا لمعالجة Publish Queue.
7. يمرر كل `IngestionEvent` إلى Content Pipeline ثم Queue.

عند `stop()`:

1. يوقف استقبال الأحداث الجديدة.
2. يزيل handlers.
3. يوقف Worker.
4. يفصل Telegram user client.
5. يغلق Telegram Bot.
6. يغلق PostgreSQL pool.

## إعدادات البيئة

أضف إلى `.env`:

```env
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/telegram_relay
API_ID=123456
API_HASH=...
BOT_TOKEN=...
V2_ACCOUNT_ID=uuid-of-imported-account
V2_SESSION_PATH=data/v2.session
V2_WORKER_ID=publisher-v2-1
V2_POLL_INTERVAL_SECONDS=1
V2_QUEUE_BATCH_SIZE=10
V2_RETRY_DELAY_SECONDS=60
SEND_INTERVAL_SECONDS=1.1
FLOOD_WAIT_RETRIES=3
```

`V2_ACCOUNT_ID` هو معرف الحساب الذي أنشأه Importer، وليس Telegram numeric user id.

## التشغيل التجريبي الآمن

هذا الأمر لا يقرأ الأسرار ولا يفتح PostgreSQL أو Telegram:

```bash
cd backend
. .venv/bin/activate
python scripts/run_v2.py --dry-run
```

ويتحقق من أن wiring المكونات موجود.

## فحص إعدادات التشغيل

```bash
python scripts/run_v2.py --env-file ../.env --once
```

هذا الخيار:

- يتصل بقاعدة البيانات.
- يبدأ listener ويثبت baselines.
- يلتقط دفعة Publish Queue واحدة.
- يوقف النظام بأمان.

لمنع فحص PostgreSQL أثناء تشخيص اتصال Telegram فقط:

```bash
python scripts/run_v2.py --env-file ../.env --once --no-database-check
```

لا يستخدم هذا الخيار في التشغيل الحقيقي إلا للتشخيص؛ لأن Listener وQueue يحتاجان PostgreSQL فعليًا.

## التشغيل المستمر

```bash
python scripts/run_v2.py --env-file ../.env
```

الإيقاف يكون عبر `Ctrl+C`، ويقوم Runtime بالتنظيف التدريجي بدل قتل العمليات مباشرة.

## عقد التشغيل

- لا يوجد تشغيل فعلي في هذه المرحلة من دون PostgreSQL وTelegram credentials.
- لا يتم استخدام SQLite في Runtime v2.
- لا يتم تشغيل Runtime v2 تلقائيًا مع النظام القديم.
- النشر المباشر يمر عبر Queue ولا يرسل مباشرة من Listener.
- الأخطاء داخل دورة Worker لا توقف Runtime؛ تسجل ويعاد تشغيل الدورة التالية.
- فشل `ContentPipeline` داخل callback يمنع تقدم checkpoint، مما يسمح بإعادة المحاولة.
- `--dry-run` لا يعني اختبار اتصال؛ هو فحص wiring فقط.

## الخطوة التالية قبل الإنتاج

1. تطبيق `schema.sql` على PostgreSQL.
2. تشغيل Importer.
3. ربط جلسة Telegram v2 بالحساب المستورد.
4. إضافة مصدر وهدف ومسار بحالة `active`.
5. التأكد من صلاحية Bot في الهدف.
6. تشغيل `--once` ومراجعة السجلات.
7. تشغيل المستمع المستمر في بيئة تجريبية.
8. إضافة Object Storage للوسائط المجدولة.
