import random
from typing import Dict, List, Tuple, Optional
import asyncio
import logging
from models import GameState, Player, PlayerRole, GamePhase
from config import ROLE_DISTRIBUTION, MIN_PLAYERS, MAX_PLAYERS

logger = logging.getLogger(__name__)

class GameManager:
    def __init__(self):
        self.active_games: Dict[str, GameState] = {}
        # user_id мафии -> chat_key игры
        self.mafia_user_to_chat_key: Dict[int, str] = {}

    def _refresh_mafia_mapping(self, chat_key: str) -> None:
        game = self.get_game(chat_key)
        if not game:
            return
        # Удаляем старые записи для этого чата
        to_delete = [uid for uid, ck in self.mafia_user_to_chat_key.items() if ck == chat_key]
        for uid in to_delete:
            del self.mafia_user_to_chat_key[uid]
        # Регистрируем живых мафий
        for p in game.get_players_by_role(PlayerRole.MAFIA):
            self.mafia_user_to_chat_key[p.user_id] = chat_key

    def get_chat_id_for_mafia_user(self, user_id: int) -> Optional[int]:
        chat_key = self.mafia_user_to_chat_key.get(user_id)
        if chat_key:
            # Извлекаем chat_id из chat_key (формат: "chat_id_thread_id")
            try:
                return int(chat_key.split('_')[0])
            except (ValueError, IndexError):
                return None
        return None

    def get_chat_key_for_mafia_user(self, user_id: int) -> Optional[str]:
        return self.mafia_user_to_chat_key.get(user_id)

    def get_mafia_peers(self, chat_key: str, exclude_user_id: int) -> List[Player]:
        game = self.get_game(chat_key)
        if not game:
            return []
        return [p for p in game.get_players_by_role(PlayerRole.MAFIA) if p.user_id != exclude_user_id]
    
    def create_game(self, chat_key: str) -> GameState:
        """Создает новую игру"""
        logger.debug(f"create_game: попытка создать игру для чата {chat_key}")
        
        if chat_key in self.active_games:
            logger.debug(f"create_game: игра для чата {chat_key} уже существует")
            return self.active_games[chat_key]
        
        game = GameState(chat_id=chat_key)
        self.active_games[chat_key] = game
        logger.info(f"create_game: создана новая игра для чата {chat_key}")
        return game
    
    def create_test_game(self, chat_key: str) -> GameState:
        """Создает тестовую игру с 10 виртуальными игроками"""
        logger.debug(f"create_test_game: попытка создать тестовую игру для чата {chat_key}")
        
        # Всегда удаляем существующую игру перед созданием тестовой
        if chat_key in self.active_games:
            logger.info(f"create_test_game: удаляем существующую игру для чата {chat_key}")
            del self.active_games[chat_key]
        
        logger.info(f"create_test_game: создание новой тестовой игры для чата {chat_key}")
        game = GameState(chat_id=chat_key, is_test_game=True)
        
        logger.debug(f"create_test_game: создан объект GameState, is_test_game: {game.is_test_game}")
        logger.debug(f"create_test_game: изначально игроков в game.players: {len(game.players)}")
        
        # Создаем 10 виртуальных игроков с разными ролями
        test_players = [
            # Мафия (2 игрока)
            {"name": "Тестовый Мафия 1", "role": PlayerRole.MAFIA},
            {"name": "Тестовый Мафия 2", "role": PlayerRole.MAFIA},
            # Доктор
            {"name": "Тестовый Доктор", "role": PlayerRole.DOCTOR},
            # Комиссар
            {"name": "Тестовый Комиссар", "role": PlayerRole.COMMISSIONER},
            # Ночная бабочка
            {"name": "Тестовая Бабочка", "role": PlayerRole.BUTTERFLY},
            # Мирные жители (5 игроков)
            {"name": "Тестовый Мирный 1", "role": PlayerRole.CIVILIAN},
            {"name": "Тестовый Мирный 2", "role": PlayerRole.CIVILIAN},
            {"name": "Тестовый Мирный 3", "role": PlayerRole.CIVILIAN},
            {"name": "Тестовый Мирный 4", "role": PlayerRole.CIVILIAN},
            {"name": "Тестовый Мирный 5", "role": PlayerRole.CIVILIAN}
        ]
        
        logger.debug(f"create_test_game: подготовлен список из {len(test_players)} шаблонов игроков")
        
        # Перемешиваем роли для разнообразия
        random.shuffle(test_players)
        logger.debug(f"create_test_game: роли перемешаны для чата {chat_key}")
        
        # Проверим, что можем создать игрока
        try:
            test_player = Player(
                user_id=-999,
                username="test_check",
                first_name="Проверочный игрок",
                role=PlayerRole.CIVILIAN,
                role_info_sent=True
            )
            logger.debug(f"create_test_game: тестовое создание игрока прошло успешно")
        except Exception as e:
            logger.exception(f"create_test_game: ОШИБКА создания тестового игрока: {e}")
            return game  # Возвращаем пустую игру
        
        # Создаем игроков
        logger.debug(f"create_test_game: начинаем создание {len(test_players)} игроков")
        for i, player_data in enumerate(test_players):
            try:
                player_id = -(i + 1)
                logger.debug(f"create_test_game: создаем игрока {i+1}: {player_data['name']} с ролью {player_data['role'].value}")
                
                player = Player(
                    user_id=player_id,  # Отрицательные ID для виртуальных игроков
                    username=f"test_player_{i+1}",
                    first_name=player_data["name"],
                    role=player_data["role"],
                    role_info_sent=True  # Уже "отправлено"
                )
                
                game.players[player_id] = player
                logger.debug(f"create_test_game: игрок {player.first_name} с ID {player_id} добавлен в игру")
            except Exception as e:
                logger.exception(f"create_test_game: ошибка создания игрока {i+1}: {e}")
        
        self.active_games[chat_key] = game
        logger.info(f"create_test_game: создана тестовая игра для чата {chat_key} с {len(game.players)} виртуальными игроками")
        
        # Дополнительная проверка
        if len(game.players) == 0:
            logger.error(f"create_test_game: ОШИБКА! Игроки не были созданы для чата {chat_key}")
        else:
            logger.info(f"create_test_game: УСПЕХ! Создано {len(game.players)} игроков")
            for player_id, player in game.players.items():
                logger.debug(f"create_test_game: игрок {player_id}: {player.first_name} ({player.role.value})")
        
        return game
    
    def execute_test_night_actions(self, chat_key: str) -> None:
        """Автоматически выполняет ночные действия для тестовой игры"""
        logger.info(f"execute_test_night_actions: выполнение ночных действий для тестовой игры {chat_key}")
        
        game = self.get_game(chat_key)
        if not game or not game.is_test_game:
            logger.warning(f"execute_test_night_actions: игра не найдена или не является тестовой")
            return
        
        alive_players = game.get_alive_players()
        logger.info(f"execute_test_night_actions: найдено {len(alive_players)} живых игроков")
        
        # Логируем состав живых игроков
        for player in alive_players:
            logger.debug(f"execute_test_night_actions: живой игрок: {player.first_name} (ID: {player.user_id}, роль: {player.role.value})")
        
        # Мафия выбирает случайную жертву (имитируем коллективное голосование)
        mafia_players = [p for p in alive_players if p.role == PlayerRole.MAFIA]
        if mafia_players:
            logger.info(f"execute_test_night_actions: найдено {len(mafia_players)} мафиози")
            potential_victims = [p for p in alive_players if p.role != PlayerRole.MAFIA]
            if potential_victims:
                # Имитируем коллективное голосование мафии
                for mafia in mafia_players:
                    victim = random.choice(potential_victims)
                    game.mafia_votes[mafia.user_id] = victim.user_id
                    logger.debug(f"execute_test_night_actions: мафия {mafia.first_name} голосует за {victim.first_name}")
                
                # Выбираем цель по большинству голосов
                vote_tally = {}
                for target_id in game.mafia_votes.values():
                    vote_tally[target_id] = vote_tally.get(target_id, 0) + 1
                
                max_votes = max(vote_tally.values())
                top_targets = [tid for tid, votes in vote_tally.items() if votes == max_votes]
                chosen_victim_id = random.choice(top_targets)
                game.night_kill_target = chosen_victim_id
                
                chosen_victim = next((p for p in alive_players if p.user_id == chosen_victim_id), None)
                if chosen_victim:
                    logger.info(f"execute_test_night_actions: 🗡️ МАФИЯ выбрала жертву: {chosen_victim.first_name} (ID: {chosen_victim.user_id}, роль: {chosen_victim.role.value})")
            else:
                logger.warning(f"execute_test_night_actions: нет потенциальных жертв для мафии")
        else:
            logger.warning(f"execute_test_night_actions: мафиози не найдены среди живых игроков")
        
        # Доктор пытается спасти случайного игрока
        doctor_players = [p for p in alive_players if p.role == PlayerRole.DOCTOR]
        if doctor_players:
            logger.info(f"execute_test_night_actions: найден доктор: {doctor_players[0].first_name}")
            if game.night_kill_target:
                # 50% шанс успешного спасения
                if random.random() < 0.5:
                    game.doctor_saves[doctor_players[0].user_id] = game.night_kill_target
                    target_name = next((p.first_name for p in alive_players if p.user_id == game.night_kill_target), "неизвестный")
                    logger.info(f"execute_test_night_actions: 💉 ДОКТОР УСПЕШНО спас: {target_name} (ID: {game.night_kill_target})")
                else:
                    game.doctor_saves[doctor_players[0].user_id] = None
                    target_name = next((p.first_name for p in alive_players if p.user_id == game.night_kill_target), "неизвестный")
                    logger.info(f"execute_test_night_actions: 💉 ДОКТОР НЕ СМОГ спасти: {target_name} (ID: {game.night_kill_target})")
            else:
                logger.info(f"execute_test_night_actions: доктор не может спасти - нет цели мафии")
        else:
            logger.info(f"execute_test_night_actions: доктор не найден среди живых игроков")
        
        # Комиссар проверяет случайного игрока
        commissioner_players = [p for p in alive_players if p.role == PlayerRole.COMMISSIONER]
        if commissioner_players:
            logger.info(f"execute_test_night_actions: найден комиссар: {commissioner_players[0].first_name}")
            target = random.choice(alive_players)
            if target.role == PlayerRole.MAFIA:
                game.commissioner_check_results[commissioner_players[0].user_id] = True
                logger.info(f"execute_test_night_actions: 👮 КОМИССАР обнаружил МАФИЮ: {target.first_name} (ID: {target.user_id})")
            else:
                game.commissioner_check_results[commissioner_players[0].user_id] = False
                logger.info(f"execute_test_night_actions: 👮 КОМИССАР проверил МИРНОГО: {target.first_name} (ID: {target.user_id}, роль: {target.role.value})")

            # Записываем проверку комиссара
            game.commissioner_checks[commissioner_players[0].user_id] = target.user_id

        else:
            logger.info(f"execute_test_night_actions: комиссар не найден среди живых игроков")
        
        # Ночная бабочка отвлекает случайного игрока
        butterfly_players = [p for p in alive_players if p.role == PlayerRole.BUTTERFLY]
        if butterfly_players:
            logger.info(f"execute_test_night_actions: найдена ночная бабочка: {butterfly_players[0].first_name}")
            # Бабочка не может отвлечь саму себя
            potential_targets = [p for p in alive_players if p.user_id != butterfly_players[0].user_id]
            if potential_targets:
                target = random.choice(potential_targets)
                game.butterfly_distract_target = target.user_id
                game.butterfly_distracted_players.add(target.user_id)
                logger.info(f"execute_test_night_actions: 💃 БАБОЧКА отвлекла: {target.first_name} (ID: {target.user_id}, роль: {target.role.value})")
            else:
                logger.info(f"execute_test_night_actions: бабочка не может отвлечь никого (нет других живых игроков)")
        else:
            logger.info(f"execute_test_night_actions: ночная бабочка не найдена среди живых игроков")
        
        # Итоговая сводка ночных действий
        logger.info(f"execute_test_night_actions: === ИТОГИ НОЧНЫХ ДЕЙСТВИЙ ===")
        logger.info(f"execute_test_night_actions: Цель мафии: {game.night_kill_target}")
        logger.info(f"execute_test_night_actions: Спасения доктора: {game.doctor_saves}")
        logger.info(f"execute_test_night_actions: Проверки комиссара: {game.commissioner_check_results}")
        logger.info(f"execute_test_night_actions: Отвлечения бабочки: {game.butterfly_distract_target}")
        logger.info(f"execute_test_night_actions: ================================")
        
        logger.info(f"execute_test_night_actions: ночные действия для тестовой игры {chat_key} выполнены")
    
    def execute_test_voting(self, chat_key: str) -> None:
        """Автоматически выполняет голосование для тестовой игры"""
        logger.info(f"execute_test_voting: выполнение голосования для тестовой игры {chat_key}")
        
        game = self.get_game(chat_key)
        if not game or not game.is_test_game:
            logger.warning(f"execute_test_voting: игра не найдена или не является тестовой")
            return
        
        alive_players = game.get_alive_players()
        if not alive_players:
            logger.warning(f"execute_test_voting: нет живых игроков для голосования")
            return
        
        logger.info(f"execute_test_voting: найдено {len(alive_players)} живых игроков для голосования")
        
        # Логируем состав голосующих
        for player in alive_players:
            logger.debug(f"execute_test_voting: голосует: {player.first_name} (ID: {player.user_id}, роль: {player.role.value})")
        
        # Сбрасываем голоса
        game.votes.clear()
        for player in game.players.values():
            player.has_voted = False
            player.vote_target = None
        
        logger.info(f"execute_test_voting: голоса сброшены, начинаем голосование")
        
        # Каждый живой игрок голосует случайным образом
        for player in alive_players:
            if player.role == PlayerRole.MAFIA:
                # Мафия голосует за случайного мирного
                peaceful_targets = [p for p in alive_players if p.role != PlayerRole.MAFIA]
                if peaceful_targets:
                    target = random.choice(peaceful_targets)
                    game.votes[player.user_id] = target.user_id
                    player.has_voted = True
                    player.vote_target = target.user_id
                    logger.info(f"execute_test_voting: 😈 МАФИЯ {player.first_name} голосует за {target.first_name} (мирный)")
                else:
                    logger.warning(f"execute_test_voting: мафия {player.first_name} не может голосовать - нет мирных целей")
            else:
                # Мирные голосуют случайным образом
                potential_targets = [p for p in alive_players if p.user_id != player.user_id]
                if potential_targets:
                    target = random.choice(potential_targets)
                    game.votes[player.user_id] = target.user_id
                    player.has_voted = True
                    player.vote_target = target.user_id
                    logger.info(f"execute_test_voting: 👔 МИРНЫЙ {player.first_name} ({player.role.value}) голосует за {target.first_name}")
                else:
                    logger.warning(f"execute_test_voting: мирный {player.first_name} не может голосовать - нет других целей")
        
        # Итоговая сводка голосования
        logger.info(f"execute_test_voting: === ИТОГИ ГОЛОСОВАНИЯ ===")
        logger.info(f"execute_test_voting: Всего голосов: {len(game.votes)}")
        for voter_id, target_id in game.votes.items():
            voter = game.players.get(voter_id)
            target = game.players.get(target_id)
            if voter and target:
                logger.info(f"execute_test_voting: {voter.first_name} ({voter.role.value}) → {target.first_name} ({target.role.value})")
        logger.info(f"execute_test_voting: ==========================")
        
        logger.info(f"execute_test_voting: голосование для тестовой игры {chat_key} выполнено")
    
    def get_game(self, chat_key: str) -> GameState:
        """Получает активную игру"""
        game = self.active_games.get(chat_key)
        if game:
            logger.debug(f"get_game: игра найдена для чата {chat_key}, фаза: {game.phase}, игроков: {len(game.players)}")
        else:
            logger.debug(f"get_game: игра не найдена для чата {chat_key}")
        return game
    
    def add_player(self, chat_key: str, user_id: int, username: str, first_name: str) -> bool:
        """Добавляет игрока в игру"""
        logger.debug(f"add_player: попытка добавить игрока {user_id} ({first_name}) в чат {chat_key}")
        
        game = self.get_game(chat_key)
        if not game:
            logger.error(f"add_player: игра не найдена для чата {chat_key}")
            return False
        
        logger.debug(f"add_player: текущая фаза игры: {game.phase}")
        if game.phase != GamePhase.LOBBY:
            logger.warning(f"add_player: игра в чате {chat_key} не в фазе лобби (текущая фаза: {game.phase})")
            return False
        
        if user_id in game.players:
            logger.warning(f"add_player: игрок {user_id} уже в игре в чате {chat_key}")
            return False
        
        if len(game.players) >= MAX_PLAYERS:
            logger.warning(f"add_player: достигнут максимум игроков в чате {chat_key}")
            return False
        
        player = Player(
            user_id=user_id,
            username=username,
            first_name=first_name
        )
        game.players[user_id] = player
        logger.info(f"add_player: игрок {first_name} добавлен в игру в чате {chat_key}. Всего игроков: {len(game.players)}")
        return True
    
    def remove_player(self, chat_key: str, user_id: int) -> bool:
        """Удаляет игрока из игры"""
        logger.debug(f"remove_player: попытка удалить игрока {user_id} из чата {chat_key}")
        
        game = self.get_game(chat_key)
        if not game:
            logger.error("remove_player: игра не найдена")
            return False
        
        if game.phase != GamePhase.LOBBY:
            logger.warning(f"remove_player: игра не в фазе лобби, текущая фаза: {game.phase}")
            return False
        
        if user_id in game.players:
            del game.players[user_id]
            logger.info(f"remove_player: игрок {user_id} удален из игры")
            return True
        else:
            logger.warning(f"remove_player: игрок {user_id} не найден в игре")
            return False
    
    def remove_players_without_start(self, chat_key: str, player_ids: List[int]) -> None:
        """Удаляет игроков, которые не начали диалог с ботом"""
        logger.debug(f"remove_players_without_start: удаление игроков {player_ids} из чата {chat_key}")
        
        game = self.get_game(chat_key)
        if not game:
            logger.error("remove_players_without_start: игра не найдена")
            return
        
        removed_count = 0
        for player_id in player_ids:
            if player_id in game.players:
                player_name = game.players[player_id].first_name
                del game.players[player_id]
                removed_count += 1
                logger.info(f"remove_players_without_start: удален игрок {player_name} (ID: {player_id}) - не начал диалог с ботом")
        
        if removed_count > 0:
            logger.info(f"remove_players_without_start: удалено {removed_count} игроков из чата {chat_key}")
    
    def can_start_game(self, chat_key: str) -> bool:
        """Проверяет, можно ли начать игру"""
        logger.debug(f"can_start_game: проверка для чата {chat_key}")
        
        game = self.get_game(chat_key)
        if not game:
            logger.error("can_start_game: игра не найдена")
            return False
        
        player_count = len(game.players)
        current_phase = game.phase
        
        logger.debug(f"can_start_game: игроков: {player_count}, фаза: {current_phase}")
        
        # Разрешаем начать игру даже с 1 игроком (для тестирования)
        can_start = player_count >= 1 and current_phase == GamePhase.LOBBY
        
        logger.debug(f"can_start_game: результат: {can_start}")
        return can_start
    
    def start_game(self, chat_key: str) -> bool:
        """Начинает игру и раздает роли"""
        logger.debug(f"start_game вызвана для чата {chat_key}")
        
        game = self.get_game(chat_key)
        if not game:
            logger.error("Игра не найдена в start_game")
            return False
        
        if not self.can_start_game(chat_key):
            logger.warning("Игра не может быть начата")
            return False
        
        logger.debug(f"Игра может быть начата, текущая фаза: {game.phase}")
        
        # Проверяем количество игроков
        player_count = len(game.players)
        if player_count < MIN_PLAYERS:
            logger.warning(f"Игра начинается с {player_count} игроками (рекомендуется минимум {MIN_PLAYERS})")
        
        # Раздаем роли
        self._distribute_roles(game)
        
        # Устанавливаем фазу NIGHT
        old_phase = game.phase
        game.phase = GamePhase.NIGHT
        game.game_started = True
        game.current_round = 1
        game.night_prompts_sent = False  # Сбрасываем флаг для отправки ночных клавиатур
        # Обновляем привязку мафии к игре для приватного чата
        self._refresh_mafia_mapping(chat_key)
        
        logger.info(f"Фаза изменена с {old_phase} на {game.phase}")
        logger.info(f"Игра началась в чате {chat_key} с {player_count} игроками")
        
        # Выводим информацию о ролях игроков
        for player_id, player in game.players.items():
            logger.info(
                f"Роль назначена: id={player_id}, name={player.first_name}, "
                f"username=@{player.username if player.username else '—'}, role={player.role}"
            )
        
        return True
    
    def get_role_description(self, role: PlayerRole) -> str:
        """Возвращает описание роли для использования в хендлерах."""
        descriptions = {
            PlayerRole.MAFIA: """Ты — человек в тени. Ночью ты выходишь на улицы и убираешь тех, кто мешает твоему бизнесу.

🕶️ Твоя цель — уничтожить всех мирных и прикинуться простым жителем.

⚜️ Ночью вместе с мафией выбирай жертву.
⚜️ Днём ври, улыбайся и обвиняй других.

Помни: мафиози никогда не признаётся, даже с петлёй на шее.""",
            PlayerRole.CIVILIAN: """Ты — обычный человек в городе, где честность стоит жизни.

🕊️ Твоя цель — выжить и найти мафию.

⚜️ Ночью ты спишь и надеешься, что тебя не тронут.
⚜️ Днём голосуй и обсуждай, кого стоит убрать.

У тебя нет оружия, но есть главное — твой голос.""",
            PlayerRole.DOCTOR: """Ты — врач, который спасает жизни, пока город утопает в крови.

💊 Твоя цель — защитить мирных и не дать мафии легко убрать всех.

⚜️ Ночью ты выбираешь, кого лечить.
⚜️ Ты можешь лечить даже себя, но один раз за игру.

В твоих руках — жизнь города, но помни: спасти всех не получится.""",
            PlayerRole.COMMISSIONER: """Ты — глаз закона в городе, где законы больше не работают.

🚔 Твоя цель — вычислить мафию.

⚜️ Ночью ты выбираешь игрока и проверяешь, кто он.
⚜️ Днём убедительно рассказывай, кому можно верить, а кого пора на виселицу.

Смотри в оба: мафия всегда улыбается честнее всех.""",
            PlayerRole.BUTTERFLY: """Ты — соблазн и хаос ночного города.

🌹 Твоя цель — путать карты и мешать мафии и комиссару.

⚜️ Ночью ты выбираешь игрока, и он пропускает свой ход (не убивает, не лечит, не проверяет).
⚜️ Днём играй невинную — никто не должен догадаться, кто ты.

В этом городе твои чары опаснее пули."""
        }
        return descriptions.get(role, "Неизвестная роль")

    def _distribute_roles(self, game: GameState):
        """Раздает роли игрокам"""
        logger.debug(f"_distribute_roles: начинаем раздачу ролей для {len(game.players)} игроков")
        
        player_count = len(game.players)
        role_dist = ROLE_DISTRIBUTION.get(player_count, ROLE_DISTRIBUTION[12])
        
        logger.debug(f"_distribute_roles: распределение ролей: {role_dist}")
        
        # Создаем список всех ролей
        roles = []
        for role_name, count in role_dist.items():
            if role_name == "мафия":
                roles.extend([PlayerRole.MAFIA] * count)
            elif role_name == "мирный":
                roles.extend([PlayerRole.CIVILIAN] * count)
            elif role_name == "доктор":
                roles.extend([PlayerRole.DOCTOR] * count)
            elif role_name == "комиссар":
                roles.extend([PlayerRole.COMMISSIONER] * count)
            elif role_name == "ночная_бабочка":
                roles.extend([PlayerRole.BUTTERFLY] * count)
        
        logger.debug(f"_distribute_roles: созданный список ролей: {roles}")
        
        # Перемешиваем роли
        random.shuffle(roles)
        
        # Назначаем роли игрокам
        player_ids = list(game.players.keys())
        random.shuffle(player_ids)
        
        logger.debug(f"_distribute_roles: перемешанные ID игроков: {player_ids}")
        
        for i, player_id in enumerate(player_ids):
            if i < len(roles):
                assigned_role = roles[i]
                game.players[player_id].role = assigned_role
                logger.debug(f"_distribute_roles: игрок {player_id} получил роль {assigned_role}")
            else:
                logger.warning(f"_distribute_roles: для игрока {player_id} не хватило роли")
        
        logger.debug(f"_distribute_roles: раздача ролей завершена")
    
    def process_night_action(self, chat_key: str, player_id: int, action_type: str, target_id: int = None) -> bool:
        """Обрабатывает ночное действие игрока"""
        logger.debug(f"process_night_action: чат {chat_key}, игрок {player_id}, действие {action_type}, цель {target_id}")
        
        game = self.get_game(chat_key)
        if not game:
            logger.error(f"process_night_action: игра не найдена для чата {chat_key}")
            return False
        
        logger.debug(f"process_night_action: фаза игры: {game.phase}")
        if game.phase != GamePhase.NIGHT:
            logger.warning(f"process_night_action: игра не в ночной фазе. Текущая фаза: {game.phase}")
            return False
        
        player = game.players.get(player_id)
        if not player:
            logger.error(f"process_night_action: игрок {player_id} не найден в игре")
            return False
        
        if not player.is_alive:
            logger.warning(f"process_night_action: игрок {player_id} мертв")
            return False
        
        logger.debug(f"process_night_action: игрок {player_id} найден, роль: {player.role}, жив: {player.is_alive}")
        # Если игрок отвлечён бабочкой этой ночью — блокируем любое ночное действие
        if game.butterfly_distract_target is not None and player_id == game.butterfly_distract_target:
            logger.warning(f"process_night_action: игрок {player_id} отвлечен бабочкой и не может совершить действие")
            return False
        
        if action_type == "mafia_kill":
            if target_id == player_id:
                logger.warning("process_night_action: нельзя выбирать себя целью (mafia_kill)")
                return False
            # Проверяем, что цель жива
            target_player = game.players.get(target_id)
            if target_player and not target_player.is_alive:
                logger.warning(f"process_night_action: нельзя убить уже мертвого игрока {target_id}")
                return False
            if player.role == PlayerRole.MAFIA:
                logger.debug(f"process_night_action: мафия {player_id} выбирает жертву {target_id}")
                # Коллективное голосование мафии
                game.mafia_votes[player_id] = target_id
                logger.debug(f"process_night_action: мафия {player_id} проголосовала за {target_id}")
                
                # Проверяем, все ли живые мафии проголосовали
                alive_mafias = [p.user_id for p in game.get_players_by_role(PlayerRole.MAFIA)]
                if alive_mafias and all(mid in game.mafia_votes for mid in alive_mafias):
                    # Все мафии проголосовали - выбираем цель по большинству
                    tally = {}
                    for tid in game.mafia_votes.values():
                        tally[tid] = tally.get(tid, 0) + 1
                    
                    if tally:
                        max_votes = max(tally.values())
                        top = [tid for tid, c in tally.items() if c == max_votes]
                        chosen = random.choice(top)
                        game.night_kill_target = chosen
                        logger.info(f"process_night_action: мафия выбрала коллективную цель: {chosen} (голоса: {tally})")
                    else:
                        logger.warning(f"process_night_action: нет голосов мафии для подсчета")
                else:
                    # Не все мафии проголосовали
                    remaining = [mid for mid in alive_mafias if mid not in game.mafia_votes]
                    logger.debug(f"process_night_action: мафия {player_id} проголосовала за {target_id}, ждем остальных: {remaining}")
                
                game.night_actions_completed.add("mafia")
                return True
            else:
                logger.warning(f"process_night_action: игрок {player_id} не мафия, роль: {player.role}")
                return False
        
        elif action_type == "doctor_save":
            if player.role != PlayerRole.DOCTOR:
                logger.warning(f"process_night_action: игрок {player_id} не доктор, роль: {player.role}")
                return False

            # Пропуск лечения
            if target_id is None:
                game.doctor_saves[player_id] = None
                game.night_actions_completed.add("doctor")
                logger.debug(f"process_night_action: доктор {player_id} пропускает лечение")
                return True

            # Самолечение: разрешено один раз за игру
            if target_id == player_id:
                if getattr(player, "doctor_self_save_used", False):
                    logger.warning(f"process_night_action: доктор {player_id} уже использовал самолечение")
                    return False
                player.doctor_self_save_used = True
                logger.debug(f"process_night_action: доктор {player_id} использует самолечение")
                game.doctor_saves[player_id] = target_id
                game.night_actions_completed.add("doctor")
                return True

            # Лечение другого игрока: цель должна быть жива и не совпадать с прошлой целью подряд
            target_player = game.players.get(target_id)
            if target_player and not target_player.is_alive:
                logger.warning(f"process_night_action: нельзя лечить уже мертвого игрока {target_id}")
                return False

            last_saved_target = game.doctor_last_save_target.get(player_id)
            if last_saved_target is not None and target_id == last_saved_target:
                logger.warning(f"process_night_action: доктор {player_id} не может два раза подряд лечить одну и ту же цель {target_id}")
                return False

            logger.debug(f"process_night_action: доктор {player_id} выбирает пациента {target_id}")
            game.doctor_saves[player_id] = target_id
            game.doctor_last_save_target[player_id] = target_id
            game.night_actions_completed.add("doctor")
            return True
        
        elif action_type == "butterfly_distract":
            if target_id is not None and target_id == player_id:
                logger.warning("process_night_action: нельзя выбирать себя целью (butterfly_distract)")
                return False
            # Проверяем, что цель жива
            if target_id is not None:
                target_player = game.players.get(target_id)
                if target_player and not target_player.is_alive:
                    logger.warning(f"process_night_action: нельзя отвлекать уже мертвого игрока {target_id}")
                    return False
            if player.role == PlayerRole.BUTTERFLY:
                logger.debug(f"process_night_action: ночная бабочка {player_id} отвлекает {target_id}")
                game.butterfly_distract_target = target_id
                # Добавляем игрока в список отвлеченных (если цель была выбрана)
                if target_id is not None:
                    try:
                        game.butterfly_distracted_players.add(target_id)
                        logger.debug(f"process_night_action: игрок {target_id} добавлен в список отвлеченных бабочкой")
                    except Exception as e:
                        logger.exception(f"process_night_action: ошибка добавления в список отвлеченных: {e}")
                game.night_actions_completed.add("butterfly")
                return True
            else:
                logger.warning(f"process_night_action: игрок {player_id} не ночная бабочка, роль: {player.role}")
                return False
        
        elif action_type == "commissioner_check":
            if target_id == player_id:
                logger.warning("process_night_action: нельзя проверять себя (commissioner_check)")
                return False
            # Проверяем, что цель жива
            target_player = game.players.get(target_id)
            if target_player and not target_player.is_alive:
                logger.warning(f"process_night_action: нельзя проверять уже мертвого игрока {target_id}")
                return False
            if player.role == PlayerRole.COMMISSIONER:
                # Проверяем, что комиссар еще не делал проверку этой ночью
                if player_id in game.commissioner_checks:
                    logger.warning(f"process_night_action: комиссар {player_id} уже делал проверку этой ночью")
                    return False
                # Проверяем, что цель еще не проверялась этой ночью
                if target_id in game.commissioner_checks.values():
                    logger.warning(f"process_night_action: игрок {target_id} уже проверялся комиссаром этой ночью")
                    return False

                target_player = game.players.get(target_id)
                if target_player:
                    logger.debug(f"process_night_action: комиссар {player_id} проверяет {target_id}")
                    game.commissioner_checks[player_id] = target_id
                    game.commissioner_check_results[player_id] = (target_player.role == PlayerRole.MAFIA)
                    game.night_actions_completed.add("commissioner")
                    return True
                else:
                    logger.error(f"process_night_action: цель {target_id} не найдена для проверки комиссаром")
            else:
                logger.warning(f"process_night_action: игрок {player_id} не комиссар, роль: {player.role}")
                return False
        
        logger.warning(f"process_night_action: действие {action_type} не выполнено для игрока {player_id}")
        return False
    
    def all_night_actions_completed(self, chat_key: str) -> bool:
        """Проверяет, завершены ли все ночные действия"""
        logger.debug(f"all_night_actions_completed: проверка для чата {chat_key}")
        
        game = self.get_game(chat_key)
        if not game:
            logger.error("all_night_actions_completed: игра не найдена")
            return False
        
        required_actions = set()
        for player in game.players.values():
            if player.is_alive:
                if player.role == PlayerRole.MAFIA:
                    required_actions.add("mafia")
                elif player.role == PlayerRole.DOCTOR:
                    required_actions.add("doctor")
                elif player.role == PlayerRole.COMMISSIONER:
                    required_actions.add("commissioner")
                elif player.role == PlayerRole.BUTTERFLY:
                    required_actions.add("butterfly")
        
        logger.debug(f"all_night_actions_completed: требуемые действия: {required_actions}")
        logger.debug(f"all_night_actions_completed: завершенные действия: {game.night_actions_completed}")
        
        result = required_actions.issubset(game.night_actions_completed)
        logger.debug(f"all_night_actions_completed: результат: {result}")
        
        return result
    
    def process_night_results(self, chat_key: str) -> Tuple[str, Optional[int]]:
        """Обрабатывает результаты ночи и возвращает сообщение и ID убитого игрока"""
        logger.info(f"process_night_results: обработка результатов ночи для чата {chat_key}")
        
        game = self.get_game(chat_key)
        if not game:
            logger.error("process_night_results: игра не найдена")
            return "Ошибка: игра не найдена", None
        
        # Сохраняем результаты прошлой ночи для дневных объявлений
        game.last_doctor_save_targets = list(game.doctor_saves.values())
        game.last_butterfly_distract_target = game.butterfly_distract_target
        game.last_commissioner_checks = []
        for commissioner_id, target_id in game.commissioner_checks.items():
            target_player = game.players.get(target_id)
            if target_player:
                is_mafia = target_player.role == PlayerRole.MAFIA
                game.last_commissioner_checks.append((commissioner_id, target_id, is_mafia))
                logger.info(f"process_night_results: комиссар {commissioner_id} проверил игрока {target_id} - {'мафия' if is_mafia else 'не мафия'}")
        
        # Обрабатываем убийство мафии
        killed_player = None
        
        # Сначала проверяем, есть ли уже выбранная цель
        if game.night_kill_target:
            target_id = game.night_kill_target
            target_player = game.players.get(target_id)
            if target_player and target_player.is_alive:
                # Проверяем, был ли игрок спасен доктором
                was_saved = False
                for doctor_id, save_target in game.doctor_saves.items():
                    if save_target == target_id:
                        was_saved = True
                        logger.info(f"process_night_results: игрок {target_id} спасен доктором {doctor_id}")
                        break
                
                if not was_saved:
                    target_player.is_alive = False
                    killed_player = target_player
                    logger.info(f"process_night_results: игрок {target_id} ({target_player.first_name}) убит мафией")
                    # На случай смерти мафии — обновляем маппинг мафии
                    self._refresh_mafia_mapping(chat_key)
                else:
                    logger.info(f"process_night_results: игрок {target_id} спасен от убийства")
        
        # Если цель не была выбрана, но есть голоса мафии - обрабатываем их
        elif game.mafia_votes and any(p.role == PlayerRole.MAFIA for p in game.get_alive_players()):
            # Есть голоса мафии, но итоговая цель не зафиксирована — считаем большинство
            vote_tally: Dict[int, int] = {}
            for tid in game.mafia_votes.values():
                vote_tally[tid] = vote_tally.get(tid, 0) + 1
            
            if vote_tally:
                max_votes = max(vote_tally.values())
                top_targets = [tid for tid, cnt in vote_tally.items() if cnt == max_votes]
                chosen_target = random.choice(top_targets)
                target_player = game.players.get(chosen_target)
                
                if target_player and target_player.is_alive:
                    # Проверяем, был ли игрок спасен доктором
                    was_saved = False
                    for doctor_id, save_target in game.doctor_saves.items():
                        if save_target == chosen_target:
                            was_saved = True
                            logger.info(f"process_night_results: игрок {chosen_target} спасен доктором {doctor_id}")
                            break
                    
                    if not was_saved:
                        target_player.is_alive = False
                        killed_player = target_player
                        game.night_kill_target = chosen_target
                        
                        if len(top_targets) > 1:
                            logger.info(f"process_night_results: ничья среди целей мафии {top_targets}, случайно убит {chosen_target} ({target_player.first_name})")
                        else:
                            logger.info(f"process_night_results: игрок {chosen_target} ({target_player.first_name}) убит мафией по большинству голосов")
                        
                        # На случай смерти мафии — обновляем маппинг мафии
                        self._refresh_mafia_mapping(chat_key)
                    else:
                        logger.info(f"process_night_results: игрок {chosen_target} спасен от убийства")
                        game.night_kill_target = chosen_target
            else:
                # Мафия голосовала, но голоса не засчитаны - выбираем случайную цель
                alive_players = [p for p in game.get_alive_players() if p.role != PlayerRole.MAFIA]
                if alive_players:
                    chosen_target = random.choice(alive_players).user_id
                    target_player = game.players.get(chosen_target)
                    
                    if target_player and target_player.is_alive:
                        # Проверяем, был ли игрок спасен доктором
                        was_saved = False
                        for doctor_id, save_target in game.doctor_saves.items():
                            if save_target == chosen_target:
                                was_saved = True
                                logger.info(f"process_night_results: игрок {chosen_target} спасен доктором {doctor_id}")
                                break
                        
                        if not was_saved:
                            target_player.is_alive = False
                            killed_player = target_player
                            game.night_kill_target = chosen_target
                            logger.info(f"process_night_results: игрок {chosen_target} ({target_player.first_name}) убит мафией (случайный выбор)")
                            
                            # На случай смерти мафии — обновляем маппинг мафии
                            self._refresh_mafia_mapping(chat_key)
                        else:
                            logger.info(f"process_night_results: игрок {chosen_target} спасен от убийства")
                            game.night_kill_target = chosen_target
        
        # Формируем сообщение о результатах ночи
        summary_lines = []
        
        # Результаты мафии
        if killed_player:
            kill_messages = [
                f"🔪 {killed_player.first_name} найден мертвым в переулке. Мафия не спит.",
                f"💀 {killed_player.first_name} больше не с нами. Ночь была долгой.",
                f"⚰️ {killed_player.first_name} попрощался с городом. Мафия не знает пощады.",
                f"🩸 {killed_player.first_name} найден бездыханным. Улицы города кровавы.",
                f"🌙 {killed_player.first_name} не пережил ночь. Мафия охотится.",
                f"💔 {killed_player.first_name} покинул нас. Ночь забрала еще одну жизнь.",
                f"🕯️ {killed_player.first_name} погас как свеча. Мафия торжествует.",
                f"⚡ {killed_player.first_name} получил смертельный удар. Ночь была жестокой."
            ]
            kill_msg = random.choice(kill_messages)
            role_name = {
                PlayerRole.MAFIA: "Мафия",
                PlayerRole.CIVILIAN: "Мирный житель", 
                PlayerRole.DOCTOR: "Доктор",
                PlayerRole.COMMISSIONER: "Комиссар",
                PlayerRole.BUTTERFLY: "Ночная бабочка"
            }.get(killed_player.role, "Неизвестная роль")
            summary_lines.append(f"{kill_msg}\nОн был {role_name}!")
        elif any(p.role == PlayerRole.MAFIA for p in game.get_alive_players()):
            # Мафия есть
            if game.mafia_votes:
                # Мафия голосовала — итог (убийство по большинству или случайно среди лидеров)
                # уже обработан выше; если жертва спасена доктором, ниже будет строка про спасение
                pass
            else:
                # Мафия не голосовала вообще — выбираем случайную цель
                alive_players = [p for p in game.get_alive_players() if p.role != PlayerRole.MAFIA]
                if alive_players:
                    chosen_target = random.choice(alive_players).user_id
                    target_player = game.players.get(chosen_target)
                    
                    if target_player and target_player.is_alive:
                        # Проверяем, был ли игрок спасен доктором
                        was_saved = False
                        for doctor_id, save_target in game.doctor_saves.items():
                            if save_target == chosen_target:
                                was_saved = True
                                logger.info(f"process_night_results: игрок {chosen_target} спасен доктором {doctor_id}")
                                break
                        
                        if not was_saved:
                            target_player.is_alive = False
                            killed_player = target_player
                            game.night_kill_target = chosen_target
                            logger.info(f"process_night_results: игрок {chosen_target} ({target_player.first_name}) убит мафией (автоматический выбор)")
                            
                            # На случай смерти мафии — обновляем маппинг мафии
                            self._refresh_mafia_mapping(chat_key)
                        else:
                            logger.info(f"process_night_results: игрок {chosen_target} спасен от убийства")
                            game.night_kill_target = chosen_target
                else:
                    # Нет мирных игроков для убийства
                    summary_lines.append("🌙 Мафия не может выбрать цель - нет мирных игроков.")
        else:
            # Мафии нет — действительно спокойная ночь
            summary_lines.append("🌅 Ночь прошла спокойно. Никто не пострадал.")
        
        # Результаты доктора
        doctor_saves = [save for save in game.doctor_saves.values() if save is not None]
        if doctor_saves:
            saved_players = [game.players.get(pid) for pid in doctor_saves if game.players.get(pid)]
            if saved_players:
                saved_names = [p.first_name for p in saved_players]
                summary_lines.append(f"💉 Доктор спас: {', '.join(saved_names)}")
        
        # Результат проверки комиссара (без раскрытия цели)
        if game.last_commissioner_checks:
            found_mafia = any(is_mafia for (_cid, _tid, is_mafia) in game.last_commissioner_checks)
            summary_lines.append(
                "👮 Комиссар провёл проверку: результат — МАФИЯ." if found_mafia
                else "👮 Комиссар провёл проверку: результат — не мафия."
            )

        # Результаты ночной бабочки
        if game.butterfly_distract_target:
            distracted_player = game.players.get(game.butterfly_distract_target)
            if distracted_player:
                summary_lines.append(f"💃 Ночная бабочка отвлекла {distracted_player.first_name}")
        
        # Объединяем все результаты
        full_message = "\n\n".join(summary_lines)
        
        # Очищаем ночные действия
        game.doctor_saves.clear()
        game.butterfly_distract_target = None
        game.commissioner_checks.clear()
        game.commissioner_check_results.clear()
        game.night_kill_target = None
        game.mafia_votes.clear()  # Очищаем голоса мафии для следующей ночи
        game.night_actions_completed.clear()
        game.all_actions_notified = False
        # Сбрасываем маркер разосланных ночных клавиатур, чтобы в следующую ночь снова отправить ЛС
        try:
            game.night_prompts_sent = False
        except Exception:
            pass
        
        # Переходим к дневной фазе
        game.phase = GamePhase.DAY
        
        logger.info(f"process_night_results: ночная фаза завершена, переход к дневной фазе в чате {chat_key}")
        
        return full_message, killed_player.user_id if killed_player else None
    
    def start_voting(self, chat_key: str) -> bool:
        """Начинает голосование"""
        logger.debug(f"start_voting: попытка начать голосование для чата {chat_key}")
        
        game = self.get_game(chat_key)
        if not game:
            logger.error("start_voting: игра не найдена")
            return False
        
        if game.phase != GamePhase.DAY:
            logger.warning(f"start_voting: игра не в дневной фазе, текущая фаза: {game.phase}")
            return False
        
        logger.info("start_voting: начинаем голосование")
        
        game.phase = GamePhase.VOTING
        game.votes.clear()
        try:
            game.skipped_voters.clear()
        except Exception:
            pass
        
        # Сбрасываем голоса игроков
        for player in game.players.values():
            player.has_voted = False
            player.vote_target = None
        # Блокируем голосование для тех, кого отвлекла бабочка прошлой ночью
        if game.last_butterfly_distract_target is not None:
            distracted_id = game.last_butterfly_distract_target
            distracted_player = game.players.get(distracted_id)
            if distracted_player and distracted_player.is_alive:
                distracted_player.has_voted = True
                distracted_player.vote_target = None
                logger.info(f"start_voting: игрок {distracted_id} не может голосовать (отвлечен бабочкой прошлой ночью)")
        
        logger.debug(f"start_voting: фаза изменена на {game.phase}, голоса сброшены")
        
        return True
    
    def process_vote(self, chat_key: str, voter_id: int, target_id: int) -> bool:
        """Обрабатывает голос игрока"""
        logger.debug(f"process_vote: попытка обработать голос игрока {voter_id} за {target_id} в чате {chat_key}")
        
        game = self.get_game(chat_key)
        if not game:
            logger.error("process_vote: игра не найдена")
            return False
        
        if game.phase != GamePhase.VOTING:
            logger.warning(f"process_vote: игра не в фазе голосования, текущая фаза: {game.phase}")
            return False
        
        voter = game.players.get(voter_id)
        target = game.players.get(target_id)
        
        if not voter:
            logger.error(f"process_vote: голосующий {voter_id} не найден")
            return False
        
        if not target:
            logger.error(f"process_vote: цель {target_id} не найдена")
            return False
        
        if not voter.is_alive:
            logger.warning(f"process_vote: голосующий {voter_id} мертв")
            return False
        
        if not target.is_alive:
            logger.warning(f"process_vote: цель {target_id} мертва")
            return False
        
        # Переголосование отключено: никаких дополнительных ограничений по целям
        
        if voter.has_voted:
            logger.warning(f"process_vote: игрок {voter_id} уже проголосовал")
            return False

        # Нельзя голосовать за себя
        if voter_id == target_id:
            logger.warning("process_vote: нельзя голосовать за себя")
            return False
        
        # Записываем голос
        game.votes[voter_id] = target_id
        voter.has_voted = True
        voter.vote_target = target_id
        
        logger.debug(f"process_vote: голос игрока {voter_id} за {target_id} записан")
        
        return True
    
    def get_voting_results(self, chat_key: str) -> Tuple[str, int]:
        """Подсчитывает результаты голосования и возвращает сообщение и ID казненного игрока"""
        logger.info(f"get_voting_results: подсчет результатов голосования для чата {chat_key}")
        
        game = self.get_game(chat_key)
        if not game:
            logger.error("get_voting_results: игра не найдена")
            return "Ошибка: игра не найдена", 0
        
        # Подсчитываем голоса
        vote_counts = {}
        for target_id in game.votes.values():
            vote_counts[target_id] = vote_counts.get(target_id, 0) + 1
        
        if not vote_counts:
            logger.info(f"get_voting_results: никто не проголосовал в чате {chat_key}")
            # Сбрасываем состояние голосования и переводим игру в ночь
            game.votes.clear()
            for player in game.players.values():
                player.has_voted = False
                player.vote_target = None
            # Переход к ночной фазе после голосования без казни
            game.phase = GamePhase.NIGHT
            game.current_round += 1
            return "🗳️ Никто не проголосовал. Сегодня казни не будет.", 0
        
        # Находим игрока с максимальным количеством голосов
        max_votes = max(vote_counts.values())
        most_voted = [player_id for player_id, votes in vote_counts.items() if votes == max_votes]
        
        if len(most_voted) > 1:
            # Ничья — выбираем случайно одного казнимого, без переголосования
            executed_id = random.choice(most_voted)
            executed_player = game.players.get(executed_id)
            if executed_player:
                executed_player.is_alive = False
                tied_names = []
                for pid in most_voted:
                    pl = game.players.get(pid)
                    if pl:
                        tied_names.append(pl.first_name)
                logger.info(
                    f"get_voting_results: ничья между {most_voted} ({', '.join(tied_names)}), случайно казнен {executed_id} в чате {chat_key}"
                )
                execution_messages = [
                    f"⚖️ {executed_player.first_name if executed_player else executed_id} приговорен случайным выбором при ничьей.",
                    f"🪢 Судьба решила: {executed_player.first_name if executed_player else executed_id} отправляется на эшафот.",
                    f"🔨 Жребий пал на {executed_player.first_name if executed_player else executed_id}.",
                    f"🎲 Равные голоса — и удача против {executed_player.first_name if executed_player else executed_id}."
                ]
                role_name = {
                    PlayerRole.MAFIA: "Мафия",
                    PlayerRole.CIVILIAN: "Мирный житель",
                    PlayerRole.DOCTOR: "Доктор",
                    PlayerRole.COMMISSIONER: "Комиссар",
                    PlayerRole.BUTTERFLY: "Ночная бабочка"
                }.get(executed_player.role if executed_player else None, "Неизвестная роль")
                message = f"{random.choice(execution_messages)}\nОн был {role_name}!"
                # Сброс состояния голосования и переход в ночь
                try:
                    game.revote_active = False
                    game.revote_candidates.clear()
                except Exception:
                    game.revote_active = False
                    game.revote_candidates = set()
                game.votes.clear()
                for player in game.players.values():
                    player.has_voted = False
                    player.vote_target = None
                game.phase = GamePhase.NIGHT
                game.current_round += 1
                return message, executed_id
        
        # Есть однозначный кандидат на казнь
        executed_id = most_voted[0]
        executed_player = game.players.get(executed_id)
        if not executed_player:
            logger.error(f"get_voting_results: игрок {executed_id} не найден в игре")
            return "Ошибка: игрок не найден", 0

        # Помечаем игрока мёртвым
        executed_player.is_alive = False
        
        # Случайные сообщения о казни
        execution_messages = [
            f"⚖️ {executed_player.first_name} приговорен к смерти. Правосудие свершилось.",
            f"🔨 {executed_player.first_name} получает свой последний билет без возврата.",
            f"🪢 {executed_player.first_name} попрощался с городом.",
            f"🏛️ {executed_player.first_name} отправляется в кошачий рай...",
            f"💀 {executed_player.first_name} уже не с нами.",
            f"🌊 {executed_player.first_name} ушёл под воду без лишних слов.",
            f"⚰️ {executed_player.first_name} отправляется в историю… и не сам.",
            f"🕯️ {executed_player.first_name} погас как свеча."
        ]
        
        role_name = {
            PlayerRole.MAFIA: "Мафия",
            PlayerRole.CIVILIAN: "Мирный житель",
            PlayerRole.DOCTOR: "Доктор", 
            PlayerRole.COMMISSIONER: "Комиссар",
            PlayerRole.BUTTERFLY: "Ночная бабочка"
        }.get(executed_player.role, "Неизвестная роль")
        
        message = f"{random.choice(execution_messages)}\nОн был {role_name}!"
        logger.info(f"get_voting_results: игрок {executed_id} ({executed_player.first_name}) казнен в чате {chat_key}")

        # Сбрасываем состояние голосования/переголосования и переводим игру в ночь
        try:
            game.revote_active = False
            game.revote_candidates.clear()
        except Exception:
            game.revote_active = False
            game.revote_candidates = set()
        game.votes.clear()
        for player in game.players.values():
            player.has_voted = False
            player.vote_target = None
        # Переход к ночной фазе после казни
        game.phase = GamePhase.NIGHT
        game.current_round += 1
        
        return message, executed_id
    
    def check_game_over(self, chat_key: str) -> Tuple[bool, str]:
        """Проверяет, закончилась ли игра"""
        logger.debug(f"check_game_over: проверка окончания игры для чата {chat_key}")
        
        game = self.get_game(chat_key)
        if not game:
            logger.error("check_game_over: игра не найдена")
            return False, ""
        
        is_over, winner = game.is_game_over()
        logger.debug(f"check_game_over: игра окончена: {is_over}, победитель: {winner}")
        
        if is_over:
            game.phase = GamePhase.ENDED
            if winner == "mafia":
                message = "Мафия захватила город. Теперь тут правим мы!"
                # Раскрываем состав мафии
                mafia_players = [p for p in game.players.values() if p.role == PlayerRole.MAFIA]
                if mafia_players:
                    mafia_list = ", ".join(
                        [f"{p.first_name}{f' (@{p.username})' if p.username else ''}" for p in mafia_players]
                    )
                    message += f"\n\n😈 Мафия: {mafia_list}"
            else:
                message = "Город очистился от мафии. Браво, синьоры!"
            
            logger.info(f"check_game_over: игра завершена, сообщение: {message}")
            return True, message
        
        return False, ""
    
    def end_game(self, chat_key: str) -> bool:
        """Принудительно завершает игру"""
        logger.debug(f"end_game: попытка завершить игру для чата {chat_key}")
        
        if chat_key in self.active_games:
            del self.active_games[chat_key]
            logger.info(f"end_game: игра для чата {chat_key} завершена")
            return True
        else:
            logger.warning(f"end_game: игра для чата {chat_key} не найдена")
            return False

# Глобальный экземпляр менеджера игр
game_manager = GameManager()
