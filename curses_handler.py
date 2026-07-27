import asyncio
import logging
import time
from typing import List, Optional, Deque
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

# Маппинг команд на типы эффектов WellDungeon API
CURSE_COMMAND_MAP: dict[str, str] = {
    "неудачи": "CurseOfUnluck",
    "боли": "CurseOfPain",
    "добычи": "CurseOfLoot",
}

# Имена эффектов для сообщений пользователю
CURSE_RUSSIAN_NAMES: dict[str, str] = {
    "CurseOfUnluck": "Проклятие неудачи",
    "CurseOfPain": "Проклятие боли",
    "CurseOfLoot": "Проклятие добычи",
}

COOLDOWN_SECONDS = 60 * 60     # 1 час между использованиями одного токена
COOLDOWN_THRESHOLD = 60        # если до разблокировки <= 60 сек — ждём, иначе отказ


class CursesHandler(BaseCommandHandler):
    """
    Обработчик команд проклятий.
    Прямой вызов WellDungeon API с локальным кулдауном токенов.
    """

    def __init__(self):
        self._tokens: List[Tuple[str, float]] = [
            (t, 0.0) for t in TokenStorage.WarlocksTokens()
        ]

        self.request_queue: Deque[Tuple[str, Message]] = deque()
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

    async def _apply_single_curse(
        self, curse_type: str, message: Message, token: str
    ) -> bool:
        player_id = await self._get_player_id(message)

        try:
            await wd_client.apply_social_effect(
                token=token,
                player_id=player_id,
                effect_type=curse_type,
            )
        except WdInsufficientVoicesError:
            await cleanup_answer(message,"Недостаточно Голосов Древних")
            return False
        except WdOnCooldownError:
            await cleanup_answer(message,"Персонаж на кулдауне (серверный)")
            return False
        except WdTargetNotFoundError:
            await cleanup_answer(message,"Цель не найдена в игре")
            return False
        except WdConditionsNotMetError:
            await cleanup_answer(message,"Персонаж не может наложить это проклятие")
            return False
        except WdForbiddenScopeError:
            logger.warning("Token %s has no social_effects scope", token[:20])
            await cleanup_answer(message,"У токена нет прав на социальные эффекты")
            return False
        except WdInvalidTokenError:
            logger.warning("Invalid WD token: %s", token[:20])
            return False
        except Exception as e:
            logger.exception("Unexpected error applying curse %s: %s", curse_type, e)
            await cleanup_answer(message,"Произошла неизвестная ошибка при наложении проклятия")
            return False

        effect_name = CURSE_RUSSIAN_NAMES.get(curse_type, curse_type)
        mention = f"@id{message.from_id}"

        await cleanup_answer(message,f"{effect_name} наложено на {mention}")
        return True

    async def _process_user_request(self, curse_type: str, message: Message):
        now = time.monotonic()

        if not self._tokens:
            await cleanup_answer(message,"Не удалось наложить проклятие — нет доступных дебов")
            return

        free = [t for t in self._tokens if now - t[1] >= COOLDOWN_SECONDS]

        if not free:
            earliest = min(self._tokens, key=lambda t: t[1])
            wait = COOLDOWN_SECONDS - (now - earliest[1])

            if wait > COOLDOWN_THRESHOLD:
                minutes = int(wait // 60) + 1
                await cleanup_answer(message,
                    f"Кулдаун баффера. До снятия кулдауна осталось {minutes} мин."
                )
                return

            await asyncio.sleep(max(wait, 0))
            free = [t for t in self._tokens if now - t[1] >= COOLDOWN_SECONDS]
            if not free:
                free = [earliest]

        for token, _ in free:
            success = await self._apply_single_curse(curse_type, message, token)
            if success:
                for i, (t, _) in enumerate(self._tokens):
                    if t == token:
                        self._tokens[i] = (token, time.monotonic())
                        break
                return

        await cleanup_answer(message,"Не удалось наложить проклятие — попробуйте позже")

    async def _queue_processor(self):
        while True:
            item = None
            async with self._queue_lock:
                if self.request_queue:
                    item = self.request_queue.popleft()

            if not item:
                await asyncio.sleep(1)
                continue

            curse_type_str, message = item
            async with self._processing_lock:
                await self._process_user_request(curse_type_str, message)

    def _ensure_processor_running(self):
        if self._processor_task is None or self._processor_task.done():
            self._processor_task = asyncio.create_task(self._queue_processor())

    async def handle(self, message: Message) -> Optional[str]:
        text = message.text or ""
        if not text:
            return None

        command = text.strip("/").lower()
        curse_type = CURSE_COMMAND_MAP.get(command)
        if curse_type is None:
            return None

        self._ensure_processor_running()

        await cleanup_answer(message,"Эффект добавлен в очередь")
        async with self._queue_lock:
            self.request_queue.append((curse_type, message))

        return None