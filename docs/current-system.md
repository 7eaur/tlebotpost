# توثيق النسخة الحالية — Telegram Channel Relay

## 1. الغرض من هذه الوثيقة

هذه الوثيقة تصف النسخة الحالية المثبتة قبل أي إعادة تصميم لـ Cloudflare. الفرع المرجعي لهذه النسخة هو:

```text
docs/current-system-baseline
```

والنسخة محفوظة أيضًا في commit سابق على `main` قبل وثائق الانتقال. لا يجب تنفيذ خطة Cloudflare على هذا الفرع؛ يستخدم هذا الفرع للرجوع، المقارنة، أو تشغيل النسخة الحالية كما هي.

## 2. ما الذي تفعله النسخة الحالية؟

النظام يراقب رسائل جديدة من عدة قنوات Telegram عبر جلسة حساب مستخدم، ثم ينظف المحتوى وينشر نسخة جديدة في قناة الهدف بواسطة Bot API.

```text
User Telegram Session
  قراءة المصادر العامة والخاصة التي يملك الحساب وصولًا إليها
          |
          v
Live Listener + baseline
          |
          v
Transformer + album collector
          |
          v
Bot API Publisher
          |
          v
Target Channel
```

### خصائص السلوك

- لا يستورد المنشورات القديمة.
- عند بدء التشغيل أو عودة الاتصال يعتمد أحدث رسالة كنقطة بداية.
- لا يخزن نص الرسائل أو الوسائط في SQLite.
- لا يستخدم Forward؛ ينشر كرسالة جديدة.
- يزيل الروابط وحقوق المصدر والرموز التعبيرية وفق الفلاتر الحالية.
- يضيف التوقيع والرابط الثابتين.
- يدعم النصوص والصور والفيديوهات والملفات والصوتيات والألبومات.
- جلسة الحساب للقراءة وتنزيل الوسائط المؤقت فقط.
- البوت هو الذي ينشر في قناة الهدف.
- لا يتقدم مؤشر المصدر إلا بعد نجاح النشر.

## 3. المتطلبات التشغيلية

- Python 3.11 أو أحدث، أو Docker.
- `API_ID` و`API_HASH` من `my.telegram.org`.
- `BOT_TOKEN` من BotFather.
- `OWNER_ID` لحماية أوامر البوت.
- حساب المستخدم عضو في المصادر المطلوبة.
- Control Bot مشرف في قناة الهدف مع صلاحية `Post Messages`.
- تخزين دائم لمجلد `data/`؛ يحتوي SQLite وملف جلسة Telegram.
- اتصال إنترنت صادر مستمر إلى Telegram.

## 4. التشغيل المحلي بواسطة Docker

```bash
cp .env.example .env
mkdir -p data
chmod 700 data
# عدّل .env ثم شغّل

docker compose up -d --build
docker compose logs -f relay
```

إيقاف وتحديث:

```bash
docker compose stop
git pull origin main
docker compose up -d --build
```

التحقق:

```bash
docker compose ps
docker compose exec relay python -m app.health
```

## 5. التشغيل على أي VPS أو Cloud VM

1. ثبّت Docker Engine وCompose.
2. استنسخ المستودع:

```bash
git clone https://github.com/7eaur/tlebotpost.git
cd tlebotpost
```

3. أنشئ `.env` من `.env.example`.
4. أنشئ `data/` بصلاحيات `700`.
5. شغّل `docker compose up -d --build`.
6. احفظ نسخة احتياطية من `data/` قبل أي ترقية.
7. فعّل جدار الحماية، ولا تفتح منافذ غير لازمة؛ النظام يستخدم اتصالات صادرة ولا يحتاج Webhook عام.

يجب أن يكون لدى الخادم Volume دائم. لا تعتمد على نظام ملفات مؤقت لأن فقدان `telegram_user.session` يتطلب تسجيل دخول جديد، وفقدان SQLite يلغي المصادر والإعدادات والمؤشرات.

## 6. التشغيل على PaaS مثل Render أو Railway

يمكن استخدام PaaS إذا كانت تدعم خدمة Worker أو Web Service تعمل باستمرار، وتوفر تخزينًا دائمًا أو Volume.

إعدادات عامة:

- مصدر الكود: مستودع GitHub `7eaur/tlebotpost`.
- Dockerfile: `backend/Dockerfile`.
- Build context: `backend` عند البناء المباشر، أو استخدم Dockerfile الموجود في `backend` حسب إعداد المنصة.
- أمر التشغيل: `python -m app.main`.
- Volume mount: `/app/data`.
- Secrets: كل قيم `.env` تُضاف من لوحة المنصة، ولا تُرفع إلى Git.
- لا تستخدم خدمة Serverless أو Cron بدل Worker دائم.
- اختبر استمرار الملف بعد Restart قبل اعتماد المنصة.

الخدمات المجانية التي تنام أو تفقد الملفات لا تصلح للإنتاج، لكنها مناسبة لـ Pilot مؤقت.

## 7. تسجيل الدخول والإعداد الأول

بعد تشغيل الخدمة، افتح محادثة Control Bot وأرسل الأوامر:

```text
/start
/login +967XXXXXXXXX
/login_code 12345
/settarget @target_channel
/addsource @source_channel
/run
```

تسلسل الاختبار المقترح:

1. مصدر واحد.
2. قناة هدف تجريبية.
3. رسالة نصية.
4. صورة وفيديو.
5. ألبوم.
6. Restart للحاوية.
7. التأكد أن الرسائل القديمة لا يعاد نشرها.
8. بعد نجاح الاختبار أضف المصادر الفعلية.

## 8. المتغيرات المطلوبة

راجع `.env.example`. الحد الأدنى:

```env
API_ID=
API_HASH=
BOT_TOKEN=
OWNER_ID=
BRAND_FOOTER=
BRAND_LINK=
```

ويجب ضبط:

```env
SESSION_PATH=data/telegram_user.session
DATABASE_PATH=data/relay.sqlite3
SEND_INTERVAL_SECONDS=1.1
FLOOD_WAIT_RETRIES=3
```

## 9. حدود النسخة الحالية

- هدف واحد فقط.
- جلسة مستخدم واحدة.
- لا توجد لوحة ويب.
- لا يوجد أرشيف للمنشورات.
- لا يوجد catch-up بعد الانقطاع.
- لا يوجد نشر مجدول؛ التصميم لحظي.
- نجاح التشغيل الفعلي يحتاج Pilot بحساب Telegram حقيقي.

## 10. فحوص ما قبل النقل

قبل نقل البيئة إلى Cloudflare أو أي استضافة أخرى، يجب حفظ:

- `.env.example` فقط، وليس `.env`.
- نسخة آمنة من `data/telegram_user.session`.
- نسخة SQLite إن كانت الإعدادات الحالية مهمة.
- commit النسخة المرجعية.
- نتائج الاختبارات المحلية.

نتيجة التحقق عند إنشاء هذه الوثيقة:

```text
pytest: 23 passed
ruff: passed
compileall: passed
```


## 11. Railway deployment baseline

- Service source: `7eaur/tlebotpost`, branch `main`.
- Railway Root Directory: `/backend`.
- Dockerfile: `/backend/Dockerfile` (resolved as `Dockerfile` from the backend root).
- Start command: `python -m app.main`.
- Persistent Volume: mount `/app/data` for SQLite and the Telegram user session.
- Required Railway variables are the values documented in `.env.example`; secrets must be entered in Railway and never committed to Git.
