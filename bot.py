import os
import re
import glob
import io
import asyncio
import logging
from datetime import datetime
from aiohttp import web
import aiohttp
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import FSInputFile, InputMediaPhoto, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from motor.motor_asyncio import AsyncIOMotorClient
import yt_dlp

# --- ضبط السجلات ---
logging.basicConfig(level=logging.INFO)

# --- المتغيرات البيئية ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
COOKIES_TEXT = os.getenv("COOKIES_TEXT", "")

# رابط سيرفر PO Token (اختياري - لتخطي حظر يوتيوب لـ "Sign in to confirm you're not a bot")
# شغّل bgutil-ytdlp-pot-provider جنب البوت وحط رابطه هنا، مثلاً: http://127.0.0.1:4416
POT_PROVIDER_URL = os.getenv("POT_PROVIDER_URL", "")

# إنشاء مجلد التحميلات المؤقتة
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# كتابة ملف الكوكيز إن وجد
COOKIE_FILE_PATH = "cookies.txt"
if COOKIES_TEXT:
    with open(COOKIE_FILE_PATH, "w", encoding="utf-8") as f:
        f.write(COOKIES_TEXT)

# --- إعداد قاعدة البيانات ---
mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client["telegram_bot_db"]
users_col = db["users"]
settings_col = db["settings"]

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- خادم الويب الخاص بمنع نوم Render ---
async def handle_health_check(request):
    return web.Response(text="Bot is running live 24/7!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_health_check)
    app.router.add_get("/health", handle_health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"🌐 Web server running on port {port}")

async def is_banned(user_id: int) -> bool:
    user = await users_col.find_one({"user_id": user_id})
    return bool(user and user.get("is_banned", False))

# --- /start ---
@dp.message(Command("start"))
async def start_handler(message: types.Message):
    if await is_banned(message.from_user.id):
        return await message.answer("❌ تم حظرك من استخدام هذا البوت.")

    user_id = message.from_user.id
    first_name = message.from_user.first_name or "بدون اسم"
    username = message.from_user.username or "بدون يوزر"

    existing_user = await users_col.find_one({"user_id": user_id})

    if not existing_user:
        new_user = {
            "user_id": user_id,
            "name": first_name,
            "username": username,
            "joined_at": datetime.now(),
            "is_banned": False
        }
        await users_col.insert_one(new_user)

        if ADMIN_ID:
            log_text = (
                f"🔔 **مستخدم جديد قام بتشغيل البوت!**\n\n"
                f"👤 **الاسم:** {first_name}\n"
                f"🆔 **الـ ID:** `{user_id}`\n"
                f"🔗 **اليوزر:** @{username}"
            )
            try:
                await bot.send_message(chat_id=ADMIN_ID, text=log_text, parse_mode="Markdown")
            except Exception as e:
                logging.error(f"Failed to send admin notification: {e}")

    custom_msg = await settings_col.find_one({"_id": "welcome_message"})
    welcome_text = custom_msg["text"] if custom_msg else (
        f"أهلاً بك يا {first_name} في بوت التحميل الشامل! 🚀\n\n"
        f"📍 **المنصات المدعومة:**\n"
        f"• TikTok (صور، مقاطع، صوتيات)\n"
        f"• Instagram & Facebook\n"
        f"• X (Twitter) & SoundCloud\n"
        f"• YouTube, YouTube Music & Spotify\n\n"
        f"🔗 **أرسل أي رابط للبدء مباشرة!**"
    )

    await message.answer(welcome_text, parse_mode="Markdown")

# --- الأوامر الإدارية والإحصائيات ---
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("⚙️ **لوحة التحكم:**\n\n• `/stats` - الإحصائيات وسعة السحابة\n• `/ban ID` - حظر\n• `/unban ID` - إلغاء حظر")

@dp.message(Command("stats"))
async def stats_handler(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    total_users = await users_col.count_documents({})
    estimated_size_mb = (total_users * 0.3) / 1024

    stats_msg = (
        f"📊 **إحصائيات البوت:**\n\n"
        f"👥 **عدد المستخدمين:** `{total_users}`\n"
        f"☁️ **استهلاك السحابة:** `{estimated_size_mb:.3f} MB` / 500 MB\n"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 تحميل قائمة المشتركين", callback_data="export_users")]
    ])
    await message.answer(stats_msg, parse_mode="Markdown", reply_markup=keyboard)

@dp.callback_query(F.data == "export_users")
async def export_users(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    users_cursor = users_col.find({})
    text_data = "قائمة المستخدمين:\n\n"
    async for u in users_cursor:
        text_data += f"ID: {u.get('user_id')} | Name: {u.get('name')} | User: @{u.get('username')}\n"

    buffer = io.BytesIO(text_data.encode('utf-8'))
    file = BufferedInputFile(buffer.getvalue(), filename="users.txt")
    await call.message.answer_document(file, caption="📄 قائمة المشتركين")
    await call.answer()

@dp.message(Command("ban"))
async def ban_user(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        args = message.text.split()
        if len(args) > 1:
            await users_col.update_one({"user_id": int(args[1])}, {"$set": {"is_banned": True}}, upsert=True)
            await message.answer("✅ تم الحظر.")

@dp.message(Command("unban"))
async def unban_user(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        args = message.text.split()
        if len(args) > 1:
            await users_col.update_one({"user_id": int(args[1])}, {"$set": {"is_banned": False}})
            await message.answer("✅ تم إلغاء الحظر.")

# --- تحميل صورة واحدة بهيدرز صحيحة (لتخطي حماية Referer عند تيك توك) ---
async def fetch_image_bytes(session: aiohttp.ClientSession, url: str) -> bytes | None:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.tiktok.com/",
    }
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status == 200:
                return await resp.read()
            logging.error(f"Image fetch failed status={resp.status} url={url}")
    except Exception as e:
        logging.error(f"Image fetch error: {e}")
    return None

# --- المعالجة والتحميل الشامل ---
@dp.message(F.text.startswith("http"))
async def process_download(message: types.Message):
    if await is_banned(message.from_user.id):
        return await message.answer("❌ أنت محظور من الاستخدام.")

    url = message.text.strip()
    status_msg = await message.answer("⏳ جاري التحميل والمعالجة...")
    timestamp = int(datetime.now().timestamp())
    output_template = f"{DOWNLOAD_DIR}/{message.from_user.id}_{timestamp}_%(title)s.%(ext)s"

    # إعدادات yt-dlp مع دعم الكوكيز والـ PO Token (يوتيوب) وهيدرز واقعية (تيك توك)
    ydl_opts = {
        'outtmpl': output_template,
        'quiet': True,
        'no_warnings': True,
        'format': 'bestvideo+bestaudio/best',
        'merge_output_format': 'mp4',
        'extractor_args': {},
    }

    if os.path.exists(COOKIE_FILE_PATH):
        ydl_opts['cookiefile'] = COOKIE_FILE_PATH

    # تفعيل مزود PO Token إذا محدد (يحل مشكلة "Sign in to confirm you're not a bot")
    if POT_PROVIDER_URL:
        ydl_opts['extractor_args']['youtubepot-bgutilhttp'] = {'base_url': [POT_PROVIDER_URL]}

    loop = asyncio.get_event_loop()

    try:
        def download_action():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(url, download=True)

        info = await loop.run_in_executor(None, download_action)

        if not info:
            return await status_msg.edit_text("❌ لم نتمكن من استخراج هذا الرابط.")

        uploader = info.get('uploader') or info.get('uploader_id') or "مستخدم"
        uploader_handle = f"@{uploader}" if not uploader.startswith('@') else uploader
        location = info.get('location') or info.get('country')
        tags = info.get('tags') or []

        caption_lines = [f"👤 **المستخدم:** {uploader_handle}"]
        if location:
            caption_lines.append(f"🌍 **الدولة:** {location}")
        if tags:
            caption_lines.append(f"🏷 **الهاشتاقات:** " + " ".join([f"#{t}" for t in tags[:5]]))

        caption = "\n".join(caption_lines)

        downloaded_files = glob.glob(f"{DOWNLOAD_DIR}/{message.from_user.id}_{timestamp}_*")

        # معالجة ألبومات صور تيك توك: نحمّل كل صورة بالسيرفر أولاً (بهيدرز صحيحة)
        # بدل ما نرسل الرابط مباشرة لتليجرام، لأن تيك توك يرفض الطلبات بدون Referer صحيح
        images = info.get('images') or []
        if images and not downloaded_files:
            await status_msg.edit_text("📸 جاري تحميل وإرسال ألبوم الصور...")
            async with aiohttp.ClientSession() as session:
                chunk_size = 10
                for i in range(0, len(images), chunk_size):
                    chunk = images[i:i + chunk_size]
                    media_group = []
                    for idx, img_url in enumerate(chunk):
                        img_bytes = await fetch_image_bytes(session, img_url)
                        if not img_bytes:
                            continue
                        photo_file = BufferedInputFile(img_bytes, filename=f"img_{i + idx}.jpg")
                        media_group.append(
                            InputMediaPhoto(
                                media=photo_file,
                                caption=caption if i == 0 and idx == 0 else "",
                                parse_mode="Markdown"
                            )
                        )
                    if media_group:
                        await bot.send_media_group(chat_id=message.chat.id, media=media_group)

            await status_msg.delete()
            return

        # رفع الملفات المحملة لتلجرام (فيديو أو صوت)
        if not downloaded_files:
            return await status_msg.edit_text("❌ لم يتم العثور على ملفات بعد التحميل. جرب رابط آخر.")

        for file_path in downloaded_files:
            file_input = FSInputFile(file_path)
            if file_path.endswith(('.mp4', '.mkv', '.webm')):
                await bot.send_video(chat_id=message.chat.id, video=file_input, caption=caption, parse_mode="Markdown")
            elif file_path.endswith(('.mp3', '.m4a', '.opus', '.wav')):
                await bot.send_audio(chat_id=message.chat.id, audio=file_input, title=f"صوت من {uploader_handle}", performer=uploader_handle)

        await status_msg.delete()

    except Exception as e:
        logging.error(f"Download Error: {e}")
        await status_msg.edit_text("❌ حدث خطأ أثناء التحميل. تأكد من صحة الرابط أو جرب مرة أخرى.")

    finally:
        # 🧹 تنظيف السيرفر وحذف الملفات المؤقتة لمنع امتلاء الذاكرة
        downloaded_files = glob.glob(f"{DOWNLOAD_DIR}/{message.from_user.id}_{timestamp}_*")
        for f in downloaded_files:
            try:
                os.remove(f)
            except Exception:
                pass


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
