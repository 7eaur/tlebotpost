# Source Inventory

## Project Overview

**Telegram Smart Poster** — نظام نشر تلقائي على Telegram عبر حساب حقيقي، يُدار بواسطة بوت تحكم خاص، مع واجهة React للإدارة. يعمل المحرك بشكل مستمر 24/7 ويُعيد تشغيل نفسه كل 6 ساعات.

---

## Core Components

### 1. Python Backend / Telegram Automation

| Component | Path | Responsibility |
|-----------|------|----------------|
| Main orchestrator | `main.py` | تهيئة البوت، تحميل الجلسة، اكتشاف المجموعات، تشغيل المحرك |
| Configuration | `config.py` | تحميل واعتمام المتغيرات من `.env` |
| Database layer | `database.py` | SQLite، إدارة المجموعات والسجلات والدورات |
| Session manager | `session_manager.py` | تشفير/فك تشفير جلسة Telegram |
| Smart engine | `smart_engine.py` | دورة النشر، التأخيرات، محاكاة الإنسان |
| Group discovery | `group_discovery.py` | اكتشاف المجموعات والقنوات والتحقق من الصلاحيات |
| Control bot | `control_bot.py` | بوت Telegram للتحكم بالمالك فقط |
| Algorithms | `algorithms/` | Distributor, Rotator, Scheduler |
| Requirements | `requirements.txt` | حزم Python |
| Runtime scripts | `run.sh`, `restart.sh` | إدارة التشغيل المستمر وإعادة التشغيل |

### 2. Frontend (React + Vite + TypeScript)

| Component | Path |
|-----------|------|
| Application entry | `src/App.tsx`, `src/main.tsx` |
| Pages | `src/pages/` |
| Components | `src/components/` |
| Hooks | `src/hooks/` |
| Contexts | `src/contexts/` |
| Types | `src/types/` |
| Styles | `src/index.css`, `tailwind.config.js` |
| Config | `vite.config.ts`, `tsconfig*.json` |
| Dependencies | `package.json`, `pnpm-workspace.yaml` |

### 3. Documentation & Configuration

| File | Purpose |
|------|---------|
| `README.md` | دليل المشروع |
| `docs/SOURCE_INVENTORY.md` | هذا الملف |
| `docs/prd.md` | متطلبات المنتج |
| `.env.example` | نموذج المتغيرات البيئية |
| `.gitignore` | استثناء الملفات الحساسة والتشغيلية |
| `biome.json` | إعدادات Biome للـ lint/format |
| `components.json` | إعدادات shadcn/ui |

---

## Directory Summary

```
app-d6g6fo9l7xtt/
├── algorithms/          # Python scheduling/distribution logic
├── src/                 # React frontend source
│   ├── components/
│   ├── contexts/
│   ├── hooks/
│   ├── pages/
│   ├── services/
│   └── types/
├── docs/                # Documentation
├── public/              # Static assets
├── .rules/              # Project-specific rules
├── config.py
├── control_bot.py
├── database.py
├── group_discovery.py
├── main.py
├── requirements.txt
├── run.sh
├── restart.sh
├── session_manager.py
├── smart_engine.py
├── package.json
├── vite.config.ts
└── tailwind.config.js
```

---

## File Count

- تقريباً **125** ملفًا/مجلدًا ضمن مصادر المشروع (بعد استثناء الملفات المُستثناة والملفات التشغيلية).
- يتضمن ذلك ملفات Python و React و TypeScript و CSS و SVG و JSON و Markdown و shell scripts.

---

## Excluded Files & Reasons

| Pattern | Reason |
|---------|--------|
| `.env` | يحتوي على Secrets (BOT_TOKEN, API_ID, API_HASH, OWNER_ID, SESSION_KEY) |
| `.env.local` | قيم محلية سرية |
| `.ssh/` | مفاتيح SSH الخاصة للنشر |
| `data/` | قاعدة البيانات التشغيلية والجلسة المشفرة |
| `*.session`, `*.session-journal`, `session.enc` | بيانات جلسة Telegram |
| `historical_context.txt` | سجل تاريخي يحتوي على Secrets ومناظرات داخلية |
| `.skills/`, `tasks/` | مهارات ومهام منصة التطوير (ليس مصدر التطبيق) |
| `.sync/`, `history/*.json` | ملفات مزامنة داخلية |
| `__pycache__/`, `*.pyc` | بايتات Python المترجمة |
| `node_modules/`, `dist/`, `dist-ssr/`, `output/` | اعتماديات ومجلدات بناء Frontend |
| `*.log` | ملفات السجلات التشغيلية |
| `package-lock.json` | غير مستخدم (نظام pnpm) |

---

## Required Services

- **Telegram API** — `API_ID` و `API_HASH` من https://my.telegram.org
- **BotFather** — `BOT_TOKEN` لبوت التحكم
- **Python 3.12+** — لتشغيل المحرك
- **Node.js + pnpm** — لبناء وتشغيل الواجهة
- **SQLite** — تخزين البيانات محليًا (بدون خادم خارجي)

---

## Required Environment Variables

انظر `.env.example` للقائمة الكاملة:

- `API_ID`
- `API_HASH`
- `BOT_TOKEN`
- `OWNER_ID`
- `SESSION_ENCRYPTION_KEY`
- `MIN_DELAY`
- `MAX_DELAY`
- `SLEEP_START`
- `SLEEP_END`
- `DEFAULT_POSTS_PER_DAY`
- `DATABASE_PATH`
- `SESSION_PATH`
- `VITE_APP_ID`
- `VITE_SENTRY_DSN`

---

## What Is Not in GitHub

- **Runtime data**: `data/telegram_smart_poster.db`, `data/session.enc`
- **Secrets**: `.env`, مفاتيح SSH
- **Platform artifacts**: `.skills/`, `tasks/`, `historical_context.txt`
- **Build outputs**: `dist/`, `node_modules/`, `__pycache__/`
- **Logs**: `*.log`

يجب إنشاء هذه الملفات وتشغيلها محليًا على الخادم أو البيئة التشغيلية.
