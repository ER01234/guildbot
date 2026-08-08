import asyncio
import logging
import re

from vkbottle import VKAPIError
from vkbottle.bot import Message

logger = logging.getLogger(__name__)

DELETE_DELAY_SECONDS = 300  # 5 минут

_scheduled_message_ids: set[int] = set()

# Игровые команды (обрабатывает сама игра, не бот): «Передать X золота/штук/штуки/осколков»
GAME_COMMAND_TRANSFER_RE = re.compile(
    r"передать\s+\d+\s+(золота|штук|штуки|осколков)"
)


async def cleanup_answer(message: Message, *args, **kwargs):
    """
    Отправить ответ бота и поставить на удаление через 5 минут:
    и сам ответ, и сообщение пользователя, на которое бот ответил.
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
        # api берём из контекста сообщения (токен Jibrill), с фолбэком на _current_api
        api = getattr(message, "api", None) or getattr(message, "ctx_api", None) or _current_api
        # 1) сам ответ бота
        conversation_message_id = getattr(sent, "conversation_message_id", None)
        if conversation_message_id is not None:
            await schedule_delete(
                peer_id=message.peer_id,
                conversation_message_id=conversation_message_id,
                api=api,
            )
        # 2) сообщение пользователя, на которое бот ответил — централизованно
        await cleanup_user_message(message, api=api)
    return sent


async def cleanup_user_message(message: Message, api=None):
    """
    Поставить сообщение пользователя на удаление через DELETE_DELAY_SECONDS.
    Вызывается централизованно из cleanup_answer (любое сообщение, на которое
    бот ответил) и из bot.py для игровых команд («Осмотреть», «Передать …»).
    Дедупликация — в schedule_delete (по conversation_message_id).
    """
    conversation_message_id = getattr(message, "conversation_message_id", None)
    if conversation_message_id is None:
        return
    api = api or getattr(message, "api", None) or getattr(message, "ctx_api", None) or _current_api
    await schedule_delete(
        peer_id=message.peer_id,
        conversation_message_id=conversation_message_id,
        api=api,
    )


def is_game_command_for_cleanup(message: Message) -> bool:
    """
    Игровые команды, которые обрабатывает сама игра (не бот):
    «Осмотреть» или «Передать X золота/штук/штуки/осколков».
    Удаляем только если сообщение содержит ответ (reply_message)
    или пересланное сообщение (fwd_messages).
    """
    if not (getattr(message, "reply_message", None) or getattr(message, "fwd_messages", None)):
        return False
    text = (message.text or "").strip().lower()
    if text == "осмотреть":
        return True
    return GAME_COMMAND_TRANSFER_RE.fullmatch(text) is not None


async def schedule_delete(
    peer_id: int,
    conversation_message_id: int,
    delay_seconds: float = DELETE_DELAY_SECONDS,
    api=None,
):
    """
    Поставить собственное сообщение бота на удаление через delay_seconds.
    api (токен Jibrill) передаётся из cleanup_answer или берётся из _current_api.
    """
    if conversation_message_id is None or conversation_message_id in _scheduled_message_ids:
        return

    api = api or _current_api
    _scheduled_message_ids.add(conversation_message_id)
    asyncio.create_task(
        _delete_after(peer_id, conversation_message_id, delay_seconds, api)
    )


async def _delete_after(peer_id: int, conversation_message_id: int, delay_seconds: float, api):
    try:
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)

        await api.messages.delete(
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
