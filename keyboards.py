from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from typing import List, Optional, Set
import logging
from models import Player

logger = logging.getLogger(__name__)

def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Главное меню игры"""
    logger.debug("get_main_menu_keyboard: создание главного меню")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📜 Правила", callback_data="rules")],
        [InlineKeyboardButton(text="🎲 Начать игру", callback_data="start_game")],
        [InlineKeyboardButton(text="🧪 Тестовая игра", callback_data="test_game")],
        [InlineKeyboardButton(text="❓ Как играть", callback_data="how_to_play")],
        [InlineKeyboardButton(text="❌ Отмена игры", callback_data="cancel_game")]
    ])
    
    logger.debug("get_main_menu_keyboard: создано главное меню")
    return keyboard

def get_back_keyboard() -> InlineKeyboardMarkup:
    """Кнопка возврата"""
    logger.debug("get_back_keyboard: создание кнопки возврата")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
    ])
    
    logger.debug("get_back_keyboard: создана кнопка возврата")
    return keyboard

def get_new_game_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для создания новой игры после завершения"""
    logger.debug("get_new_game_keyboard: создание клавиатуры новой игры")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 Создать новую игру", callback_data="start_game")],
        [InlineKeyboardButton(text="⬅️ В главное меню", callback_data="back_to_main")]
    ])
    
    logger.debug("get_new_game_keyboard: создана клавиатура новой игры")
    return keyboard

def get_test_game_control_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для управления тестовой игрой"""
    logger.debug("get_test_game_control_keyboard: создание клавиатуры управления тестовой игрой")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Запустить тест", callback_data="start_test_game")],
        [InlineKeyboardButton(text="⏸️ Пауза теста", callback_data="pause_test_game")],
        [InlineKeyboardButton(text="🔄 Сбросить тест", callback_data="reset_test_game")],
        [InlineKeyboardButton(text="⏹️ Остановить тест", callback_data="stop_test_game")],
        [InlineKeyboardButton(text="⬅️ В главное меню", callback_data="back_to_main")]
    ])
    
    logger.debug("get_test_game_control_keyboard: создана клавиатура управления тестовой игрой")
    return keyboard

def get_lobby_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура лобби"""
    logger.debug("get_lobby_keyboard: создание клавиатуры лобби")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Присоединиться к игре", callback_data="join_game")],
        [InlineKeyboardButton(text="✅ Готово, раздать роли", callback_data="ready_to_start")],
        [InlineKeyboardButton(text="🚪 Выйти из игры", callback_data="leave_game")],
        [InlineKeyboardButton(text="❌ Отмена игры", callback_data="cancel_game")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
    ])
    
    logger.debug("get_lobby_keyboard: создана клавиатура лобби")
    return keyboard

def get_player_selection_keyboard(
    players: List[Player],
    action_type: str,
    group_chat_key: str,
    exclude_user_id: Optional[int] = None,
    exclude_target_ids: Optional[Set[int]] = None,
) -> InlineKeyboardMarkup:
    """Клавиатура для выбора игрока (ночные действия)
    callback_data формат: {action_type}:{group_chat_key}:{target_id|skip}
    exclude_user_id — не показывать этого игрока (нельзя выбирать себя)
    """
    logger.debug(f"get_player_selection_keyboard: создание клавиатуры для действия {action_type}, игроков: {len(players)}, group_chat_key: {group_chat_key}, exclude: {exclude_user_id}")

    buttons = []
    excluded = exclude_target_ids or set()
    for player in players:
        # Показываем только живых игроков, исключая себя и заблокированные цели
        if (player.is_alive 
            and (exclude_user_id is None or player.user_id != exclude_user_id) 
            and (player.user_id not in excluded)):
            username = f"@{player.username}" if getattr(player, 'username', None) else None
            label = f"{player.first_name}{f' ({username})' if username else ''}"
            buttons.append([
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"{action_type}:{group_chat_key}:{player.user_id}"
                )
            ])
            logger.debug(f"get_player_selection_keyboard: добавлена кнопка для игрока {player.first_name} (ID: {player.user_id})")

    # Добавляем кнопку "Пропустить" для некоторых действий
    if action_type in ["doctor_save", "butterfly_distract"]:
        buttons.append([InlineKeyboardButton(text="🚫 Пропустить", callback_data=f"{action_type}:{group_chat_key}:skip")])
        logger.debug(f"get_player_selection_keyboard: добавлена кнопка 'Пропустить' для действия {action_type}")

    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    logger.debug(f"get_player_selection_keyboard: создана клавиатура с {len(buttons)} кнопками")

    return keyboard

def get_voting_keyboard(players: List[Player]) -> InlineKeyboardMarkup:
    """Клавиатура для голосования"""
    logger.debug(f"get_voting_keyboard: создание клавиатуры для голосования, игроков: {len(players)}")
    
    buttons = []
    for player in players:
        if player.is_alive:
            username = f"@{player.username}" if getattr(player, 'username', None) else None
            label = f"{player.first_name}{f' ({username})' if username else ''}"
            buttons.append([
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"vote_{player.user_id}"
                )
            ])
            logger.debug(f"get_voting_keyboard: добавлена кнопка для игрока {player.first_name} (ID: {player.user_id})")
    
    # Добавляем кнопку пропуска голоса
    buttons.append([InlineKeyboardButton(text="🚫 Пропустить голос", callback_data="vote_skip")])
    logger.debug("get_voting_keyboard: добавлена кнопка 'Пропустить голос'")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    logger.debug(f"get_voting_keyboard: создана клавиатура с {len(buttons)} кнопками")
    
    return keyboard

def get_game_control_keyboard() -> InlineKeyboardMarkup:
    """Минимальная клавиатура управления (только завершение игры)"""
    logger.debug("get_game_control_keyboard: создание минимальной клавиатуры управления")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Завершить игру", callback_data="end_game")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
    ])
    logger.debug("get_game_control_keyboard: создана минимальная клавиатура управления")
    return keyboard

