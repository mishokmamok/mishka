from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
from enum import Enum
import logging

logger = logging.getLogger(__name__)

class GamePhase(Enum):
    LOBBY = "lobby"
    NIGHT = "night"
    DAY = "day"
    VOTING = "voting"
    ENDED = "ended"

class PlayerRole(Enum):
    MAFIA = "мафия"
    CIVILIAN = "мирный"
    DOCTOR = "доктор"
    COMMISSIONER = "комиссар"
    BUTTERFLY = "ночная_бабочка"

@dataclass
class Player:
    user_id: int
    username: str
    first_name: str
    role: Optional[PlayerRole] = None
    is_alive: bool = True
    is_connected: bool = True
    night_action_target: Optional[int] = None
    has_voted: bool = False
    vote_target: Optional[int] = None
    role_info_sent: bool = False
    # Для доктора: может один раз за игру лечить себя
    doctor_self_save_used: bool = False

@dataclass
class GameState:
    chat_id: str  # Теперь это chat_key
    # Кто создал лобби: только он (или администраторы чата) могут раздать роли
    lobby_creator_id: Optional[int] = None
    phase: GamePhase = GamePhase.LOBBY
    players: Dict[int, Player] = field(default_factory=dict)
    current_round: int = 0
    night_kill_target: Optional[int] = None
    # Ночные действия (мульти-роли поддерживаются)
    doctor_saves: Dict[int, Optional[int]] = field(default_factory=dict)  # doctor_id -> target_id|None
    butterfly_distract_target: Optional[int] = None
    commissioner_checks: Dict[int, int] = field(default_factory=dict)  # commissioner_id -> target_id
    commissioner_check_results: Dict[int, bool] = field(default_factory=dict)  # commissioner_id -> is_mafia
    votes: Dict[int, int] = field(default_factory=dict)  # player_id -> target_id
    mafia_votes: Dict[int, int] = field(default_factory=dict)  # mafia_player_id -> target_id
    game_started: bool = False
    night_actions_completed: Set[str] = field(default_factory=set)
    # Для дневных объявлений: сохраняем итоги прошлой ночи
    last_doctor_save_targets: List[int] = field(default_factory=list)
    last_butterfly_distract_target: Optional[int] = None
    last_commissioner_checks: List[tuple] = field(default_factory=list)  # (commissioner_id, target_id, is_mafia)
    # Чтобы не дублировать ночные клавиатуры
    night_prompts_sent: bool = False
    # Ограничение врача: нельзя лечить одного и того же игрока две ночи подряд (кроме одноразового самолечения)
    doctor_last_save_target: Dict[int, Optional[int]] = field(default_factory=dict)  # doctor_id -> last non-None target_id
    # Флаг для предотвращения дублирования сообщения "все действия получены"
    all_actions_notified: bool = False
    # ID текущего сообщения с голосованием/табло, чтобы удалять старое
    current_voting_message_id: Optional[int] = None
    # Множество игроков, которые выбрали "пропустить голос"
    skipped_voters: Set[int] = field(default_factory=set)
    # Флаг: первое дневное голосование пропущено (после самой первой ночи)
    first_voting_skipped: bool = False
    # Флаг: идёт переголосование (после ничьей)
    revote_active: bool = False
    # Кандидаты переголосования (IDs игроков с равным числом голосов)
    revote_candidates: Set[int] = field(default_factory=set)
    # Игроки, которых уже отвлекала ночная бабочка (нельзя отвлекать повторно)
    butterfly_distracted_players: Set[int] = field(default_factory=set)
    # Флаг тестовой игры
    is_test_game: bool = False
    
    def get_alive_players(self) -> List[Player]:
        alive_players = [p for p in self.players.values() if p.is_alive]
        logger.debug(f"get_alive_players: найдено {len(alive_players)} живых игроков из {len(self.players)}")
        return alive_players
    
    def get_players_by_role(self, role: PlayerRole) -> List[Player]:
        players_with_role = [p for p in self.players.values() if p.role == role and p.is_alive]
        logger.debug(f"get_players_by_role: найдено {len(players_with_role)} игроков с ролью {role}")
        return players_with_role
    
    def count_alive_by_role(self, role: PlayerRole) -> int:
        count = len(self.get_players_by_role(role))
        logger.debug(f"count_alive_by_role: количество живых игроков с ролью {role}: {count}")
        return count
    
    def is_game_over(self) -> tuple[bool, Optional[str]]:
        alive_mafia = self.count_alive_by_role(PlayerRole.MAFIA)
        alive_civilians = self.count_alive_by_role(PlayerRole.CIVILIAN)
        alive_doctors = self.count_alive_by_role(PlayerRole.DOCTOR)
        alive_commissioners = self.count_alive_by_role(PlayerRole.COMMISSIONER)
        alive_butterflies = self.count_alive_by_role(PlayerRole.BUTTERFLY)
        
        total_civilians = alive_civilians + alive_doctors + alive_commissioners + alive_butterflies
        
        logger.debug(f"is_game_over: мафия: {alive_mafia}, мирные: {total_civilians}")
        
        # Мафия побеждает, если их количество больше или равно мирных
        if alive_mafia >= total_civilians:
            logger.debug(f"is_game_over: мафия побеждает")
            return True, "mafia"
        
        # Мирные побеждают, если вся мафия убита
        if alive_mafia == 0:
            logger.debug(f"is_game_over: мирные побеждают")
            return True, "civilians"
        
        logger.debug(f"is_game_over: игра продолжается")
        return False, ""



