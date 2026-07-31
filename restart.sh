#!/usr/bin/env bash
# إعادة تشغيل Telegram Smart Poster كل 6 ساعات (أو عند الحاجة)
set -e

PROJECT_DIR="/workspace/app-d6g6fo9l7xtt"
LOG_FILE="/tmp/smart_poster.log"
PID_FILE="/tmp/smart_poster.pid"

echo "🔄 جاري إعادة تشغيل Telegram Smart Poster..."

# إيقاف العملية الحالية إن وجدت
if pgrep -f "${PROJECT_DIR}/main.py" > /dev/null 2>&1 || [ -f "$PID_FILE" ]; then
    pkill -9 -f "${PROJECT_DIR}/main.py" || true
    sleep 3
fi

# التأكد من عدم وجود عملية قديمة
pgrep -f "${PROJECT_DIR}/main.py" | xargs -r kill -9 2>/dev/null || true

# تشغيل النظام من جديد
cd "$PROJECT_DIR"
nohup python -u main.py > "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"

echo "✅ تم التشغيل. PID: $(cat "$PID_FILE")"
echo "📄 السجلات: $LOG_FILE"
