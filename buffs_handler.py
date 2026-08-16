import asyncio
import logging
import time
from enum import Enum
from typing import List, Optional, Tuple, Deque
from collections import deque

from chat_cleaner_handler import cleanup_answer
from vkbottle.bot import Message

from base_command_handler import BaseCommandHandler
from token_storage import TokenStorage
from wd_api_client import (
    wd_client,
    WdInsufficientVoicesError,
    WdOnCooldownError,
    WdTargetNotFoundError,
    WdConditionsNotMetError,
    WdInvalidTokenError,
    WdForbiddenScopeError,
)


logger = logging.getLogger(__name__)

# Маппинг букв команд на типы эффектов WellDungeon API
BUFF_LETTER_MAP: dict[str, str] = {
    "у": "BlessOfLuck",
    "а": "BlessOfAttack",
    "з": "BlessOfDefense",
    "ч": "BlessOfHuman",
    "н": "BlessOfUndead",
    "э": "BlessOfElf",
    "д": "BlessOfDemon",
    "о": "BlessOfOrk",
    "г": "BlessOfGoblin",
    "м": "BlessOfGnome",
}

# Имена эффектов для сообщений пользователю
BUFF_RUSSIAN_NAMES: dict[str, str] = {
    "BlessOfLuck": "благословение удачи",
    "BlessOfAttack": "благословение атаки",
    "BlessOfDefense": "благословение защиты",
    "BlessOfHuman": "благословение человека",
    "BlessOfUndead": "благословение нежити",
    "BlessOfElf": "благословение эльфа",
    "BlessOfDemon": "благословение демона",
    "BlessOfOrk": "благословение орка",
    "BlessOfGoblin": "благословение гоблина",
    "BlessOfGnome": "благословение гнома",
}

# Короткие имена для финального уведомления (без "благословение ")
BUFF_SHORT_NAMES: dict[str, str] = {
    "BlessOfLuck": "удачи",
    "BlessOfAttack": "атаки",
    "BlessOfDefense": "защиты",
    "BlessOfHuman": "человека",
    "BlessOfUndead": "нежити",
    "BlessOfElf": "эльфа",
    "BlessOfDemon": "демона",
    "BlessOfOrk": "орка",
    "BlessOfGoblin": "гоблина",
    "BlessOfGnome": "гнома",
}

COOLDOWN_SECONDS = 60          # 1 минута между использованиями одного токена


class BuffApplyResult(Enum):
    """Результат попытки применения одного эффекта одним токеном."""
    SUCCESS = "success"
    INSUFFICIENT_VOICES = "insufficient_voices"      # у токена закончились голоса
    CHARACTER_ON_COOLDOWN = "character_on_cooldown"  # цель на кулдауне (не зависит от токена)
    TARGET_NOT_FOUND = "target_not_found"
    CONDITIONS_NOT_MET = "conditions_not_met"
    FORBIDDEN_SCOPE = "forbidden_scope"
    INVALID_TOKEN = "invalid_token"
    ERROR = "error"


class BuffsHandler(BaseCommandHandler):
    """
    Обработчик команд бафов.
    Прямой вызов WellDungeon API с локальным кулдауном токенов.
    """

    def __init__(self):
        # Токены: (wd_token, доступные_буквы, last_used_timestamp)
        raw = TokenStorage.BuffersTokens()
        self._tokens: List[Tuple[str, str, float]] = [
            (t[0], t[1], 0.0) for t in raw
        ]

        self.request_queue: Deque[Tuple[List[str], Message]] = deque()
        self._processor_task: Optional[asyncio.Task] = None
        self._queue_lock = asyncio.Lock()
        self._processing_lock = asyncio.Lock()

    async def _get_player_id(self, message: Message) -> int:
        reply = getattr(message, "reply_message", None)
        if reply and reply.from_id:
            return reply.from_id
        fwd = getattr(message, "fwd_messages", None)
        if fwd and len(fwd) > 0 and fwd[0].from_id:
            return fwd[0].from_id
        return message.from_id

    async def _apply_single_buff(
        self, effect_type: str, player_id: int, token: str
    ) -> BuffApplyResult:
        try:
            await wd_client.apply_social_effect(
                token=token,
                player_id=player_id,
                effect_type=effect_type,
            )
        except WdInsufficientVoicesError:
            return BuffApplyResult.INSUFFICIENT_VOICES
        except WdOnCooldownError:
            return BuffApplyResult.CHARACTER_ON_COOLDOWN
        except WdTargetNotFoundError:
            return BuffApplyResult.TARGET_NOT_FOUND
        except WdConditionsNotMetError:
            return BuffApplyResult.CONDITIONS_NOT_MET
        except WdForbiddenScopeError:
            logger.warning("Token %s has no social_effects scope", token[:20])
            return BuffApplyResult.FORBIDDEN_SCOPE
        except WdInvalidTokenError:
            logger.warning("Invalid WD token: %s", token[:20])
            return BuffApplyResult.INVALID_TOKEN
        except Exception as e:
            logger.exception("Unexpected error applying buff %s: %s", effect_type, e)
            return BuffApplyResult.ERROR

        return BuffApplyResult.SUCCESS

    def _can_token_apply(self, token: Tuple[str, str, float], effect_type: str) -> bool:
        """Проверить, подходит ли токен для эффекта по буквам-фильтру."""
        _, available_letters, _ = token
        for letter, wd_type in BUFF_LETTER_MAP.items():
            if wd_type == effect_type:
                return letter in available_letters
        return False

    def _mark_token_used(self, token: str) -> None:
        """Обновить last_used timestamp токена после успешного применения."""
        for i, (t, letters, _) in enumerate(self._tokens):
            if t == token:
                self._tokens[i] = (t, letters, time.monotonic())
                break

    async def _apply_effect_with_retry(
        self, effect_type: str, player_id: int, message: Message
    ) -> bool:
        """Применить один эффект, перебирая токены и дожидаясь кулдауна, пока не выйдет."""
        name = BUFF_RUSSIAN_NAMES.get(effect_type, effect_type)

        active = [t for t in self._tokens if self._can_token_apply(t, effect_type)]
        if not active:
            await cleanup_answer(message, f"Нет апо для {name}")
            return False

        # Токены, которые для этого запроса уже бесполезны:
        #  - нет голосов (за время ожидания кд голоса не появятся)
        #  - неверный токен / нет scope / ошибка (постоянная проблема)
        skipped: set[str] = set()

        while True:
            now = time.monotonic()

            # Готовые к попытке: кд прошёл и токен ещё не отброшен
            ready = [
                t for t in active
                if t[0] not in skipped and now - t[2] >= COOLDOWN_SECONDS
            ]

            for token, _, _ in ready:
                result = await self._apply_single_buff(effect_type, player_id, token)

                if result is BuffApplyResult.SUCCESS:
                    self._mark_token_used(token)
                    return True

                if result in (
                    BuffApplyResult.INSUFFICIENT_VOICES,
                    BuffApplyResult.FORBIDDEN_SCOPE,
                    BuffApplyResult.INVALID_TOKEN,
                    BuffApplyResult.ERROR,
                ):
                    # Этот токен не подходит — пробуем следующий
                    skipped.add(token)
                    continue

                if result is BuffApplyResult.CHARACTER_ON_COOLDOWN:
                    await cleanup_answer(message, f"Персонаж на кулдауне для {name}")
                    return False

                if result is BuffApplyResult.TARGET_NOT_FOUND:
                    await cleanup_answer(message, "Цель не найдена в игре")
                    return False

                if result is BuffApplyResult.CONDITIONS_NOT_MET:
                    return False

            # Никто из готовых не смог применить.
            # Остались ли токены на кд, на которые есть смысл подождать?
            now = time.monotonic()
            pending = [
                t for t in active
                if t[0] not in skipped and now - t[2] < COOLDOWN_SECONDS
            ]

            if not pending:
                await cleanup_answer(message, f"Не удалось применить {name}")
                return False

            earliest = min(pending, key=lambda t: t[2])
            wait = COOLDOWN_SECONDS - (now - earliest[2])
            if wait > 0:
                await asyncio.sleep(wait)

    async def _process_user_request(
        self, effect_types: List[str], message: Message
    ):
        player_id = await self._get_player_id(message)
        applied_effects: List[str] = []

        for effect_type in effect_types:
            ok = await self._apply_effect_with_retry(effect_type, player_id, message)
            if ok:
                applied_effects.append(effect_type)

        # Итоговое уведомление
        if applied_effects:
            mention = f"@id{message.from_id}"

            names = [BUFF_SHORT_NAMES.get(e, e) for e in applied_effects]
            await cleanup_answer(message,
                f"Благословения применены для {mention}: {', '.join(names)}"
            )

    async def _queue_processor(self):
        while True:
            item = None
            async with self._queue_lock:
                if self.request_queue:
                    item = self.request_queue.popleft()

            if not item:
                await asyncio.sleep(1)
                continue

            effect_types, message = item
            async with self._processing_lock:
                await self._process_user_request(effect_types, message)

    def _ensure_processor_running(self):
        if self._processor_task is None or self._processor_task.done():
            self._processor_task = asyncio.create_task(self._queue_processor())

    async def handle(self, message: Message) -> Optional[str]:
        text = message.text or ""
        if not text or not text.startswith("/баф "):
            return None

        self._ensure_processor_running()

        parts = text.split()
        if len(parts) < 2:
            await cleanup_answer(message,"Укажите буквы эффектов")
            return None

        letters_arg = parts[1].lower()
        invalid_letters = [
            ch for ch in letters_arg if ch not in BUFF_LETTER_MAP
        ]
        if invalid_letters:
            valid_chars = ", ".join(BUFF_LETTER_MAP.keys())
            await cleanup_answer(message,
                f"Недопустимые буквы: {', '.join(invalid_letters)}. "
                f"Допустимые: {valid_chars}"
            )
            return None

        effect_types = [BUFF_LETTER_MAP[ch] for ch in letters_arg]

        await cleanup_answer(message,"Эффекты добавлены в очередь")
        async with self._queue_lock:
            self.request_queue.append((effect_types, message))

        return None