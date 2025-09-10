from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
import asyncio
import logging
import random

from keyboards import get_main_menu_keyboard, get_back_keyboard, get_new_game_keyboard, get_test_game_control_keyboard, get_lobby_keyboard, get_player_selection_keyboard, get_voting_keyboard, get_game_control_keyboard
from game_logic import game_manager
from models import GamePhase, PlayerRole
from config import MAX_PLAYERS, NIGHT_TIMEOUT_SECS, DAY_DISCUSS_TIMEOUT_SECS, VOTING_TIMEOUT_SECS
from config import BROADCAST_CHAT_ID, BROADCAST_THREAD_ID

router = Router()

# Фоновые задачи автопилота по chat_key
_autopilot_tasks: dict[str, asyncio.Task] = {}

logger = logging.getLogger(__name__)

# ID администратора/разработчика для специальных команд
ADMIN_USER_ID = 833357704

# Ожидание текста рассылки в ЛС: user_id -> True
_broadcast_waiting: dict[int, bool] = {}

# Команда /broadcast: инициирует запрос текста рассылки (только ЛС и только ADMIN_USER_ID)
@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message):
    # Только в ЛС
    if message.chat.type != "private":
        return
    # Только для конкретного администратора
    if message.from_user.id != ADMIN_USER_ID:
        return

    _broadcast_waiting[message.from_user.id] = True
    await message.answer(
        "✍️ Отправьте текст рассылки одним сообщением. Для отмены — /cancel"
    )

# Отмена режима рассылки
@router.message(Command("cancel"))
async def cmd_cancel_broadcast(message: Message):
    if message.chat.type != "private":
        return
    if message.from_user.id != ADMIN_USER_ID:
        return
    if _broadcast_waiting.pop(message.from_user.id, None):
        await message.answer("❌ Режим рассылки отменён")
    else:
        await message.answer("ℹ️ Нечего отменять: режим рассылки не активен")

# Обработка ЛС в режиме ожидания текста рассылки (ставим раньше общего приватного перехватчика)
@router.message(F.chat.type == "private")
async def handle_broadcast_input(message: Message):
    # Игнорируем команды здесь, ими занимаются соответствующие хендлеры
    if not message.text or message.text.startswith("/"):
        return
    user_id = message.from_user.id
    if user_id != ADMIN_USER_ID:
        return
    if not _broadcast_waiting.get(user_id):
        return

    # Получили текст рассылки
    content = message.text
    # Выходим из режима ожидания до начала отправки
    _broadcast_waiting.pop(user_id, None)

    # Сбор целей: все активные игры (по chat_key вида chatId_threadId)
    try:
        active_keys = list(game_manager.active_games.keys())
    except Exception:
        active_keys = []

    if not active_keys:
        # Пытаемся отправить в резервную тему, если настроена
        if BROADCAST_CHAT_ID != 0:
            try:
                message_thread_id = None if BROADCAST_THREAD_ID == 0 else BROADCAST_THREAD_ID
                await message.bot.send_message(
                    BROADCAST_CHAT_ID,
                    content,
                    message_thread_id=message_thread_id
                )
                await message.answer("✅ Разослано в резервную тему")
                return
            except Exception as e:
                logger.warning(f"broadcast: ошибка отправки в резервную тему: {e}")
        await message.answer("⚠️ Нет активных тем для рассылки")
        return

    sent = 0
    failed = 0
    for chat_key in active_keys:
        try:
            chat_id = get_chat_id_from_key(chat_key)
            thread_id = get_thread_id_from_key(chat_key)
            message_thread_id = None if thread_id == 0 else thread_id
            await message.bot.send_message(
                chat_id,
                content,
                message_thread_id=message_thread_id
            )
            sent += 1
            await asyncio.sleep(0.05)  # легкое размежевание, чтобы не словить лимиты
        except TelegramForbiddenError:
            failed += 1
        except TelegramBadRequest:
            failed += 1
        except Exception as e:
            failed += 1
            logger.warning(f"broadcast: ошибка отправки в {chat_key}: {e}")

    await message.answer(f"✅ Разослано: {sent}. Ошибок: {failed}.")

# Обработчик команды /start
@router.message(Command("start"))
async def start_command(message: Message):
    """Обработчик команды /start - только для ЛС, получение роли"""
    user_id = message.from_user.id
    first_name = message.from_user.first_name or "Игрок"
    
    logger.info(f"start_command: пользователь {first_name} (ID: {user_id}) написал /start")
    
    # Команда /start работает ТОЛЬКО в ЛС
    if message.chat.type != "private":
        # В групповых чатах игнорируем команду /start
        return
    
    # В ЛС отправляем сообщение о получении роли
    role_message = (
        "🎭 Buonasera, синьор! 🎭\n\n"
        "Теперь вы можете получить свою роль в игре!\n\n"
        "📋 Что делать дальше:\n"
        "1. Перейдите в групповой чат\n"
        "2. Найдите тему «Игра в «Мафию»»\n"
        "3. Напишите команду /mafia\n"
        "4. Присоединитесь к игре\n\n"
        "⚠️ Важно: роль вы получите только после присоединения к игре в групповом чате!"
    )
    
    await message.answer(role_message)

# Пересылка ЛС мафии их сообщникам во время ночи
@router.message(F.chat.type == "private")
async def relay_mafia_private_chat(message: Message):
    user_id = message.from_user.id
    text = message.text or ""
    # Игнорируем команды
    if not text or text.startswith("/"):
        return
    chat_key = game_manager.get_chat_key_for_mafia_user(user_id)
    if not chat_key:
        return
    game = game_manager.get_game(chat_key)
    if not game or game.phase != GamePhase.NIGHT:
        return
    player = game.players.get(user_id)
    if not player or not player.is_alive or player.role != PlayerRole.MAFIA:
        return
    peers = game_manager.get_mafia_peers(chat_key, exclude_user_id=user_id)
    if not peers:
        return
    try:
        sender_name = message.from_user.first_name or "Мафия"
        forwarded = 0
        for peer in peers:
            try:
                await message.bot.send_message(peer.user_id, f"😈 {sender_name}: {text}")
                forwarded += 1
            except Exception as e:
                logger.debug(f"relay_mafia_private_chat: не удалось переслать мафии {peer.user_id}: {e}")
        if forwarded:
            try:
                await message.reply("📨 Сообщение отправлено сообщникам")
            except Exception:
                pass
    except Exception as e:
        logger.exception(f"relay_mafia_private_chat: ошибка пересылки: {e}")

# Рандомные фразы в духе мафиози
DON_VITTE_GREETINGS = [
    "🎭 Добро пожаловать, синьоры и синьориты. 🎭\n\n"
    "       Я — Дон Витте, хозяин этого стола и хранитель тайных правил города.\n"
    "В этот вечер каждый из вас наденет маску. Но будьте осторожны: за улыбкой может прятаться нож, "
    "а за дружеским словом — смертный приговор.\n\n"
    "🌙 Ночью улицы принадлежат мафии. Они решают, чью жизнь оборвётся.\n"
    "🩺 Доктор бродит по переулкам, надеясь спасти того, кто ещё может дышать.\n"
    "👮 Комиссар ищет правду, но правда редко живёт дольше рассвета.\n"
    "💃 А ночная бабочка… она может спутать карты даже самым сильным игрокам.\n"
    "👔 Мирные жители спят, веря, что их дом — крепость. Но в этом городе крепостей нет.\n\n"
    "☀️ Днём же вас ждут громкие речи, обвинения и тяжёлый выбор. "
    "Каждое слово может стать последним гвоздём в чужой гроб, "
    "или — в ваш собственный.\n\n"
    "💼 Здесь выживет не тот, кто честен, а тот, кто хитёр. "
    "Тот, кто сумеет убедить других в своей правоте… даже если его руки в крови.\n\n"
    "       Ну что, готовы сыграть в эту маленькую игру судьбы? Для начала убедитесь, что все написали мне /start, иначе не получите роль, а без роли вы — никто.\n"
    "       ⚠️ И помните, если что-то пойдёт не так или возникнут вопросы по игре — моё доверенное ухо готово помочь: @gazonokosilkins.\n"
    "       Он следит, чтобы всё шло по правилам, но при этом остаётся в тени, как и подобает настоящему синьору.\n\n"
    "Теперь выбирайте своё действие:",

    "🎭 Приветствую вас, уважаемые синьоры! 🎭\n\n"
    "       Я — Дон Витте, и сегодня вечером мы сыграем в игру, где ставки — человеческие жизни.\n"
    "В этом городе каждый носит маску, но не все маски одинаково опасны.\n\n"
    "🌙 Когда солнце садится, просыпается настоящая власть. Мафия выходит на охоту, "
    "доктор пытается спасти обречённых, комиссар ищет правду в тени, "
    "а ночная бабочка плетёт свои коварные сети.\n\n"
    "☀️ Днём город превращается в арену для подозрений и обвинений. "
    "Каждое слово может стать приговором, каждое молчание — признанием вины.\n\n"
    "💼 Помните, синьоры: в этой игре выживает не самый честный, а самый хитрый. "
    "Тот, кто умеет читать между строк и видеть то, что скрыто от глаз простых смертных.\n\n"
    "       Прежде чем начать, убедитесь, что все написали мне /start. Без роли вы — просто пешки на доске.\n"
    "       ⚠️ Если возникнут вопросы — мой доверенный помощник @gazonokosilkins всегда готов помочь.\n\n"
    "Теперь выбирайте своё действие:",

    "🎭 Salve, синьоры! 🎭\n\n"
    "       Я — Дон Витте, и сегодня мы погрузимся в мир, где правда — роскошь, а ложь — искусство.\n"
    "В этом городе каждый игрок — актёр в театре жизни и смерти.\n\n"
    "🌙 Ночью город принадлежит тем, кто не боится крови на руках. "
    "Мафия выбирает жертв, доктор пытается исправить их ошибки, "
    "комиссар ищет предателей, а бабочка создаёт хаос.\n\n"
    "☀️ Днём все становятся детективами и прокуроры одновременно. "
    "Обвинения летят, как пули, а защита строится на хитрости и красноречии.\n\n"
    "💼 В этой игре нет места сантиментам. Выживает тот, кто умеет думать как преступник, "
    "но действовать как праведник.\n\n"
    "       Не забудьте написать /start — без роли вы беспомощны.\n"
    "       ⚠️ Помощь всегда рядом: @gazonokosilkins.\n\n"
    "Теперь выбирайте своё действие:",

    "🎭 Buonasera, дорогие друзья! 🎭\n\n"
    "       Я — Дон Витте, и сегодня мы сыграем в игру, которая проверит вашу способность к выживанию.\n"
    "В этом городе каждый — потенциальная жертва и потенциальный убийца одновременно.\n\n"
    "🌙 Ночью просыпаются истинные хозяева города. "
    "Мафия решает судьбы, доктор пытается их изменить, "
    "комиссар ищет улики, а бабочка путает следы.\n\n"
    "☀️ Днём город превращается в суд, где каждый — и судья, и подсудимый. "
    "Ваша задача — найти виновных, не став одним из них.\n\n"
    "💼 Помните: в этой игре честность — слабость, а хитрость — сила. "
    "Выживает тот, кто умеет играть по правилам, не соблюдая их.\n\n"
    "       Напишите /start для получения роли — без неё вы обречены.\n"
    "       ⚠️ Вопросы? Обращайтесь к @gazonokosilkins.\n\n"
    "Теперь выбирайте своё действие:",

    "🎭 Добро пожаловать в мир теней, синьоры! 🎭\n\n"
    "       Я — Дон Витте, и сегодня мы сыграем в игру, где ставки выше, чем в любом казино.\n"
    "В этом городе каждый игрок — либо охотник, либо добыча.\n\n"
    "🌙 Когда наступает ночь, просыпаются те, кто не боится тёмных дел. "
    "Мафия выбирает цели, доктор пытается их спасти, "
    "комиссар ищет правду, а бабочка создаёт неразбериху.\n\n"
    "☀️ Днём город превращается в поле битвы умов. "
    "Каждое слово может стать оружием, каждое молчание — признанием.\n\n"
    "💼 В этой игре нет места для слабости. Выживает тот, кто умеет читать людей "
    "и манипулировать их страхами.\n\n"
    "       Не забудьте /start — роль даёт силу.\n"
    "       ⚠️ Помощь: @gazonokosilkins.\n\n"
    "Теперь выбирайте своё действие:",

    "🎭 Приветствую вас, уважаемые синьоры! 🎭\n\n"
    "       Я — Дон Витте, и сегодня мы погрузимся в мир, где мораль — понятие относительное.\n"
    "В этом городе каждый — потенциальный герой и потенциальный злодей.\n\n"
    "🌙 Ночью город принадлежит тем, кто не боится принимать сложные решения. "
    "Мафия убивает, доктор спасает, комиссар расследует, бабочка запутывает.\n\n"
    "☀️ Днём все становятся участниками детективного романа. "
    "Ваша задача — разгадать загадку, не став её жертвой.\n\n"
    "💼 Помните: в этой игре выживает не самый умный, а самый проницательный. "
    "Тот, кто умеет видеть то, что скрыто от других.\n\n"
    "       Напишите /start для получения роли.\n"
    "       ⚠️ Вопросы к @gazonokosilkins.\n\n"
    "Теперь выбирайте своё действие:",

    "🎭 Salve, дорогие синьоры! 🎭\n\n"
    "       Я — Дон Витте, и сегодня мы сыграем в игру, которая проверит вашу способность к анализу.\n"
    "В этом городе каждый игрок — загадка, которую нужно разгадать.\n\n"
    "🌙 Ночью просыпаются истинные мастера игры. "
    "Мафия планирует, доктор защищает, комиссар ищет, бабочка отвлекает.\n\n"
    "☀️ Днём город превращается в лабораторию по изучению человеческой природы. "
    "Каждый жест, каждое слово — подсказка к разгадке.\n\n"
    "💼 В этой игре выживает тот, кто умеет соединять разрозненные факты "
    "в единую картину преступления.\n\n"
    "       Не забудьте /start для получения роли.\n"
    "       ⚠️ Помощь: @gazonokosilkins.\n\n"
    "Теперь выбирайте своё действие:",

    "🎭 Buonasera, синьоры! 🎭\n\n"
    "       Я — Дон Витте, и сегодня мы сыграем в игру, где каждый — и судья, и палач.\n"
    "В этом городе нет места для простых решений.\n\n"
    "🌙 Когда наступает ночь, просыпаются те, кто не боится принимать на себя ответственность. "
    "Мафия решает судьбы, доктор пытается их изменить, "
    "комиссар ищет истину, а бабочка создаёт хаос.\n\n"
    "☀️ Днём город превращается в театр, где каждый — и актёр, и зритель. "
    "Ваша задача — разгадать сценарий, не став его жертвой.\n\n"
    "💼 Помните: в этой игре выживает тот, кто умеет играть по правилам, "
    "не подчиняясь им полностью.\n\n"
    "       Напишите /start для получения роли.\n"
    "       ⚠️ Вопросы к @gazonokosilkins.\n\n"
    "Теперь выбирайте своё действие:"
]

NIGHT_PHASE_MESSAGES = [
    "🌙 Город засыпает... лишь мафиози и прочая братва выходят на охоту.",
    "🌙 Тени сгущаются над городом... время для тёмных дел.",
    "🌙 Ночь опустилась на город... мафия выходит на улицы.",
    "🌙 Город погружается во тьму... братва начинает свою работу.",
    "🌙 Звёзды скрылись за тучами... время для ночных операций.",
    "🌙 Луна освещает пустые улицы... мафия выбирает цели.",
    "🌙 Город затих... лишь преступники не спят.",
    "🌙 Тьма окутала город... братва выходит на охоту."
]

DAY_PHASE_MESSAGES = [
    "☀️ День наступил! 🗣️ Обсуждение началось. У вас немного времени, синьоры.",
    "☀️ Солнце встало над городом! Время для обсуждений и подозрений.",
    "☀️ Новый день пришёл! Город просыпается для жарких дебатов.",
    "☀️ Рассвет наступил! Время разоблачать предателей.",
    "☀️ День начался! Город готов к обсуждению ночных событий.",
    "☀️ Солнце осветило город! Время для детективной работы.",
    "☀️ Новый день! Город просыпается для поиска виновных.",
    "☀️ Рассвет! Время анализировать ночные происшествия."
]

VOTING_START_MESSAGES = [
    "🗳️ Голосование! Кого отправим на дно реки?",
    "🗳️ Время голосовать! Кто сегодня поплатится жизнью?",
    "🗳️ Голосование началось! Выбираем жертву.",
    "🗳️ Время решать судьбу! Кого казним сегодня?",
    "🗳️ Голосование! Кто сегодня покинет игру?",
    "🗳️ Время выносить приговор! Выбираем кандидата.",
    "🗳️ Голосование! Кого отправим в мир иной?",
    "🗳️ Время решать! Кто сегодня умрёт?"
]

REVOTE_MESSAGES = [
    "🗳️ Переголосование! Выбираем из кандидатов ничьей.",
    "🗳️ Ничья! Голосуем снова среди равных.",
    "🗳️ Переголосование! Выбираем из подозреваемых.",
    "🗳️ Ничья! Время для второго тура.",
    "🗳️ Переголосование! Выбираем среди равных.",
    "🗳️ Ничья! Голосуем ещё раз.",
    "🗳️ Переголосование! Выбираем из кандидатов.",
    "🗳️ Ничья! Время для финального решения."
]

# Рандомные фразы для ночных действий ролей
NIGHT_ACTION_MESSAGES = {
    PlayerRole.MAFIA: [
        "🔪 Тени сгущаются... мафия выбирает свою жертву.",
        "🔪 Ночь принадлежит братве... время принимать решения.",
        "🔪 Мафия выходит на охоту... кто станет добычей?",
        "🔪 Тёмные силы активизируются... мафия планирует убийство.",
        "🔪 Время для кровавых дел... мафия выбирает цель.",
        "🔪 Ночь мафии... кто не доживёт до рассвета?",
        "🔪 Братва собирается... время для тёмных дел.",
        "🔪 Мафия просыпается... выбираем жертву."
    ],
    PlayerRole.DOCTOR: [
        "🩺 Доктор выходит на дежурство... кого спасти сегодня?",
        "🩺 Медицинская помощь нужна... доктор выбирает пациента.",
        "🩺 Время для исцеления... доктор ищет того, кто нуждается в помощи.",
        "🩺 Доктор готов к работе... кто получит лечение?",
        "🩺 Медицинский осмотр... доктор выбирает больного.",
        "🩺 Время для спасения... доктор ищет нуждающихся.",
        "🩺 Доктор на дежурстве... кого лечить сегодня?",
        "🩺 Медицинская помощь... доктор выбирает пациента."
    ],
    PlayerRole.COMMISSIONER: [
        "👮 Комиссар выходит на след... кого проверить сегодня?",
        "👮 Полицейское расследование... комиссар ищет улики.",
        "👮 Время для проверки... комиссар выбирает подозреваемого.",
        "👮 Следствие продолжается... комиссар ищет правду.",
        "👮 Полицейская работа... комиссар проверяет подозреваемых.",
        "👮 Время для расследования... комиссар выбирает цель.",
        "👮 Комиссар на задании... кого проверить сегодня?",
        "👮 Полицейское дело... комиссар ищет виновных."
    ],
    PlayerRole.BUTTERFLY: [
        "💃 Ночная бабочка выходит на охоту... кого отвлечь сегодня?",
        "💃 Время для соблазнения... бабочка выбирает цель.",
        "💃 Ночная бабочка активизируется... кто станет её жертвой?",
        "💃 Время для отвлечения... бабочка ищет добычу.",
        "💃 Ночная бабочка на задании... кого запутать сегодня?",
        "💃 Время для коварства... бабочка выбирает цель.",
        "💃 Ночная бабочка просыпается... кто попадёт в её сети?",
        "💃 Время для соблазна... бабочка ищет жертву."
    ]
}

# Рандомные фразы для напоминаний о времени
TIME_REMINDER_MESSAGES = {
    "night": [
        "⏳ До рассвета осталось: {time} сек.",
        "🌙 Время истекает... осталось: {time} сек.",
        "⏰ Ночь подходит к концу... {time} сек.",
        "🕐 До утра осталось: {time} сек.",
        "⏳ Тени рассеиваются через: {time} сек.",
        "🌙 Ночь заканчивается... {time} сек.",
        "⏰ Время ночи истекает: {time} сек.",
        "🕐 До рассвета: {time} сек."
    ],
    "day": [
        "⏳ До конца дня осталось: {time} сек.",
        "☀️ Солнце садится через: {time} сек.",
        "⏰ День подходит к концу... {time} сек.",
        "🕐 До вечера осталось: {time} сек.",
        "⏳ Время обсуждения: {time} сек.",
        "☀️ День заканчивается... {time} сек.",
        "⏰ Время дня истекает: {time} сек.",
        "🕐 До ночи: {time} сек."
    ],
    "voting": [
        "⏳ До конца голосования осталось: {time} сек.",
        "🗳️ Время голосования: {time} сек.",
        "⏰ Голосование заканчивается... {time} сек.",
        "🕐 До конца голосования: {time} сек.",
        "⏳ Время принимать решение: {time} сек.",
        "🗳️ Голосование истекает... {time} сек.",
        "⏰ Время голосования: {time} сек.",
        "🕐 До финала: {time} сек."
    ]
}

# Рандомные фразы для сообщения об отсутствии голосования в первый день
NO_VOTING_FIRST_DAY_MESSAGES = [
    "🔔 Сегодня голосования не будет (первый день после первой ночи).\nПожалуйста, обсудите итоги ночи и подготовьтесь к следующему голосованию.",
    "📢 Внимание! Первый день — голосования нет.\nОбсудите ночные события и готовьтесь к завтрашнему голосованию.",
    "🚫 Сегодня голосования не проводится (первый день).\nОбсудите ночь и планируйте завтрашнее голосование.",
    "⚠️ Первый день — без голосования.\nОбсудите ночь и планируйте завтрашнее голосование.",
    "🔕 Голосования сегодня не будет (первый день).\nВремя для дискуссий и подготовки к завтра.",
    "📋 Первый день — голосование отменено.\nОбсудите ночные события и готовьтесь к следующему раунду.",
    "🚷 Сегодня голосования нет (первый день).\nВремя для обсуждения и планирования.",
    "⏸️ Голосование приостановлено (первый день).\nОбсудите ночь и готовьтесь к завтрашнему решению."
]

# Рандомные фразы для сообщений о неактивированных ЛС
NO_DM_MESSAGES = [
    "⚠️ Внимание! Следующие игроки не активировали личные сообщения с ботом:\n{players}\nОни не получат ролей и не смогут участвовать в игре!",
    "🚨 Проблема! Эти игроки не написали /start боту:\n{players}\nБез активации ЛС они останутся без ролей!",
    "❌ Ошибка! Данные игроки не активировали бота:\n{players}\nОни не смогут играть без ролей!",
    "💥 Внимание! Следующие участники не активировали ЛС:\n{players}\nОни останутся без ролей в игре!",
    "🔴 Проблема с активацией! Эти игроки не написали /start:\n{players}\nБез ролей они не смогут участвовать!",
    "⚡ Тревога! Данные участники не активировали бота:\n{players}\nОни останутся без ролей!",
    "🚫 Внимание! Следующие игроки не активировали ЛС:\n{players}\nБез ролей они не смогут играть!",
    "💢 Проблема! Эти участники не написали /start:\n{players}\nОни останутся без ролей в игре!"
]

# Рандомные фразы для сообщения о начале игры
GAME_START_MESSAGES = [
    "🎭 ИГРА НАЧАЛАСЬ! 🎭\n\n💼 Внимание, синьоры и синьориты… Сегодня ночью решится судьба этого города.\nОдни из вас будут охотиться, другие — спасать, кто-то проверять подозрительных, а кто-то путать всех своими чарами.\nКаждый шаг, каждое слово, каждый взгляд может стать роковым.",
    "🎭 ИГРА НАЧАЛАСЬ! 🎭\n\n💼 Синьоры и синьориты, сегодня ночью начнется охота.\nОдни будут убивать, другие — спасать, кто-то искать правду, а кто-то создавать хаос.\nКаждое решение может стоить жизни.",
    "🎭 ИГРА НАЧАЛАСЬ! 🎭\n\n💼 Внимание, игроки! Сегодня ночью город погрузится в тьму.\nМафия выйдет на охоту, доктор попытается спасти, комиссар будет искать предателей, а бабочка запутает всех.",
    "🎭 ИГРА НАЧАЛАСЬ! 🎭\n\n💼 Синьоры, игра началась! Сегодня ночью решатся судьбы.\nОдни будут охотиться, другие защищаться, кто-то расследовать, а кто-то запутывать следы.",
    "🎭 ИГРА НАЧАЛАСЬ! 🎭\n\n💼 Внимание! Сегодня ночью город станет ареной для тайных операций.\nМафия выбирает цели, доктор спасает, комиссар ищет, бабочка отвлекает.",
    "🎭 ИГРА НАЧАЛАСЬ! 🎭\n\n💼 Синьоры и синьориты, игра началась! Сегодня ночью начнется охота.\nОдни будут убивать, другие — спасать, кто-то проверять, а кто-то запутывать.",
    "🎭 ИГРА НАЧАЛАСЬ! 🎭\n\n💼 Внимание, игроки! Сегодня ночью город погрузится в тени.\nМафия выйдет на улицы, доктор попытается спасти, комиссар будет искать правду.",
    "🎭 ИГРА НАЧАЛАСЬ! 🎭\n\n💼 Синьоры, игра началась! Сегодня ночью решатся судьбы города.\nОдни будут охотиться, другие защищаться, кто-то расследовать, а кто-то создавать хаос."
]

def get_chat_key(message_or_callback) -> str:
    """Получает уникальный ключ чата с учётом темы"""
    chat_id = message_or_callback.chat.id
    thread_id = getattr(message_or_callback, 'message_thread_id', None) or 0
    return f"{chat_id}_{thread_id}"

def get_chat_id_from_key(chat_key: str) -> int:
    """Получает chat_id из chat_key"""
    return int(chat_key.split('_')[0])

def get_thread_id_from_key(chat_key: str) -> int:
    """Получает thread_id из chat_key"""
    return int(chat_key.split('_')[1])

def check_topic_permission(message_or_callback) -> bool:
    """Проверяет, что действие разрешено в данной теме"""
    # Для callback query используем message.message_thread_id
    if hasattr(message_or_callback, 'message'):
        # Это callback query
        thread_id = message_or_callback.message.message_thread_id
        chat = message_or_callback.message.chat
    else:
        # Это обычное сообщение
        thread_id = message_or_callback.message_thread_id
        chat = message_or_callback.chat
    
    logger.debug(f"check_topic_permission: chat.is_forum={chat.is_forum}, thread_id={thread_id}")
    
    if chat.is_forum:
        # В форумах разрешаем только в теме "Игра в «Мафию»" (ID: 39431)
        result = thread_id == 39431
        logger.debug(f"check_topic_permission: thread_id={thread_id}, разрешенный=39431, результат={result}")
        return result
    else:
        # В обычных группах запрещаем
        logger.debug("check_topic_permission: не форум, результат=False")
        return False

# Отображение ролей: эмодзи, имена и инструкции
ROLE_EMOJI = {
    PlayerRole.MAFIA: "😈",
    PlayerRole.CIVILIAN: "🕊️",
    PlayerRole.DOCTOR: "💉",
    PlayerRole.COMMISSIONER: "👮",
    PlayerRole.BUTTERFLY: "💃",
}

ROLE_NAMES = {
    PlayerRole.MAFIA: "Мафия",
    PlayerRole.CIVILIAN: "Мирный житель",
    PlayerRole.DOCTOR: "Доктор",
    PlayerRole.COMMISSIONER: "Комиссар",
    PlayerRole.BUTTERFLY: "Ночная бабочка",
}

INSTRUCTIONS_BY_ROLE = {
    PlayerRole.MAFIA: (
        "Ты мафия. Каждую ночь выбирай жертву по кнопкам. "
        "Переписывайся с соратниками в этом чате, цель выбирается по большинству. Себя выбрать нельзя."
    ),
    PlayerRole.DOCTOR: (
        "Ты доктор. Каждую ночь выбери одного игрока, чтобы попытаться спасти его от смерти. "
        "Себя можно лечить только один раз за игру."
    ),
    PlayerRole.COMMISSIONER: (
        "Ты комиссар. Каждую ночь проверяй одного игрока — я скажу, мафия он или нет. "
        "Себя проверять нельзя."
    ),
    PlayerRole.BUTTERFLY: (
        "Ты ночная бабочка. Каждую ночь отвлекай кого-то, чтобы спутать планы. Себя выбрать нельзя."
    ),
    PlayerRole.CIVILIAN: (
        "Ты мирный житель. Днём обсуждай в общем чате и голосуй, чтобы утопить подозрительного синьора."
    ),
}

def build_role_prompt(role: PlayerRole, action_line: str) -> str:
    emoji = ROLE_EMOJI.get(role, "❓")
    role_name = ROLE_NAMES.get(role, "Неизвестная роль")
    description = INSTRUCTIONS_BY_ROLE.get(role, "Неизвестная роль")
    return (
        f"🎭 Твоя роль — {emoji} {role_name}\n\n"
        f"{description}\n\n"
        f"{action_line}"
    )

async def _send_night_action_keyboards(chat_key: str, bot):
    game = game_manager.get_game(chat_key)
    if not game:
        return
    # Не дублируем, если уже отправляли в этой ночи
    if getattr(game, "night_prompts_sent", False):
        return
    alive_players = game.get_alive_players()
    # Если бабочка отвлекла цель прошлой ночью — запретим её выбирать в текущей
    # Исключения для разных ролей
    mafia_excluded_targets = set()
    doctor_excluded_targets = set()
    commissioner_excluded_targets = set()
    
    # Если бабочка отвлекла цель прошлой ночи — запретим её выбирать в текущей для мафии и доктора
    if game.last_butterfly_distract_target is not None:
        mafia_excluded_targets.add(game.last_butterfly_distract_target)
        doctor_excluded_targets.add(game.last_butterfly_distract_target)
        commissioner_excluded_targets.add(game.last_butterfly_distract_target)
    
    # НЕ добавляем всех ранее отвлеченных игроков - они должны быть доступны для выбора
    # Отвлечение действует только на одну ночь
    for player in alive_players:
        try:
            # Если игрок отвлечен этой ночью — не отправляем ему клавиатуру действий
            if game.butterfly_distract_target is not None and player.user_id == game.butterfly_distract_target:
                logger.debug(f"_send_night_action_keyboards: пропускаем отправку для отвлеченного игрока {player.user_id}")
                continue
            if player.role == PlayerRole.MAFIA:
                # Выбираем случайную фразу для мафии
                mafia_message = random.choice(NIGHT_ACTION_MESSAGES[PlayerRole.MAFIA])
                await bot.send_message(
                    player.user_id,
                    build_role_prompt(PlayerRole.MAFIA, mafia_message),
                    reply_markup=get_player_selection_keyboard(alive_players, "mafia_kill", chat_key, exclude_user_id=player.user_id, exclude_target_ids=mafia_excluded_targets)
                )
                # Показать состав мафии для координации
                peers = game_manager.get_mafia_peers(chat_key, exclude_user_id=player.user_id)
                if peers:
                    mafia_list = ", ".join([f"@{p.username}" if p.username else p.first_name for p in peers])
                    await bot.send_message(player.user_id, f"🤫 Твои сообщники: {mafia_list}. Можете обсуждать прямо здесь в ЛС — я передам им твои сообщения.")
            elif player.role == PlayerRole.DOCTOR:
                # Выбираем случайную фразу для доктора
                doctor_message = random.choice(NIGHT_ACTION_MESSAGES[PlayerRole.DOCTOR])
                await bot.send_message(
                    player.user_id,
                    build_role_prompt(PlayerRole.DOCTOR, doctor_message),
                    # Разрешаем самолечение — не исключаем себя
                    reply_markup=get_player_selection_keyboard(alive_players, "doctor_save", chat_key, exclude_target_ids=doctor_excluded_targets)
                )
            elif player.role == PlayerRole.COMMISSIONER:
                # Выбираем случайную фразу для комиссара
                commissioner_message = random.choice(NIGHT_ACTION_MESSAGES[PlayerRole.COMMISSIONER])
                await bot.send_message(
                    player.user_id,
                    build_role_prompt(PlayerRole.COMMISSIONER, commissioner_message),
                    reply_markup=get_player_selection_keyboard(alive_players, "commissioner_check", chat_key, exclude_user_id=player.user_id, exclude_target_ids=commissioner_excluded_targets)
                )
            elif player.role == PlayerRole.BUTTERFLY:
                # Выбираем случайную фразу для ночной бабочки
                butterfly_message = random.choice(NIGHT_ACTION_MESSAGES[PlayerRole.BUTTERFLY])
                await bot.send_message(
                    player.user_id,
                    build_role_prompt(PlayerRole.BUTTERFLY, butterfly_message),
                    reply_markup=get_player_selection_keyboard(alive_players, "butterfly_distract", chat_key, exclude_user_id=player.user_id)
                )
        except Exception as e:
            logger.exception(f"_send_night_action_keyboards: ошибка отправки игроку {player.user_id}: {e}")
    # Помечаем, что ночные клавиатуры разосланы
    game.night_prompts_sent = True

async def _autopilot_loop(chat_key: str, bot):
    logger.info(f"старт автопилота для чата {chat_key}")
    try:
        # Получаем chat_id и thread_id из chat_key один раз в начале
        # Делаем их глобальными для всей функции
        global_chat_id = get_chat_id_from_key(chat_key)
        global_thread_id = get_thread_id_from_key(chat_key)
        
        # Если thread_id равен 0, не передаем его (главная тема)
        # Но для главной темы нужно передавать None, а не 0
        global_message_thread_id = None if global_thread_id == 0 else global_thread_id
        
        logger.debug(f"автопилот: chat_id={global_chat_id}, thread_id={global_thread_id}, message_thread_id={global_message_thread_id}")
        
        while True:
            game = game_manager.get_game(chat_key)
            if not game or game.phase == GamePhase.ENDED:
                logger.info(f"автопилот завершен для чата {chat_key} (игра отсутствует или закончена)")
                break

            # Ночь
            logger.debug(f"автопилот: проверяем ночную фазу, текущая фаза: {game.phase}, раунд: {game.current_round}")
            if game.phase == GamePhase.NIGHT:
                logger.info(f"автопилот: начинается ночная фаза в чате {chat_key}")
                # Выбираем случайное сообщение о начале ночи
                night_message = random.choice(NIGHT_PHASE_MESSAGES)
                await bot.send_message(global_chat_id, night_message, message_thread_id=global_message_thread_id)
                await _send_night_action_keyboards(chat_key, bot)

                # Ждем строго фиксированное время ночи, независимо от того, завершили ли все действия, с напоминаниями
                waited = 0
                interval = 1  # Уменьшаем интервал для более точного отслеживания
                notified_night = set()
                logger.debug(f"ночная фаза: начинаем таймер, длительность: {NIGHT_TIMEOUT_SECS} сек.")
                while waited < NIGHT_TIMEOUT_SECS:
                    remaining = NIGHT_TIMEOUT_SECS - waited
                    logger.debug(f"ночная фаза: прошло {waited} сек., осталось {remaining} сек.")
                    
                    # Проверяем, завершили ли все ночные действия
                    try:
                        current_game = game_manager.get_game(chat_key)
                        if current_game and current_game.phase == GamePhase.NIGHT:
                            # Проверяем, все ли роли выполнили действия
                            required_roles = set()
                            if any(p.role == PlayerRole.MAFIA for p in current_game.get_alive_players()):
                                required_roles.add("mafia")
                            if any(p.role == PlayerRole.DOCTOR for p in current_game.get_alive_players()):
                                required_roles.add("doctor")
                            if any(p.role == PlayerRole.COMMISSIONER for p in current_game.get_alive_players()):
                                required_roles.add("commissioner")
                            if any(p.role == PlayerRole.BUTTERFLY for p in current_game.get_alive_players()):
                                required_roles.add("butterfly")
                            
                            completed_actions = current_game.night_actions_completed
                            if required_roles.issubset(completed_actions):
                                logger.info(f"ночная фаза: все действия завершены, завершаем досрочно")
                                break
                            
                            # Дополнительная проверка: если мафия не может договориться, ждем еще немного
                            if "mafia" in required_roles and "mafia" not in completed_actions:
                                # Проверяем, голосовала ли мафия, но не выбрала цель
                                if current_game.mafia_votes and len(current_game.mafia_votes) > 0:
                                    alive_mafias = [p.user_id for p in current_game.get_players_by_role(PlayerRole.MAFIA)]
                                    if len(current_game.mafia_votes) == len(alive_mafias):
                                        # Все мафии проголосовали, но цель не выбрана (ничья)
                                        logger.info(f"ночная фаза: мафия проголосовала, но не выбрала цель - ждем еще немного")
                                        # Не завершаем досрочно, даем время на обдумывание
                    except Exception as e:
                        logger.debug(f"ночная фаза: ошибка при проверке завершения действий: {e}")
                    
                    if remaining in {30, 15, 5} and remaining not in notified_night:
                        try:
                            # Выбираем случайную фразу для напоминания о ночи
                            night_reminder = random.choice(TIME_REMINDER_MESSAGES["night"]).format(time=remaining)
                            await bot.send_message(
                                global_chat_id,
                                night_reminder,
                                message_thread_id=global_message_thread_id,
                            )
                            notified_night.add(remaining)
                            logger.debug(f"отправлено напоминание о ночи: {remaining} сек. осталось")
                        except Exception as e:
                            logger.exception(f"ошибка отправки напоминания о ночи: {e}")
                    await asyncio.sleep(interval)
                    waited += interval

                msg, _ = game_manager.process_night_results(chat_key)
                if msg:
                    await bot.send_message(global_chat_id, msg, message_thread_id=global_message_thread_id)
                    logger.info(f"ночная фаза: отправлено сообщение о результатах: {msg[:100]}...")
                else:
                    logger.warning(f"ночная фаза: не получено сообщение о результатах")
                
                over, winner_msg = game_manager.check_game_over(chat_key)
                if over:
                    logger.info(f"автопилот: игра завершена в чате {chat_key} - победа {'мафии' if 'мафия' in winner_msg else 'мирных'}")
                    await bot.send_message(global_chat_id, winner_msg, message_thread_id=global_message_thread_id, reply_markup=get_new_game_keyboard())
                    game_manager.end_game(chat_key)
                    break

            # День
            current_game = game_manager.get_game(chat_key)
            logger.debug(f"автопилот: проверяем дневную фазу, текущая фаза: {current_game.phase if current_game else 'None'}, раунд: {current_game.current_round if current_game else 'None'}")
            if current_game and current_game.phase == GamePhase.DAY:
                logger.info(f"автопилот: начинается дневная фаза в чате {chat_key}")
                # Сообщение про итоги действий ролей ночью
                game = game_manager.get_game(chat_key)
                
                # Отправляем результаты комиссара в ЛС (без дублирования в общий чат)
                if game.last_commissioner_checks:
                    for _cid, _target_id, is_mafia in game.last_commissioner_checks:
                        commissioner = game.players.get(_cid)
                        target = game.players.get(_target_id)
                        if target:
                            uname = f"@{target.username}" if target.username else None
                            disp = f"{target.first_name}{f' ({uname})' if uname else ''}"
                        else:
                            disp = "игрок"
                        # ЛС комиссару — с раскрытием цели
                        if commissioner:
                            try:
                                await bot.send_message(
                                    commissioner.user_id,
                                    f"👮 Результат проверки: {disp} — {'МАФИЯ' if is_mafia else 'не мафия'}."
                                )
                            except Exception as e:
                                logger.exception(f"не удалось отправить результат проверки комиссару {_cid}: {e}")

                # Отправляем дневное приветствие
                day_message = random.choice(DAY_PHASE_MESSAGES)
                await bot.send_message(global_chat_id, day_message, message_thread_id=global_message_thread_id)
                
                # Не дублируем: после ночи уже отправлена единая сводка. Публичная сводка комиссара опускается.

                # Таймер дня с напоминаниями за 30/15/5 секунд
                waited_day = 0
                interval = 1  # Уменьшаем интервал для более точного отслеживания
                notified = set()
                logger.debug(f"дневная фаза: начинаем таймер, длительность: {DAY_DISCUSS_TIMEOUT_SECS} сек.")
                while waited_day < DAY_DISCUSS_TIMEOUT_SECS:
                    remaining = DAY_DISCUSS_TIMEOUT_SECS - waited_day
                    logger.debug(f"дневная фаза: прошло {waited_day} сек., осталось {remaining} сек.")
                    if remaining in {30, 15, 5} and remaining not in notified:
                        try:
                            # Выбираем случайную фразу для напоминания о дне
                            day_reminder = random.choice(TIME_REMINDER_MESSAGES["day"]).format(time=remaining)
                            await bot.send_message(
                                global_chat_id,
                                day_reminder,
                                message_thread_id=global_message_thread_id,
                            )
                            notified.add(remaining)
                            logger.debug(f"отправлено напоминание о дне: {remaining} сек. осталось")
                        except Exception as e:
                            logger.exception(f"ошибка отправки напоминания о дне: {e}")
                    await asyncio.sleep(interval)
                    waited_day += interval

                # Особое правило: после самой первой ночи пропускаем первое голосование
                if not getattr(game, "first_voting_skipped", False) and game.current_round <= 1:
                    # Публикуем списки живых/мертвых и сразу уходим в ночь без голосования
                    alive = []
                    dead = []
                    for p in game.players.values():
                        uname = f"@{p.username}" if p.username else None
                        disp = f"{p.first_name}{f' ({uname})' if uname else ''}"
                        (alive if p.is_alive else dead).append(disp)
                    # Выбираем случайное сообщение об отсутствии голосования
                    no_voting_message = random.choice(NO_VOTING_FIRST_DAY_MESSAGES)
                    msg = (
                        no_voting_message + "\n\n" +
                        f"👥 Живые ({len(alive)}):\n" + ("\n".join([f"• {n}" for n in alive]) if alive else "—") + "\n" +
                        f"💀 Мертвые ({len(dead)}):\n" + ("\n".join([f"• {n}" for n in dead]) if dead else "—")
                    )
                    await bot.send_message(global_chat_id, msg, message_thread_id=global_message_thread_id)
                    # Переходим к ночи
                    game.phase = GamePhase.NIGHT
                    game.current_round += 1
                    game.all_actions_notified = False
                    game.first_voting_skipped = True
                    continue

                if game_manager.start_voting(chat_key):
                    logger.info(f"автопилот: начинается голосование в чате {chat_key}")
                    game = game_manager.get_game(chat_key)
                    alive = game.get_alive_players()
                    # Переголосование отключено — кандидатов не фильтруем
                    # Блокируем право голоса у отвлеченного прошлой ночью
                    if game.last_butterfly_distract_target is not None:
                        for p in alive:
                            if p.user_id == game.last_butterfly_distract_target:
                                p.has_voted = True
                                break
                    # Выбираем случайное сообщение о начале голосования
                    title = random.choice(VOTING_START_MESSAGES)
                    sent = await bot.send_message(global_chat_id, title, reply_markup=get_voting_keyboard(alive), message_thread_id=global_message_thread_id)
                    try:
                        game.current_voting_message_id = sent.message_id
                    except Exception:
                        pass

                    # Ждем строго фиксированное время голосования с напоминаниями
                    waited_vote = 0
                    interval = 1  # Уменьшаем интервал для более точного отслеживания
                    notified_vote = set()
                    logger.debug(f"голосование: начинаем таймер, длительность: {VOTING_TIMEOUT_SECS} сек.")
                    while waited_vote < VOTING_TIMEOUT_SECS:
                        # Раннее завершение: все живые (и допущенные) проголосовали
                        try:
                            game = game_manager.get_game(chat_key)
                            if game and game.phase == GamePhase.VOTING:
                                eligible = game.get_alive_players()
                                if eligible and all(p.has_voted for p in eligible):
                                    logger.info("голосование: все голоса получены — завершаем досрочно")
                                    break
                        except Exception as e:
                            logger.debug(f"голосование: ошибка при проверке раннего завершения: {e}")
                        remaining = VOTING_TIMEOUT_SECS - waited_vote
                        logger.debug(f"голосование: прошло {waited_vote} сек., осталось {remaining} сек.")
                        if remaining in {30, 15, 5} and remaining not in notified_vote:
                            try:
                                # Выбираем случайную фразу для напоминания о голосовании
                                vote_reminder = random.choice(TIME_REMINDER_MESSAGES["voting"]).format(time=remaining)
                                await bot.send_message(
                                    global_chat_id,
                                    vote_reminder,
                                    message_thread_id=global_message_thread_id,
                                )
                                notified_vote.add(remaining)
                                logger.debug(f"отправлено напоминание о голосовании: {remaining} сек. осталось")
                            except Exception as e:
                                logger.exception(f"ошибка отправки напоминания о голосовании: {e}")
                        await asyncio.sleep(interval)
                        waited_vote += interval

                    result_msg, executed_id = game_manager.get_voting_results(chat_key)
                    await bot.send_message(global_chat_id, result_msg, message_thread_id=global_message_thread_id)

                    # executed_id может быть 0 только когда никто не проголосовал — ничья теперь не требует переголосования
                    
                    # Проверяем окончание игры только если кого-то казнили
                    over, winner_msg = game_manager.check_game_over(chat_key)
                    if over:
                        logger.info(f"автопилот: игра завершена в чате {chat_key} - победа {'мафии' if 'мафия' in winner_msg else 'мирных'}")
                        await bot.send_message(global_chat_id, winner_msg, message_thread_id=global_message_thread_id, reply_markup=get_new_game_keyboard())
                        game_manager.end_game(chat_key)
                        break
                    else:
                        # Сбрасываем флаги переголосования, если казнь состоялась
                        game = game_manager.get_game(chat_key)
                        if game:
                            game.revote_active = False
                            try:
                                game.revote_candidates.clear()
                            except Exception:
                                game.revote_candidates = set()
                        
                        # Проверяем, что игра действительно перешла в ночную фазу
                        game = game_manager.get_game(chat_key)
                        if game and game.phase == GamePhase.NIGHT:
                            logger.info(f"автопилот: голосование завершено, переход к ночной фазе, раунд {game.current_round}")
                            # Сбрасываем флаги для следующей ночи
                            game.night_prompts_sent = False
                            game.all_actions_notified = False
                            # Продолжаем цикл для обработки ночной фазы
                            continue
                        else:
                            logger.warning(f"автопилот: после голосования игра не перешла в ночную фазу, фаза: {game.phase if game else 'None'}")
                            # Принудительно переводим в ночь
                            if game:
                                game.phase = GamePhase.NIGHT
                                game.current_round += 1
                                game.night_prompts_sent = False
                                game.all_actions_notified = False
                                logger.info(f"автопилот: принудительно переведена в ночную фазу, раунд {game.current_round}")
                                continue
                else:
                    # Если не удалось начать голосование, маленькая пауза и попытка снова
                    await asyncio.sleep(3)

    except asyncio.CancelledError:
        logger.info(f"автопилот отменен для чата {chat_key}")
        raise
    except Exception as e:
        logger.exception(f"ошибка автопилота в чате {chat_key}: {e}")
        logger.debug(f"global_chat_id={global_chat_id if 'global_chat_id' in locals() else 'не определен'}, global_message_thread_id={global_message_thread_id if 'global_message_thread_id' in locals() else 'не определен'}")

@router.message(Command("mafia"))
async def cmd_mafia(message: Message):
    """Обработчик команды /mafia"""
    logger.debug(f"cmd_mafia: команда /mafia вызвана в чате {message.chat.id}, thread_id={message.message_thread_id}")
    
    # Строгая проверка темы - бот работает ТОЛЬКО в теме "Игра в «Мафию»" (ID: 39431)
    if not check_topic_permission(message):
        await message.answer("⚠️ Команда /mafia должна быть вызвана в теме «Игра в «Мафию»!")
        return
    
    logger.debug(f"cmd_mafia: доступ разрешен, команда в теме {message.message_thread_id}")
    
    # Создаем игру для этого чата (с учётом темы)
    chat_key = f"{message.chat.id}_{message.message_thread_id or 0}"
    game = game_manager.create_game(chat_key)
    logger.debug(f"cmd_mafia: создана игра для чата {chat_key}")
    
    # Выбираем случайное приветствие от Дона Витте
    greeting = random.choice(DON_VITTE_GREETINGS)
    
    await message.answer(
        greeting,
        reply_markup=get_main_menu_keyboard()
    )

@router.callback_query(F.data == "test_game")
async def show_test_game_menu(callback: CallbackQuery):
    """Показывает меню тестовой игры"""
    logger.debug(f"show_test_game_menu: показано меню тестовой игры для чата {callback.message.chat.id}")
    
    # Проверяем права доступа - в теме "Игра в «Мафию»" разрешаем всем админам
    is_admin = False
    is_creator = False
    chat_type = callback.message.chat.type
    
    logger.debug(f"show_test_game_menu: тип чата: {chat_type}, user_id: {callback.from_user.id}")
    
    try:
        member = await callback.message.bot.get_chat_member(callback.message.chat.id, callback.from_user.id)
        is_admin = member.status in {"administrator", "creator"}
        is_creator = member.status == "creator"
        logger.debug(f"show_test_game_menu: проверка прав - статус: {member.status}, is_admin: {is_admin}, is_creator: {is_creator}")
    except Exception as e:
        logger.warning(f"show_test_game_menu: не удалось проверить права пользователя: {e}")
        # Если не удалось проверить права в форуме, разрешаем доступ
        if chat_type == "supergroup":
            is_admin = True
            logger.debug(f"show_test_game_menu: разрешен доступ из-за невозможности проверки прав в супергруппе")
    
    # Дополнительная проверка: если это тема "Игра в «Мафию»", то разрешаем доступ
    if not (is_admin or is_creator):
        if check_topic_permission(callback.message):
            is_admin = True
            logger.debug(f"show_test_game_menu: разрешен доступ в теме «Игра в «Мафию»»")
    
    # ВРЕМЕННО: для разработки разрешаем всем в теме "Игра в «Мафию»"
    if not (is_admin or is_creator):
        if check_topic_permission(callback.message):
            logger.warning(f"show_test_game_menu: ВРЕМЕННЫЙ доступ к тестовой игре для разработки")
            # Разрешаем доступ для разработки
        else:
            await callback.answer("⚠️ Тестовая игра доступна только в теме «Игра в «Мафию»»!", show_alert=True)
            return
    
    await callback.message.answer(
        "🧪 ТЕСТОВАЯ ИГРА 🧪\n\n"
        "Здесь вы можете запустить тестовую игру с 10 виртуальными игроками для тестирования нововведений.\n\n"
        "Возможности:\n"
        "• Автоматическое выполнение ночных действий\n"
        "• Автоматическое голосование\n"
        "• Полный цикл игры для тестирования\n\n"
        "Управление:\n"
        "• Запуск/остановка теста\n"
        "• Пауза и сброс\n"
        "• Только для администраторов\n\n"
        "Выберите действие:",
        reply_markup=get_test_game_control_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "start_test_game")
async def start_test_game(callback: CallbackQuery):
    """Запускает тестовую игру"""
    logger.debug(f"start_test_game: попытка запуска тестовой игры в чате {callback.message.chat.id}")
    
    # Проверяем права доступа - более гибкая проверка
    is_admin = False
    is_creator = False
    chat_type = callback.message.chat.type
    
    try:
        member = await callback.message.bot.get_chat_member(callback.message.chat.id, callback.from_user.id)
        is_admin = member.status in {"administrator", "creator"}
        is_creator = member.status == "creator"
        logger.debug(f"start_test_game: статус пользователя: {member.status}")
    except Exception as e:
        logger.warning(f"start_test_game: не удалось проверить права пользователя: {e}")
        # Если не удалось проверить права, разрешаем доступ в супергруппе
        if chat_type == "supergroup":
            is_admin = True
            logger.debug(f"start_test_game: разрешен доступ в супергруппе")
    
    # Дополнительная проверка: если это тема "Игра в «Мафию»", то разрешаем доступ
    if not (is_admin or is_creator):
        if check_topic_permission(callback.message):
            is_admin = True
            logger.debug(f"start_test_game: разрешен доступ в теме «Игра в «Мафию»»")
    
    # ВРЕМЕННО: для разработки разрешаем всем в теме "Игра в «Мафию»"
    if not (is_admin or is_creator):
        if check_topic_permission(callback.message):
            logger.warning(f"start_test_game: ВРЕМЕННЫЙ доступ к тестовой игре для разработки")
            # Разрешаем доступ для разработки
        else:
            await callback.answer("⚠️ Тестовая игра доступна только в теме «Игра в «Мафию»»!", show_alert=True)
            return
    
    chat_key = get_chat_key(callback.message)
    
    # Останавливаем предыдущий автопилот если есть
    if _autopilot_tasks.get(chat_key) and not _autopilot_tasks[chat_key].done():
        logger.info(f"start_test_game: остановка предыдущего автопилота для чата {chat_key}")
        _autopilot_tasks[chat_key].cancel()
        await asyncio.sleep(1)  # Ждем завершения
    
    # Принудительно завершаем предыдущую игру
    game_manager.end_game(chat_key)
    
    # Создаем тестовую игру
    logger.info(f"start_test_game: создание тестовой игры для чата {chat_key}")
    game = game_manager.create_test_game(chat_key)
    
    if game:
        # Устанавливаем начальную фазу игры
        game.phase = GamePhase.NIGHT
        game.current_round = 1
        logger.info(f"start_test_game: игра создана, фаза: {game.phase}, раунд: {game.current_round}, игроков: {len(game.players)}")
        
        # Дополнительная диагностика
        if len(game.players) == 0:
            logger.error(f"start_test_game: КРИТИЧЕСКАЯ ОШИБКА! Игроки не созданы!")
            await callback.message.answer(
                "❌ ОШИБКА СОЗДАНИЯ ТЕСТОВОЙ ИГРЫ!\n\n"
                "Игроки не были созданы. Проверьте логи для диагностики.",
                reply_markup=get_test_game_control_keyboard()
            )
        else:
            logger.info(f"start_test_game: успешно создано {len(game.players)} игроков")
            for player_id, player in game.players.items():
                logger.debug(f"start_test_game: игрок {player_id}: {player.first_name} ({player.role.value})")
        
            await callback.message.answer(
                "🧪 ТЕСТОВАЯ ИГРА ЗАПУЩЕНА! 🧪\n\n"
                f"Создано {len(game.players)} виртуальных игроков:\n"
                + "\n".join([f"• {p.first_name} ({p.role.value})" for p in game.players.values()]) +
                "\n\nИгра будет автоматически проходить все фазы.\n"
                "Используйте кнопки управления для контроля теста.",
                reply_markup=get_test_game_control_keyboard()
            )
        
        # Запускаем автопилот для тестовой игры
        logger.info(f"start_test_game: запуск тестового автопилота для чата {chat_key}")
        if _autopilot_tasks.get(chat_key) and not _autopilot_tasks[chat_key].done():
            logger.info(f"start_test_game: отмена предыдущего автопилота для чата {chat_key}")
            _autopilot_tasks[chat_key].cancel()
        
        _autopilot_tasks[chat_key] = asyncio.create_task(_test_autopilot_loop(chat_key, callback.bot))
        logger.info(f"start_test_game: тестовый автопилот создан как задача для чата {chat_key}")
        
        logger.info(f"start_test_game: тестовая игра запущена в чате {chat_key}")
    else:
        await callback.answer("❌ Не удалось запустить тестовую игру!", show_alert=True)
    
    await callback.answer()

@router.callback_query(F.data == "stop_test_game")
async def stop_test_game(callback: CallbackQuery):
    """Останавливает тестовую игру"""
    logger.debug(f"stop_test_game: попытка остановки тестовой игры в чате {callback.message.chat.id}")
    
    # Проверяем права доступа - более гибкая проверка
    is_admin = False
    is_creator = False
    chat_type = callback.message.chat.type
    
    try:
        member = await callback.message.bot.get_chat_member(callback.message.chat.id, callback.from_user.id)
        is_admin = member.status in {"administrator", "creator"}
        is_creator = member.status == "creator"
        logger.debug(f"stop_test_game: статус пользователя: {member.status}")
    except Exception as e:
        logger.warning(f"stop_test_game: не удалось проверить права пользователя: {e}")
        # Если не удалось проверить права, разрешаем доступ в супергруппе
        if chat_type == "supergroup":
            is_admin = True
            logger.debug(f"stop_test_game: разрешен доступ в супергруппе")
    
    # Дополнительная проверка: если это тема "Игра в «Мафию»", то разрешаем доступ
    if not (is_admin or is_creator):
        if check_topic_permission(callback.message):
            is_admin = True
            logger.debug(f"stop_test_game: разрешен доступ в теме «Игра в «Мафию»»")
    
    # ВРЕМЕННО: для разработки разрешаем всем в теме "Игра в «Мафию»"
    if not (is_admin or is_creator):
        if check_topic_permission(callback.message):
            logger.warning(f"stop_test_game: ВРЕМЕННЫЙ доступ к тестовой игре для разработки")
            # Разрешаем доступ для разработки
        else:
            await callback.answer("⚠️ Тестовая игра доступна только в теме «Игра в «Мафию»»!", show_alert=True)
            return
    
    chat_key = get_chat_key(callback.message)
    
    # Останавливаем автопилот
    if _autopilot_tasks.get(chat_key):
        _autopilot_tasks[chat_key].cancel()
        del _autopilot_tasks[chat_key]
    
    # Завершаем игру
    game_manager.end_game(chat_key)
    
    await callback.message.answer(
        "⏹️ ТЕСТОВАЯ ИГРА ОСТАНОВЛЕНА ⏹️\n\n"
        "Игра завершена. Все данные очищены.\n"
        "Можете запустить новый тест или вернуться в главное меню.",
        reply_markup=get_main_menu_keyboard()
    )
    
    logger.info(f"stop_test_game: тестовая игра остановлена в чате {chat_key}")
    await callback.answer()

async def _test_autopilot_loop(chat_key: str, bot):
    """Автопилот для тестовой игры"""
    logger.info(f"старт тестового автопилота для чата {chat_key}")
    try:
        global_chat_id = get_chat_id_from_key(chat_key)
        global_thread_id = get_thread_id_from_key(chat_key)
        global_message_thread_id = None if global_thread_id == 0 else global_thread_id
        
        logger.debug(f"тестовый автопилот: chat_id={global_chat_id}, thread_id={global_thread_id}")
        
        # Начинаем с ночной фазы
        game = game_manager.get_game(chat_key)
        if game:
            game.phase = GamePhase.NIGHT
            game.current_round = 1
            logger.info(f"тестовый автопилот: установлена начальная фаза NIGHT для чата {chat_key}")
        
        while True:
            game = game_manager.get_game(chat_key)
            if not game or game.phase == GamePhase.ENDED or not game.is_test_game:
                logger.info(f"тестовый автопилот завершен для чата {chat_key}")
                break
            
            logger.info(f"тестовый автопилот: === НАЧАЛО ЦИКЛА ===")
            logger.info(f"тестовый автопилот: фаза: {game.phase}, раунд: {game.current_round}")
            logger.info(f"тестовый автопилот: живых игроков: {len(game.get_alive_players())}")
            logger.info(f"тестовый автопилот: всего игроков: {len(game.players)}")
            
            # Логируем состояние всех игроков
            for player_id, player in game.players.items():
                status = "ЖИВ" if player.is_alive else "МЕРТВ"
                logger.debug(f"тестовый автопилот: игрок {player.first_name} (ID: {player_id}): {status}, роль: {player.role.value}")
            
            logger.info(f"тестовый автопилот: ===================")
            
            # Ночь
            if game.phase == GamePhase.NIGHT:
                logger.info(f"тестовый автопилот: ночная фаза в чате {chat_key}")
                night_message = random.choice(NIGHT_PHASE_MESSAGES)
                await bot.send_message(global_chat_id, night_message, message_thread_id=global_message_thread_id)
                
                # Ждем немного для имитации размышлений игроков
                await asyncio.sleep(5)
                
                # Автоматически выполняем ночные действия
                logger.info(f"тестовый автопилот: выполнение ночных действий для раунда {game.current_round}")
                game_manager.execute_test_night_actions(chat_key)
                
                # Проверяем результаты ночных действий
                game = game_manager.get_game(chat_key)
                if game:
                    logger.info(f"тестовый автопилот: === ПРОВЕРКА РЕЗУЛЬТАТОВ НОЧИ {game.current_round} ===")
                    logger.info(f"тестовый автопилот: Цель мафии: {game.night_kill_target}")
                    logger.info(f"тестовый автопилот: Спасения доктора: {game.doctor_saves}")
                    logger.info(f"тестовый автопилот: Проверки комиссара: {game.commissioner_check_results}")
                    logger.info(f"тестовый автопилот: Отвлечения бабочки: {game.butterfly_distract_target}")
                    
                    # Проверяем, кто выжил
                    alive_after_night = game.get_alive_players()
                    logger.info(f"тестовый автопилот: После ночи живых игроков: {len(alive_after_night)}")
                    for player in alive_after_night:
                        logger.debug(f"тестовый автопилот: жив: {player.first_name} ({player.role.value})")
                    logger.info(f"тестовый автопилот: ==========================================")
                
                # Переходим к дню
                game.phase = GamePhase.DAY
                game.current_round += 1
                logger.info(f"тестовый автопилот: переход к дневной фазе, раунд {game.current_round}")
                
                # Ждем немного перед днем
                await asyncio.sleep(3)
            
            # День
            elif game.phase == GamePhase.DAY:
                logger.info(f"тестовый автопилот: дневная фаза в чате {chat_key}")
                day_message = random.choice(DAY_PHASE_MESSAGES)
                await bot.send_message(global_chat_id, day_message, message_thread_id=global_message_thread_id)
                
                # Ждем немного для имитации обсуждения
                await asyncio.sleep(10)
                
                # Переходим к голосованию
                logger.info(f"тестовый автопилот: попытка начать голосование в чате {chat_key}")
                if game_manager.start_voting(chat_key):
                    game.phase = GamePhase.VOTING
                    logger.info(f"тестовый автопилот: голосование начато, фаза изменена на VOTING")
                else:
                    # Если не удалось начать голосование, переходим к ночи
                    logger.warning(f"тестовый автопилот: не удалось начать голосование, переходим к ночи")
                    game.phase = GamePhase.NIGHT
                    continue
            
            # Голосование
            elif game.phase == GamePhase.VOTING:
                logger.info(f"тестовый автопилот: голосование в чате {chat_key}")
                voting_message = random.choice(VOTING_START_MESSAGES)
                await bot.send_message(global_chat_id, voting_message, message_thread_id=global_message_thread_id)
                
                # Ждем немного для имитации голосования
                await asyncio.sleep(8)
                
                # Автоматически выполняем голосование
                logger.info(f"тестовый автопилот: выполнение автоматического голосования в чате {chat_key}")
                game_manager.execute_test_voting(chat_key)
                
                # Проверяем результаты голосования
                game = game_manager.get_game(chat_key)
                if game:
                    logger.info(f"тестовый автопилот: === ПРОВЕРКА РЕЗУЛЬТАТОВ ГОЛОСОВАНИЯ ===")
                    logger.info(f"тестовый автопилот: Всего голосов: {len(game.votes)}")
                    for voter_id, target_id in game.votes.items():
                        voter = game.players.get(voter_id)
                        target = game.players.get(target_id)
                        if voter and target:
                            logger.info(f"тестовый автопилот: голос: {voter.first_name} ({voter.role.value}) → {target.first_name} ({target.role.value})")
                    logger.info(f"тестовый автопилот: ==========================================")
                
                # Получаем результаты голосования
                logger.info(f"тестовый автопилот: получение результатов голосования в чате {chat_key}")
                result_msg, executed_id = game_manager.get_voting_results(chat_key)
                await bot.send_message(global_chat_id, result_msg, message_thread_id=global_message_thread_id)
                
                # Проверяем окончание игры
                over, winner_msg = game_manager.check_game_over(chat_key)
                if over:
                    logger.info(f"тестовый автопилот: игра завершена в чате {chat_key}")
                    await bot.send_message(global_chat_id, winner_msg, message_thread_id=global_message_thread_id)
                    game_manager.end_game(chat_key)
                    break
                
                # Переходим к ночи
                game.phase = GamePhase.NIGHT
                game.current_round += 1
                logger.info(f"тестовый автопилот: переход к ночной фазе, раунд {game.current_round}")
                await asyncio.sleep(3)
            
            # Небольшая пауза между циклами
            await asyncio.sleep(1)
            
    except asyncio.CancelledError:
        logger.info(f"тестовый автопилот отменен для чата {chat_key}")
        raise
    except Exception as e:
        logger.exception(f"ошибка тестового автопилота в чате {chat_key}: {e}")

@router.callback_query(F.data == "rules")
async def show_rules(callback: CallbackQuery):
    """Показывает правила игры"""
    logger.debug(f"show_rules: показ правил для чата {callback.message.chat.id}")
    
    rules_text = (
        "📜 ПРАВИЛА ИГРЫ «МАФИЯ МУИВ» 📜\n\n"
        "       🌃 Город засыпает, тени сгущаются над МУИВом... Просыпается мафия…\n\n"
        "Каждый житель носит маску. Кто-то скрывает кровь на руках, кто-то – страх в глазах.\n"
        "Твоя задача проста: выжить. Но способ у каждого свой.\n\n"
        "       👥 Роли:\n"
        "• 🔪 Мафия — убийцы в дорогих костюмах. Ночью они решают, кто не доживёт до утра.\n"
        "• 👮 Комиссар — глаз закона. Каждую ночь проверяет одного игрока.\n"
        "• 🩺 Доктор — единственный, кто может спасти жизнь. Лечит выбранного игрока.\n"
        "• 💃 Ночная бабочка — коварная красавица. Лишает игрока хода.\n"
        "• 👔 Мирный житель — обычные люди, у которых есть лишь голос и вера друг в друга.\n\n"
        "       🌙 Ночь: Город спит. Мафия убивает. Комиссар проверяет. Доктор лечит. Бабочка соблазняет.\n"
        "       ☀️ День: Город просыпается. Все обсуждают и подозревают друг друга.\n"
        "       ⚖️ Казнь: По итогам голосования один игрок уходит из игры.\n\n"
        "       🏆 Конец игры:\n"
        "• Если мафия уничтожает всех мирных — город во власти преступников.\n"
        "• Если мирные вычисляют всех мафиози — город снова дышит свободой.\n\n"
        "       💼 В этом городе выживает не самый честный, а самый хитрый."
    )

    await callback.message.answer(
        rules_text,
        reply_markup=get_back_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "how_to_play")
async def show_how_to_play(callback: CallbackQuery):
    """Показывает инструкцию по игре"""
    logger.debug(f"show_how_to_play: показ инструкции для чата {callback.message.chat.id}")
    
    how_to_play_text = (
        "❓ КАК ИГРАТЬ ❓\n\n"
        "1️⃣ Присоединяйся к игре — нажми «➕ Присоединиться к игре»\n\n"
        "2️⃣ Дождись начала — как только наберётся хотя бы 4 игрока, нужно будет нажать кнопку «✅ Готово»\n\n"
        "3️⃣ Получи свою роль — я шепну её тебе на ухо\n\n"
        "4️⃣ Живи по фазам:\n"
        "   🌙 Ночь — мафия выходит на улицы, доктор спасает, комиссар ищет правду, бабочка сбивает с пути\n"
        "   ☀️ День — весь город собирается, идёт жаркое обсуждение и поиск предателей\n"
        "   🗳️ Голосование — каждый отдаёт свой голос за того, кто, по его мнению, замешан в крови\n\n"
        "5️⃣ Победа достанется тем, кто переживёт врагов и сумеет переиграть остальных\n\n"
        "💡 Советы бывалых:\n"
        "• Слушай, кто что говорит — и кто слишком много молчит\n"
        "• Запоминай, кто голосует против кого\n"
        "• Не спеши раскрывать свою карту, это может стоить тебе жизни\n"
        "• Доверяй, но всегда проверяй — в этом городе каждый может оказаться с ножом за спиной"
    )
    
    await callback.message.answer(
        how_to_play_text,
        reply_markup=get_back_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "start_game")
async def start_game_lobby(callback: CallbackQuery):
    """Начинает лобби игры"""
    # Проверяем разрешение на работу в данной теме
    if not check_topic_permission(callback.message):
        await callback.answer("⚠️ Действие разрешено только в теме «Игра в «Мафию»»!", show_alert=True)
        return
    
    chat_id = callback.message.chat.id
    thread_id = callback.message.message_thread_id or 0
    chat_key = f"{chat_id}_{thread_id}"
    
    logger.info(f"start_game_lobby: создание лобби для чата {chat_key} пользователем {callback.from_user.first_name} (@{callback.from_user.username})")
    
    # Создаем игру, если её нет
    game = game_manager.get_game(chat_key)
    if not game:
        logger.debug(f"start_game_lobby: игра не найдена, создаем новую")
        game = game_manager.create_game(chat_key)
        logger.info(f"start_game_lobby: создана новая игра для чата {chat_key}")
    
    # Сохраняем создателя лобби (если еще не установлен)
    if not getattr(game, "lobby_creator_id", None):
        try:
            game.lobby_creator_id = callback.from_user.id
            logger.debug(f"start_game_lobby: установлен создатель лобби: {callback.from_user.id}")
        except Exception as e:
            logger.exception(f"start_game_lobby: ошибка установки создателя лобби: {e}")
    
        logger.debug(f"start_game_lobby: лобби игры для чата {chat_key}, игроков: {len(game.players)}")
    
        # Формируем список игроков
    player_list = []
    for player in game.players.values():
        username = f"@{player.username}" if player.username else None
        display = f"{player.first_name}{f' ({username})' if username else ''}"
        player_list.append(f"• {display}")
    
    player_names = "\n".join(player_list) if player_list else "Пока никого"
    
    await callback.message.answer(
        "🎲 ЛОББИ ИГРЫ 🎲\n\n"
        f"👥 За столом собрались: {len(game.players)}/{MAX_PLAYERS} синьоров и синьорит\n"
        f"📋 Игроки:\n{player_names}\n\n"
        "📋 Минимум для начала: 1 игрок, но для настоящей игры лучше не меньше 4\n\n"
        "💼 В этом городе каждый скрывает свои намерения. Ночью мафия будет охотиться, "
        "доктор спасать, комиссар проверять, а ночная бабочка плести интриги.\n"
        "Только самые хитрые переживут рассвет.\n\n"
        "⚠️ Держись рядом со своими союзниками, но доверяй с осторожностью. "
        "Кто готов рискнуть и сыграть — жмите кнопку ниже, и пусть начнётся игра...",
        reply_markup=get_lobby_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "join_game")
async def join_game(callback: CallbackQuery):
    """Игрок присоединяется к игре"""
    # Проверяем разрешение на работу в данной теме
    if not check_topic_permission(callback.message):
        await callback.answer("⚠️ Действие разрешено только в теме «Игра в «Мафию»»!", show_alert=True)
        return
    
    chat_id = callback.message.chat.id
    thread_id = callback.message.message_thread_id or 0
    chat_key = f"{chat_id}_{thread_id}"
    user_id = callback.from_user.id
    username = callback.from_user.username or "Unknown"
    first_name = callback.from_user.first_name or "Unknown"
    
    logger.info(f"join_game: игрок {first_name} (@{callback.from_user.username}) присоединяется к игре в чате {chat_key}")
    
    if game_manager.add_player(chat_key, user_id, username, first_name):
        logger.info(f"join_game: игрок {first_name} успешно присоединился к игре в чате {chat_key}")
        game = game_manager.get_game(chat_key)
        
        # Удаляем старое сообщение лобби
        try:
            await callback.message.delete()
            logger.debug(f"join_game: удалено старое сообщение лобби")
        except TelegramBadRequest as e:
            logger.debug(f"join_game: не удалось удалить старое сообщение лобби: {e}")
        
        # Формируем обновленный список игроков
        player_list = []
        for player in game.players.values():
            username = f"@{player.username}" if player.username else None
            display = f"{player.first_name}{f' ({username})' if username else ''}"
            player_list.append(f"• {display}")
        
        player_names = "\n".join(player_list) if player_list else "Пока никого"
        
        # Создаем новое сообщение лобби с обновленным списком
        await callback.message.answer(
            f"✅ {first_name} присоединился к игре!\n\n"
            f"👥 Игроков: {len(game.players)}/{MAX_PLAYERS}\n"
            f"📋 Игроки:\n{player_names}\n\n"
            "📋 Минимум для начала: 1 игрок\n"
            "💡 Рекомендуется: минимум 4 игрока",
            reply_markup=get_lobby_keyboard()
        )
    else:
        logger.warning(f"join_game: не удалось присоединить игрока {first_name} к игре в чате {chat_key}")
        await callback.answer("Не удалось присоединиться к игре!", show_alert=True)
    
    await callback.answer()

@router.callback_query(F.data == "leave_game")
async def leave_game(callback: CallbackQuery):
    """Игрок выходит из лобби"""
    # Проверяем разрешение на работу в данной теме
    if not check_topic_permission(callback.message):
        await callback.answer("⚠️ Действие разрешено только в теме «Игра в «Мафию»»!", show_alert=True)
        return
    
    chat_id = callback.message.chat.id
    thread_id = callback.message.message_thread_id or 0
    chat_key = f"{chat_id}_{thread_id}"
    user_id = callback.from_user.id
    first_name = callback.from_user.first_name or "Unknown"
    
    logger.info(f"leave_game: игрок {first_name} выходит из игры в чате {chat_key}")
    
    if game_manager.remove_player(chat_key, user_id):
        game = game_manager.get_game(chat_key)
        # Удаляем старое сообщение лобби
        try:
            await callback.message.delete()
            logger.debug("leave_game: удалено старое сообщение лобби")
        except TelegramBadRequest as e:
            logger.debug(f"leave_game: не удалось удалить старое сообщение лобби: {e}")
        
        # Формируем обновленный список игроков
        player_list = []
        for player in game.players.values():
            username = f"@{player.username}" if player.username else None
            display = f"{player.first_name}{f' ({username})' if username else ''}"
            player_list.append(f"• {display}")
        player_names = "\n".join(player_list) if player_list else "Пока никого"
        
        await callback.message.answer(
            f"🚪 {first_name} вышел из игры.\n\n"
            f"👥 Игроков: {len(game.players)}/{MAX_PLAYERS}\n"
            f"📋 Игроки:\n{player_names}",
            reply_markup=get_lobby_keyboard()
        )
    else:
        logger.warning(f"leave_game: не удалось удалить игрока {first_name} из игры в чате {chat_key}")
        await callback.answer("Не удалось выйти из игры!", show_alert=True)
    
    await callback.answer()

@router.callback_query(F.data == "ready_to_start")
async def ready_to_start(callback: CallbackQuery):
    """Админ готов начать игру"""
    # Проверяем разрешение на работу в данной теме
    if not check_topic_permission(callback.message):
        await callback.answer("⚠️ Действие разрешено только в теме «Игра в «Мафию»»!", show_alert=True)
        return
    
    chat_key = get_chat_key(callback.message)
    
    logger.debug(
        f"ready_to_start вызван для чата {chat_key} пользователем: "
        f"{callback.from_user.first_name} (@{callback.from_user.username}) id={callback.from_user.id}"
    )
    
    if not game_manager.can_start_game(chat_key):
        logger.warning("ready_to_start: игра не может быть начата")
        await callback.answer("Не удалось начать игру! Убедитесь, что есть хотя бы 1 игрок.", show_alert=True)
        return
    
    # Разрешаем любому пользователю нажимать кнопку старта
    game = game_manager.get_game(chat_key)
    
    # Логируем, кто нажал кнопку старта
    user_info = f"{callback.from_user.first_name} (@{callback.from_user.username}) id={callback.from_user.id}"
    logger.info(f"ready_to_start: кнопка старта нажата пользователем {user_info} в чате {chat_key}")
    
    # Проверяем статус пользователя для информационных целей
    is_creator = getattr(game, "lobby_creator_id", None) == callback.from_user.id if game else False
    is_admin = False
    try:
        member = await callback.message.bot.get_chat_member(callback.message.chat.id, callback.from_user.id)
        is_admin = member.status in {"administrator", "creator"}
        logger.debug(f"ready_to_start: статус пользователя - {member.status}, is_admin: {is_admin}, is_creator: {is_creator}")
    except TelegramBadRequest as e:
        logger.debug(f"ready_to_start: не удалось проверить статус пользователя: {e}")
    
    logger.info(f"ready_to_start: запускаем игру, нажал: {user_info}")
    
    if game_manager.start_game(chat_key):
        logger.info(f"ready_to_start: игра успешно начата в чате {chat_key} с {len(game.players)} игроками")
        game = game_manager.get_game(chat_key)
        
        # Отправляем сообщение в чат
        # Выбираем случайное сообщение о начале игры
        game_start_message = random.choice(GAME_START_MESSAGES)
        full_game_start_message = (
            game_start_message + "\n\n"
            "📋 Сегодня за этим столом:\n" +
            "\n".join([
                f"• {p.first_name}{f' (@{p.username})' if p.username else ''}"
                for p in game.players.values()
            ]) +
            "\n\n🌙 Ночью:\n"
            "• Мафия решает, чью жизнь оборвётся\n"
            "• Доктор пытается спасти кого-то от смерти\n"
            "• Комиссар тайно проверяет одного игрока, чтобы найти предателя\n"
            "• Ночная бабочка спутывает планы и лишает выбранного игрока хода\n\n"
            "☀️ Днём город проснётся для обсуждений и голосования за казнь. Помните: доверие здесь — роскошь, а ошибка стоит жизни."
        )
        await callback.message.answer(full_game_start_message)

        
        # Раздаем роли в личные сообщения (одно сообщение с клавиатурой)
        not_started_dm = []
        players_to_remove = []  # Список ID игроков для удаления
        logger.info(f"ready_to_start: начинаем раздачу ролей для {len(game.players)} игроков")
        for player in game.players.values():
            try:
                role_emoji = {
                    PlayerRole.MAFIA: "😈",
                    PlayerRole.CIVILIAN: "🕊️",
                    PlayerRole.DOCTOR: "💉",
                    PlayerRole.COMMISSIONER: "👮",
                    PlayerRole.BUTTERFLY: "💃"
                }.get(player.role, "❓")
                
                role_name = {
                    PlayerRole.MAFIA: "Мафия",
                    PlayerRole.CIVILIAN: "Мирный житель",
                    PlayerRole.DOCTOR: "Доктор",
                    PlayerRole.COMMISSIONER: "Комиссар",
                    PlayerRole.BUTTERFLY: "Ночная бабочка"
                }.get(player.role, "Неизвестная роль")
                
                role_description = INSTRUCTIONS_BY_ROLE.get(player.role, "Неизвестная роль")

                # Готовим один текст роли и в том же сообщении прикладываем нужную клавиатуру
                base_text = (
                    f"🎭 Твоя роль — {role_emoji} {role_name}\n\n"
                    f"{role_description}"
                )
                if not getattr(player, "role_info_sent", False):
                    if player.role == PlayerRole.MAFIA:
                        await callback.bot.send_message(
                            player.user_id,
                            base_text + "\n\n😈 Выберите жертву:",
                            reply_markup=get_player_selection_keyboard(list(game.players.values()), "mafia_kill", chat_key)
                        )
                        # Сразу после раздачи ролей сообщим мафии о сообщниках
                        try:
                            peers = game_manager.get_mafia_peers(chat_key, exclude_user_id=player.user_id)
                            if peers:
                                mafia_list = ", ".join([f"@{p.username}" if p.username else p.first_name for p in peers])
                                await callback.bot.send_message(
                                    player.user_id,
                                    f"🤫 Твои сообщники: {mafia_list}. Можете обсуждать прямо здесь в ЛС — я передам им твои сообщения."
                                )
                        except Exception as e:
                            logger.exception(f"не удалось отправить список сообщников мафии игроку {player.user_id}: {e}")
                    elif player.role == PlayerRole.DOCTOR:
                        await callback.bot.send_message(
                            player.user_id,
                            base_text + "\n\n💉 Выберите, кого лечить:",
                            reply_markup=get_player_selection_keyboard(list(game.players.values()), "doctor_save", chat_key)
                        )
                    elif player.role == PlayerRole.COMMISSIONER:
                        await callback.bot.send_message(
                            player.user_id,
                            base_text + "\n\n👮 Выберите, кого проверить:",
                            reply_markup=get_player_selection_keyboard(list(game.players.values()), "commissioner_check", chat_key)
                        )
                    elif player.role == PlayerRole.BUTTERFLY:
                        await callback.bot.send_message(
                            player.user_id,
                            base_text + "\n\n💃 Выберите, кого отвлечь:",
                            reply_markup=get_player_selection_keyboard(list(game.players.values()), "butterfly_distract", chat_key)
                        )
                    else:
                        # Мирному просто отправляем роль без клавиатуры
                        await callback.bot.send_message(player.user_id, base_text)
                    player.role_info_sent = True
                
            except TelegramForbiddenError as e:
                # Пользователь не начал диалог с ботом
                uname = f"@{player.username}" if player.username else None
                disp = f"{player.first_name}{f' ({uname})' if uname else ''}"
                not_started_dm.append(disp)
                players_to_remove.append(player.user_id)  # Добавляем в список для удаления
                logger.warning(f"Роль не доставлена игроку {player.user_id} (нет /start): {e}")
            except Exception as e:
                logger.exception(f"Ошибка отправки роли игроку {player.user_id}: {e}")

        # Удаляем игроков, которые не начали диалог с ботом
        if players_to_remove:
            game_manager.remove_players_without_start(chat_key, players_to_remove)
            logger.info(f"ready_to_start: удалено {len(players_to_remove)} игроков без /start из игры")

        # Мы уже разослали ночные клавиатуры в составе первого сообщения —
        # не даём автопилоту отправлять их повторно в эту ночь
        try:
            game.night_prompts_sent = True
        except Exception:
            pass
        
        logger.info(f"ready_to_start: раздача ролей завершена, запускаем автопилот для чата {chat_key}")
        
        # Сообщим в общий чат, кто не активировал ЛС с ботом
        if not_started_dm:
            # Выбираем случайное сообщение о неактивированных ЛС
            no_dm_message = random.choice(NO_DM_MESSAGES)
            players_list = "\n".join([f"• {name}" for name in not_started_dm])
            full_message = no_dm_message.format(players=players_list) + "\n\nОни были удалены из игры. Напишите боту в ЛС команду /start, чтобы участвовать в следующих играх."
            await callback.message.answer(full_message)
        
        # Запускаем автопилот цикла фаз
        if _autopilot_tasks.get(chat_key) and not _autopilot_tasks[chat_key].done():
            _autopilot_tasks[chat_key].cancel()
        _autopilot_tasks[chat_key] = asyncio.create_task(_autopilot_loop(chat_key, callback.bot))
        logger.info(f"ready_to_start: автопилот запущен для чата {chat_key}")
    else:
        logger.warning("ready_to_start: start_game вернул False")
        await callback.answer("Не удалось начать игру!", show_alert=True)
    
    await callback.answer()

@router.callback_query(F.data == "cancel_game")
async def cancel_game(callback: CallbackQuery):
    """Отменяет игру"""
    # Проверяем разрешение на работу в данной теме
    if not check_topic_permission(callback.message):
        await callback.answer("⚠️ Действие разрешено только в теме «Игра в «Мафию»»!", show_alert=True)
        return
    
    chat_key = get_chat_key(callback.message)
    
    logger.debug(
        f"cancel_game вызван для чата {chat_key} пользователем: "
        f"{callback.from_user.first_name} (@{callback.from_user.username}) id={callback.from_user.id}"
    )
    
    # Логируем, кто нажал кнопку отмены
    user_info = f"{callback.from_user.first_name} (@{callback.from_user.username}) id={callback.from_user.id}"
    logger.info(f"cancel_game: кнопка отмены нажата пользователем {user_info} в чате {chat_key}")
    
    # Останавливаем автопилот если есть
    if _autopilot_tasks.get(chat_key) and not _autopilot_tasks[chat_key].done():
        _autopilot_tasks[chat_key].cancel()
        del _autopilot_tasks[chat_key]
    
    # Завершаем игру
    game_manager.end_game(chat_key)
    
    await callback.message.answer(
        "❌ Игра отменена.\n\n"
        "Все данные очищены. Можете начать новую игру.",
        reply_markup=get_new_game_keyboard()
    )
    
    logger.info(f"cancel_game: игра отменена в чате {chat_key} пользователем {user_info}")
    await callback.answer()

@router.callback_query(F.data.startswith("mafia_kill:"))
async def mafia_kill_action(callback: CallbackQuery):
    """Мафия выбирает жертву"""
    # Проверяем разрешение на работу в данной теме (для личных сообщений пропускаем)
    if callback.message.chat.type != "private" and not check_topic_permission(callback.message):
        await callback.answer("⚠️ Действие разрешено только в теме «Игра в «Мафию»»!", show_alert=True)
        return
    
    user_id = callback.from_user.id
    parts = callback.data.split(":")
    group_chat_key = parts[1]  # Теперь это chat_key, а не chat_id
    target_id = int(parts[2])
    logger.debug(f"mafia_kill_action: чат {group_chat_key}, пользователь {user_id}, цель {target_id}")
    
    # Проверяем, что игра существует и в ночной фазе
    game = game_manager.get_game(group_chat_key)
    if not game:
        await callback.answer("❌ Игра не найдена!", show_alert=True)
        return
    
    if game.phase != GamePhase.NIGHT:
        await callback.answer("❌ Сейчас не ночь!", show_alert=True)
        return
    
    # Проверяем, что игрок жив и имеет роль мафии
    player = game.players.get(user_id)
    if not player or not player.is_alive:
        await callback.answer("❌ Вы мертвы или не участвуете в игре!", show_alert=True)
        return
    
    if player.role != PlayerRole.MAFIA:
        await callback.answer("❌ У вас нет роли мафии!", show_alert=True)
        return
    
    # Блокируем действие, если игрок отвлечен бабочкой
    if game.butterfly_distract_target is not None and user_id == game.butterfly_distract_target:
        await callback.answer("Вы отвлечены ночной бабочкой и не можете действовать этой ночью.", show_alert=True)
        return
        
    if game_manager.process_night_action(group_chat_key, user_id, "mafia_kill", target_id):
        target = game_manager.get_game(group_chat_key).players.get(target_id)
        target_mention = f"@{target.username}" if target and target.username else (target.first_name if target else "игрок")
        await callback.bot.send_message(user_id, f"✅ Жертва выбрана: {target_mention}")
        
        # Проверяем, завершены ли все ночные действия (отправляем сообщение только один раз)
        game = game_manager.get_game(group_chat_key)
        if game and game_manager.all_night_actions_completed(group_chat_key) and not game.all_actions_notified:
            game.all_actions_notified = True
            await callback.bot.send_message(
                get_chat_id_from_key(group_chat_key),
                "🌙 Все ночные действия получены. Ночь продолжается до рассвета.",
                message_thread_id=get_thread_id_from_key(group_chat_key)
            )
    else:
        logger.warning("process_night_action вернул False для мафии")
        await callback.answer("❌ Не удалось выполнить действие! Проверьте, что игра в ночной фазе и вы живы.", show_alert=True)
    
    await callback.answer()

@router.callback_query(F.data.startswith("doctor_save:"))
async def doctor_save_action(callback: CallbackQuery):
    """Доктор выбирает, кого лечить"""
    # Проверяем разрешение на работу в данной теме (для личных сообщений пропускаем)
    if callback.message.chat.type != "private" and not check_topic_permission(callback.message):
        await callback.answer("⚠️ Действие разрешено только в теме «Игра в «Мафию»»!", show_alert=True)
        return
    
    user_id = callback.from_user.id
    logger.debug(f"doctor_save_action: callback.data: {callback.data}")
    parts = callback.data.split(":")
    group_chat_key = parts[1]
    target_id = None if parts[2] == "skip" else int(parts[2])
    logger.debug(f"doctor_save_action: process_night_action params: chat_key={group_chat_key}, user_id={user_id}, action_type='doctor_save', target_id={target_id}")

    # Проверяем, что игра существует и в ночной фазе
    game = game_manager.get_game(group_chat_key)
    if not game:
        await callback.answer("❌ Игра не найдена!", show_alert=True)
        return
    
    if game.phase != GamePhase.NIGHT:
        await callback.answer("❌ Сейчас не ночь!", show_alert=True)
        return
    
    # Проверяем, что игрок жив и имеет роль доктора
    player = game.players.get(user_id)
    if not player or not player.is_alive:
        await callback.answer("❌ Вы мертвы или не участвуете в игре!", show_alert=True)
        return
    
    if player.role != PlayerRole.DOCTOR:
        await callback.answer("❌ У вас нет роли доктора!", show_alert=True)
        return
    
    if game.butterfly_distract_target is not None and user_id == game.butterfly_distract_target:
        await callback.answer("Вы отвлечены ночной бабочкой и не можете лечить этой ночью.", show_alert=True)
        return
        
    if game_manager.process_night_action(group_chat_key, user_id, "doctor_save", target_id):
        if target_id:
            target_player = game_manager.get_game(group_chat_key).players.get(target_id)
            mention = f"@{target_player.username}" if target_player and target_player.username else (target_player.first_name if target_player else "игрок")
            await callback.bot.send_message(user_id, f"✅ Вы решили лечить {mention}! Возможно, вы спасёте его от смерти.")
        else:
            await callback.bot.send_message(user_id, "✅ Вы решили никого не лечить!")
        
        # Проверяем, завершены ли все ночные действия (отправляем сообщение только один раз)
        game = game_manager.get_game(group_chat_key)
        if game and game_manager.all_night_actions_completed(group_chat_key) and not game.all_actions_notified:
            game.all_actions_notified = True
            await callback.bot.send_message(
                get_chat_id_from_key(group_chat_key),
                "🌙 Все ночные действия получены. Ночь продолжается до рассвета.",
                message_thread_id=get_thread_id_from_key(group_chat_key)
            )
    else:
        logger.warning("process_night_action вернул False для доктора")
        await callback.answer("❌ Не удалось выполнить действие! Проверьте, что игра в ночной фазе и вы живы.", show_alert=True)
    
    await callback.answer()

@router.callback_query(F.data.startswith("commissioner_check:"))
async def commissioner_check_action(callback: CallbackQuery):
    """Комиссар проверяет игрока"""
    # Проверяем разрешение на работу в данной теме (для личных сообщений пропускаем)
    if callback.message.chat.type != "private" and not check_topic_permission(callback.message):
        await callback.answer("⚠️ Действие разрешено только в теме «Игра в «Мафию»»!", show_alert=True)
        return
    
    user_id = callback.from_user.id
    parts = callback.data.split(":")
    group_chat_key = parts[1]
    target_id = int(parts[2])
    logger.debug(f"commissioner_check_action: чат {group_chat_key}, пользователь {user_id}, цель {target_id}")

    # Проверяем, что игра существует и в ночной фазе
    game = game_manager.get_game(group_chat_key)
    if not game:
        await callback.answer("❌ Игра не найдена!", show_alert=True)
        return
    
    if game.phase != GamePhase.NIGHT:
        await callback.answer("❌ Сейчас не ночь!", show_alert=True)
        return
    
    # Проверяем, что игрок жив и имеет роль комиссара
    player = game.players.get(user_id)
    if not player or not player.is_alive:
        await callback.answer("❌ Вы мертвы или не участвуете в игре!", show_alert=True)
        return
    
    if player.role != PlayerRole.COMMISSIONER:
        await callback.answer("❌ У вас нет роли комиссара!", show_alert=True)
        return
    
    if game.butterfly_distract_target is not None and user_id == game.butterfly_distract_target:
        await callback.answer("Вы отвлечены ночной бабочкой и не можете проверять этой ночью.", show_alert=True)
        return
        
    if game_manager.process_night_action(group_chat_key, user_id, "commissioner_check", target_id):
        game = game_manager.get_game(group_chat_key)
        target_player = game.players.get(target_id)
        
        # Не показываем результат проверки сразу - только подтверждение действия
        checked_mention = f"@{target_player.username}" if target_player and target_player.username else (target_player.first_name if target_player else "игрок")
        await callback.bot.send_message(user_id, f"✅ Вы проверили {checked_mention}. Результат будет объявлен утром.")
        
        # Проверяем, завершены ли все ночные действия (отправляем сообщение только один раз)
        game = game_manager.get_game(group_chat_key)
        if game and game_manager.all_night_actions_completed(group_chat_key) and not game.all_actions_notified:
            game.all_actions_notified = True
            await callback.bot.send_message(
                get_chat_id_from_key(group_chat_key),
                "🌙 Все ночные действия получены. Ночь продолжается до рассвета.",
                message_thread_id=get_thread_id_from_key(group_chat_key)
            )
    else:
        logger.warning("process_night_action вернул False для комиссара")
        await callback.answer("❌ Не удалось выполнить действие! Проверьте, что игра в ночной фазе и вы живы.", show_alert=True)
    
    await callback.answer()

@router.callback_query(F.data.startswith("butterfly_distract:"))
async def butterfly_distract_action(callback: CallbackQuery):
    """Ночная бабочка отвлекает игрока"""
    # Проверяем разрешение на работу в данной теме (для личных сообщений пропускаем)
    if callback.message.chat.type != "private" and not check_topic_permission(callback.message):
        await callback.answer("⚠️ Действие разрешено только в теме «Игра в «Мафию»»!", show_alert=True)
        return
    
    user_id = callback.from_user.id
    logger.debug(f"butterfly_distract_action: callback.data: {callback.data}")
    parts = callback.data.split(":")
    group_chat_key = parts[1]
    target_id = None if parts[2] == "skip" else int(parts[2])

    # Проверяем, что игра существует и в ночной фазе
    game = game_manager.get_game(group_chat_key)
    if not game:
        await callback.answer("❌ Игра не найдена!", show_alert=True)
        return
    
    if game.phase != GamePhase.NIGHT:
        await callback.answer("❌ Сейчас не ночь!", show_alert=True)
        return
    
    # Проверяем, что игрок жив и имеет роль ночной бабочки
    player = game.players.get(user_id)
    if not player or not player.is_alive:
        await callback.answer("❌ Вы мертвы или не участвуете в игре!", show_alert=True)
        return
    
    if player.role != PlayerRole.BUTTERFLY:
        await callback.answer("❌ У вас нет роли ночной бабочки!", show_alert=True)
        return

    if game_manager.process_night_action(group_chat_key, user_id, "butterfly_distract", target_id):
        if target_id:
            target_player = game_manager.get_game(group_chat_key).players.get(target_id)
            mention = f"@{target_player.username}" if target_player and target_player.username else (target_player.first_name if target_player else "игрок")
            await callback.bot.send_message(user_id, f"✅ Вы отвлекли {mention}! У него была бурная ночь.")
        else:
            await callback.bot.send_message(user_id, "✅ Вы решили никого не отвлекать!")
        
        # Проверяем, завершены ли все ночные действия (отправляем сообщение только один раз)
        game = game_manager.get_game(group_chat_key)
        if game and game_manager.all_night_actions_completed(group_chat_key) and not game.all_actions_notified:
            game.all_actions_notified = True
            await callback.bot.send_message(
                get_chat_id_from_key(group_chat_key),
                "🌙 Все ночные действия получены. Ночь продолжается до рассвета.",
                message_thread_id=get_thread_id_from_key(group_chat_key)
            )
    else:
        logger.warning("process_night_action вернул False для ночной бабочки")
        await callback.answer("❌ Не удалось выполнить действие! Проверьте, что игра в ночной фазе и вы живы.", show_alert=True)
    
    await callback.answer()

@router.callback_query(F.data.startswith("vote_"))
async def process_vote(callback: CallbackQuery):
    """Обрабатывает голос игрока"""
    # Проверяем разрешение на работу в данной теме
    if not check_topic_permission(callback.message):
        await callback.answer("⚠️ Действие разрешено только в теме «Игра в «Мафию»»!", show_alert=True)
        return
    
    chat_key = get_chat_key(callback.message)
    user_id = callback.from_user.id
    
    if callback.data == "vote_skip":
        logger.debug(f"process_vote: игрок {user_id} пропускает голос в чате {chat_key}")
        game = game_manager.get_game(chat_key)
        if not game or game.phase != GamePhase.VOTING:
            try:
                await callback.answer("❌ Сейчас не идёт голосование.", show_alert=True)
            except TelegramBadRequest:
                pass
            return
        voter = game.players.get(user_id)
        if not voter or not voter.is_alive:
            try:
                await callback.answer("❌ Голосовать могут только живые игроки.", show_alert=True)
            except TelegramBadRequest:
                pass
            return
        if voter.has_voted:
            try:
                await callback.answer("❌ Вы уже сделали выбор в этом голосовании.", show_alert=True)
            except TelegramBadRequest:
                pass
            return
        # Отмечаем пропуск голоса
        try:
            game.skipped_voters.add(user_id)
        except Exception:
            pass
        voter.has_voted = True
        # Пересобираем табло
        vote_counts = {}
        for tid in game.votes.values():
            vote_counts[tid] = vote_counts.get(tid, 0) + 1
        lines = ["🗳️ Текущие голоса:"]
        for p in game.get_alive_players():
            cnt = vote_counts.get(p.user_id, 0)
            uname = f"@{p.username}" if p.username else None
            disp = f"{p.first_name}{f' ({uname})' if uname else ''}"
            lines.append(f"- {disp}: {cnt}")
        # Отдельный блок — кто пропустил голос
        if game.skipped_voters:
            skipped_lines = []
            for uid in sorted(game.skipped_voters):
                pl = game.players.get(uid)
                if pl:
                    uname = f"@{pl.username}" if pl.username else None
                    disp = f"{pl.first_name}{f' ({uname})' if uname else ''}"
                    skipped_lines.append(f"• {disp}")
            if skipped_lines:
                lines.append("\n🚫 Пропустили голос:")
                lines.extend(skipped_lines)
        scoreboard = "\n".join(lines)
        # Удаляем предыдущее табло
        try:
            if getattr(game, "current_voting_message_id", None):
                await callback.message.bot.delete_message(chat_id=callback.message.chat.id, message_id=game.current_voting_message_id)
        except TelegramBadRequest as e:
            logger.debug(f"process_vote(skip): не удалось удалить предыдущее табло: {e}")
        # Переголосование отключено: клавиатура строится по всем живым
        alive_for_keyboard = game.get_alive_players()
        sent = await callback.message.answer(
            f"✅ Голос пропущен!\n\n" + scoreboard,
            reply_markup=get_voting_keyboard(alive_for_keyboard)
        )
        try:
            game.current_voting_message_id = sent.message_id
        except Exception:
            pass
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass
        return
    
    target_id = int(callback.data.split("_")[1])
    
    logger.debug(f"process_vote: попытка обработать голос игрока {user_id} за {target_id} в чате {chat_key}")
    
    # Проверяем, что игра существует и в фазе голосования
    game = game_manager.get_game(chat_key)
    if not game:
        await callback.answer("❌ Игра не найдена!", show_alert=True)
        return
    
    if game.phase != GamePhase.VOTING:
        await callback.answer("❌ Сейчас не идёт голосование!", show_alert=True)
        return
    
    # Проверяем, что игрок жив и не голосовал
    voter = game.players.get(user_id)
    if not voter or not voter.is_alive:
        await callback.answer("❌ Голосовать могут только живые игроки!", show_alert=True)
        return
    
    if voter.has_voted:
        await callback.answer("❌ Вы уже сделали выбор в этом голосовании!", show_alert=True)
        return
    
    if game_manager.process_vote(chat_key, user_id, target_id):
        logger.debug("process_vote: голос обработан успешно")
        
        # Обновляем табло голосования: пересобираем список и показываем счет
        game = game_manager.get_game(chat_key)
        target_player = game.players.get(target_id)
        # Считаем текущие голоса
        vote_counts = {}
        for tid in game.votes.values():
            vote_counts[tid] = vote_counts.get(tid, 0) + 1
        # Формируем строку со счетом
        lines = ["🗳️ Текущие голоса:"]
        for p in game.get_alive_players():
            cnt = vote_counts.get(p.user_id, 0)
            uname = f"@{p.username}" if p.username else None
            disp = f"{p.first_name}{f' ({uname})' if uname else ''}"
            lines.append(f"- {disp}: {cnt}")
        # Отдельный блок — кто пропустил голос
        if getattr(game, "skipped_voters", None) and game.skipped_voters:
            skipped_lines = []
            for uid in sorted(game.skipped_voters):
                pl = game.players.get(uid)
                if pl:
                    uname = f"@{pl.username}" if pl.username else None
                    disp = f"{pl.first_name}{f' ({uname})' if uname else ''}"
                    skipped_lines.append(f"• {disp}")
            if skipped_lines:
                lines.append("\n🚫 Пропустили голос:")
                lines.extend(skipped_lines)
        scoreboard = "\n".join(lines)
        
        # Удаляем предыдущее сообщение с голосованием
        try:
            if getattr(game, "current_voting_message_id", None):
                await callback.message.bot.delete_message(chat_id=callback.message.chat.id, message_id=game.current_voting_message_id)
        except TelegramBadRequest as e:
            logger.debug(f"process_vote: не удалось удалить предыдущее сообщение голосования: {e}")
        
        # Переголосование отключено: клавиатура строится по всем живым
        alive_for_keyboard = game.get_alive_players()
        
        # Отправляем новое сообщение с обновленным табло
        target_mention = f"@{target_player.username}" if target_player and target_player.username else (target_player.first_name if target_player else "игрок")
        sent = await callback.message.answer(
            f"✅ Голос за {target_mention} учтён!\n\n" + scoreboard,
            reply_markup=get_voting_keyboard(alive_for_keyboard)
        )
        try:
            game.current_voting_message_id = sent.message_id
        except Exception:
            pass
    else:
        logger.warning("process_vote: голос не обработан")
        try:
            await callback.answer("❌ Ошибка! Возможно, вы уже голосовали или игра не в фазе голосования.", show_alert=True)
        except TelegramBadRequest:
            pass
        return

    try:
        await callback.answer()
    except TelegramBadRequest:
        pass

@router.callback_query(F.data == "back_to_main")
async def back_to_main_menu(callback: CallbackQuery):
    """Возврат в главное меню"""
    # Выбираем случайное приветствие из массива
    greeting = random.choice(DON_VITTE_GREETINGS)
    
    await callback.message.answer(
        greeting,
        reply_markup=get_main_menu_keyboard()
    )
    await callback.answer()
