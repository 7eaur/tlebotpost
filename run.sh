#!/usr/bin/env bash
# تشغيل Telegram Smart Poster مع إعادة تشغيل تلقائية كل 6 ساعات
while true; do
    /workspace/app-d6g6fo9l7xtt/restart.sh
    echo "⏰ انتظار 6 ساعات قبل إعادة التشغيل التالية..."
    sleep 21600
done
