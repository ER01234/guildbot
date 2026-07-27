import asyncio
import logging
import random
import os
import textwrap
import json
import time
from vkbottle import API, PhotoMessageUploader
from token_storage import TokenStorage
from dev_aiohttp_client import DevAiohttpClient

logger = logging.getLogger(__name__)

class Autopost:
    def __init__(self, state_file: str = "data/autopost_state.json"):
        self.state_file = state_file
        self.interval = 3600
        self.target_peer_id = 2000000004
        self.resolved_peer_id = None
        self.token = TokenStorage.AutopostToken()
        self.api = API(token=self.token, http_client=DevAiohttpClient()) if self.token else None
        self.uploader = PhotoMessageUploader(self.api) if self.api else None

    def _get_last_post_time(self) -> float:
        if not os.path.exists(self.state_file):
            return 0.0
        try:
            with open(self.state_file, 'r') as f:
                data = json.load(f)
                return data.get("last_post_timestamp", 0.0)
        except:
            return 0.0

    def _save_last_post_time(self, timestamp: float):
        try:
            with open(self.state_file, 'w') as f:
                json.dump({"last_post_timestamp": timestamp}, f)
        except Exception as e:
            logger.error(f"Failed to save autopost state: {e}")

    def get_message_template(self) -> str:
        return textwrap.dedent("""
            Добро пожаловать в мир, где ВСЁ решается игрой

            Гильдия The Empty ищет игроков в свой список достойных
            Прием от 30lvl
            Небольшой налог

            Сейчас мы небольшая группа, с базово необходимыми постройками, однако игроки с опытом помогут разобраться в игре и накинут советов по разным сферам

            Примыкайте к Пустым
        """)

    async def _resolve_peer(self):
        if not self.api:
            return

        if self.resolved_peer_id:
            return
        
        try:
            await asyncio.sleep(1)
            await self.api.messages.get_conversations_by_id(peer_ids=[self.target_peer_id])
            self.resolved_peer_id = self.target_peer_id
        except Exception:
            try:
                await asyncio.sleep(1)
                conversations = await self.api.messages.get_conversations(count=200)
                for item in conversations.items:
                     if item.conversation.peer.id == self.target_peer_id:
                         self.resolved_peer_id = self.target_peer_id
                         return
            except Exception:
                pass

    async def start(self):
        if not self.api:
            return

        await self._resolve_peer()

        while True:
            try:
                last_run = self._get_last_post_time()
                now = time.time()
                next_run = last_run + self.interval
                
                wait_time = next_run - now
                
                if wait_time > 0:
                    logger.info(f"Autopost: sleeping for {wait_time:.2f} seconds")
                    await asyncio.sleep(wait_time)

                if not self.resolved_peer_id:
                    await self._resolve_peer()

                if self.resolved_peer_id:
                    message = self.get_message_template().strip()
                    attachment = None
                    
                    if self.uploader and os.path.exists("autopostpicture.png"):
                        try:
                            await asyncio.sleep(1)
                            attachment = await self.uploader.upload("autopostpicture.png")
                        except Exception as e:
                            logger.error(f"Error uploading autopost picture: {e}")

                    if message or attachment:
                        await asyncio.sleep(1)
                        await self.api.messages.send(
                            peer_id=self.resolved_peer_id,
                            message=message,
                            attachment=attachment,
                            random_id=random.randint(1, 2_000_000_000)
                        )
                        self._save_last_post_time(time.time())
                        
            except Exception as e:
                logger.error(f"Autopost error: {e}")
                await asyncio.sleep(60)