import os
import asyncio
import logging
import base64
import glob
from aiohttp import web
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from motor.motor_asyncio import AsyncIOMotorClient

# --- إعدادات اللوك (Logging) ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- المتغيرات البيئية ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "توكن_البوت")
MONGO_URI = os.getenv("MONGO_URI", "رابط_مونكو")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
ADMIN_CHANNEL = os.getenv("ADMIN_CHANNEL", None)
PORT = int(os.environ.get("PORT", 8080))
COOKIES_DATA = os.getenv("COOKIES_DATA", "")
CUSTOM_USER_AGENT = os.getenv("USER_AGENT", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# --- الاتصال بقاعدة البيانات ---
client = AsyncIOMotorClient(MONGO_URI)
db = client['media_bot_db']
users_collection = db['users']
settings_collection = db['settings']

# ==========================================
# قسم الإعدادات الديناميكية (MongoDB)
# ==========================================
async def get_bot_settings():
    """جلب الإعدادات أو إنشائها الافتراضية"""
    settings = await settings_collection.find_one({"config": "main"})
    if not settings:
        default_settings = {
            "config": "main",
            "welcome_message": "مرحباً {name}! 👋\n\nأنا بوت تحميل الميديا. أقدر أحملك الصور والمقاطع من التيك توك، الانستا، الفيس، إكس، وساوند كلاود.\n\nللتحميل من يوتيوب وسبوتيفاي استخدم: @ReiSave_bot"
        }
        await settings_collection.insert_one(default_settings)
        return default_settings
    return settings

def setup_cookies():
    """فك تشفير الكوكيز لحسابات الانستا والفيس"""
    if COOKIES_DATA:
        try:
            decoded_cookies = base64.b64decode(COOKIES_DATA).decode('utf-8')
            with open("cookies.txt", "w", encoding="utf-8") as f:
                f.write(decoded_cookies)
            logger.info("✅ تم تجهيز ملف الكوكيز بنجاح.")
        except Exception as e:
            logger.error(f"❌ خطأ بالكوكيز: {e}")

# ==========================================
# قسم التحميل الذكي (يدعم فيديوهات وصور التيك توك والمنصات)
# ==========================================
def _download_sync(url):
    """إعدادات متطورة لتخطي حظر الانستا والفيس وتنزيل البومات الصور"""
    ydl_opts = {
        'outtmpl': 'downloads/%(id)s_%(playlist_index)s.%(ext)s',
        'cookiefile': 'cookies.txt' if os.path.exists('cookies.txt') else None,
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 40,
        'allow_playlist_handle': True,
        'extract_flat': False,
        'http_headers': {
            'User-Agent': CUSTOM_USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Sec-Fetch-Mode': 'navigate',
        },
        'extractor_args': {
            'instagram': {'apps': True},
            'twitter': {'api': 'graphql'}
        }
    }
    
    if not os.path.exists('downloads'):
        os.makedirs('downloads')
        
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return info

async def download_media(url):
    return await asyncio.to_thread(_download_sync, url)

# ==========================================
# إدارة المستخدمين والإشعارات
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
        
        # إشعار الآدمن
        notification_text = (
            f"👤 <b>مستخدم جديد دخل للبوت أول مرة!</b>\n\n"
            f"الاسم: {user.first_name}\n"
            f"اليوزر: @{user.username if user.username else 'لا يوجد'}\n"
            f"الآيدي: <code>{user.id}</code>"
        )
        target_chat = ADMIN_CHANNEL if ADMIN_CHANNEL else ADMIN_ID
        if target_chat and target_chat != 0:
            try:
                await context.bot.send_message(chat_id=target_chat, text=notification_text, parse_mode='HTML')
            except Exception as e:
                logger.error(f"⚠️ لم يتم إرسال إشعار الآدمن: {e}. (تأكد انك مفعل البوت أولاً ودزيتله /start)")
    return existing_user

# ==========================================
# معالجة الرسائل والروابط المدعومة
# ==========================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_db = await check_and_add_user(user, context)
    if user_db and user_db.get("is_banned"): return
    
    settings = await get_bot_settings()
    welcome_text = settings["welcome_message"].format(name=user.first_name)
    await update.message.reply_text(welcome_text)

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text
    
    user_db = await check_and_add_user(user, context)
    if user_db and user_db.get("is_banned"): return

    # حالة انتظار مدخلات الآدمن لتغيير الرسالة أو الحظر
    if context.user_data.get("admin_state") and user.id == ADMIN_ID:
        state = context.user_data.get("admin_state")
        if state == "waiting_welcome":
            await settings_collection.update_one({"config": "main"}, {"$set": {"welcome_message": text}})
            await update.message.reply_text("✅ تم تحديث رسالة الترحيب بنجاح!")
            context.user_data.clear()
            return
        elif state == "waiting_ban":
            try:
                uid = int(text)
                await users_collection.update_one({"user_id": uid}, {"$set": {"is_banned": True}})
                await update.message.reply_text(f"🔒 تم حظر المستخدم {uid}")
            except:
                await update.message.reply_text("❌ الآيدي غير صالح.")
            context.user_data.clear()
            return
        elif state == "waiting_unban":
            try:
                uid = int(text)
                await users_collection.update_one({"user_id": uid}, {"$set": {"is_banned": False}})
                await update.message.reply_text(f"🔓 تم إلغاء حظر المستخدم {uid}")
            except:
                await update.message.reply_text("❌ الآيدي غير صالح.")
            context.user_data.clear()
            return

    # توجيه منصات يوتيوب وسبوتيفاي
    redirect_domains = ['youtube.com', 'youtu.be', 'music.youtube.com', 'spotify.com']
    if any(domain in text.lower() for domain in redirect_domains):
        await update.message.reply_text("للتنزيل من هاي المنصة استخدم هذا البوت: @ReiSave_bot 🤖")
        return

    # المنصات المدعومة
    supported_domains = ['tiktok.com', 'facebook.com', 'fb.watch', 'instagram.com', 'twitter.com', 'x.com', 'soundcloud.com']
    if any(domain in text.lower() for domain in supported_domains):
        status_msg = await update.message.reply_text("⏳ جاري سحب ومعالجة الرابط، انتظر ثواني...")
        
        try:
            info = await download_media(text)
            
            # فحص إذا كان المنشور البوم صور (مثل تيك توك)
            if 'entries' in info or (info.get('_type') == 'playlist') or ('requested_downloads' in info and len(info['requested_downloads']) > 1):
                downloaded_files = glob.glob("downloads/*")
                images = [f for f in downloaded_files if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp'))]
                audio = [f for f in downloaded_files if f.lower().endswith(('.mp3', '.m4a', '.wav', '.ogg', '.webm'))]
                
                uploader = info.get('uploader', 'Unknown')
                description = info.get('description', 'بدون وصف')
                total_pics = len(images)
                
                caption = f"👤 الحساب: @{uploader}\n📸 عدد الصور: {total_pics}\n\n📝 الوصف والهاشتاقات:\n{description[:800]}"
                
                # إرسال الصور كمجموعات (كل مجموعة 10 صور كحد أقصى)
                for i in range(0, len(images), 10):
                    chunk = images[i:i+10]
                    media_group = []
                    for idx, img in enumerate(chunk):
                        if i == 0 and idx == 0:
                            media_group.append(InputMediaPhoto(open(img, 'rb'), caption=caption))
                        else:
                            media_group.append(InputMediaPhoto(open(img, 'rb')))
                    await update.message.reply_media_group(media=media_group)
                
                # إرسال الصوت الخلفي للمنشور إذا وجد
                if audio:
                    await update.message.reply_audio(audio=open(audio[0], 'rb'), caption="🎵 صوت المنشور المصاحب")
                    
            else:
                # تحميل فيديو عادي أو ملف صوت منفرد
                downloaded_files = glob.glob("downloads/*")
                if downloaded_files:
                    target_file = downloaded_files[0]
                    if target_file.lower().endswith(('.mp3', '.m4a', '.ogg', '.wav')):
                        await update.message.reply_audio(audio=open(target_file, 'rb'))
                    else:
                        await update.message.reply_video(video=open(target_file, 'rb'))
                else:
                    raise Exception("No files downloaded")
                    
            # تنظيف المجلد المؤقت فوراً لحفظ مساحة السيرفر
            for f in glob.glob("downloads/*"):
                os.remove(f)
            await status_msg.delete()
            
        except Exception as e:
            logger.error(f"Error processing {text}: {e}")
            for f in glob.glob("downloads/*"): 
                try: os.remove(f)
                except: pass
            await status_msg.edit_text("❌ حدث خطأ أثناء التحميل. تأكد أن الرابط عام وليس من حساب خاص (Private)، أو أعد المحاولة لاحقاً.")
        return

# ==========================================
# لوحة تحكم الآدمن المتقدمة بأزرار إنلاين
# ==========================================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    
    keyboard = [
        [InlineKeyboardButton("📊 الإحصائيات والسحابة", callback_data="adm_stats")],
        [InlineKeyboardButton("📝 تعديل رسالة الترحيب", callback_data="adm_edit_welcome")],
        [InlineKeyboardButton("🚫 حظر مستخدم", callback_data="adm_ban"), InlineKeyboardButton("🔓 إلغاء حظر", callback_data="adm_unban")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🛠️ <b>أهلاً بك في لوحة تحكم الآدمن:</b>", reply_markup=reply_markup, parse_mode='HTML')

async def admin_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "adm_stats":
        db_stats = await db.command("dbstats")
        data_size_mb = db_stats.get("dataSize", 0) / (1024 * 1024)
        
        users_cursor = users_collection.find({})
        users_list = await users_cursor.to_list(length=None)
        
        users_text = "📊 <b>إحصائيات البوت الحالية:</b>\n\n"
        users_text += f"👥 عدد المشتركين: {len(users_list)}\n"
        users_text += f"💾 استهلاك MongoDB: {data_size_mb:.2f} MB / 500 MB\n\n"
        users_text += "<b>قائمة الأعضاء ويوزراتهم:</b>\n"
        
        for u in users_list[:40]: # جلب أول 40 عضو بالرسالة للحفاظ على الحجم
            uname = f"@{u['username']}" if u.get('username') else "لا يوجد"
            status = " [محظور]" if u.get('is_banned') else ""
            users_text += f"- {u.get('first_name')} | {uname} | <code>{u.get('user_id')}</code>{status}\n"
            
        await query.edit_message_text(users_text, parse_mode='HTML')
        
    elif query.data == "adm_edit_welcome":
        context.user_data["admin_state"] = "waiting_welcome"
        await query.edit_message_text("📝 <b>أرسل الآن نص رسالة الترحيب الجديدة كرسالة عادية:</b>\n\n(تلميح: يمكنك استخدام <code>{name}</code> بداخل النص ليتم استبداله باسم المستخدم تلقائياً)", parse_mode='HTML')
        
    elif query.data == "adm_ban":
        context.user_data["admin_state"] = "waiting_ban"
        await query.edit_message_text("🚫 <b>أرسل آيدي (ID) المستخدم الذي تريد حظره الآن:</b>", parse_mode='HTML')
        
    elif query.data == "adm_unban":
        context.user_data["admin_state"] = "waiting_unban"
        await query.edit_message_text("🔓 <b>أرسل آيدي (ID) المستخدم لإلغاء الحظر عنه:</b>", parse_mode='HTML')

# ==========================================
# سيرفر الريندر والتفعيل الأساسي
# ==========================================
async def health_handler(request): return web.Response(text="Bot is operational")

async def _start_health_server():
    app = web.Application()
    app.router.add_get('/', health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()

async def _post_init(app: Application):
    await _start_health_server()

def main():
    setup_cookies()
    app = Application.builder().token(BOT_TOKEN).post_init(_post_init).build()

    # الأوامر ومستمعات الأحداث
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CallbackQueryHandler(admin_callbacks, pattern="^adm_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages))

    logger.info("🚀 البوت انطلق وجاهز للعمل!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
