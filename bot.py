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

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")

if not BOT_TOKEN:
    logger.error("BOT_TOKEN не знайдено!")
    sys.exit(1)

if not API_ID or not API_HASH:
    logger.error("API_ID або API_HASH не знайдено!")
    sys.exit(1)

API_ID = int(API_ID)
BANNER_PATH = os.getenv("BANNER_PATH", "banner.mp4")
TEMP_DIR = Path(os.getenv("TEMP_DIR", "temp_processing"))
TEMP_DIR.mkdir(parents=True, exist_ok=True)

ADMIN_IDS = [int(i.strip()) for i in os.getenv("ADMIN_IDS", "").split(",") if i.strip().isdigit()]

app = Client(
    "banner_bot_session",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    parse_mode=ParseMode.HTML,
    workdir=str(TEMP_DIR)
)

def is_admin(user_id: int) -> bool:
    if not ADMIN_IDS:
        return True
    return user_id in ADMIN_IDS

def is_banner_valid() -> bool:
    return os.path.exists(BANNER_PATH) and os.path.getsize(BANNER_PATH) > 500

@app.on_message(filters.command("start"))
async def cmd_start(client: Client, message: Message):
    text = (
        "👋 <b>Привіт! Я бот для вставки анімованого банера у відео (файли до 2 ГБ).</b>\n\n"
        "⚡ <b>Як це працює:</b>\n"
        "1. Надішліть будь-яке відео у чат.\n"
        "2. Бот автоматично вставить банер з паузою:\n"
        "   • Відео &lt; 60 сек ➔ банер <b>посередині</b>.\n"
        "   • Відео &ge; 60 сек ➔ банер на <b>20-й секунді</b>.\n\n"
        "📹 <b>Оновлення банера:</b>\n"
        "Надішліть відео банера з текстом <code>/setbanner</code> або зробіть Reply з текстом <code>/setbanner</code>."
    )
    await message.reply_text(text)

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

    await message.reply_text("📹 Надішліть відео банера з текстом <code>/setbanner</code> у підписі або зробіть <b>Reply (Відповісти)</b> на відео з текстом <code>/setbanner</code>.")

async def save_new_banner(message: Message, media_msg: Message):
    status_msg = await message.reply_text("⏳ Завантажую та оновлюю банер...")
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
            f"Тепер можете надсилати відео для обробки!"
        )
    except Exception as e:
        logger.exception("Error saving banner")
        await status_msg.edit_text(f"❌ Помилка оновлення банера: {e}")

@app.on_message(filters.video | filters.document | filters.animation)
async def handle_media(client: Client, message: Message):
    user_id = message.from_user.id if message.from_user else 0
    caption = (message.caption or "").strip().lower()
    is_set_banner = "/setbanner" in caption

    if message.document:
        mime = message.document.mime_type or ""
        fname = (message.document.file_name or "").lower()
        if not ("video" in mime or "octet-stream" in mime or fname.endswith((".mp4", ".mov", ".avi", ".mkv"))):
            if not is_set_banner:
                await message.reply_text("⚠️ Будь ласка, надішліть саме відеофайл.")
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
            "Надішліть відео банера з підписом <code>/setbanner</code> або зробіть Reply на відео з текстом <code>/setbanner</code>."
        )
        return

    req_id = str(uuid.uuid4())[:8]
    input_path = str(TEMP_DIR / f"input_{req_id}.mp4")
    output_path = str(TEMP_DIR / f"output_{req_id}.mp4")

    status_msg = await message.reply_text("📥 Завантажую відео (MTProto до 2 ГБ)...")

    try:
        await message.download(file_name=input_path)

        info = get_media_info(input_path)
        dur = info["duration"]
        insert_time = calculate_insertion_time(dur)

        await status_msg.edit_text(
            f"⚡ <b>Обробка відео...</b>\n"
            f"• Тривалість: <b>{dur:.1f} сек</b>\n"
            f"• Пауза та банер на: <b>{insert_time:.1f} сек</b>\n\n"
            f"⏳ FFmpeg обробка..."
        )

        await process_video_with_banner(input_path, BANNER_PATH, output_path)

        await status_msg.edit_text("📤 Готово! Завантажую результат у чат...")

        out_info = get_media_info(output_path)

        await message.reply_video(
            video=output_path,
            duration=int(out_info["duration"]),
            width=out_info["width"],
            height=out_info["height"],
            caption="🎬 <b>Ваше відео готове!</b>",
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

async def handle_ping(request):
    return web.Response(text="Bot is running OK (Pyrogram 2GB)")

async def start_web_server():
    port = int(os.getenv("PORT", 10000))
    web_app = web.Application()
    web_app.router.add_get("/", handle_ping)
    web_app.router.add_get("/health", handle_ping)
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Health-check server running on port {port}")

async def main():
    await start_web_server()
    await app.start()
    logger.info("Bot started and listening for messages...")
    await idle()
    await app.stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")
