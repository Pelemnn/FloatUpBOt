import os
import sys
import uuid
import logging
import asyncio
from pathlib import Path
from dotenv import load_dotenv

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import FSInputFile, Message
from aiogram.client.default import DefaultBotProperties

from video_processor import process_video_with_banner, calculate_insertion_time, get_media_info

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("telegram_banner_bot")

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    print("ПОМИЛКА: BOT_TOKEN не знайдено в змінних середовища!")
    sys.exit(1)

BANNER_PATH = os.getenv("BANNER_PATH", "banner.mp4")
TEMP_DIR = Path(os.getenv("TEMP_DIR", "temp_processing"))
TEMP_DIR.mkdir(parents=True, exist_ok=True)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

ADMIN_IDS = [int(i.strip()) for i in os.getenv("ADMIN_IDS", "").split(",") if i.strip().isdigit()]

def is_admin(user_id: int) -> bool:
    if not ADMIN_IDS:
        return True
    return user_id in ADMIN_IDS

def is_banner_valid() -> bool:
    return os.path.exists(BANNER_PATH) and os.path.getsize(BANNER_PATH) > 500

@dp.message(CommandStart())
async def cmd_start(message: Message):
    text = (
        "👋 <b>Привіт! Я бот для швидкої вставки анімованого банера у відео.</b>\n\n"
        "⚡ <b>Як це працює:</b>\n"
        "1. Надішліть відео у чат.\n"
        "2. Бот автоматично вставить банер з паузою основного відео:\n"
        "   • Відео &lt; 60 сек $\\rightarrow$ банер <b>посередині</b>.\n"
        "   • Відео &ge; 60 сек $\\rightarrow$ банер на <b>20-й секунді</b>.\n\n"
        "📹 <b>Оновлення банера:</b>\n"
        "Надішліть відео банера з текстом <code>/setbanner</code> або зробіть Reply на відео з командою <code>/setbanner</code>."
    )
    await message.answer(text)

@dp.message(Command("setbanner"))
async def cmd_setbanner_command(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У вас немає прав для зміни банера.")
        return
        
    reply = message.reply_to_message
    if reply:
        video_file = reply.video or reply.animation or reply.document
        if video_file:
            await save_new_banner(message, video_file)
            return
            
    await message.answer("📹 Надішліть MP4-відео банера з текстом підпису <code>/setbanner</code> або зробіть <b>Reply (Відповісти)</b> на відео з текстом <code>/setbanner</code>.")

async def save_new_banner(message: Message, video_file):
    status_msg = await message.answer("⏳ Завантажую та оновлюю банер...")
    try:
        file_info = await bot.get_file(video_file.file_id)
        await bot.download_file(file_info.file_path, BANNER_PATH)
        banner_info = get_media_info(BANNER_PATH)
        await status_msg.edit_text(
            f"✅ <b>Банер успішно встановлено!</b>\n"
            f"• Тривалість: <b>{banner_info['duration']:.2f} сек</b>\n"
            f"• Роздільна здатність: <b>{banner_info['width']}x{banner_info['height']}</b>\n\n"
            f"Тепер можете надсилати відео для обробки!"
        )
    except Exception as e:
        logger.exception("Error updating banner")
        await status_msg.edit_text(f"❌ Помилка оновлення банера: {e}")

@dp.message(F.video | F.document | F.animation)
async def handle_media(message: Message):
    caption = (message.caption or "").strip().lower()
    is_set_banner = "/setbanner" in caption
    
    video_file = message.video or message.animation or message.document
    
    if message.document and not (message.document.mime_type and ("video" in message.document.mime_type or "octet-stream" in message.document.mime_type)):
        if not is_set_banner:
            await message.answer("⚠️ Будь ласка, надішліть саме відеофайл (MP4/MOV тощо).")
            return

    if is_set_banner:
        if not is_admin(message.from_user.id):
            await message.answer("⛔ У вас немає прав для оновлення банера.")
            return
        await save_new_banner(message, video_file)
        return

    # Перевірка наявності банера перед початком
    if not is_banner_valid():
        await message.answer(
            "⚠️ <b>Файл банера ще не встановлено або він порожній!</b>\n\n"
            "Щоб встановити банер:\n"
            "1. Надішліть сюди відео банера з підписом <code>/setbanner</code>\n"
            "   <i>або</i>\n"
            "2. Зробіть Reply (Відповісти) на вже надіслане відео банера з текстом <code>/setbanner</code>."
        )
        return

    req_id = str(uuid.uuid4())[:8]
    input_path = TEMP_DIR / f"input_{req_id}.mp4"
    output_path = TEMP_DIR / f"output_{req_id}.mp4"
    
    status_msg = await message.answer("📥 Завантажую відео...")
    
    try:
        file_info = await bot.get_file(video_file.file_id)
        await bot.download_file(file_info.file_path, input_path)
        
        info = get_media_info(str(input_path))
        dur = info["duration"]
        insert_time = calculate_insertion_time(dur)
        
        await status_msg.edit_text(
            f"⚡ <b>Ультрашвидка обробка...</b>\n"
            f"• Тривалість: <b>{dur:.1f} сек</b>\n"
            f"• Пауза та банер на: <b>{insert_time:.1f} сек</b>\n\n"
            f"⏳ Обробляємо..."
        )
        
        # Обробка відео через оптимізований FFmpeg
        await process_video_with_banner(str(input_path), BANNER_PATH, str(output_path))
        
        await status_msg.edit_text("📤 Готово! Відправляю відео...")
        
        final_video = FSInputFile(str(output_path))
        await message.answer_video(
            video=final_video,
            caption="🎬 <b>Ваше відео готове!</b>"
        )
        await status_msg.delete()
        
    except Exception as e:
        logger.exception("Error processing video")
        await status_msg.edit_text(f"❌ <b>Помилка під час обробки:</b>\n<code>{e}</code>")
    finally:
        for p in (input_path, output_path):
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass

async def handle_ping(request):
    return web.Response(text="OK")

async def start_web_server():
    port = int(os.getenv("PORT", 10000))
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/health", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Health-check server running on port {port}")

async def main():
    logger.info("Starting bot and healthcheck server...")
    await start_web_server()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
