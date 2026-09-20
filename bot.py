import os
import sys
import uuid
import logging
import asyncio
from pathlib import Path
from dotenv import load_dotenv

from aiohttp import web
from pyrogram import Client, filters, idle
from pyrogram.types import Message
from pyrogram.enums import ParseMode

from video_processor import process_video_with_banner, calculate_insertion_time, get_media_info

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("telegram_banner_bot")

raw_token = os.getenv("BOT_TOKEN", "").strip().strip("\"'").strip()
raw_api_id = os.getenv("API_ID", "").strip().strip("\"'").strip()
raw_api_hash = os.getenv("API_HASH", "").strip().strip("\"'").strip()

if not raw_token:
    logger.critical("❌ ПОМИЛКА: BOT_TOKEN не знайдено!")
    sys.exit(1)

if not raw_api_id or not raw_api_hash:
    logger.critical("❌ ПОМИЛКА: API_ID або API_HASH відсутні в Environment Variables на Render!")
    sys.exit(1)

try:
    API_ID = int(raw_api_id)
    API_HASH = str(raw_api_hash)
    BOT_TOKEN = str(raw_token)
except Exception as e:
    logger.critical(f"❌ ПОМИЛКА формату API_ID (має бути число): {e}")
    sys.exit(1)

BANNER_PATH = os.getenv("BANNER_PATH", "banner.mp4")
TEMP_DIR = Path(os.getenv("TEMP_DIR", "temp_processing"))
TEMP_DIR.mkdir(parents=True, exist_ok=True)

ADMIN_IDS = [int(i.strip()) for i in os.getenv("ADMIN_IDS", "").split(",") if i.strip().isdigit()]

app = Client(
    name="banner_bot_session",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    parse_mode=ParseMode.HTML,
    in_memory=True
)

def is_admin(user_id: int) -> bool:
    if not ADMIN_IDS:
        return True
    return user_id in ADMIN_IDS

def is_banner_valid() -> bool:
    return os.path.exists(BANNER_PATH) and os.path.getsize(BANNER_PATH) > 500

@app.on_message(filters.command("start"))
async def cmd_start(client: Client, message: Message):
    logger.info(f"Отримано /start від {message.from_user.id if message.from_user else 'Unknown'}")
    text = (
        "👋 <b>Привіт! Я оновлений бот для вставки банера у відео (файли 50+ МБ та до 2 ГБ!).</b>\n\n"
        "⚡ <b>Як це працює:</b>\n"
        "1. Просто надішліть мені відео будь-якого розміру.\n"
        "2. Бот вставить банер <b>рівно посередині (час / 2)</b> з ефектом паузи головного відео.\n"
        "3. Після банера головне відео продовжує грати далі!\n\n"
        "📹 <b>Зміна банера:</b>\n"
        "Надішліть відео банера з текстом <code>/setbanner</code> або зробіть Reply з текстом <code>/setbanner</code>."
    )
    await message.reply_text(text)

@app.on_message(filters.command("help"))
async def cmd_help(client: Client, message: Message):
    text = (
        "📖 <b>Допомога:</b>\n\n"
        "• Надішліть будь-яке відео (розмір до 2 ГБ).\n"
        "• Бот вставить банер рівно посередині.\n"
        "• Для оновлення банера: надішліть відео з підписом <code>/setbanner</code>."
    )
    await message.reply_text(text)

async def save_new_banner(message: Message, media_msg: Message):
    status_msg = await message.reply_text("⏳ Завантажую та зберігаю новий банер...")
    try:
        if os.path.exists(BANNER_PATH):
            try:
                os.remove(BANNER_PATH)
            except Exception:
                pass
                
        await media_msg.download(file_name=BANNER_PATH)
        banner_info = get_media_info(BANNER_PATH)
        await status_msg.edit_text(
            f"✅ <b>Банер успішно збережено!</b>\n"
            f"• Тривалість: <b>{banner_info['duration']:.2f} сек</b>\n"
            f"• Роздільна здатність: <b>{banner_info['width']}x{banner_info['height']}</b>\n\n"
            f"🚀 Тепер надсилайте ваші відео для обробки!"
        )
    except Exception as e:
        logger.exception("Error saving banner")
        await status_msg.edit_text(f"❌ Помилка оновлення банера: {e}")

@app.on_message(filters.command("setbanner"))
async def cmd_setbanner(client: Client, message: Message):
    user_id = message.from_user.id if message.from_user else 0
    if not is_admin(user_id):
        await message.reply_text("⛔ У вас немає прав для зміни банера.")
        return

    reply = message.reply_to_message
    if reply and (reply.video or reply.animation or reply.document):
        await save_new_banner(message, reply)
        return

    await message.reply_text("📹 Надішліть відео банера з текстом <code>/setbanner</code> у підписі або зробіть <b>Reply (Відповісти)</b> на відео з командою <code>/setbanner</code>.")

@app.on_message(filters.video | filters.document | filters.animation)
async def handle_media(client: Client, message: Message):
    user_id = message.from_user.id if message.from_user else 0
    caption = (message.caption or "").strip().lower()
    is_set_banner = "/setbanner" in caption

    if message.document:
        mime = message.document.mime_type or ""
        fname = (message.document.file_name or "").lower()
        if not ("video" in mime or "octet-stream" in mime or fname.endswith((".mp4", ".mov", ".avi", ".mkv", ".webm"))):
            if not is_set_banner:
                await message.reply_text("⚠️ Будь ласка, надішліть саме відеофайл (MP4/MOV тощо).")
                return

    if is_set_banner:
        if not is_admin(user_id):
            await message.reply_text("⛔ У вас немає прав для оновлення банера.")
            return
        await save_new_banner(message, message)
        return

    if not is_banner_valid():
        await message.reply_text(
            "⚠️ <b>Файл банера ще не встановлено!</b>\n\n"
            "Будь ласка, надішліть відео банера з підписом <code>/setbanner</code> або зробіть Reply на відео з текстом <code>/setbanner</code>."
        )
        return

    req_id = str(uuid.uuid4())[:8]
    input_path = str(TEMP_DIR / f"input_{req_id}.mp4")
    output_path = str(TEMP_DIR / f"output_{req_id}.mp4")

    status_msg = await message.reply_text("📥 <b>Завантажую відео (MTProto без ліміту 20 МБ)...</b>")

    try:
        await message.download(file_name=input_path)

        info = get_media_info(input_path)
        dur = info["duration"]
        insert_time = calculate_insertion_time(dur)

        await status_msg.edit_text(
            f"⚡ <b>Обробка відео через FFmpeg...</b>\n"
            f"• Повна тривалість: <b>{dur:.1f} сек</b>\n"
            f"• Вставка банера: <b>{insert_time:.1f} сек (середина відео)</b>\n\n"
            f"⏳ Обробляємо, зачекайте..."
        )

        await process_video_with_banner(input_path, BANNER_PATH, output_path)

        await status_msg.edit_text("📤 <b>Відео готове! Відправляю у чат...</b>")

        out_info = get_media_info(output_path)

        await message.reply_video(
            video=output_path,
            duration=int(out_info["duration"]),
            width=out_info["width"],
            height=out_info["height"],
            caption=f"🎬 <b>Відео готове!</b>\n• Банер вставлено на <b>{insert_time:.1f} сек</b> (середина відео з ефектом паузи).",
            supports_streaming=True
        )
        await status_msg.delete()

    except Exception as e:
        logger.exception("Error processing video")
        await status_msg.edit_text(f"❌ <b>Помилка під час обробки:</b>\n<code>{e}</code>")
    finally:
        for p in (input_path, output_path):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

@app.on_message(filters.text)
async def handle_text(client: Client, message: Message):
    await message.reply_text(
        "🎬 <b>Надішліть мені відео будь-якого розміру</b>, і я вставлю банер рівно посередині з ефектом паузи!\n\n"
        "💡 <i>Для оновлення банера: надішліть відео з текстом /setbanner</i>"
    )

async def handle_ping(request):
    return web.Response(text="Bot is running OK (Pyrogram in_memory MTProto)")

async def start_web_server():
    port = int(os.getenv("PORT", 10000))
    web_app = web.Application()
    web_app.router.add_get("/", handle_ping)
    web_app.router.add_get("/health", handle_ping)
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Health-check веб-сервер запущено на порту {port}")

async def main():
    await start_web_server()
    logger.info("Підключення до Telegram MTProto...")
    async with app:
        logger.info("🚀 БОТ УСПІШНО ПІДКЛЮЧИВСЯ ТА СЛУХАЄ ПОВІДОМЛЕННЯ!")
        await idle()

if __name__ == "__main__":
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот зупинений.")
