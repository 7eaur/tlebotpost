# Telegram Ingestion v2

## الحالة

طبقة الاستماع الجديدة موجودة على فرع:

```text
feature/v2-postgres-schema
```

وهي منفصلة عن `app.telegram.listener` القديم حتى لا تؤثر على النظام الحالي أثناء إعادة البناء.

## الملفات

- `backend/app/telegram/v2_client.py`: مدير دورة حياة عميل Telethon لحساب المستخدم.
- `backend/app/telegram/v2_listener.py`: المستمع live-only والمسارات النشطة.
- `backend/app/db/repositories.py`: `SourceBinding` و`SourceCheckpointRepository`.
- `backend/tests/test_v2_telegram_listener.py`: اختبارات الأحداث والمسارات والـ checkpoints.

## التدفق

```text
Telegram User Client
        ↓
V2TelegramListener
        ↓
SourceRouteRepository.list_active_bindings()
        ↓
SourceCheckpointRepository
        ↓
IngestionEvent لكل SourceRoute نشط
        ↓
المرحلة التالية: Content Pipeline
```

## سياسة live-only

عند تشغيل المستمع:

1. يقرأ المسارات النشطة فقط.
2. يجمع المسارات بحسب `source.telegram_chat_id`.
3. يطلب آخر رسالة فقط من كل مصدر باستخدام `limit=1`.
4. يرفع checkpoint إلى آخر رسالة إن كان أحدث.
5. يسجل handler صريحًا للمصدر.
6. يبدأ قبول أحداث Telegram الجديدة بعد اكتمال كل الـ baselines.

لا يتم جلب تاريخ الرسائل ولا إعادة إرسال أي منشور قديم.

## مصدر واحد لعدة أهداف

إذا كان المصدر مرتبطًا بعدة أهداف، يسجل Handler واحد للمصدر، ثم ينشئ `IngestionEvent` منفصلًا لكل `SourceRoute` نشط:

```text
Source A
├── Route A → Destination 1
└── Route B → Destination 2
```

كل حدث يحمل:

- `account_id`
- `source_id`
- `route_id`
- `destination_id`
- `chat_id`
- `message_id`
- `grouped_id`
- كائن رسالة Telegram
- وقت الاستلام

## checkpoints

يتم تحديث `source_checkpoints.last_seen_message_id` فقط بعد نجاح callback لجميع المسارات المرتبطة بالرسالة.

هذا يعطي سلوك **at-least-once** عند حدوث فشل، ويترك منع التكرار للـ Content Pipeline/Publisher. في حال نجاح بعض المسارات وفشل مسار آخر، قد تعاد المحاولة للمسارات الناجحة؛ لذلك يجب أن تكون طبقة النشر idempotent.

التحديث monotonic ولا يسمح بتحريك المؤشر إلى الخلف.

## الاتصال وإعادة الاستخدام

`TelegramClientManager` مسؤول عن:

- إنشاء `TelegramSession`.
- الاتصال بجلسة مستخدم مصادق عليها.
- رفض الجلسة غير المصرح بها.
- `ensure_connected()`.
- الفصل الآمن.
- إبقاء `run_until_disconnected()` خارج منطق المستمع.

لا يتم إنشاء جلسة Telegram أو ترحيل ملف الجلسة تلقائيًا بواسطة Importer.

## التشغيل البرمجي

```python
from app.db import Database
from app.telegram.v2_client import TelegramClientConfig, TelegramClientManager
from app.telegram.v2_listener import V2TelegramListener

config = TelegramClientConfig(
    api_id=api_id,
    api_hash=api_hash,
    session_path=session_path,
)
client_manager = TelegramClientManager(config)
database = Database.from_env()

async def on_message(event):
    # المرحلة التالية: إدخال الحدث في Content Pipeline
    print(event.source_id, event.route_id, event.message_id)

listener = V2TelegramListener(
    client_manager,
    database.session_factory,
    account_id,
    on_message,
)

await listener.start()
# يبقى التطبيق حيًا، ثم:
await listener.stop()
await client_manager.disconnect()
await database.close()
```

## قواعد التغيير

- لا يقرأ المستمع نص الرسالة أو يحولها؛ يسلمها إلى `IngestionEvent` فقط.
- لا ينشر مباشرة إلى Telegram Bot API.
- لا يكتب `ContentItem` في هذه المرحلة؛ ذلك مسؤولية Content Pipeline التالية.
- لا يستخدم SQLite أو مستودعات النظام القديم.
- أي خطأ في callback يمنع تقدم checkpoint لتلك الرسالة.
- تحديث إعدادات المصادر والمسارات يتطلب `listener.reload()`.

## التحقق

```bash
cd backend
. .venv/bin/activate
pytest -q
ruff check .
python -m compileall -q app scripts
```
