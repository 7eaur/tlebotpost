# اختبارات تكامل Runtime v2

## ما الذي يتم اختباره؟

الاختبار التكاملي يستخدم PostgreSQL حقيقيًا ويقوم بالتالي:

1. يتأكد من اتصال PostgreSQL.
2. يتحقق من وجود الجداول الأساسية من `schema.sql`.
3. ينشئ بيانات Tenant مؤقتة:
   - Account.
   - Project.
   - Telegram Account.
   - Source.
   - Destination.
   - Source Route.
4. ينشئ `IngestionEvent` تجريبيًا.
5. يمرره فعليًا إلى `ContentPipeline`.
6. يتحقق من حفظ `ContentItem` و`ContentFingerprint`.
7. يمرر النتيجة المقبولة إلى `PublishQueue`.
8. يتحقق من إنشاء `PublishJob`.
9. يحذف بيانات الاختبار تلقائيًا.

لا يرسل الاختبار أي رسالة حقيقية إلى Telegram؛ يتم عزل Bot API وTelegram User Listener حتى يركز الاختبار على تكامل PostgreSQL ومسار Runtime الداخلي.

## التشغيل داخل Docker

بعد ضبط `.env` وتشغيل PostgreSQL:

```bash
docker compose --profile integration run --rm runtime-v2-integration
```

الخدمة تستخدم نفس صورة Runtime v2 ونفس شبكة Compose، وتصل إلى PostgreSQL عبر:

```text
postgres:5432
```

يمكن تشغيل PostgreSQL والخدمة الاختبارية معًا تلقائيًا بسبب `depends_on` و`service_healthy`:

```bash
docker compose --profile integration run --rm runtime-v2-integration
```

## التشغيل من داخل بيئة Python

إذا كان لديك PostgreSQL يعمل محليًا:

```bash
cd backend
. .venv/bin/activate
export TEST_DATABASE_URL='postgresql+asyncpg://relay:password@localhost:5432/telegram_relay'
pytest -m integration -q
```

أو شغّل smoke test المستقل:

```bash
python scripts/integration_postgres.py
```

## سلوك عدم توفر PostgreSQL

اختبارات pytest التكاملية تُتخطى بوضوح إذا لم يوجد:

```text
TEST_DATABASE_URL
DATABASE_URL
```

أما الأمر المستقل داخل Docker فيفشل، وهذا مقصود حتى لا يعطي CI أو التشغيل اليدوي نتيجة نجاح وهمية.

## متطلبات البيئة

- Docker Compose.
- PostgreSQL 16 عند التشغيل بالحاويات.
- تطبيق `backend/db/schema.sql` على قاعدة الاختبار.
- تبعيات المشروع المثبتة.

## ملاحظات السلامة

- يستخدم الاختبار UUID عشوائيًا لكل تشغيل.
- لا يستخدم بيانات الحساب الحقيقي.
- يحذف Account الاختبار في `finally`، فتُحذف الكيانات التابعة عبر `ON DELETE CASCADE`.
- لا تستخدم قاعدة الإنتاج مع هذا الاختبار.
- لا تستخدم `docker compose down -v` إلا على بيئة اختبار لأن الأمر يحذف Volume PostgreSQL بالكامل.
