# تشغيل Runtime v2 عبر Docker Compose

## المكونات

يحتوي `docker-compose.yml` على خدمتين:

- `postgres`: PostgreSQL 16 مع volume دائم وتهيئة أولية من `backend/db/schema.sql`.
- `runtime-v2`: صورة Python تشغل `scripts/run_v2.py` وتعتمد على جاهزية PostgreSQL.

## الإعداد

انسخ نموذج البيئة:

```bash
cp .env.example .env
```

ثم اضبط القيم الحساسة في `.env`:

```env
API_ID=123456
API_HASH=...
BOT_TOKEN=...
V2_ACCOUNT_ID=...
POSTGRES_PASSWORD=ضع_كلمة_مرور_قوية
```

تأكد أن مجلد البيانات موجود، لأن جلسة Telegram تحفظ فيه:

```bash
mkdir -p data
```

## التشغيل

```bash
docker compose up -d --build
```

مراقبة السجلات:

```bash
docker compose logs -f runtime-v2
```

حالة الخدمات:

```bash
docker compose ps
```

إيقاف الحاويات مع إبقاء بيانات PostgreSQL:

```bash
docker compose down
```

## التهيئة الأولى

عند إنشاء volume PostgreSQL لأول مرة، ينفذ Compose:

```text
backend/db/schema.sql
```

ملف التهيئة ينفذ مرة واحدة فقط على volume جديد. بعد تعديل schema في بيئة تطوير جديدة استخدم migration صريحًا، ولا تحذف volume الإنتاج تلقائيًا.

لإعادة إنشاء قاعدة بيانات تطويرية بالكامل فقط:

```bash
docker compose down -v
docker compose up -d --build
```

هذا الأمر يحذف بيانات PostgreSQL، لذلك لا يستخدم في الإنتاج.

## نقاط مهمة

- داخل Runtime تكون قاعدة البيانات على المضيف `postgres` وليس `localhost`.
- Compose يفرض `DATABASE_URL` داخليًا ويستخدم `POSTGRES_*` لإنشاء الرابط.
- جلسة Telegram تحفظ على المضيف في `./data/v2.session` عبر `/app/data/v2.session`.
- لا يتم تشغيل النظام القديم `relay` من هذا Compose؛ هذا الملف خاص بـ Runtime v2.
- يجب أن يكون Bot مشرفًا في القنوات المستهدفة.
- يجب أن تكون جلسة Telegram user مصادقًا عليها قبل التشغيل المستمر.
- إذا كانت كلمة مرور PostgreSQL تحتوي رموزًا خاصة فيجب URL-encode للرابط أو استخدام كلمة مرور آمنة مناسبة لـ URL.

## الفحص

```bash
docker compose config
docker compose build runtime-v2
docker compose up -d
```

بعد ذلك راجع:

```bash
docker compose logs --tail=100 postgres
docker compose logs --tail=100 runtime-v2
```
