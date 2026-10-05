# تشغيل Telegram Channel Relay على خادم دائم

## المتطلبات

- Docker Engine وDocker Compose plugin.
- حساب Telegram يملك صلاحية القراءة من المصادر.
- Bot Token من BotFather.
- `API_ID` و`API_HASH` من [my.telegram.org](https://my.telegram.org).
- قناة هدف يملك الحساب أو البوت صلاحية النشر فيها.

## الإعداد الأول

```bash
cp .env.example .env
mkdir -p data
chmod 700 data
$EDITOR .env
```

القيم المطلوبة:

- `API_ID`
- `API_HASH`
- `BOT_TOKEN`
- `OWNER_ID`
- `BRAND_FOOTER` أو `BRAND_LINK`

## التشغيل

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f relay
```

يبدأ البوت بعد التشغيل، ثم من محادثة البوت مع المالك:

```text
/login +967XXXXXXXXX
/login_code 12345
/settarget @my_target_channel
/addsource @source_channel
/run
```

ابدأ بمصدر واحد وقناة هدف تجريبية. لا تضف المصادر الفعلية قبل نجاح اختبار النص والصورة والألبوم.

## الإيقاف والتحديث

```bash
docker compose stop
git pull
docker compose up -d --build
```

يستخدم النظام Volume `./data` لحفظ SQLite وملف جلسة Telegram. لا تحذف `data/` أثناء التحديث.

## النسخ الاحتياطي

أوقف الخدمة قبل النسخ حتى لا تنسخ جلسة قيد الكتابة:

```bash
docker compose stop
cp -a data "backup-data-$(date +%Y%m%d-%H%M%S)"
docker compose start
```

احمِ النسخة الاحتياطية لأنها تحتوي على جلسة حساب Telegram.

## التحقق

```bash
docker compose ps
docker compose exec relay python -m app.health
```

يجب أن تكون الحالة `healthy`. يفحص Healthcheck وجود إعدادات SQLite فقط، بينما حالة Telegram التفصيلية تظهر في logs.

## ملاحظات أمنية

- لا ترفع `.env` أو `data/` إلى Git.
- لا تشارك ملف `.session`.
- حدّث صلاحيات الخادم وDocker بانتظام.
- استخدم `OWNER_ID` الصحيح؛ كل أوامر البوت محمية به.
- لا ترسل كلمة مرور التحقق بخطوتين في قناة عامة.
