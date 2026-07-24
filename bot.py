import os
import io
import re
import asyncio
import logging
from datetime import datetime
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, InputMediaPhoto, InlineKeyboardMarkup, InlineKeyboardButton
from motor.motor_asyncio import AsyncIOMotorClient
import yt_dlp

# --- ضبط السجلات (Logging) ---
logging.basicConfig(level=logging.INFO)

# --- جلب المتغيرات البيئية (ENV) ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
LOG_CHANNEL_ID = os.getenv("LOG_CHANNEL_ID")  # معرف القناة أو الآيدي (اختياري)
COOKIES_TEXT = os.getenv("COOKIES_TEXT", "")  # إمكانية وضع الكوكيز كمتغير

# --- إعداد قاعدة البيانات MongoDB ---
mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client["telegram_bot_db"]
users_col = db["users"]
settings_col = db["settings"]

# --- إعداد البوت والموزع ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- دالة للتحقق من حظر المستخدم ---
async def is_banned(user_id: int) -> bool:
    user = await users_col.find_one({"user_id": user_id})
    return bool(user and user.get("is_banned", False))

# --- أمر /start أو أي رسالة ترحيبية ---
@dp.message(Command("start"))
async def start_handler(message: types.Message):
    if await is_banned(message.from_user.id):
        return await message.answer("❌ تم حظرك من استخدام هذا البوت.")

    user_id = message.from_user.id
    first_name = message.from_user.first_name or ""
    username = message.from_user.username or "بدون يوزر"

    # البحث عن المستخدم في القاعدة
    existing_user = await users_col.find_one({"user_id": user_id})

    if not existing_user:
        # إضافة مستخدم جديد
        new_user = {
            "user_id": user_id,
            "name": first_name,
            "username": username,
            "joined_at": datetime.now(),
            "is_banned": False
        }
        await users_col.insert_one(new_user)

        # إرسال إشعار للمطور/القناة عند دخول شخص جديد
        log_text = (
            f"👤 **مستخدم جديد انضم للبوت!**\n\n"
            f"▫️ **الاسم:** {first_name}\n"
            f"▫️ **الـ ID:** `{user_id}`\n"
            f"▫️ **اليوزر:** @{username}"
        )
        if LOG_CHANNEL_ID:
            try:
                await bot.send_message(LOG_CHANNEL_ID, log_text, parse_mode="Markdown")
            except Exception:
                pass
        if ADMIN_ID:
            try:
                await bot.send_message(ADMIN_ID, log_text, parse_mode="Markdown")
            except Exception:
                pass

    # جلب الرسالة الترحيبية المخصصة من القاعدة أو استخدام الافتراضية
    custom_msg = await settings_col.find_one({"_id": "welcome_message"})
    welcome_text = custom_msg["text"] if custom_msg else (
        f"أهلاً بك يا {first_name} في بوت التحميل الشامل! 🚀\n\n"
        f"📍 **المنصات المدعومة للتحميل:**\n"
        f"• TikTok (صور، مقاطع، ستوريات، ألبومات صوتية)\n"
        f"• Instagram (صور، مقاطع، ستوريات، ريلز)\n"
        f"• Facebook (مقاطع وصور)\n"
        f"• X / Twitter (مقاطع وصور)\n"
        f"• SoundCloud (صوتيات)\n"
        f"• YouTube & YouTube Music (مقاطع وصوتيات)\n"
        f"• Spotify (صوتيات)\n\n"
        f"🔗 **أرسل رابط المقطع أو الصورة للبدء مباشرة!**"
    )

    await message.answer(welcome_text, parse_mode="Markdown")

# --- لوحة التحكم بالآدمن والأوامر الخاصة ---
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    admin_text = (
        "⚙️ **لوحة تحكم الأدمن:**\n\n"
        "• `/stats` - لعرض الإحصائيات واستهلاك القاعدة وجلب قائمة المشتركين.\n"
        "• `/ban [ID]` - لحظر مستخدم.\n"
        "• `/unban [ID]` - لإلغاء حظر مستخدم.\n"
        "• `/setwelcome [الرسالة]` - لتغيير رسالة الترحيب."
    )
    await message.answer(admin_text, parse_mode="Markdown")

# --- أمر الإحصائيات /stats ---
@dp.message(Command("stats"))
async def stats_handler(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    total_users = await users_col.count_documents({})
    
    # حساب تقريبي لحجم استخدام قاعدة البيانات (بدون استهلاك للميديا)
    # كل وثيقة مستخدم تستهلك حوالي 0.2 إلى 0.5 كيلو بايت
    estimated_size_mb = (total_users * 0.3) / 1024

    stats_msg = (
        f"📊 **إحصائيات البوت:**\n\n"
        f"👥 **إجمالي المستخدمين:** `{total_users}`\n"
        f"☁️ **استهلاك السحابة (MongoDB):** `{estimated_size_mb:.3f} MB` / 500 MB\n"
        f"💡 *ملاحظة: البيانات المحفوظة نصية فقط، لا يتم تخزين أية وسائط.*"
    )

    # زر لإرسال قائمة بكل المستخدمين في ملف نصي
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 تحميل قائمة اليوزرات", callback_data="export_users")]
    ])

    await message.answer(stats_msg, parse_mode="Markdown", reply_markup=keyboard)

# --- استخراج تصدير قائمة المستخدمين ---
@dp.callback_query(F.data == "export_users")
async def export_users_callback(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("غير مسموح لك.", show_alert=True)

    users_cursor = users_col.find({})
    user_list_text = "قائمة مستخدمي البوت:\n\n"
    async for u in users_cursor:
        user_list_text += f"ID: {u.get('user_id')} | Name: {u.get('name')} | Username: @{u.get('username')}\n"

    buffer = io.BytesIO(user_list_text.encode('utf-8'))
    file = BufferedInputFile(buffer.getvalue(), filename="users_list.txt")

    await call.message.answer_document(file, caption="📄 قائمة بجميع مستخدمي البوت")
    await call.answer()

# --- أومر الحظر وإلغاء الحظر ---
@dp.message(Command("ban"))
async def ban_user(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.split()
    if len(args) < 2:
        return await message.answer("يرجى كتابة الـ ID: `/ban 12345678`")
    
    target_id = int(args[1])
    await users_col.update_one({"user_id": target_id}, {"$set": {"is_banned": True}}, upsert=True)
    await message.answer(f"✅ تم حظر المستخدم `{target_id}` بنجاح.")

@dp.message(Command("unban"))
async def unban_user(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.split()
    if len(args) < 2:
        return await message.answer("يرجى كتابة الـ ID: `/unban 12345678`")
    
    target_id = int(args[1])
    await users_col.update_one({"user_id": target_id}, {"$set": {"is_banned": False}})
    await message.answer(f"✅ تم إلغاء حظر المستخدم `{target_id}` بنجاح.")

# --- تغيير الرسالة الترحيبية ---
@dp.message(Command("setwelcome"))
async def set_welcome(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    new_text = message.text.replace("/setwelcome", "").strip()
    if not new_text:
        return await message.answer("يرجى كتابة النص الجديد بعد الأمر.")
    
    await settings_col.update_one({"_id": "welcome_message"}, {"$set": {"text": new_text}}, upsert=True)
    await message.answer("✅ تم تحديث الرسالة الترحيبية بنجاح!")

# --- معالجة روابط التحميل العامة وتيك توك بشكل خاص ---
@dp.message(F.text.startswith("http"))
async def process_download(message: types.Message):
    if await is_banned(message.from_user.id):
        return await message.answer("❌ أنت محظور من استخدام البوت.")

    url = message.text.strip()
    status_msg = await message.answer("⏳ جاري جلب وسائط الرابط، انتظر لحظة...")

    # خيارات yt-dlp لاستخراج البيانات والوسائط
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'format': 'best',
    }

    loop = asyncio.get_event_loop()

    try:
        def extract_info():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(url, download=False)

        info = await loop.run_in_executor(None, extract_info)
        
        if not info:
            return await status_msg.edit_text("❌ تعذر استخراج البيانات من هذا الرابط.")

        extractor = info.get('extractor_key', '').lower()

        # ----------------------------------------------------
        # 🎵 SPECIAL HANDLING FOR TIKTOK (PHOTOS & VIDEOS)
        # ----------------------------------------------------
        if 'tiktok' in extractor:
            uploader = info.get('uploader') or info.get('uploader_id') or "TikTok_User"
            uploader_handle = f"@{uploader}" if not uploader.startswith('@') else uploader
            location = info.get('location') or info.get('country')  # جلب الدولة إن وجدت
            tags = info.get('tags') or []

            # تشكيل الهاشتاقات
            hashtags_str = " ".join([f"#{t}" for t in tags[:5]]) if tags else ""

            # صياغة النص التوضيحي للرسالة
            caption_parts = [f"👤 **المستخدم:** {uploader_handle}"]
            if location:
                caption_parts.append(f"🌍 **الدولة:** {location}")
            if hashtags_str:
                caption_parts.append(f"🏷 **الهاشتاقات:** {hashtags_str}")
            
            caption = "\n".join(caption_parts)

            # التحقق مما إذا كان المنشور عبارة عن صور متكررة (Photos / Slideshow)
            images = info.get('images') or [e.get('url') for e in info.get('entries', []) if e.get('url')]

            if images:
                await status_msg.edit_text("📸 جاري إرسال مجموعة الصور والصوت...")

                # إرسال الصور في مجموعات (كل ألبوم أقصاه 10 صور كحد أقصى لتيلجرام)
                chunk_size = 10
                for i in range(0, len(images), chunk_size):
                    chunk = images[i:i + chunk_size]
                    media_group = []
                    for idx, img_url in enumerate(chunk):
                        # إضافة الكابشن مع أول صورة في الألبوم الأول فقط
                        media_group.append(
                            InputMediaPhoto(media=img_url, caption=caption if (i == 0 and idx == 0) else "", parse_mode="Markdown")
                        )
                    await bot.send_media_group(chat_id=message.chat.id, media=media_group)

                # إرسال الصوت الخاص بالبوست المخصص وتغيير اسم الصوت إلى اسم الحساب
                audio_url = info.get('requested_subtitles', {}).get('audio', {}).get('url') or info.get('url')
                if audio_url:
                    try:
                        await bot.send_audio(
                            chat_id=message.chat.id,
                            audio=audio_url,
                            title=f"صوت من {uploader_handle}",
                            performer=uploader_handle
                        )
                    except Exception:
                        pass

                await status_msg.delete()
                return

        # ----------------------------------------------------
        # 🎥 GENERAL HANDLING (Instagram, Facebook, Twitter, YT, SoundCloud, etc.)
        # ----------------------------------------------------
        direct_url = info.get('url')
        title = info.get('title', 'تم التحميل بنجاح')

        if info.get('vcodec') != 'none' and direct_url:
            await bot.send_video(
                chat_id=message.chat.id,
                video=direct_url,
                caption=f"🎬 **{title}**\n\nتم التحميل بواسطة البوت ✨",
                parse_mode="Markdown"
            )
        elif direct_url:
            await bot.send_audio(
                chat_id=message.chat.id,
                audio=direct_url,
                caption=f"🎵 **{title}**\n\nتم التحميل بواسطة البوت ✨",
                parse_mode="Markdown"
            )
        else:
            await status_msg.edit_text("❌ لم نتمكن من جلب رابط مباشر صالحة للإرسال.")
            return

        await status_msg.delete()

    except Exception as e:
        logging.error(f"Download error: {e}")
        await status_msg.edit_text("❌ حدث خطأ أثناء التحميل. يرجى التأكد من صحة الرابط أو تجربة رابط آخر.")

from aiohttp import web

# --- خادم ويب مصغر لمنع نوم السيرفر على Render ---
async def handle_health_check(request):
    return web.Response(text="Bot is running live 24/7!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_health_check)
    app.router.add_get("/health", handle_health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    
    # Render يمرر البورت تلقائياً عبر متغير البيئة PORT
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"🌐 Web server running on port {port}")

# --- تشغيل البوت مع السيرفر ---
async def main():
    # تشغيل سرفر الويب أولاً ليقبله Render كـ Web Service
    await start_web_server()
    
    print("🚀 البوت يعمل الآن بنجاح...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
    asyncio.run(main())
