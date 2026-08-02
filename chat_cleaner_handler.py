import asyncio
import logging

from vkbottle import VKAPIError
from vkbottle.bot import Message

logger = logging.getLogger(__name__)

DELETE_DELAY_SECONDS = 300  # 5 минут

_scheduled_message_ids: set[int] = set()


async def cleanup_answer(message: Message, *args, **kwargs):
    """
    Отправить ответ бота и поставить его на удаление через 5 минут.
    Бот не видит свои сообщения через longpoll, поэтому удаляем
    по conversation_message_id сразу после отправки.

    Работает из любого места — из bot.py и из внутренних хендлеров
    (эффекты, благословения и т.п.), где тоже используется message.answer.
    """
    # VK отклоняет пустой текст (VKAPIError_100) — защита от пустых ответов хендлеров
    text = kwargs.get("message", args[0] if args else None)
    if text is None or not str(text).strip():
        logger.warning("cleanup_answer: пропущена отправка пустого сообщения в peer %s", message.peer_id)
        return None

    sent = await message.answer(*args, **kwargs)
    if sent is not None:
        conversation_message_id = getattr(sent, "conversation_message_id", None)
        if conversation_message_id is not None:
            await schedule_delete(
                peer_id=message.peer_id,
                conversation_message_id=conversation_message_id,
            )
    return sent


async def schedule_delete(peer_id: int, conversation_message_id: int, delay_seconds: float = DELETE_DELAY_SECONDS):
    """
    Поставить собственное сообщение бота на удаление через delay_seconds.
    Использует message.api из контекста сообщения (токен Jibrill).
    """
    if conversation_message_id is None or conversation_message_id in _scheduled_message_ids:
        return

    _scheduled_message_ids.add(conversation_message_id)
    asyncio.create_task(
        _delete_after(peer_id, conversation_message_id, delay_seconds)
    )


async def _delete_after(peer_id: int, conversation_message_id: int, delay_seconds: float):
    try:
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)

        await _current_api.messages.delete(
            peer_id=peer_id,
            conversation_message_ids=[conversation_message_id],
            delete_for_all=True,
        )
    except VKAPIError as e:
        logger.info(
            "Failed to delete own message %s in peer %s: %s",
            conversation_message_id,
            peer_id,
            e,
        )
    finally:
        _scheduled_message_ids.discard(conversation_message_id)


# api (токен Jibrill) передаётся из bot.py при старте
_current_api = None


def bind_api(api):
    """Сохранить api (токен Jibrill) для удаления сообщений."""
    global _current_api
    _current_api = api


class ChatCleanerHandler:
    """
    Удаляет только те сообщения, которые бот (Jibrill) отправил сам.
    Longpoll не доставляет боту события о его собственных сообщениях,
    поэтому мы не сканируем историю, а ставим на удаление сразу после
    отправки, зная conversation_message_id ответа.
    """

    def __init__(self, api):
        bind_api(api)

    async def schedule_own_message(
        self, peer_id: int, conversation_message_id: int, delay_seconds: float = DELETE_DELAY_SECONDS
    ):
        await schedule_delete(peer_id, conversation_message_id, delay_seconds)
