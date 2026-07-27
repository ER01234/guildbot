import asyncio
from dataclasses import dataclass
from typing import Generic, TypeVar

from vkbottle import API

TokenT = TypeVar("TokenT")


@dataclass
class TokenState(Generic[TokenT]):
    """Состояние токена: сам токен + флаг использования."""
    token: TokenT
    used: bool = False


async def resolve_peer_id(api: API, title: str) -> int:
    """
    Найти peer_id чата по названию.
    """
    await asyncio.sleep(1)
    conversations = await api.messages.get_conversations(count=200)
    for item in conversations.items:
        if title and item.conversation.chat_settings and title in item.conversation.chat_settings.title:
            return item.conversation.peer.id
    return 0

