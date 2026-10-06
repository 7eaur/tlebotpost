# Publisher & Publish Queue v2

## الحالة

نظام النشر موجود على فرع:

```text
feature/v2-postgres-schema
```

## الملفات

- `backend/app/publishing/scheduler.py`: حساب أوقات النشر.
- `backend/app/publishing/queue.py`: إنشاء وحجز وتحويل حالات `PublishJob`.
- `backend/app/publishing/publisher.py`: الإرسال من خلال Telegram Bot API.
- `backend/app/publishing/__init__.py`: واجهة الحزمة.
- `backend/tests/test_publishing_v2.py`: اختبارات الجدولة وخرائط الوسائط.

## التدفق الكامل

```text
PipelineResult(accepted)
          ↓
PublishQueue.enqueue_result()
          ↓
PublishJob(queued)
          ↓
claim_due() + FOR UPDATE SKIP LOCKED
          ↓
PublishWorker
          ↓
BotPublisher
          ↓
Telegram Bot API
          ↓
Published / RetryWait / Failed
```

## النشر المباشر

عندما يكون `Destination.publishing_mode = direct`:

- ينشأ `PublishJob` بحالة `queued`.
- يكون `scheduled_for` هو الوقت الحالي.
- يلتقطه العامل في أول دورة تشغيل.
- لا يتم تجاوز الطابور حتى في النشر المباشر، لضمان التتبع وإعادة المحاولة ومنع التزامن غير المنضبط.

## النشر المجدول

يدعم Scheduler حاليًا:

- `immediate`.
- `interval`.
- `daily_window` مع timezone.

أما `cron` فيُرفض بوضوح حتى يتم ربط Worker مخصص بمفسر cron؛ لا يتم تخمين وقت cron أو نشره في وقت خاطئ.

## حالات PublishJob

```text
queued
processing
publishing
published
retry_wait
failed
cancelled
expired
```

المسار الحالي يستخدم:

```text
queued → processing → published
queued → processing → retry_wait → processing
queued → processing → failed
```

## الحجز الآمن

`claim_due()` يستخدم:

```sql
FOR UPDATE SKIP LOCKED
```

ويقوم بـ:

- حجز المهام على مستوى Worker.
- تسجيل `locked_by` و`locked_at`.
- زيادة `attempt_count`.
- إنشاء `PublicationAttempt` بحالة `started`.
- السماح بأكثر من Worker دون أن يلتقطا نفس المهمة في الوقت نفسه.
- إعادة التقاط المهام العالقة بعد انتهاء `lock_timeout_seconds`.

## إعادة المحاولة

- `RetryAfter` من Telegram يتحول إلى `retry_wait` حسب عدد الثواني الذي يحدده Telegram.
- الأخطاء المؤقتة الأخرى تعاد بعد `retry_delay_seconds`.
- أخطاء الوسائط غير المتوفرة تعتبر `failed` لأنها لن تنجح بإعادة المحاولة دون تخزين الوسيط.
- كل محاولة تحدث سجل `PublicationAttempt`.

## النشر النصي

يرسل النص المطبع من `ContentItem.text_normalized` مع:

```python
disable_web_page_preview=True
```

لمنع ظهور تفاصيل معاينة روابط Telegram داخل المنشور.

## النشر بالوسائط

`BotPublisher` يدعم:

- photo.
- video.
- audio.
- voice.
- document.

لكن يجب أن يحتوي `ContentMedia.storage_key` على معرف ملف Telegram أو رابط/مسار تخزين قابل للإرسال. إذا لم يكن الوسيط مخزنًا، تكون النتيجة `media_unavailable` بدل إسقاط الرسالة بصمت.

تنزيل الوسائط من جلسة Telegram وحفظها في Object Storage سيكون مرحلة مستقلة لاحقة، لأن `Content Pipeline` الحالية تحفظ metadata فقط.

## منع تكرار مهام النشر

يوجد قيد فريد على:

```text
(content_item_id, destination_id)
```

كما يتحقق `enqueue_result()` من المهمة الموجودة قبل إنشاء مهمة جديدة. لذلك إعادة استدعاء enqueue لنفس نتيجة Pipeline لا تنشئ مهمة نشر ثانية.

## الاستخدام

```python
from app.publishing import BotPublisher, PublishQueue, PublishWorker

queue = PublishQueue(
    database.session_factory,
    account_id,
    worker_id="publisher-1",
)

job = await queue.enqueue_result(pipeline_result)

publisher = BotPublisher(
    bot,
    database.session_factory,
    account_id,
)
worker = PublishWorker(queue, publisher)
await worker.run_once(limit=10)
```

## ملاحظات تشغيلية

- يجب تشغيل `schema.sql` قبل استخدام الطابور.
- لا تستخدم queue خارج `account_id` الخاص بها.
- يجب أن يكون Bot لديه صلاحية النشر في الوجهة.
- النشر المباشر لا يعني تجاوز Queue.
- لا يتم اعتبار المهمة منشورة إلا بعد نجاح Telegram Bot API وتسجيل `telegram_message_id`.

## التحقق

```bash
cd backend
. .venv/bin/activate
pytest -q
ruff check .
python -m compileall -q app scripts
```
