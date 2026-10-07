# تقرير إطلاق Telegram Content Distribution Platform v2

## بيانات الإصدار

| الحقل | القيمة |
|---|---|
| الإصدار | v2.0.0 |
| الفرع المصدر | `feature/v2-postgres-schema` |
| الفرع الهدف | `main` |
| طبيعة الإصدار | إعادة بناء معماري تدريجية مع Runtime جديد |
| قاعدة البيانات | PostgreSQL 16+ |
| النشر | Docker Compose |

## ملخص تنفيذي

تم تجهيز النسخة v2 كمسار تشغيل مستقل وقابل للتوسع، مع إبقاء النظام القديم متاحًا أثناء الانتقال. المسار الجديد يفصل بين استقبال رسائل Telegram، معالجة المحتوى، الطابور، والنشر.

```text
Telegram User Client
        ↓
V2 Listener
        ↓
Content Pipeline
        ↓
PostgreSQL Publish Queue
        ↓
Publish Worker
        ↓
Telegram Bot API
```

## المكونات التي دخلت الإصدار

### قاعدة البيانات

- مخطط PostgreSQL كامل في `backend/db/schema.sql`.
- Enums وقيود وفهارس وTriggers.
- SQLAlchemy async models.
- Async database engine وsession lifecycle.
- عزل البيانات حسب `account_id`.
- Repositories وService Layer.
- Importer من SQLite القديم إلى PostgreSQL.

### الاستقبال والمعالجة

- Telegram Client Manager لجلسة User واحدة.
- Listener v2 للمصادر والمسارات المتعددة.
- Live-only baseline لمنع إعادة نشر الرسائل القديمة عند التشغيل.
- Content Pipeline لتطبيع النص، إزالة حقوق المصدر، إزالة روابط Telegram، الحفاظ على الرموز، وتطبيق الفلاتر.
- Deduplication fingerprints على مستوى الهدف.

### النشر والجدولة

- Publish Queue بمعاملة PostgreSQL.
- `FOR UPDATE SKIP LOCKED` لدعم عدة Workers.
- نشر مباشر عبر Queue.
- جدولة `immediate` و`interval` و`daily_window`.
- تسجيل المحاولات وحالات retry.
- التعامل مع Telegram `RetryAfter`.
- دعم النصوص والصور والفيديو والصوت والملفات.
- تعطيل معاينة روابط Telegram.

### Runtime وDocker

- Runtime v2 lifecycle موحد.
- إيقاف تدريجي يغلق Listener وWorker وTelegram وPostgreSQL.
- Docker Compose بخدمتي PostgreSQL وRuntime v2.
- PostgreSQL healthcheck.
- Volume دائم.
- خدمة تكامل منفصلة عبر profile باسم `integration`.

## التغييرات الرئيسية

```text
fae6db9 feat: add v2 PostgreSQL schema and SQLAlchemy data layer
a2d1c05 feat: add account-scoped v2 repositories
1c808a7 feat: add v2 domain services
2e1a8b7 feat: add legacy SQLite to PostgreSQL importer
d2da4af feat: add v2 Telegram ingestion listeners
00c34a7 feat: add v2 content filtering pipeline
4e3c0d0 feat: add v2 publish queue and publisher
dce2ce3 feat: integrate v2 runtime pipeline
5187ca3 chore: add postgres and v2 runtime compose
2c29dca test: add postgres runtime integration suite
```

## التحقق المنفذ

تم تشغيل:

```bash
pytest -q
pytest -q -m integration
ruff check .
python -m compileall -q app scripts
git diff --check
```

النتيجة في بيئة Sandbox:

```text
73 passed, 1 skipped
ruff: passed
compileall: passed
git diff --check: passed
```

سبب تخطي اختبار التكامل في Sandbox هو عدم توفر Docker/PostgreSQL. الاختبار نفسه موجود ومهيأ ليعمل داخل Compose.

## اختبار PostgreSQL داخل Docker

بعد نسخ وضبط `.env`:

```bash
mkdir -p data
docker compose --profile integration run --rm runtime-v2-integration
```

الاختبار يتحقق من:

- اتصال PostgreSQL.
- وجود جداول schema.
- إنشاء بيانات Tenant مؤقتة.
- حفظ ContentItem وFingerprints.
- إنشاء PublishJob.
- حذف بيانات الاختبار تلقائيًا.

## متطلبات التفعيل

قبل التشغيل الفعلي يجب توفير:

```env
API_ID=...
API_HASH=...
BOT_TOKEN=...
V2_ACCOUNT_ID=...
POSTGRES_PASSWORD=...
```

كما يجب:

1. تطبيق `schema.sql` على قاعدة PostgreSQL.
2. تشغيل Importer عند الانتقال من SQLite.
3. ربط جلسة Telegram User مصادق عليها.
4. إنشاء مصدر وهدف ومسار بحالة active.
5. منح Bot صلاحية النشر في الهدف.
6. اختبار دورة `--once` قبل التشغيل المستمر.

## المخاطر والحدود المعروفة

- لا توجد migrations رسمية بعد؛ `schema.sql` هو bootstrap الأولي.
- الوسائط المجدولة تحتاج Object Storage أو `storage_key` صالحًا قبل النشر.
- cron موجود في نموذج الجدولة لكنه يرفض التنفيذ حتى إضافة Worker مخصص له.
- الاختبار الحالي لا يرسل فعليًا إلى Telegram Bot API.
- النظام القديم لا يزال موجودًا ولم يتم حذفه؛ Runtime v2 مسار مستقل.
- يجب عدم استخدام `docker compose down -v` على بيانات الإنتاج.

## خطة التفعيل المقترحة

### المرحلة 1: تحقق البيئة

```bash
docker compose config
docker compose build runtime-v2
docker compose --profile integration run --rm runtime-v2-integration
```

### المرحلة 2: تهيئة البيانات

```bash
python backend/scripts/import_legacy.py \
  --sqlite data/relay.sqlite3 \
  --dry-run
```

بعد مراجعة المعاينة، نفذ الترحيل إلى PostgreSQL.

### المرحلة 3: التشغيل التجريبي

```bash
docker compose up -d --build
python backend/scripts/run_v2.py --once
```

راجع السجلات قبل التحول للتشغيل المستمر.

### المرحلة 4: التشغيل المستمر

```bash
docker compose up -d runtime-v2
```

## قرار الإطلاق

النسخة v2 **جاهزة للدمج في `main` وبدء التشغيل التجريبي**. لا تعتبر جاهزة لإنتاج واسع قبل تنفيذ اختبار PostgreSQL داخل Docker وربط Telegram الحقيقي ومراجعة أول دفعة منشورة.
