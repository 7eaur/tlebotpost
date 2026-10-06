# قاعدة بيانات النظام v2

## الحالة

طبقة PostgreSQL v2 موجودة على فرع:

```text
feature/v2-postgres-schema
```

لم يتم ربطها بعد بنقطة تشغيل النظام القديم، ولا يجب أن يستخدم النظام الحالي `DATABASE_URL` تلقائيًا قبل اكتمال مراحل إعادة البناء.

## الملفات

- `backend/db/schema.sql`: المخطط الأساسي وEnums وTriggers والفهارس.
- `backend/app/db/base.py`: Declarative Base وmetadata.
- `backend/app/db/models.py`: نماذج SQLAlchemy 2.0 المطابقة للجداول.
- `backend/app/db/config.py`: قراءة والتحقق من إعدادات الاتصال.
- `backend/app/db/connection.py`: AsyncEngine وAsyncSession والمعاملات.
- `backend/app/db/repositories.py`: مستودعات Account/Project/Destination/Source/SourceRoute مع عزل الحساب.
- `backend/app/db/services.py`: خدمات المجال والتحقق من الملكية والتعارضات وقواعد الإدخال.
- `backend/app/db/importer.py`: تحميل وترحيل إعدادات SQLite والمصادر وcursors فقط.
- `backend/scripts/import_legacy.py`: أداة CLI للمعاينة والتنفيذ.
- `backend/app/db/__init__.py`: واجهة الاستيراد العامة.
- `backend/tests/test_v2_database.py`: اختبارات metadata وإعدادات الاتصال دون خادم PostgreSQL.

## الإعدادات

```env
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/telegram_relay
DATABASE_POOL_SIZE=5
DATABASE_MAX_OVERFLOW=10
DATABASE_POOL_TIMEOUT=30
DATABASE_POOL_RECYCLE=1800
DATABASE_ECHO=false
```

يجب ألا ترفع كلمة المرور أو ملف البيئة إلى Git.

## الاستخدام

```python
from app.db import Database


database = Database.from_env()

async with database.transaction() as session:
    # تنفيذ عمليات القراءة/الكتابة هنا
    ...

await database.close()
```

لعملية قراءة مع rollback تلقائي عند الخطأ:

```python
async with database.session() as session:
    result = await session.execute(...)
```

وفحص الاتصال:

```python
healthy = await database.ping()
```

## ملاحظات مهمة

1. `schema.sql` هو مصدر إنشاء قاعدة البيانات الأولي في هذه المرحلة.
2. النماذج تستخدم `create_type=False` لأن PostgreSQL Enums ينشئها `schema.sql`، وهذا يمنع SQLAlchemy من محاولة إعادة إنشائها.
3. لا نستخدم `Base.metadata.create_all()` في الإنتاج؛ سنضيف migrations رسمية قبل تشغيل النظام الجديد.
4. قاعدة SQLite و`app.database.Database` تخص النسخة الحالية القديمة، وتبقى مؤقتًا حتى اكتمال الترحيل.
5. الوسائط لا تحفظ داخل PostgreSQL؛ النموذج يحتفظ بالبيانات الوصفية و`storage_key` فقط.
6. النص الأصلي/المطبع اختياريان على مستوى سياسة الاحتفاظ، وليس معنى وجود الحقول أن كل المحتوى سيحفظ دائمًا.
7. مدير الاتصال يستخدم `pool_pre_ping` و`expire_on_commit=False` ويفصل `session` عن `transaction` لتجنب تسرب الاتصالات أو المعاملات.

## Importer من SQLite القديم

الـ Importer يقرأ الجداول القديمة التالية فقط:

- `settings`.
- `sources`.

ولا يقرأ أو يرحّل:

- نصوص المنشورات.
- الصور أو الفيديوهات أو الملفات.
- `event_log`.

هذا يحافظ على سياسة النظام القديم **live-only**. قيمة `baseline_message_id` تنتقل إلى `source_checkpoints` حتى لا يعيد النظام الجديد معالجة رسائل التاريخ عند بدء الاستماع.

### خريطة الترحيل

| SQLite القديم | PostgreSQL v2 |
|---|---|
| صف `settings` | Account + Project + Destination |
| `target_chat_id` | `destinations.telegram_chat_id` |
| `brand_footer` و`brand_link` | `branding_profiles` |
| كلمات التضمين والاستبعاد والوسائط | `filter_profiles` |
| إعدادات المصدر | `sources` |
| `baseline_message_id` | `source_checkpoints.last_seen_message_id` |
| المصدر والهدف | `source_routes` |
| حساب القراءة المنطقي | `telegram_accounts` بحالة disconnected |

### المعاينة أولًا

```bash
cd backend
. .venv/bin/activate
python scripts/import_legacy.py \
  --sqlite ../data/relay.sqlite3 \
  --dry-run
```

وضع `dry-run` لا يفتح اتصال PostgreSQL ولا يكتب أي بيانات، ويعرض عدد المصادر وما إذا كان الهدف مضبوطًا.

### التنفيذ

بعد تطبيق `backend/db/schema.sql` على PostgreSQL وضبط `DATABASE_URL`:

```bash
cd backend
. .venv/bin/activate
python scripts/import_legacy.py \
  --sqlite ../data/relay.sqlite3 \
  --account-name "My account" \
  --account-slug my-account \
  --project-name "Legacy project" \
  --project-slug legacy
```

التنفيذ يتم داخل معاملة PostgreSQL واحدة. إذا فشل أي جزء، يتم rollback للترحيل كاملًا. التشغيل المتكرر آمن على مستوى الهوية؛ يعيد استخدام الحساب والمشروع والهدف والمصدر والمسار بدل إنشاء نسخ جديدة.

الـ Importer لا ينشئ جلسة Telegram جديدة ولا يرحّل ملف الجلسة؛ ينشئ فقط `telegram_accounts` بحالة `disconnected`. يجب تسجيل/ربط الجلسة من خلال مسار الحساب المخصص بعد الترحيل.

## المرحلة التالية: مستودعات المجال

تمت إضافة مستودعات أولية للكيانات التي تشكل مسار الإدارة:

```text
Account
  └── Project
      ├── Destination
      └── SourceRoute
          └── Source
```

كل مستودع يستقبل `account_id` ويضيفه إلى استعلاماته، حتى لا يصبح الوصول إلى بيانات حساب آخر ممكنًا من خلال الاستعلامات العادية. المستودعات لا تنفذ `commit` بنفسها؛ caller يختار `database.transaction()` لإدارة المعاملة.

المستودعات لا تنفذ التحقق المركب بين الكيانات؛ هذا التحقق أصبح مسؤولية Service Layer الموضحة أدناه، مع بقاء اختبار معاملات PostgreSQL الحقيقية ضمن مرحلة التكامل اللاحقة.

## Service Layer

تم تنفيذ الطبقة التالية فوق المستودعات:

- `ProjectService`: إنشاء وقراءة المشاريع مع تطبيع الاسم والـ slug.
- `DestinationService`: لا ينشئ هدفًا إلا إذا كان المشروع تابعًا للحساب نفسه.
- `SourceService`: يتحقق من ملكية حساب Telegram قبل إنشاء المصدر.
- `SourceRouteService`: يتحقق من ملكية المصدر والهدف، ويمنع تكرار الربط بينهما.

الأخطاء المتوقعة موحدة:

- `EntityNotFoundError`: الكيان غير موجود أو خارج حساب المستخدم.
- `EntityConflictError`: العملية ستنشئ تعارضًا أو ربطًا مكررًا.
- `DomainValidationError`: البيانات لا تطابق قواعد المجال.

الخدمات لا تنفذ `commit` بنفسها. يجب استدعاؤها داخل:

```python
async with database.transaction() as session:
    service = DestinationService(session, account_id)
    destination = await service.create(
        project_id=project_id,
        name="Main target",
        telegram_chat_id=-100123,
    )
```

بهذا تظل المعاملة مسؤولية طبقة التشغيل، ويمكن ضم إنشاء المشروع والهدف والمسار في معاملة واحدة عند الحاجة.

## التحقق المحلي

```bash
cd backend
. .venv/bin/activate
pip install -e '.[dev]'
pytest -q
ruff check .
python -m compileall -q app
```

الاتصال الفعلي يتطلب PostgreSQL يعملًا وقيمة `DATABASE_URL` صحيحة. الاختبارات الحالية لا تحتاج خادمًا خارجيًا؛ تتحقق من استيراد النماذج، metadata، والتحقق من الإعدادات.
