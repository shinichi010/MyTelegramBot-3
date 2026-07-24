#!/bin/bash
set -e

echo "🚀 Starting PO Token provider..."
cd /pot-provider/server
node build/main.js &
POT_PID=$!

# انتظار بسيط حتى يبدأ سيرفر PO Token قبل تشغيل البوت
sleep 3

echo "🚀 Starting Telegram bot..."
cd /app
python bot.py &
BOT_PID=$!

# إذا وحدة من العمليتين وكعت، نوقف الحاوية عشان Render يعيد التشغيل
wait -n $POT_PID $BOT_PID
exit $?
