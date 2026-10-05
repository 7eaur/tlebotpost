# خطة الانتقال إلى Cloudflare

## 1. الهدف

نقل النظام من خدمة Python/Telethon طويلة التشغيل إلى بنية تستفيد من Cloudflare، مع المحافظة على:

- استقبال المحتوى الجديد.
- تنظيف النص والروابط والحقوق والرموز التعبيرية.
- نشر Bot API في القناة الهدف.
- دعم الألبومات.
- التحكم من Control Bot.
- عدم إعادة نشر التاريخ القديم.
- عدم حفظ محتوى المنشورات.

هذه وثيقة **خطة تنفيذ وليست تغييرًا للكود الحالي**. الكود الحالي يبقى قابلًا للتشغيل على Docker في الفرع المرجعي.

## 2. القرار المعماري الحاسم

Cloudflare Workers لا يشغل Python/Telethon كـ process دائم، ولا يحافظ على جلسة MTProto مستخدم بالطريقة التي يعتمدها النظام الحالي. لذلك توجد مساران مختلفان:

### المسار A — Cloudflare-first مع تغيير مصدر القراءة

يُستخدم إذا أصبحت كل قنوات المصادر قابلة للقراءة بواسطة Bot API، وكان البوت عضوًا/مشرفًا في المصادر عند الحاجة.

```text
Telegram Channel Updates
          |
          v
Cloudflare Worker Webhook
          |
          +--> D1: settings, sources, cursors, event metadata
          +--> R2: temporary media when required
          +--> Queue: ordered publish jobs
                         |
                         v
                  Worker Consumer
                         |
                         v
                  Telegram Bot API
                         |
                         v
                   Target Channel
```

هذا المسار يلغي جلسة المستخدم وTelethon، لكنه يغيّر شرط الوصول إلى القنوات الخاصة. يجب التحقق من أن البوت يستطيع رؤية كل مصدر قبل اعتماده.

### المسار B — Hybrid Cloudflare مع قارئ خارجي

يُستخدم إذا كان يجب الحفاظ على قراءة القنوات الخاصة عبر حساب مستخدم.

```text
Small Python Worker / VPS
  Telethon user session + source listener
              |
              | HTTPS signed events
              v
Cloudflare Worker
  auth + normalization + Queue
              |
              v
Cloudflare Queue Consumer
              |
              v
Telegram Bot API -> target channel
```

Cloudflare هنا يدير البوابة والطوابير والتخزين، لكن خدمة Python الصغيرة تبقى ضرورية لجلسة المستخدم. هذا المسار ليس مجانيًا بالضرورة، لكنه يحافظ على قدرة قراءة المصادر الخاصة.

## 3. ما الذي لا يجوز فعله؟

- لا نحاول تشغيل `python -m app.main` مباشرة داخل Worker.
- لا ننقل ملف `telegram_user.session` إلى KV أو D1 على أنه بديل لجلسة Telethon.
- لا نخزن الوسائط الكبيرة في D1؛ نستخدم R2 أو نقلًا مباشرًا مؤقتًا.
- لا نعتمد على Cron لإبقاء Listener حيًا.
- لا نعلن نجاح النقل قبل اختبار الصلاحيات في كل قناة مصدر.
- لا نخلط كود النسخة الحالية مع كود Cloudflare في نفس نقاط الدخول.

## 4. الخدمات المطلوبة في Cloudflare

### Workers

مسؤول عن:

- Webhook أو Endpoint استقبال.
- التحقق من التوقيع والمالك.
- قراءة الإعدادات من D1.
- وضع مهام النشر في Queue.
- واجهة إدارة محدودة عند الحاجة.

### D1

الجداول المقترحة:

```sql
settings(
  id TEXT PRIMARY KEY,
  target_chat_id TEXT,
  brand_footer TEXT,
  brand_link TEXT,
  enabled INTEGER,
  include_keywords_json TEXT,
  exclude_keywords_json TEXT,
  updated_at TEXT
)

sources(
  chat_id TEXT PRIMARY KEY,
  username TEXT,
  title TEXT,
  enabled INTEGER,
  baseline_message_id INTEGER,
  updated_at TEXT
)

event_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_chat_id TEXT,
  source_message_id INTEGER,
  event_type TEXT,
  status TEXT,
  error_code TEXT,
  created_at TEXT
)
```

لا يحتوي أي جدول على نص الرسالة الكامل أو ملف الوسائط.

### Queues

تستخدم لضمان:

- ترتيب النشر.
- منع التوازي غير المنضبط.
- إعادة المحاولة.
- فصل استقبال الحدث عن إرسال Bot API.
- التعامل مع Telegram rate limits.

يجب أن يحتوي كل Job على معرف الرسالة والبيانات اللازمة أو رابط وسائط مؤقت فقط، وليس أرشيفًا دائمًا لمحتوى المنشور.

### R2

اختياري للوسائط التي لا يمكن إرسالها مباشرة إلى Bot API. يجب:

- وضع TTL أو Lifecycle قصير.
- تشفير/تقييد الوصول.
- حذف الملف بعد نجاح النشر أو انتهاء المهلة.
- عدم اعتبار R2 أرشيف منشورات.

### D1 migrations

تُدار الهجرات بإصدارات مرقمة:

```text
migrations/0001_settings_sources_events.sql
migrations/0002_indexes.sql
migrations/0003_delivery_attempts.sql
```

## 5. خطة التنفيذ المرحلية

### Phase CF-0 — تثبيت العقد الحالي

- اعتماد `docs/current-system.md` كمرجع.
- حفظ branch `docs/current-system-baseline`.
- تثبيت نتائج الاختبارات الحالية.
- تحديد هل المصادر الخاصة مطلوبة في النسخة Cloudflare.
- اتخاذ قرار بين المسار A وB.

**بوابة القرار:** لا نبدأ كتابة Worker قبل حسم طريقة قراءة المصادر.

### Phase CF-1 — تصميم عقود البيانات

- تعريف Event DTO موحد.
- تعريف Publish Job.
- تعريف Result/Retry contract.
- تحديد حالات المؤشر:
  - `baseline`
  - `received`
  - `queued`
  - `published`
  - `failed`
- تحديد Idempotency key بصيغة:

```text
source_chat_id:source_message_id
```

- كتابة مخطط D1 والهجرات.

### Phase CF-2 — استخراج منطق مستقل عن Telethon

من الكود الحالي نعيد استخدام المفاهيم فقط، ونفصل:

- Transformer إلى TypeScript pure functions.
- قواعد الروابط والحقوق والرموز.
- Album grouping.
- Retry policy.
- Rate limiting.
- Event logging.

لا ننسخ Publisher الحالي كما هو لأن عميله Bot API مكتوب بعقد Python.

### Phase CF-3 — بناء Worker وD1

- إنشاء Worker TypeScript.
- إنشاء D1 database.
- تطبيق migrations.
- بناء health endpoint.
- بناء endpoint استقبال محمي بـ secret.
- إضافة config validation.
- إضافة اختبارات Worker بدون Telegram حقيقي.

### Phase CF-4 — بناء Queue وPublisher

- إنشاء Queue نشر.
- Consumer بترتيب واضح.
- `sendMessage` للنص.
- `sendPhoto`, `sendVideo`, `sendDocument`, `sendMediaGroup` للوسائط.
- احترام `RetryAfter`.
- عدد محاولات محدود.
- Dead-letter أو سجل فشل مختصر.
- تحديث المؤشر فقط بعد النجاح أو قرار تخطٍ مقبول.

### Phase CF-5 — مصدر القراءة

#### إن اخترنا المسار A

- إضافة البوت إلى كل مصدر مطلوب.
- تفعيل استقبال تحديثات القنوات عبر Bot API.
- التحقق من أن الرسائل تصل إلى Worker.
- اختبار القنوات العامة والخاصة.
- إيقاف Telethon بعد نجاح المقارنة.

#### إن اخترنا المسار B

- إبقاء Telethon في خدمة Python منفصلة.
- إضافة Event Gateway موقّع.
- إرسال الحد الأدنى من metadata والوسائط المؤقتة.
- منع Gateway من قبول أحداث بلا توقيع أو timestamp صالح.
- اختبار إعادة الإرسال الآمن.

### Phase CF-6 — Control Bot

- نقل أوامر الإعداد إلى Worker أو خدمة إدارة منفصلة.
- قصرها على `OWNER_ID`.
- حفظ الإعدادات في D1.
- عدم وضع الأسرار في رسائل Telegram أو logs.
- إضافة أوامر status وpause وresume وsources.

### Phase CF-7 — التشغيل المتوازي

- تشغيل النسخة الحالية والنسخة Cloudflare بمصدر تجريبي منفصل.
- عدم نشر نفس المصدر إلى الهدف الحقيقي من النظامين في الوقت نفسه.
- مقارنة:
  - latency
  - الألبومات
  - فشل الصلاحيات
  - RetryAfter
  - restart/redeploy
  - عدم إعادة القديم

### Phase CF-8 — القطع النهائي

لا يتم التحويل إلا بعد تحقق كل التالي:

- 24 ساعة مراقبة على مصدر تجريبي.
- لا تكرار في `idempotency_key`.
- لا فقدان للرسائل الجديدة في الاختبار.
- نجاح النص والصورة والفيديو والألبوم.
- نجاح إعادة النشر بعد RetryAfter.
- نجاح pause/resume.
- نجاح حذف الوسائط المؤقتة.
- وجود نسخة من بيانات D1 وقواعد استرجاع.
- وجود خطة رجوع إلى Docker والفرع المرجعي.

## 6. خطة الرجوع

عند فشل Cloudflare:

1. أوقف Worker أو أوقف Queue consumer.
2. أعد تشغيل نسخة Docker الحالية.
3. اعتمد baseline جديدًا من وقت الرجوع حتى لا تستورد التاريخ القديم.
4. لا تشغل الناشرين معًا على نفس الهدف.
5. راجع event log وحدد آخر رسالة مؤكدة.

## 7. اختبارات القبول

### الاختبارات الوظيفية

- رسالة نصية عربية.
- رسالة فيها رابط وحقوق ورموز.
- صورة مع caption.
- فيديو.
- ملف.
- ألبوم من صورتين.
- ألبوم بأكثر من 10 عناصر.
- رسالة بلا نص.
- محتوى يطابق exclude filter.
- محتوى لا يطابق include filter.

### اختبارات الاعتمادية

- إعادة تشغيل Worker.
- تكرار نفس webhook.
- وصول الأحداث بترتيب مختلف.
- `RetryAfter`.
- انتهاء رابط الوسائط.
- فشل Bot API.
- فشل D1 أو Queue.
- حذف الوسائط بعد النجاح.
- عدم تخزين النص أو الوسائط في D1/logs.

### اختبارات الأمن

- طلب بلا توقيع.
- توقيع خاطئ.
- timestamp قديم.
- أمر من مستخدم غير `OWNER_ID`.
- تسريب secret في logs.
- محاولة الوصول إلى R2 بدون token.

## 8. مصفوفة القرار

| الحاجة | Cloudflare-only | Hybrid |
|---|---:|---:|
| Bot API للنشر | نعم | نعم |
| Worker/Queue | نعم | نعم |
| قراءة قناة عامة عبر Bot API | غالبًا نعم بعد إضافة البوت | نعم |
| قراءة قناة خاصة بحساب مستخدم | لا | نعم |
| تشغيل Telethon Python | لا | خارج Cloudflare |
| جلسة مستخدم دائمة | لا | في Python worker |
| نقل الكود الحالي دون إعادة كتابة | لا | جزئي |
| أقل تغيير ممكن | لا | نعم |

## 9. التوصية

بما أن المتطلب الأصلي يشمل قنوات خاصة يكون حساب المستخدم عضوًا فيها، فالمسار الآمن هو **Hybrid**. أما إذا قررنا أن كل المصادر يمكن أن يضاف إليها البوت، فالمسار Cloudflare-only يصبح ممكنًا بعد إعادة كتابة المصدر والـ Transformer والـ Publisher إلى TypeScript.

لا يبدأ التنفيذ قبل أن يحدد صاحب المشروع أحد الخيارين:

```text
A = Cloudflare-only، مع اشتراط أن البوت يصل إلى جميع المصادر
B = Hybrid، Cloudflare للإدارة والطوابير + Python/Telethon للقراءة
```
