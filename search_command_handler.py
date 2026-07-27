import logging
import random
import time
from typing import Optional, Dict, Any
from collections import defaultdict
from vkbottle.bot import Message
from vkbottle import VKAPIError
from base_command_handler import BaseCommandHandler
from user_handler import UserHandler

logger = logging.getLogger(__name__)

class SearchCommandHandler(BaseCommandHandler):
    def __init__(self, user_handler: UserHandler):
        self.user_handler = user_handler
        self._last_usage: Dict[str, float] = defaultdict(float)
        self.COOLDOWN_SECONDS = 3600
        self.phrases: Dict[str, str] = {
            "найди пидора": "Пидорас найден",
            "кому вкусняшку": "Вкусняшка достаётся"
        }

    @staticmethod
    def _normalize_phrase(text: str) -> str:
        return (text or "").strip().lower()

    def _get_phrase_response(self, text: str) -> Optional[str]:
        return self.phrases.get(self._normalize_phrase(text))

    def _get_phrase_cooldown_key(self, phrase: str) -> str:
        return self._normalize_phrase(phrase)

    def _get_user_phrase_cooldown_key(self, user_id: int, phrase: str) -> str:
        return f"{int(user_id)}::{self._get_phrase_cooldown_key(phrase)}"

    @staticmethod
    def _format_selected_user(prefix: str, user: Dict[str, Any]) -> str:
        first_name = user.get('first_name', 'User')
        last_name = user.get('last_name', '')
        return f"{prefix} @id{user['id']} ({first_name} {last_name})"

    async def handle(self, message: Message) -> Optional[str]:
        text = message.text or ""
        phrase = self._normalize_phrase(text)

        if phrase not in self.phrases:
            return None

        if message.peer_id < 2000000000:
            return "Команда доступна только в беседах"

        cooldown_key = self._get_user_phrase_cooldown_key(message.from_id, phrase)
        current_time = time.time()
        last_used = self._last_usage[cooldown_key]

        if current_time - last_used < self.COOLDOWN_SECONDS:
            remaining = int(self.COOLDOWN_SECONDS - (current_time - last_used))
            return f"Команда на перезарядке. Осталось {remaining // 60} мин {remaining % 60} с."

        try:
            users = await self.user_handler.get_chat_users(message)

            if not users:
                return "В беседе не найдено подходящих пользователей"

            selected_user = random.SystemRandom().choice(users)

            self._last_usage[cooldown_key] = current_time
            prefix = self._get_phrase_response(phrase) or ""
            return self._format_selected_user(prefix, selected_user)

        except VKAPIError as e:
            logger.error(f"VK API Error in SearchCommandHandler: {e}")
            return "Ошибка API"
        except Exception as e:
            logger.error(f"Unexpected error in SearchCommandHandler: {e}")
            return "Ошибка"