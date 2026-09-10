import json
import logging
import os
from typing import Optional, List, Tuple, Dict

logger = logging.getLogger(__name__)

MAX_CONTEXT_CHARS = 2000


class ConversationHistoryStorage:
    """Хранилище истории диалогов с Джибрилл.

    Сквозной формат без разделения на пользователей.
    Пары "ИМЯ: сообщение / ДЖИБРИЛЛ: ответ" хранятся в одном списке.
    При сборке контекста берутся самые новые пары, пока не наберётся
    MAX_CONTEXT_CHARS символов (2000). Если следующая пара превысит
    лимит — она не добавляется.
    """

    def __init__(self, file_path: Optional[str] = None):
        if file_path is None:
            self.file_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "data", "conversation_history.json"
            )
        else:
            self.file_path = os.path.abspath(file_path)

        self._ensure_dir()
        logger.info(f"ConversationHistoryStorage initialized: {self.file_path}")

    def _ensure_dir(self):
        dir_path = os.path.dirname(self.file_path)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)

    def add_pair(self, user_name: str, user_message: str, bot_response: str) -> None:
        """Добавляет пару (вопрос пользователя → ответ бота) в историю."""
        data = self._read_file()
        pair = {
            "name": user_name,
            "message": user_message[:4000],
            "response": bot_response[:4000],
        }
        data.append(pair)
        self._write_file(data)

    def build_context(self) -> Tuple[str, List[dict]]:
        """Собирает контекст из последних пар (макс. 2000 символов).

        Проходит с конца (самые новые), форматирует каждую пару как:
            ИМЯ: сообщение
            ДЖИБРИЛЛ: ответ

        Добавляет пары, пока не превысит MAX_CONTEXT_CHARS.
        Если следующая пара превысит лимит — останавливается.

        Returns:
            (context_text, api_messages)
            - context_text: отформатированный текст (от старого к новому)
            - api_messages: список {"role": "user"/"assistant", "content": "..."}
                            для передачи в API Яндекса (в правильном порядке)
        """
        data = self._read_file()
        if not data:
            return "", []

        # Собираем с конца (самые новые)
        pairs_to_include = []
        total_len = 0

        for pair in reversed(data):
            name = pair.get("name", "Пользователь")
            user_msg = pair.get("message", "")
            bot_resp = pair.get("response", "")

            # Форматируем пару: "ИМЯ: сообщение\nДЖИБРИЛЛ: ответ\n"
            formatted = f"{name}: {user_msg}\nДЖИБРИЛЛ: {bot_resp}\n"

            if total_len + len(formatted) > MAX_CONTEXT_CHARS:
                break

            pairs_to_include.append(formatted)
            total_len += len(formatted)

        # Переворачиваем — от старого к новому
        pairs_to_include.reverse()
        context_text = "".join(pairs_to_include).strip()

        # Собираем api_messages в правильном порядке (user → assistant)
        api_messages = []
        for pair in data[-len(pairs_to_include):]:
            api_messages.append({"role": "user", "content": pair.get("message", "")})
            api_messages.append({"role": "assistant", "content": pair.get("response", "")})

        return context_text, api_messages

    def clear_all(self) -> None:
        """Очищает всю историю (всех пользователей)."""
        self._write_file([])

    def _read_file(self) -> List[Dict[str, str]]:
        if not os.path.exists(self.file_path):
            return []
        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            # Старый формат (словарь по user_id) — сбрасываем в пустой список
            if isinstance(data, dict):
                logger.warning(
                    "Old format detected in %s (dict by user_id), "
                    "resetting to empty list (new format: list of pairs)",
                    self.file_path
                )
                self._write_file([])
                return []
            return data
        except (json.JSONDecodeError, IOError):
            logger.error(f"Error reading conversation history: {self.file_path}")
            return []

    def _write_file(self, data: List[Dict[str, str]]) -> None:
        try:
            with open(self.file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except IOError as e:
            logger.error(f"Error writing conversation history: {e}")
