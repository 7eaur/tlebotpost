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
