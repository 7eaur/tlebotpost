# Telegram Smart Poster

نظام نشر تلقائي ذكي على Telegram يعمل عبر حساب Telegram حقيقي، ويُدار بالكامل عبر بوت تحكم خاص. يتضمن المشروع:

- **Backend**: محرك Python يعمل 24/7 مع اكتشاف المجموعات والنشر الذكي.
- **Frontend**: لوحة تحكم React + Vite + TypeScript + Tailwind CSS + shadcn/ui.
- **Algorithms**: خوارزميات توزيع، دوران، وجدولة تكيفية.
- **Control Bot**: بوت Telegram مقتصر على `OWNER_ID`.
- **Runtime Scripts**: إدارة التشغيل المستمر وإعادة التشغيل كل 6 ساعات.

---

## Stack

- **Python 3.12+** — backend, Telegram automation, SQLite
- **Telethon** — جلسة Telegram
- **python-telegram-bot** — بوت التحكم
- **React 18+** — frontend
- **Vite** — build tool
- **TypeScript** — typed frontend
- **Tailwind CSS + shadcn/ui** — UI components
- **Biome** — lint/format
- **pnpm** — package manager

---

## Project Structure

```
├── algorithms/          # Distributor, Rotator, Scheduler
├── src/                 # React source
│   ├── components/
│   ├── contexts/
│   ├── hooks/
│   ├── pages/
│   ├── services/
│   └── types/
├── docs/                # Documentation
├── public/              # Static assets
├── config.py            # Env loader
├── control_bot.py       # Telegram control bot
├── database.py          # SQLite layer
├── group_discovery.py   # Group/channel discovery
├── main.py              # Orchestrator
├── session_manager.py   # Encrypted session manager
├── smart_engine.py      # Smart posting engine
├── requirements.txt     # Python deps
├── package.json         # Node deps
├── run.sh               # 6-hour restart loop
├── restart.sh           # Manual restart script
└── README.md
```

See `docs/SOURCE_INVENTORY.md` for a full inventory.

---

## Environment Variables

Copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env
nano .env
```

Required variables:

| Variable | Description |
|----------|-------------|
| `API_ID` | Telegram API ID from https://my.telegram.org |
| `API_HASH` | Telegram API hash |
| `BOT_TOKEN` | Control bot token from @BotFather |
| `OWNER_ID` | Telegram user ID allowed to control the bot |
| `SESSION_ENCRYPTION_KEY` | 32+ byte encryption key for session.enc |
| `MIN_DELAY` | Minimum delay between posts (seconds) |
| `MAX_DELAY` | Maximum delay between posts (seconds) |
| `SLEEP_START` | Sleep start hour (0-23) |
| `SLEEP_END` | Sleep end hour (0-23) |
| `DEFAULT_POSTS_PER_DAY` | Default posts per day |
| `DATABASE_PATH` | SQLite database path |
| `SESSION_PATH` | Encrypted session path |
| `VITE_APP_ID` | Frontend app identifier |
| `VITE_SENTRY_DSN` | Sentry DSN (optional) |

> **Never commit `.env` or `data/session.enc` to Git.**

---

## Backend Setup

```bash
# 1. Create virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# edit .env

# 4. Run the orchestrator
python main.py
```

On first run, the system will ask for your Telegram phone number and verification code. The session is encrypted and saved to `data/session.enc`.

---

## Frontend Setup

```bash
# 1. Install Node dependencies
pnpm install

# 2. Start dev server
pnpm dev

# 3. Build for production
pnpm build

# 4. Lint / format
pnpm lint
pnpm format
```

---

## Running with 6-Hour Restart

```bash
# Start the background loop
nohup ./run.sh > /tmp/runner.log 2>&1 &

# Or restart manually
./restart.sh
```

`run.sh` will keep the bot running and restart it every 6 hours.

---

## Control Bot Commands

- `/start` — Show welcome menu and status
- `/login` — Start a new Telegram session
- `/logout` — Delete current session

Menu buttons:

- **تشغيل/إيقاف النشر** — Toggle posting engine
- **المجموعات** — Group management
- **الإعدادات** — Delay/sleep settings
- **السيرفر** — Engine controls
- **الجلسة** — Session management
- **اكتشاف** — Refresh group list

---

## Docker

No Dockerfile is currently included. To run the backend, use the Python steps above. Frontend can be served statically or via `pnpm preview` after `pnpm build`.

---

## Database

The project uses SQLite. The database file is created at `DATABASE_PATH` (default `data/telegram_smart_poster.db`). No migrations are needed; schemas are created automatically on startup.

---

## Tests

- **Python**: no test suite currently included.
- **Frontend**: lint via `pnpm lint` and type-check via `tsc --noEmit`.

---

## Deployment Notes

- Keep `.env` and `data/session.enc` only on the server.
- Use a process manager or `run.sh` for 24/7 operation.
- Ensure the Telegram account is a member of the target groups and has permission to post.
- Respect Telegram ToS and avoid spam.

---

## Legal Notice

Use this tool responsibly. You must own the Telegram account, have permission to post in all target groups, and comply with Telegram's Terms of Service. The author is not responsible for misuse.

---

## Documentation

- `docs/SOURCE_INVENTORY.md` — Full source inventory and exclusions
- `docs/prd.md` — Product requirements
