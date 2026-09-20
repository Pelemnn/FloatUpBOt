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
from aiogram.exceptions import TelegramBadRequest
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
    logger.critical("ПОМИЛКА: BOT_TOKEN не знайдено в змінних середовища!")
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
        "👋 <b>Привіт! Я оновлений бот для вставки анімованого банера у відео.</b>\n\n"
        "⚡ <b>Як я працюю:</b>\n"
        "1. Просто надішліть мені будь-яке відео.\n"
        "2. Банер вставляється <b>рівно посередині відео</b> (час ділиться на 2).\n"
        "3. На моменті банера головне відео <b>призупиняється</b>, банер програється зі своїм звуком, і після цього відео продовжує грати далі!\n\n"
        "📹 <b>Оновлення банера:</b>\n"
        "Надішліть відео банера з підписом <code>/setbanner</code> або зробіть <b>Reply (Відповісти)</b> на відео з командою <code>/setbanner</code>."
    )
    await message.answer(text)

@dp.message(Command("help"))
async def cmd_help(message: Message):
    text = (
        "📖 <b>Допомога:</b>\n\n"
        "• Надішліть будь-яке відео у цей чат.\n"
        "• Бот сам вирахує тривалість і вставить банер <b>рівно посередині (час / 2)</b> з ефектом паузи.\n"
        "• Для зміни банера: надішліть відео з текстом <code>/setbanner</code>."
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
            
    await message.answer("📹 Надішліть MP4-відео банера з підписом <code>/setbanner</code> або зробіть <b>Reply (Відповісти)</b> на відео з командою <code>/setbanner</code>.")

async def save_new_banner(message: Message, video_file):
    status_msg = await message.answer("⏳ Завантажую та оновлюю банер...")
    try:
        file_info = await bot.get_file(video_file.file_id)
        await bot.download_file(file_info.file_path, BANNER_PATH)
        banner_info = get_media_info(BANNER_PATH)
        await status_msg.edit_text(
            f"✅ <b>Банер успішно збережено!</b>\n"
            f"• Тривалість: <b>{banner_info['duration']:.2f} сек</b>\n"
            f"• Роздільна здатність: <b>{banner_info['width']}x{banner_info['height']}</b>\n\n"
            f"🚀 Тепер надсилайте будь-які відео — вони будуть оброблятися з цим банером!"
        )
    except Exception as e:
        logger.exception("Error updating banner")
        await status_msg.edit_text(f"❌ Помилка оновлення банера: {e}")

@dp.message(F.video | F.document | F.animation)
async def handle_media(message: Message):
    caption = (message.caption or "").strip().lower()
    is_set_banner = "/setbanner" in caption
    
    video_file = message.video or message.animation or message.document
    
    if message.document:
        mime = message.document.mime_type or ""
        fname = (message.document.file_name or "").lower()
        if not ("video" in mime or "octet-stream" in mime or fname.endswith((".mp4", ".mov", ".avi", ".mkv", ".webm"))):
            if not is_set_banner:
                await message.answer("⚠️ Будь ласка, надішліть саме відеофайл (MP4/MOV тощо).")
                return

    if is_set_banner:
        if not is_admin(message.from_user.id):
            await message.answer("⛔ У вас немає прав для оновлення банера.")
            return
        await save_new_banner(message, video_file)
        return

    # Перевірка наявності банера
    if not is_banner_valid():
        await message.answer(
            "⚠️ <b>Файл банера ще не встановлено!</b>\n\n"
            "Будь ласка, надішліть відео банера з підписом <code>/setbanner</code> або зробіть Reply на відео з текстом <code>/setbanner</code>."
        )
        return

    req_id = str(uuid.uuid4())[:8]
    input_path = TEMP_DIR / f"input_{req_id}.mp4"
    output_path = TEMP_DIR / f"output_{req_id}.mp4"
    
    status_msg = await message.answer("📥 <b>Завантажую відео...</b>")
    
    try:
        file_info = await bot.get_file(video_file.file_id)
        await bot.download_file(file_info.file_path, input_path)
        
        info = get_media_info(str(input_path))
        dur = info["duration"]
        insert_time = calculate_insertion_time(dur)
        
        await status_msg.edit_text(
            f"⚡ <b>Обробка відео через FFmpeg...</b>\n"
            f"• Повна тривалість: <b>{dur:.1f} сек</b>\n"
            f"• Вставка банера на паузу: <b>{insert_time:.1f} сек (середина)</b>\n\n"
            f"⏳ Зачекайте декілька секунд..."
        )
        
        # Обробка відео через ультрашвидкий FFmpeg
        await process_video_with_banner(str(input_path), BANNER_PATH, str(output_path))
        
        await status_msg.edit_text("📤 <b>Відео готове! Завантажую в Telegram...</b>")
        
        final_video = FSInputFile(str(output_path))
        await message.answer_video(
            video=final_video,
            caption=f"🎬 <b>Відео оброблено!</b>\n• Банер вставлено на <b>{insert_time:.1f} сек</b> (середина відео з ефектом паузи).",
            supports_streaming=True
        )
        await status_msg.delete()
        
    except TelegramBadRequest as e:
        if "file is too big" in str(e).lower():
            await status_msg.edit_text("❌ Відео завелике (понад 20 МБ). Надішліть його як звичайне стиснене відео через Telegram.")
        else:
            await status_msg.edit_text(f"❌ Помилка Telegram API: {e}")
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

# Відповідь на будь-який інший текст/стікер, щоб бот ніколи не мовчав!
@dp.message()
async def handle_any_other_message(message: Message):
    await message.answer(
        "🎬 <b>Надішліть мені відео</b>, і я автоматично вставлю банер рівно посередині з ефектом паузи!\n\n"
        "💡 <i>Якщо ви хочете оновити сам банер — надішліть відео банера з підписом /setbanner</i>"
    )

async def handle_ping(request):
    return web.Response(text="Bot is running OK (aiogram 3.x)")

async def start_web_server():
    port = int(os.getenv("PORT", 10000))
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/health", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Health-check веб-сервер запущено на порту {port}")

async def main():
    logger.info("Запуск бота...")
    await start_web_server()
    # Скидаємо старі підвислі оновлення Telegram, щоб бот стартував чисто
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Бот готовий до роботи і слухає повідомлення!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
