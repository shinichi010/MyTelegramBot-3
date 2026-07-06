import os
import asyncio
import logging
import base64
from aiohttp import web
import yt_dlp
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from motor.motor_asyncio import AsyncIOMotorClient

# --- إعدادات اللوك (Logging) ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- المتغيرات البيئية (Environment Variables) ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "توكن_البوت")
MONGO_URI = os.getenv("MONGO_URI", "رابط_مونكو")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0")) # حط الأيدي مالتك هنا بالريندر
ADMIN_CHANNEL = os.getenv("ADMIN_CHANNEL", None)
PORT = int(os.environ.get("PORT", 8080))
COOKIES_DATA = os.getenv("COOKIES_DATA", "")

# --- الاتصال بقاعدة البيانات ---
client = AsyncIOMotorClient(MONGO_URI)
db = client['media_bot_db']
users_collection = db['users']

# ==========================================
# قسم تجهيز الكوكيز
# ==========================================
def setup_cookies():
    """دالة لفك تشفير الكوكيز من Base64 وتحويلها لملف txt"""
    if COOKIES_DATA:
        try:
            decoded_cookies = base64.b64decode(COOKIES_DATA).decode('utf-8')
            with open("cookies.txt", "w", encoding="utf-8") as f:
                f.write(decoded_cookies)
            logger.info("✅ تم تجهيز ملف الكوكيز (cookies.txt) بنجاح.")
        except Exception as e:
            logger.error(f"❌ صار خطأ أثناء فك تشفير الكوكيز: {e}")
    else:
        logger.warning("⚠️ متغير COOKIES_DATA غير موجود.")

# ==========================================
# قسم التحميل (yt-dlp)
# ==========================================
def _download_sync(url):
    """دالة التحميل المتزامنة (Sync) اللي تستخدم yt-dlp"""
    ydl_opts = {
        'outtmpl': '%(id)s.%(ext)s',
        'format': 'best', # يختار أفضل جودة
        'cookiefile': 'cookies.txt' if os.path.exists('cookies.txt') else None,
        'quiet': True,
        'no_warnings': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info)

async def download_media(url):
    """تشغيل دالة التحميل بثريد منفصل علمود ميوكف البوت عن باقي المستخدمين"""
    return await asyncio.to_thread(_download_sync, url)

# ==========================================
# قسم قاعدة البيانات والإدارة
# ==========================================
async def check_and_add_user(user, context: ContextTypes.DEFAULT_TYPE):
    existing_user = await users_collection.find_one({"user_id": user.id})
    if not existing_user:
        user_data = {
            "user_id": user.id,
            "first_name": user.first_name,
            "username": user.username,
            "is_banned": False
        }
        await users_collection.insert_one(user_data)
        
        # إرسال إشعار للآدمن
        notification_text = (
            f"👤 مستخدم جديد استخدم البوت!\n\n"
            f"الاسم: {user.first_name}\n"
            f"اليوزر: @{user.username if user.username else 'لا يوجد'}\n"
            f"الآيدي: `{user.id}`"
        )
        target_chat = ADMIN_CHANNEL if ADMIN_CHANNEL else ADMIN_ID
        if target_chat and target_chat != 0:
            try:
                await context.bot.send_message(chat_id=target_chat, text=notification_text, parse_mode='Markdown')
            except Exception as e:
                logger.error(f"Failed to send admin notification: {e}")
    return existing_user

# ==========================================
# أوامر التليكرام
# ==========================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_db = await check_and_add_user(user, context)
    
    if user_db and user_db.get("is_banned"):
        return
        
    welcome_msg = (
        f"مرحباً {user.first_name}! 👋\n\n"
        "أنا بوت تحميل الميديا. أقدر أحملك الصور، المقاطع، والستوريات من المنصات التالية:\n"
        "✅ تيك توك\n"
        "✅ فيسبوك\n"
        "✅ انستكرام\n"
        "✅ إكس (تويتر)\n"
        "✅ ساوند كلاود\n\n"
        "🎶 أما بالنسبة للـ (YouTube, YouTube Music, Spotify) "
        "فقط أرسل الرابط وراح أوجهك للبوت المخصص إلهن."
    )
    await update.message.reply_text(welcome_msg)

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text
    
    user_db = await check_and_add_user(user, context)
    if user_db and user_db.get("is_banned"):
        return
        
    # 1. فحص منصات يوتيوب وسبوتيفاي
    redirect_domains = ['youtube.com', 'youtu.be', 'music.youtube.com', 'spotify.com']
    if any(domain in text.lower() for domain in redirect_domains):
        await update.message.reply_text("للتنزيل من هاي المنصة استخدم هذا البوت: @ReiSave_bot 🤖")
        return

    # 2. فحص المنصات المدعومة
    supported_domains = ['tiktok.com', 'facebook.com', 'fb.watch', 'instagram.com', 'twitter.com', 'x.com', 'soundcloud.com']
    if any(domain in text.lower() for domain in supported_domains):
        status_msg = await update.message.reply_text("⏳ جاري التحميل، انتظر ثواني...")
        
        try:
            # تحميل الملف
            file_path = await download_media(text)
            
            # إرسال الملف للمستخدم
            await update.message.reply_video(video=open(file_path, 'rb'))
            
            # حذف الملف من السيرفر حتى ما ياخذ مساحة
            os.remove(file_path)
            await status_msg.delete()
            
        except Exception as e:
            logger.error(f"Error downloading {text}: {e}")
            await status_msg.edit_text("❌ عذراً، صار خطأ أثناء التحميل. تأكد من الرابط أو حاول مرة ثانية.")
            
        return
        
    await update.message.reply_text("❌ عذراً، هذا الرابط غير مدعوم أو غير صالح.")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
        
    msg = await update.message.reply_text("🔄 جاري جمع الإحصائيات...")
    db_stats = await db.command("dbstats")
    data_size_mb = db_stats.get("dataSize", 0) / (1024 * 1024)
    
    users_cursor = users_collection.find({})
    users_list = await users_cursor.to_list(length=None)
    total_users = len(users_list)
    
    users_text = "قائمة المستخدمين:\n\n"
    for u in users_list:
        uname = f"@{u['username']}" if u.get('username') else "لا يوجد"
        users_text += f"- {u.get('first_name')} | {uname} | {u.get('user_id')}\n"
    
    with open("users_list.txt", "w", encoding="utf-8") as f:
        f.write(users_text)
        
    stats_msg = (
        f"📊 **إحصائيات البوت**\n\n"
        f"👥 عدد المستخدمين الكلي: {total_users}\n"
        f"💾 استهلاك مساحة السحابة (MongoDB): {data_size_mb:.2f} MB من أصل 500 MB\n"
    )
    
    await update.message.reply_document(document=open("users_list.txt", "rb"), caption=stats_msg, parse_mode='Markdown')
    os.remove("users_list.txt")
    await msg.delete()

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        target_id = int(context.args[0])
        await users_collection.update_one({"user_id": target_id}, {"$set": {"is_banned": True}})
        await update.message.reply_text(f"✅ تم حظر المستخدم `{target_id}` بنجاح.", parse_mode='Markdown')
    except (IndexError, ValueError):
        await update.message.reply_text("❌ أرسل الأمر متبوعاً بآيدي المستخدم.\nمثال: `/ban 123456789`")

# ==========================================
# قسم سيرفر الريندر (Render Health Server)
# ==========================================
async def health_handler(request):
    return web.Response(text="Bot is alive and running!")

async def _start_health_server():
    app = web.Application()
    app.router.add_get('/', health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"🌐 Health server started on port {PORT}")

async def _keep_alive(app):
    while True:
        await asyncio.sleep(600)

async def _post_init(app: Application):
    await _start_health_server()
    asyncio.create_task(_keep_alive(app))

# ==========================================
# التشغيل الأساسي
# ==========================================
def main():
    # 1. تجهيز الكوكيز قبل تشغيل أي شيء
    setup_cookies()
    
    # 2. بناء التطبيق
    app = Application.builder().token(BOT_TOKEN).post_init(_post_init).build()

    # 3. الأوامر
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages))

    # 4. تشغيل البوت
    logger.info("🚀 Bot started!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
