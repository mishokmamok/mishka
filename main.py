import os
import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN, BOT_WORK_TIMEOUT_HOURS
from handlers import router

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    """Главная функция бота"""
    bot = Bot(token=BOT_TOKEN)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    
    dp.include_router(router)
    
    try:
        logger.info("🤖 Бот запускается...")
        if BOT_WORK_TIMEOUT_HOURS and BOT_WORK_TIMEOUT_HOURS > 0:
            logger.info(f"⏰ Бот будет работать {BOT_WORK_TIMEOUT_HOURS} часов (таймаут включен)")
            # Запускаем бота с таймером
            bot_task = asyncio.create_task(dp.start_polling(bot))
            await asyncio.wait_for(bot_task, timeout=BOT_WORK_TIMEOUT_HOURS*60*60)
        else:
            logger.info("♾️ Таймаут отключен (Render/прод). Бот будет работать без ограничения времени.")
            await dp.start_polling(bot)
        
    except asyncio.TimeoutError:
        logger.info(f"⏰ Время работы истекло ({BOT_WORK_TIMEOUT_HOURS} часов), завершаем...")
        # Останавливаем бота
        await dp.stop_polling()
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
    finally:
        await bot.session.close()
        logger.info("🔒 Сессия бота закрыта")

if __name__ == "__main__":
    asyncio.run(main())