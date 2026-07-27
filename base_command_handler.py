from abc import ABC, abstractmethod
from typing import Optional, Any
from vkbottle.bot import Message

class BaseCommandHandler(ABC):
    @abstractmethod
    async def handle(self, message: Message) -> Optional[str]:
        pass
