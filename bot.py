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

@dp.message(CommandStart())
async def cmd_start(message: Message):
    text = (
        "👋 <b>Привіт! Я бот для вставки анімованого банера у відео.</b>\n\n"
        "🎬 <b>Як це працює:</b>\n"
        "1. Надішліть мені відео (як відео або файл).\n"
        "2. Я автоматично вирахую таймінг:\n"
        "   • Якщо відео &lt; 60 сек — банер буде рівно <b>посередині</b>.\n"
        "   • Якщо відео &ge; 60 сек — банер з'явиться на <b>20-й секунді</b>.\n"
        "3. На моменті банера головне відео <b>призупиняється</b> (кадр завмирає), а банер програється зі своїм звуком поверх нього.\n"
        "4. Після завершення банера головне відео продовжує грати далі!\n\n"
        "⚙️ <i>Адміністратор може надіслати відео з підписом /setbanner для оновлення банера</i>"
    )
    await message.answer(text)

@dp.message(Command("help"))
async def cmd_help(message: Message):
    text = (
        "📖 <b>Допомога:</b>\n"
        "• Надішліть будь-яке відео у чат.\n"
        "• Для зміни банера: надішліть MP4-відео з підписом <code>/setbanner</code>."
    )
    await message.answer(text)

@dp.message(Command("setbanner"))
async def cmd_setbanner_hint(message: Message):
    if ADMIN_IDS and message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас немає прав для зміни банера.")
        return
    await message.answer("📹 Надішліть MP4-відео банера з текстом підпису <code>/setbanner</code>.")

@dp.message(F.video | F.document)
async def handle_video(message: Message):
    is_set_banner = False
    caption = message.caption or ""
    if "/setbanner" in caption:
        if ADMIN_IDS and message.from_user.id not in ADMIN_IDS:
            await message.answer("⛔ У вас немає прав для оновлення банера.")
            return
        is_set_banner = True

    video_file = message.video or message.document
    if message.document and not (message.document.mime_type and "video" in message.document.mime_type):
        if not is_set_banner:
            await message.answer("⚠️ Будь ласка, надішліть саме відеофайл (MP4/MOV тощо).")
            return

    if is_set_banner:
        status_msg = await message.answer("⏳ Завантажую та оновлюю банер...")
        try:
            file_info = await bot.get_file(video_file.file_id)
            await bot.download_file(file_info.file_path, BANNER_PATH)
            banner_info = get_media_info(BANNER_PATH)
            await status_msg.edit_text(f"✅ <b>Банер успішно оновлено!</b>\nТривалість банера: {banner_info['duration']:.2f} сек.")
        except Exception as e:
            logger.exception("Error updating banner")
            await status_msg.edit_text(f"❌ Помилка оновлення банера: {e}")
        return

    if not os.path.exists(BANNER_PATH):
        await message.answer(
            "⚠️ Файл банера ще не встановлено на сервері!\n"
            "Надішліть відео банера з підписом <code>/setbanner</code> або додайте <code>banner.mp4</code> у корінь бота."
        )
        return

    req_id = str(uuid.uuid4())[:8]
    input_path = TEMP_DIR / f"input_{req_id}.mp4"
    output_path = TEMP_DIR / f"output_{req_id}.mp4"
    
    status_msg = await message.answer("📥 Завантажую ваше відео...")
    
    try:
        file_info = await bot.get_file(video_file.file_id)
        await bot.download_file(file_info.file_path, input_path)
        
        info = get_media_info(str(input_path))
        dur = info["duration"]
        insert_time = calculate_insertion_time(dur)
        
        await status_msg.edit_text(
            f"⚙️ <b>Обробка відео...</b>\n"
            f"• Тривалість відео: <b>{dur:.1f} сек</b>\n"
            f"• Пауза та банер на: <b>{insert_time:.1f} сек</b>\n\n"
            f"⏳ Зачекайте декілька секунд..."
        )
        
        # Обробка через FFmpeg
        await process_video_with_banner(str(input_path), BANNER_PATH, str(output_path))
        
        await status_msg.edit_text("📤 Відео оброблено! Відправляю у чат...")
        
        final_video = FSInputFile(str(output_path))
        await message.answer_video(
            video=final_video,
            caption="🎬 <b>Ваше відео готове!</b>\nБанер накладено з ефектом паузи головного відео."
        )
        await status_msg.delete()
        
    except Exception as e:
        logger.exception("Error processing video")
        await status_msg.edit_text(f"❌ <b>Помилка під час обробки:</b> {e}")
    finally:
        for p in (input_path, output_path):
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass

# Створюємо простий HTTP сервер для Render Web Service Health Check
async def handle_ping(request):
    return web.Response(text="Bot is running OK!")

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
    logger.info("Запуск бота та веб-сервера...")
    await start_web_server()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
