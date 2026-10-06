# Content Pipeline v2

## الحالة

طبقة معالجة المحتوى موجودة على فرع:

```text
feature/v2-postgres-schema
```

## الملفات

- `backend/app/content/pipeline.py`: التطبيع والفلترة ومنع التكرار والحفظ.
- `backend/app/content/__init__.py`: واجهة الحزمة.
- `backend/tests/test_content_pipeline.py`: اختبارات القواعد الحتمية.

بعد قبول المحتوى، ينتقل إلى [نظام النشر والطابور](v2-publishing.md) بدل الإرسال المباشر من هذه الطبقة.

## التدفق

```text
IngestionEvent
    ↓
Route Configuration
    ↓
Text + Media Extraction
    ↓
Normalization
    ↓
FilterProfile
    ↓
Deduplication Fingerprints
    ↓
ContentItem + ContentMedia + ContentFingerprint
    ↓
PipelineResult
```

## التطبيع

- تحويل نهايات الأسطر إلى `LF`.
- الحفاظ على فواصل الأسطر المهمة.
- الحفاظ على الرموز التعبيرية.
- إزالة روابط Telegram عند تفعيل `remove_urls`.
- إزالة كتل حقوق المصدر التي تحتوي على رابط Telegram وفاصل زخرفي.
- إزالة الفراغات الزائدة.
- عدم تنزيل الوسائط؛ يحفظ النظام بياناتها التعريفية فقط في هذه المرحلة.

## الفلاتر

يتم تحميل `FilterProfile` الخاص بالـ `SourceRoute` وليس إعدادًا عامًا للمصدر.

القواعد الحالية:

- كلمات التضمين: إذا وجدت القائمة، يجب أن تطابق الرسالة كلمة واحدة على الأقل.
- كلمات الاستبعاد: تجاهل الرسالة عند تطابق أي كلمة.
- أنواع الوسائط المسموحة.
- تجاهل المحتوى الفارغ.

القيم المحتملة للنتيجة:

```text
accepted
filtered
 duplicate
```

وأسباب التجاهل:

```text
missing_include_keyword
matched_exclude_keyword
media_type_not_allowed
empty_content
```

## منع التكرار

يحسب النظام بصمات داخل نطاق الهدف `destination_id`:

- `telegram_message`: المصدر + رقم رسالة Telegram.
- `text`: SHA-256 للنص المطبع.
- `media`: SHA-256 لهويات الوسائط.
- `combined`: SHA-256 للنص والوسائط معًا.

يتم فحص البصمات قبل إنشاء المحتوى. كما توجد Unique Constraints في PostgreSQL لحماية التزامن بين أكثر من Worker.

إعدادات `DeduplicationProfile` تحدد مقارنة:

- رسالة Telegram.
- النص.
- الوسائط.

عند تعطيل الملف بالكامل لا تنشأ بصمات، لكن يجب استخدام ذلك بحذر لأنه يسمح بالتكرار.

## التخزين

عند قبول الرسالة، ينشئ Pipeline:

- `ContentItem`.
- `ContentFingerprint` لكل بصمة.
- `ContentMedia` لكل وسيط، دون تنزيل الملف.

لا ينشئ Pipeline مهام نشر بعد؛ هذه مسؤولية مرحلة Publisher/Queue التالية.

إذا حدث تعارض بصمة أثناء الحفظ المتزامن، يستخدم Savepoint ويعيد نتيجة `duplicate` دون إسقاط المعاملة الخارجية.

## النتيجة البرمجية

```python
result = await pipeline.process(event)

if result.decision.value == "accepted":
    content_id = result.content_item_id
elif result.decision.value in {"filtered", "duplicate"}:
    # لا ينشأ نشر
    pass
```

## ملاحظات التصميم

- Pipeline لا تنشر مباشرة إلى Telegram.
- Pipeline لا تستخدم SQLite.
- Pipeline لا تتجاوز الحساب الحالي؛ يتحقق الاستعلام من `account_id` و`route_id` و`source_id` و`destination_id`.
- رسالة واحدة يمكن أن تمر عبر مسارين مختلفين إلى هدفين مختلفين، ولكل مسار ملف فلترة ومنع تكرار مستقل.
- callback الخاص بالاستماع لم يعد مسؤولًا عن التطبيع أو التخزين؛ يمرر `IngestionEvent` فقط.

## التحقق

```bash
cd backend
. .venv/bin/activate
pytest -q
ruff check .
python -m compileall -q app scripts
```
