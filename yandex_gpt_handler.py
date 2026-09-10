import asyncio
import logging
from typing import Optional, List, Dict, Any
from vkbottle.bot import Message
from base_command_handler import BaseCommandHandler
from conversation_history import ConversationHistoryStorage
from local_secrets import get_secret

logger = logging.getLogger(__name__)

# Конфигурация Yandex AI Studio (Responses API)
AI_STUDIO_API_URL = "https://ai.api.cloud.yandex.net/v1/responses"
AI_STUDIO_API_KEY = get_secret("AI_STUDIO_API_KEY", "")
AI_STUDIO_PROJECT = "b1g0vv1cis632orhnu1k"
AI_STUDIO_AGENT_ID = "fvtgoocp7mp4f9dmqg4l"

# Лимиты
MAX_MESSAGE_LENGTH = 2000  # макс. длина одного сообщения пользователя


async def _call_ai_studio(prompt: str, history: Optional[List[Dict[str, str]]] = None) -> str:
    """Отправляет запрос в Yandex AI Studio Responses API и возвращает ответ.

    Args:
        prompt: текущий запрос пользователя
        history: список предыдущих сообщений [{"role": "user"|"assistant", "content": "..."}]
    """
    import aiohttp

    if not AI_STUDIO_API_KEY:
        logger.error("AI_STUDIO_API_KEY не задан (env AI_STUDIO_API_KEY или data/secrets.json)")
        return ""

    headers = {
        "Authorization": f"Api-Key {AI_STUDIO_API_KEY}",
        "Content-Type": "application/json",
        "OpenAI-Project": AI_STUDIO_PROJECT
    }

    payload: Dict[str, Any] = {
        "prompt": {
            "id": AI_STUDIO_AGENT_ID
        },
        "input": prompt
    }

    # История передаётся массивом сообщений в input:
    # top-level "messages" Responses API молча игнорирует.
    if history:
        messages = []
        for msg in history:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})

        if messages:
            messages.append({"role": "user", "content": prompt})
            payload["input"] = messages

    async with aiohttp.ClientSession() as session:
        async with session.post(AI_STUDIO_API_URL, headers=headers, json=payload, ssl=False) as resp:
            if resp.status == 401:
                logger.error("AI Studio: неверный API Key")
                return "Ошибка авторизации в AI Studio"
            if resp.status == 404:
                logger.error("AI Studio: агент не найден")
                return "Агент не найден. Проверьте настройки"
            if resp.status != 200:
                error_text = await resp.text()
                logger.error(f"AI Studio API error {resp.status}: {error_text}")
                return "Ошибка при обращении к нейросети"

            data = await resp.json()
            return _extract_text_from_response(data)


def _extract_text_from_response(data: dict) -> str:
    """Извлекает текст ответа из Responses API.

    Формат output — массив объектов с разными type:
    - type='web_search_call' — вызов поиска (пропускаем)
    - type='message' — финальный ответ, текст в content[0]['text']
    """
    output = data.get("output", [])

    for item in output:
        if item.get("type") == "message":
            content = item.get("content", [])
            for block in content:
                if block.get("type") == "output_text" and block.get("text"):
                    return block["text"]

    # Fallback: старый формат (если output[0].content[0].text)
    try:
        text = output[0]["content"][0]["text"]
        return text
    except (KeyError, IndexError, TypeError):
        pass

    logger.error(f"Unexpected AI Studio response format: {data}")
    return "Не удалось разобрать ответ нейросети"


class YandexGPTHandler(BaseCommandHandler):
    """Обрабатывает обращения 'Джи' или 'Джибрилл'.

    История диалогов — сквозная, без разделения по пользователям.
    Хранятся пары (вопрос → ответ). При запросе собирается контекст
    из последних пар, пока не наберётся 2000 символов.
    """

    def __init__(self):
        self._processing_lock = asyncio.Lock()
        self.history_storage = ConversationHistoryStorage(
            file_path="data/conversation_history.json"
        )

    def clear_all_history(self):
        """Очищает всю историю диалогов."""
        self.history_storage.clear_all()

    async def handle(self, message: Message) -> Optional[str]:
        text = (message.text or "").strip()

        # Проверяем обращение: "Джи, ..." или "Джибрилл, ..."
        lower = text.lower()
        query = None

        if lower.startswith("джи,") or lower.startswith("джи, "):
            idx = text.lower().index("джи,") + 4
            query = text[idx:].strip()
        elif lower.startswith("джибрилл,") or lower.startswith("джибрилл, "):
            idx = text.lower().index("джибрилл,") + 10
            query = text[idx:].strip()

        if not query:
            return None

        # Специальная команда: очистка истории
        if query.lower() in ("забудь всё", "забудь все", "очисти историю", "забудь"):
            self.clear_all_history()
            return "История диалога очищена. Начинаем заново!"

        # Ограничиваем длину запроса
        if len(query) > MAX_MESSAGE_LENGTH:
            return "Слишком длинный вопрос. Максимум 2000 символов."

        async with self._processing_lock:
            try:
                # Имя пользователя — @id для VK-упоминания
                user_name = f"@id{message.from_id}"

                # Собираем контекст из последних пар (макс. 2000 символов)
                context_text, api_messages = self.history_storage.build_context()
                context = api_messages if api_messages else None

                # Вызываем AI
                answer = await _call_ai_studio(query, history=context)

                # Пустой ответ = ключ не задан или API промолчал.
                # Молчим, чтобы не отправлять в чат пустое сообщение "💬 ".
                if not answer:
                    return None

                # Сохраняем пару (вопрос → ответ)
                self.history_storage.add_pair(user_name, query, answer)

                return f"💬 {answer}"
            except Exception as e:
                logger.error(f"Error calling AI Studio: {e}")
                return "Произошла ошибка при обращении к нейросети"