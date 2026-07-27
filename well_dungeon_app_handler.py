import logging
import json
import aiohttp
from typing import Optional
from urllib.parse import urlencode
from base_command_handler import BaseCommandHandler
from vkbottle.bot import Message
from items_storage import ItemsStorage
from user_repository import UserRepository

logger = logging.getLogger(__name__)

WELL_DUNGEON_BASE_URL = "https://well2.activeusers.ru/app/?"

def WellDungeonAppId():
    return 6987489

class WellDungeonAppHandler(BaseCommandHandler):
    def __init__(self):
        self.storage = ItemsStorage()
        self.users_repository = UserRepository(db_path="data/users_db.json")

    @staticmethod
    def _build_user_name(user_data) -> str:
        return f"{getattr(user_data, 'first_name', '')} {getattr(user_data, 'last_name', '')}".strip()

    def _get_item_users_report(self, item_name: str) -> str:
        users = self.users_repository.load_users()
        eaters = []
        devourers = []

        for user_data in users.values():
            user_tag = f"@id{user_data.user_id} ({self._build_user_name(user_data)})"

            if item_name in user_data.eat_books:
                eaters.append(user_tag)

            if item_name in user_data.devour_books:
                devourers.append(user_tag)

        response = []
        if eaters:
            response.append("Едят:")
            response.append("\n".join(eaters))

        if devourers:
            response.append("Жрут:")
            response.append("\n".join(devourers))

        if not response:
            return f"{item_name} никто не ест и не жрет"

        return "\n".join(response)

    async def handle(self, message: Message) -> Optional[str]:
        text = message.text or ""
        text_fwd = message.fwd_messages[0].text if message.fwd_messages else ""

        query = None

        if text.startswith("/курс"):
            return await self.get_corse()
        elif text.startswith("/цена"):
            parts = text.split(maxsplit=1)
            query = parts[1].strip() if len(parts) > 1 else ""

        elif text_fwd and (text_fwd.startswith("👝1*") or "👝1*" in text_fwd):
             lines = text_fwd.splitlines()
             first_line = lines[0]

             if first_line.startswith("👝1*"):
                 cleaned = first_line[len("👝1*"):]
                 if "-" in cleaned:
                     query = cleaned.split("-", 1)[1].strip()
                 else:
                     query = cleaned.strip()

        if query is None:
            return None

        return await self.get_price(query)

    async def get_corse(self) -> str:
        query_params = {
            "act": "a_program_say",
            "ch": "u391196432",
            "text": "Обновить курс",
            "context": "1",
            "messages[0][message]": "Обновить курс",
            "bid": "w_156"
        }
        new_url = WELL_DUNGEON_BASE_URL + urlencode(query_params)

        try:
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
                async with session.get(new_url) as resp:
                    data = await resp.text()
        except Exception as e:
            logger.error(f"Failed to query auction: {e}")
            return "Не удалось узнать курс"

        try:
            payload = json.loads(data)
        except Exception as e:
            logger.error(f"Invalid JSON from auction: {e}")
            return "Не удалось узнать курс"


        message_object = payload.get("message")
        message = message_object[0].get("message")
        course = message.split("У Вас")[0] or ""
        course = course.strip("\r\n\r\n")
        return course

    async def get_price(self, query: str) -> str:
        q = (query or "").strip()
        if not q:
            return "Использование: /цена <название>"

        item_name = self.storage.get_item_name(q)

        if item_name is None:
            return "Товар не найден"

        item_id = self.storage.get_item_id(item_name)
        item_name_lower = item_name.lower()

        query_params = {
            "act": "auc_lots",
            "item_id": item_id,
            "type": "sell"
        }
        new_url = WELL_DUNGEON_BASE_URL + urlencode(query_params)

        try:
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
                async with session.get(new_url) as resp:
                    data = await resp.text()
        except Exception as e:
            logger.error(f"Failed to query auction: {e}")
            return "Не удалось получить цену"

        try:
            payload = json.loads(data)
        except Exception as e:
            logger.error(f"Invalid JSON from auction: {e}")
            return "Не удалось получить цену"

        lots = payload.get("lots") or []
        price_line = f"Мин цена {item_name_lower} на ауке не найдена"
        prices = []
        if lots:
            for lot in lots:
                try:
                    qty = lot[1]
                    price = lot[2]
                    if qty:
                        prices.append(price / qty)
                except Exception:
                    pass

        if prices:
            x = min(prices)
            x_str = f"{x:.2f}".rstrip('0').rstrip('.') if isinstance(x, float) else str(x)
            price_line = f"Мин цена {item_name_lower} на ауке {x_str}"

        users_report = self._get_item_users_report(item_name)
        return f"{price_line}\n\n{users_report}"
