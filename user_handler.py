import json
import os
import re
import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Dict, List
from vkbottle.bot import Message
from vkbottle import VKAPIError
from base_command_handler import BaseCommandHandler
from items_storage import ItemsStorage
from user_repository import UserRepository, UserDocument

logger = logging.getLogger(__name__)


def get_required_tax(level: int):
    if level < 50:
        gold = 50 * level
        trophies = 10
    else:
        gold = 100 * level
        trophies = 20
    return gold, trophies


TAX_GRACE_PERIOD_SECONDS = 7 * 24 * 60 * 60


class UserHandler(BaseCommandHandler):
    def __init__(self):
        self.users_repository = UserRepository(db_path="data/users_db.json")
        self.state_file = "data/bot_state.json"
        self.processed_messages_file = "data/processed_tax_messages.json"
        self.users: Dict[str, UserDocument] = self._load_data()
        self.processed_messages: List[str] = self._load_processed_messages()
        self.items_storage = ItemsStorage()

    @staticmethod
    def _build_user_name(user_data: UserDocument) -> str:
        return f"{user_data.first_name} {user_data.last_name}".strip()

    @staticmethod
    def _is_tax_grace_period(user_data: UserDocument) -> bool:
        try:
            registered_on = float(user_data.registered_on)
        except (TypeError, ValueError, AttributeError):
            return False

        return (time.time() - registered_on) < TAX_GRACE_PERIOD_SECONDS

    def _format_tax_balance(self, user_data: UserDocument, *, tax_free_message: Optional[str] = None) -> str:
        full_name = self._build_user_name(user_data)

        if user_data.tax_free:
            return tax_free_message or f"{full_name} освобожден от налога!"

        lvl = user_data.user_lvl
        req_gold, req_trophies = get_required_tax(lvl)
        balance_trophies = user_data.user_tax_trophies
        balance_gold = user_data.user_tax_gold
        left_trophies = max(0, -balance_trophies)
        left_gold = max(0, -balance_gold)
        status_trophies = f"Долг: {left_trophies}" if left_trophies > 0 else f"Переплата: {balance_trophies}"
        status_gold = f"Долг: {left_gold}" if left_gold > 0 else f"Переплата: {balance_gold}"

        result = f"{full_name} налоговый баланс (Уровень {lvl}):\n"
        if user_data.buffer_tax_free:
            result += f"Требуется в неделю: {req_trophies} трофеев\n"
            result += f"🏆 Налог трофеями\n{status_trophies}"
        else:
            result += f"Требуется в неделю: {req_gold} золота, {req_trophies} трофеев\n"
            result += f"💰 Налог золотом\n{status_gold}\n"
            result += f"🏆 Налог трофеями\n{status_trophies}"

        return result

    @staticmethod
    def _get_first_forwarded_message(message: Message):
        fwd_messages = getattr(message, "fwd_messages", None) or []
        if not fwd_messages:
            return None
        return fwd_messages[0]

    @staticmethod
    def _extract_user_id_from_message(message_obj) -> Optional[int]:
        user_id = getattr(message_obj, "from_id", None)
        if isinstance(user_id, int) and user_id > 0:
            return user_id

        text = getattr(message_obj, "text", "") or ""
        id_match = re.search(r"\[id(\d+)\|[^]]+]", text)
        if id_match:
            return int(id_match.group(1))

        return None

    def _resolve_user_from_message_object(
        self,
        message_obj,
        error_message: str
    ) -> tuple[Optional[UserDocument], Optional[str]]:
        user_id = self._extract_user_id_from_message(message_obj)
        if not user_id:
            return None, error_message

        user = self.users.get(str(user_id))
        if not user:
            return None, f"Пользователь @id{user_id} не зарегистрирован. Используйте /регистрация"

        return user, None

    def _resolve_user_from_forwarded_message(self, message: Message) -> tuple[Optional[UserDocument], Optional[str]]:
        forwarded_message = self._get_first_forwarded_message(message)
        if not forwarded_message:
            return None, "Перешлите сообщение пользователя"

        return self._resolve_user_from_message_object(
            forwarded_message,
            "Не удалось определить пользователя из пересланного сообщения"
        )

    def _extract_target_user_id(self, message: Message) -> tuple[Optional[int], Optional[str]]:
        """Извлекает user_id из reply/fwd/текста команды. Работает даже для незарегистрированных."""
        # 1) Ответ (reply)
        reply_message = getattr(message, "reply_message", None)
        if reply_message:
            user_id = self._extract_user_id_from_message(reply_message)
            if user_id is not None:
                return user_id, None

        # 2) Пересланные сообщения
        forwarded_message = self._get_first_forwarded_message(message)
        if forwarded_message:
            user_id = self._extract_user_id_from_message(forwarded_message)
            if user_id is not None:
                return user_id, None

        # 3) ID из текста команды (например: /потеряй пидора 123456)
        parts = message.text.split(maxsplit=1)
        if len(parts) >= 2:
            try:
                user_id = int(parts[1].strip())
                if user_id > 0:
                    return user_id, None
            except ValueError:
                pass

        return None, "Перешлите сообщение пользователя, ответьте на него или укажите ID"

    def _resolve_user_from_id(self, user_id: int) -> Optional[UserDocument]:
        """Возвращает UserDocument если пользователь зарегистрирован, иначе None."""
        return self.users.get(str(user_id))

    def _resolve_target_user_for_admin_action(self, message: Message) -> tuple[Optional[UserDocument], Optional[str]]:
        """Для действий, где нужна регистрация (налоги, еда и т.д.)."""
        target_id, error = self._extract_target_user_id(message)
        if target_id is None:
            return None, error

        user = self._resolve_user_from_id(target_id)
        if user is not None:
            return user, None
        return None, f"Пользователь @id{target_id} не зарегистрирован. Используйте /регистрация"

    def _build_debtors_report(self, *, previous_week: bool = False) -> str:
        debtors = []

        for uid_str, user in self.users.items():
            if user.tax_free:
                continue

            balance_gold = user.user_tax_gold
            balance_trophies = user.user_tax_trophies

            if previous_week:
                req_gold, req_trophies = get_required_tax(user.user_lvl)
                if user.buffer_tax_free:
                    balance_trophies -= req_trophies
                else:
                    balance_gold -= req_gold
                    balance_trophies -= req_trophies

            left_gold = max(0, -balance_gold)
            left_trophies = max(0, -balance_trophies)

            last_show = user.last_show
            if last_show is None:
                last_show = -time.time()

            days_from_last_show = (time.time() - last_show) / 60 / 60 / 24
            profile = "Покажи профиль" if days_from_last_show >= 5 else ""

            user_name = self._build_user_name(user)
            mention = f"@id{uid_str} ({user_name})"

            if user.buffer_tax_free:
                if left_trophies > 0:
                    debtors.append(f"{mention} (Долг: {left_trophies} трофеев) {profile}".strip())
            else:
                if left_gold > 0 or left_trophies > 0:
                    debtors.append(f"{mention} (Долг: {left_gold} золота, {left_trophies} трофеев) {profile}".strip())

        if not debtors:
            return "Все сдали налог"

        title = "Долги за прошлую неделю" if previous_week else "Не сдали налог"
        return title + ":\n" + "\n".join(debtors)

    def _build_food_list_report(self) -> str:
        sections = []

        for item_name in self.items_storage.items.keys():
            eaters = []
            devourers = []

            for user_data in self.users.values():
                user_tag = f"@id{user_data.user_id} ({self._build_user_name(user_data)})"

                if item_name in user_data.eat_books:
                    eaters.append(user_tag)

                if item_name in user_data.devour_books:
                    devourers.append(user_tag)

            if not eaters and not devourers:
                continue

            section = [item_name]
            if eaters:
                section.append("Едят:")
                section.append("\n".join(eaters))

            if devourers:
                section.append("Жрут:")
                section.append("\n".join(devourers))

            sections.append("\n".join(section))

        if not sections:
            return "Никто ничего не ест и не жрет"

        return "\n\n".join(sections)

    def _load_processed_messages(self) -> List[str]:
        if not os.path.exists(self.processed_messages_file):
            return []
        try:
            with open(self.processed_messages_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []

    def _save_processed_messages(self):
        try:
            with open(self.processed_messages_file, 'w', encoding='utf-8') as f:
                json.dump(self.processed_messages, f, ensure_ascii=False, indent=2)
        except IOError as e:
            print(f"Error saving processed messages: {e}")

    def _load_data(self) -> Dict[str, UserDocument]:
        try:
            return self.users_repository.load_users()
        except Exception as e:
            logger.error(f"Error loading users from repository: {e}")
            return {}

    def _save_data(self):
        try:
            self.users_repository.save_users(self.users)
        except Exception as e:
            logger.error(f"Error saving users to repository: {e}")

    def get_last_reset_time(self) -> float:
        if not os.path.exists(self.state_file):
            return 0.0
        try:
            with open(self.state_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get("last_reset_timestamp", 0.0)
        except (json.JSONDecodeError, IOError):
            return 0.0

    def set_last_reset_time(self, timestamp: float):
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump({"last_reset_timestamp": timestamp}, f)
        except IOError as e:
            print(f"Error saving state: {e}")

    async def start_tax_scheduler(self):
        while True:
            try:
                now = datetime.now(timezone.utc)
                today = now.date()
                monday = today - timedelta(days=today.weekday())
                reset_time = datetime(monday.year, monday.month, monday.day, 0, 0, 0, tzinfo=timezone.utc)
                if now < reset_time:
                     monday = monday - timedelta(days=7)
                     reset_time = datetime(monday.year, monday.month, monday.day, 0, 0, 0, tzinfo=timezone.utc)
                last_reset = self.get_last_reset_time()
                if last_reset < reset_time.timestamp():
                    print(f"Performing weekly tax deduction. Last: {last_reset}, Target: {reset_time.timestamp()}")
                    for user_data in self.users.values():
                        if user_data.tax_free:
                            continue
                        if self._is_tax_grace_period(user_data):
                            continue

                        lvl = user_data.user_lvl
                        req_gold, req_trophies = get_required_tax(lvl)
                        user_data.user_tax_trophies = user_data.user_tax_trophies - req_trophies
                        if user_data.buffer_tax_free:
                            continue

                        user_data.user_tax_gold = user_data.user_tax_gold - req_gold
                    self._save_data()
                    self.set_last_reset_time(now.timestamp())

                now = datetime.now(timezone.utc)
                today = now.date()
                monday = today - timedelta(days=today.weekday())
                this_week_reset = datetime(monday.year, monday.month, monday.day, 0, 0, 0, tzinfo=timezone.utc)
                if now >= this_week_reset:
                    next_reset = this_week_reset + timedelta(days=7)
                else:
                    next_reset = this_week_reset
                diff = (next_reset - now).total_seconds()
                wait_seconds = max(60.0, diff)
                sleep_time = wait_seconds + 5

                logger.info(f"Tax scheduler sleeping for {sleep_time:.2f} seconds")
                await asyncio.sleep(sleep_time)

            except Exception as e:
                logger.error(f"Error in tax scheduler: {e}")
                await asyncio.sleep(600)

    async def handle(self, message: Message) -> Optional[str]:
        text = (message.text or "").strip()
        text_lower = text.lower()

        if text_lower == "/регистрация":
            return await self.handle_registration(message)
        elif text_lower == "/потеряй пидора":
             return await self.handle_kick_user(message)
        elif "ваш профиль" in text_lower or text.startswith("👑"):
             return await self.handle_profile_update(message)
        elif text_lower.startswith("/я ем"):
            return self.handle_i_eat(message)
        elif text_lower.startswith("/ем+"):
            return self.handle_eat_plus(message)
        elif text_lower.startswith("/ем-"):
            return self.handle_eat_minus(message)
        elif text_lower.startswith("/жру+"):
            return self.handle_devour_plus(message)
        elif text_lower.startswith("/жру-"):
            return self.handle_devour_minus(message)
        elif text_lower.startswith("/кому"):
             return self.handle_who_to(message)
        elif text_lower == "/список еды":
             return self.handle_food_list()
        elif text_lower == "/мой налог":
             return self.handle_my_tax(message)
        elif text_lower == "/твой налог":
             return self.handle_forwarded_tax(message)
        elif text_lower == "/кто не сдал":
             return self.handle_who_not_paid(message)
        elif text_lower == "/должники":
             return self.handle_debtors(message)
        elif text_lower == "/налог":
             return await self.handle_tax_payment(message)
        elif text_lower == "/налог-":
             return await self.handle_free_from_tax(message)
        elif text_lower == "/налог+":
             return await self.handle_force_pay_tax(message)
        elif text_lower == "/бафферналог-":
             return await self.handle_free_from_tax_buffer(message)
        elif text_lower == "/бафферналог+":
                     return await self.handle_force_pay_tax_buffer(message)
        elif text_lower.startswith("/начислить голду"):
                return await self.handle_increase_tax_gold(message)
        elif text_lower.startswith("/списать голду"):
                return await self.handle_reduce_tax_gold(message)
        elif text_lower.startswith("/начислить трофы"):
                return await self.handle_increase_tax_trophy(message)
        elif text_lower.startswith("/списать трофы"):
                return await self.handle_reduce_tax_trophy(message)
        elif "элитных трофеев сдано в гильдию:" in text_lower:
                return await self.handle_trophy_donation(message)

        return None

    async def handle_kick_user(self, message: Message) -> str:
        if message.from_id != 391196432:
             return "Кик игроков доступен только главгаду"

        # Извлекаем user_id любым способом — регистрация НЕ требуется
        target_user_id, error = self._extract_target_user_id(message)
        if target_user_id is None:
             return error or "Не удалось определить пользователя"

        # Исключаем из беседы
        if message.peer_id > 2000000000:
             chat_id = message.peer_id - 2000000000
             try:
                 await asyncio.sleep(1)
                 await message.ctx_api.messages.remove_chat_user(chat_id=chat_id, user_id=target_user_id)
             except VKAPIError as e:
                 logger.error(f"Failed to kick user {target_user_id}: {e}")

        # Удаляем из базы, если зарегистрирован
        target_user_id_str = str(target_user_id)
        if target_user_id_str in self.users:
             del self.users[target_user_id_str]
             self._save_data()

        return "Пользователь исключен из беседы"

    async def handle_profile_update(self, message: Message) -> str:
        text = message.text or ""
        lines = text.split('\n')
        first_line = lines[0]

        user_id = None

        id_match = re.search(r"\[id(\d+)\|[^\]]+\]", first_line)
        if id_match:
            user_id = int(id_match.group(1))

        if not user_id:
            clean_line = first_line.strip()
            name_match = re.search(r"^👑\s*(?:\[[^\]]+\]\s*)?(?P<name>.*)", clean_line)

            if name_match:
                target_name = name_match.group("name").strip().lower()
                if "," in target_name:
                    target_name = target_name.split(",")[0].strip()
                if "ваш профиль" in target_name:
                    target_name = target_name.split("ваш профиль")[0].strip()

                found_ids = []

                for uid_str, u_data in self.users.items():
                    u_first = u_data.first_name.strip()
                    u_full = self._build_user_name(u_data).lower()

                    if u_full == target_name or u_first == target_name:
                        found_ids.append(int(uid_str))

                if len(found_ids) == 1:
                    user_id = found_ids[0]
                elif len(found_ids) > 1:
                    return f"Найдено несколько пользователей с именем '{target_name}'"

        if not user_id:
             return "Не удалось определить пользователя"

        level_match = re.search(r"Уровень:\s*(\d+)", text)
        if not level_match:
            return "Не удалось найти уровень в сообщении"

        level = int(level_match.group(1))

        user_id_str = str(user_id)

        if user_id_str not in self.users:
            return f"Пользователь @id{user_id} не зарегистрирован. Используйте /регистрация"

        old_level = self.users[user_id_str].user_lvl

        if level > old_level:
            if not self._is_tax_grace_period(self.users[user_id_str]):
                if old_level == 1:
                    old_gold, old_trophies = 0, 0
                else:
                    old_gold, old_trophies = get_required_tax(old_level)

                new_gold, new_trophies = get_required_tax(level)

                diff_gold = new_gold - old_gold
                diff_trophies = new_trophies - old_trophies

                if diff_gold > 0:
                    self.users[user_id_str].user_tax_gold = self.users[user_id_str].user_tax_gold - diff_gold
                if diff_trophies > 0:
                    self.users[user_id_str].user_tax_trophies = self.users[user_id_str].user_tax_trophies - diff_trophies

        self.users[user_id_str].user_lvl = level
        self.users[user_id_str].last_show = time.time()

        self._save_data()

        name = self.users[user_id_str].first_name or "Пользователь"
        msg = ""
        if level > old_level:
            msg = f"Уровень пользователя @id{user_id}({name}) обновлен до {level}"

        # Level limits calculation
        x = 0
        y = 0
        z = 0

        for line in lines:
            if line.startswith("👊"):
                parts = line.split()
                try:
                    for part in parts:
                        if part.startswith('👊'):
                            x = int(part[1:])
                        elif part.startswith('🖐'):
                            y = int(part[1:])
                        elif part.startswith('❤'):
                            z = int(part[1:])
                except (ValueError, IndexError):
                    pass

        if x != 0 and y != 0 and z != 0:
            endurance = 3 * level + 45 - z
            strength_agility = 6 * level + 90 - (x + y)
            msg += f"\n\nДо капа:\nВыносливость - {endurance}\nСила+ловкость - {strength_agility}"

        return msg

    async def handle_tax_payment(self, message: Message) -> str:
        if not message.reply_message:
            return "Перешлите сообщение о переводе золота с командой /налог"

        msg_unique_id = f"{message.peer_id}_{message.reply_message.conversation_message_id}"
        if msg_unique_id in self.processed_messages:
            return "Этот перевод уже был учтен ранее"

        fwd_text = message.reply_message.text

        gold_match = re.search(r"получено (\d+) золота от игрока", fwd_text)
        if not gold_match:
             return "Не удалось определить сумму золота в пересланном сообщении"

        sender_match = re.search(r"от игрока\s*\[id(\d+)\|.*?\]", fwd_text)
        if not sender_match:
             return "Не удалось определить отправителя золота"

        receiver_match = re.search(r"\s*\[id(\d+)\|.*?\],", fwd_text)
        if receiver_match and int(receiver_match.group(1)) != 391196432:
             return "Получателем золота должен быть главгад"

        paid_user_id = int(sender_match.group(1))

        if paid_user_id != message.from_id:
             return f"Нельзя использовать сообщение другого пользователя для оплаты налога"

        target_user_id = str(paid_user_id)
        if target_user_id not in self.users:
             return f"Пользователь @id{target_user_id} не зарегистрирован. Используйте /регистрация"

        gold_amount = Decimal(gold_match.group(1))
        gold_amount = (gold_amount / Decimal("0.9")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        gold_amount = int(gold_amount)
        current_gold = self.users[target_user_id].user_tax_gold
        self.users[target_user_id].user_tax_gold = current_gold + gold_amount
        self._save_data()

        self.processed_messages.append(msg_unique_id)
        self._save_processed_messages()

        name = self._build_user_name(self.users[target_user_id])
        return f"Записано {gold_amount} золота в налог для @id{target_user_id} ({name})"

    async def handle_trophy_donation(self, message: Message) -> str:
        text = message.text or ""
        first_line = text.split('\n')[0]

        user_id = None

        id_match = re.search(r"\[id(\d+)\|[^\]]+\]", first_line)
        if id_match:
            user_id = int(id_match.group(1))

        if not user_id:
            clean_line = first_line.strip()
            name_match = re.search(r"^(?:👑\s*)?(?:\[[^\]]+\]\s*)?(?P<name>.*)", clean_line)

            if name_match:
                target_name = name_match.group("name").strip().lower()
                if "," in target_name:
                    target_name = target_name.split(",")[0].strip()
                if "элитных трофеев" in target_name:
                    target_name = target_name.split("элитных трофеев")[0].strip()

                found_ids = []
                for uid_str, u_data in self.users.items():
                    u_first = u_data.first_name.strip()
                    u_full = self._build_user_name(u_data).lower()
                    if u_full == target_name or u_first == target_name:
                        found_ids.append(int(uid_str))

                if len(found_ids) == 1:
                    user_id = found_ids[0]
                elif len(found_ids) > 1:
                    return f"Найдено несколько пользователей с именем '{target_name}'"

        if not user_id:
            return "Не удалось определить пользователя из сообщения"

        user_id_str = str(user_id)
        if user_id_str not in self.users:
             return f"Пользователь @id{user_id} не зарегистрирован"

        trophy_match = re.search(r"элитных трофеев сдано в гильдию:\s*(\d+)", text)
        if not trophy_match:
            return "Не удалось определить количество трофеев"

        trophies = int(trophy_match.group(1))

        user_data = self.users[user_id_str]
        name = self._build_user_name(user_data)

        if user_data.initial_trophies is None:
            user_data.initial_trophies = trophies
            user_data.current_trophies = trophies
            self._save_data()
            return f"Начальное количество трофеев установлено для @id{user_id} ({name})"

        prev_current = user_data.current_trophies
        if prev_current is None:
            prev_current = user_data.initial_trophies

        if trophies != prev_current:
            diff = trophies - prev_current
            user_data.current_trophies = trophies
            user_data.user_tax_trophies = user_data.user_tax_trophies + diff
            msg = f"Записано {diff} трофеев в налог для @id{user_id} ({name})"
        else:
            msg = f"Количество трофеев не изменилось для @id{user_id} ({name})."

        self._save_data()
        return msg

    async def handle_registration(self, message: Message) -> str:
        user_id_str = str(message.from_id)

        users = await self.get_chat_users(message)
        current_user = next((u for u in users if u['id'] == message.from_id), None)

        first_name = (current_user['first_name'] if current_user else "").strip()
        last_name = (current_user['last_name'] if current_user else "").strip()

        if user_id_str in self.users:
            self.users[user_id_str].first_name = first_name
            self.users[user_id_str].last_name = last_name

            self._save_data()
            return "Вы уже зарегистрированы."

        self.users[user_id_str] = UserDocument(
            user_id=message.from_id,
            first_name=first_name,
            last_name=last_name,
            eat_books=[],
            devour_books=[],
            user_lvl=1,
            user_tax_gold=0,
            user_tax_trophies=0,
            initial_trophies=None,
            current_trophies=None,
            tax_free=False,
            buffer_tax_free=False,
            registered_on=time.time(),
            last_show=None,
        )
        self._save_data()
        return "Вы успешно зарегистрированы!"

    def handle_i_eat(self, message: Message) -> str:
        user_id = message.from_id
        user_id_str = str(user_id)

        if user_id_str not in self.users:
            if user_id == message.from_id:
                return "Вы не зарегистрированы. Используйте /регистрация"

        user_data = self.users[user_id_str]
        eat_books = user_data.eat_books
        devour_books = user_data.devour_books
        full_name = self._build_user_name(user_data)

        if not eat_books and not devour_books:
            return f"Пользователь @id{user_id} ({full_name}) ничего не ест и не жрет"

        response = [f"Пользователь @id{user_id} ({full_name}):"]

        if eat_books:
            response.append(f"Ест: {', '.join(eat_books)}")

        if devour_books:
            response.append(f"Жрет: {', '.join(devour_books)}")

        return "\n".join(response)

    def handle_eat_plus(self, message: Message) -> str:
        user_id_str = str(message.from_id)
        if user_id_str not in self.users:
             return "Сначала зарегистрируйтесь с помощью /регистрация"

        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            return "Укажите название книги или предмета"

        queries = [q.strip() for q in parts[1].split(',')]
        added_items = []
        already_added = False
        not_found = []

        for query in queries:
            if not query:
                continue
            item_name = self.items_storage.get_item_name(query)

            if not item_name:
                not_found.append(query)
                continue

            if item_name in self.users[user_id_str].eat_books:
                already_added = True
                continue

            self.users[user_id_str].eat_books.append(item_name)
            added_items.append(item_name)

        self._save_data()

        response = []
        if added_items:
            response.append(f"Записано: {', '.join(added_items)}")
        if already_added:
            response.append("Уже добавлено в ем")
        if not_found:
            response.append(f"Не найдено: {', '.join(not_found)}")

        if not response:
            return "Уже добавлено в ем"

        return "\n".join(response)

    def handle_eat_minus(self, message: Message) -> str:
        user_id_str = str(message.from_id)
        if user_id_str not in self.users:
             return "Сначала зарегистрируйтесь с помощью /регистрация"

        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            return "Укажите название книги или предмета для удаления"

        queries = [q.strip() for q in parts[1].split(',')]
        removed_items = []
        not_found_in_db = []

        for query in queries:
            if not query:
                continue
            item_name = self.items_storage.get_item_name(query)

            if not item_name:
                not_found_in_db.append(query)
                continue

            if item_name in self.users[user_id_str].eat_books:
                self.users[user_id_str].eat_books.remove(item_name)
                removed_items.append(item_name)

        self._save_data()

        response = []
        if removed_items:
            response.append(f"Удалено: {', '.join(removed_items)}")
        if not_found_in_db:
            response.append(f"Не найдено: {', '.join(not_found_in_db)}")

        return "\n".join(response)

    def handle_devour_plus(self, message: Message) -> str:
        user_id_str = str(message.from_id)
        if user_id_str not in self.users:
            return "Сначала зарегистрируйтесь с помощью /регистрация"

        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            return "Укажите название книги или предмета"

        queries = [q.strip() for q in parts[1].split(',')]
        added_items = []
        already_added = False
        not_found = []

        for query in queries:
            if not query:
                continue
            item_name = self.items_storage.get_item_name(query)

            if not item_name:
                not_found.append(query)
                continue

            if item_name in self.users[user_id_str].devour_books:
                already_added = True
                continue

            self.users[user_id_str].devour_books.append(item_name)
            added_items.append(item_name)

        self._save_data()

        response = []
        if added_items:
            response.append(f"Записано: {', '.join(added_items)}")
        if already_added:
            response.append("Уже добавлено в жру")
        if not_found:
            response.append(f"Не найдено: {', '.join(not_found)}")

        if not response:
            return "Уже добавлено в жру"

        return "\n".join(response)

    def handle_devour_minus(self, message: Message) -> str:
        user_id_str = str(message.from_id)
        if user_id_str not in self.users:
            return "Сначала зарегистрируйтесь с помощью /регистрация"

        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            return "Укажите название книги или предмета для удаления"

        queries = [q.strip() for q in parts[1].split(',')]
        removed_items = []
        not_found_in_db = []

        for query in queries:
            if not query:
                continue
            item_name = self.items_storage.get_item_name(query)

            if not item_name:
                not_found_in_db.append(query)
                continue

            if item_name in self.users[user_id_str].devour_books:
                self.users[user_id_str].devour_books.remove(item_name)
                removed_items.append(item_name)

        self._save_data()

        response = []
        if removed_items:
            response.append(f"Удалено: {', '.join(removed_items)}")
        if not_found_in_db:
            response.append(f"Не найдено: {', '.join(not_found_in_db)}")

        return "\n".join(response)

    def handle_who_to(self, message: Message) -> str:
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            return "Укажите название книги или предмета"

        query = parts[1]
        item_name = self.items_storage.get_item_name(query)

        if not item_name:
            return f"Предмет '{query}' не найден"

        eaters = []
        devourers = []

        for user_data in self.users.values():
            user_id = user_data.user_id
            full_name = self._build_user_name(user_data)

            user_tag = f"@id{user_id} ({full_name})"

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
            return f"\n{item_name} никто не ест и не жрет"

        return f"\n{item_name}\n".join(response)

    def handle_food_list(self) -> str:
        return self._build_food_list_report()

    def handle_my_tax(self, message: Message) -> str:
        user_id_str = str(message.from_id)
        if user_id_str not in self.users:
            return "Сначала зарегистрируйтесь /регистрация"

        return self._format_tax_balance(self.users[user_id_str], tax_free_message="Вы освобождены от налога!")

    def handle_forwarded_tax(self, message: Message) -> str:
        user_data, error = self._resolve_target_user_for_admin_action(message)
        if error:
            return error

        if user_data is None:
            return "Не удалось определить пользователя из пересланного сообщения"

        return self._format_tax_balance(user_data)

    def handle_who_not_paid(self, message: Message) -> str:
        if message.from_id != 391196432:
            return "Проверка налога доступна только главгаду"

        return self._build_debtors_report(previous_week=False)

    def handle_debtors(self, message: Message) -> str:
        if message.from_id != 391196432:
            return "Проверка налога доступна только главгаду"

        return self._build_debtors_report(previous_week=True)

    async def handle_free_from_tax(self, message: Message) -> str:
        if message.from_id != 391196432:
            return "Освобождение от налога доступно только главгаду"

        target_user, error = self._resolve_target_user_for_admin_action(message)
        if error:
            return error

        if target_user is None:
            return "Не удалось определить пользователя"

        target_id = target_user.user_id
        target_id_str = str(target_id)

        self.users[target_id_str].tax_free = True
        self._save_data()

        name = self._build_user_name(self.users[target_id_str])
        return f"Пользователь @id{target_id} ({name}) освобожден от налога"

    async def handle_force_pay_tax(self, message: Message) -> str:
        if message.from_id != 391196432:
            return "Назначение налога доступно только главгаду"

        target_user, error = self._resolve_target_user_for_admin_action(message)
        if error:
            return error

        if target_user is None:
            return "Не удалось определить пользователя"

        target_id = target_user.user_id
        target_id_str = str(target_id)

        self.users[target_id_str].tax_free = False
        self._save_data()

        name = self._build_user_name(self.users[target_id_str])
        return f"Пользователь @id{target_id} ({name}) теперь платит налог"

    async def handle_free_from_tax_buffer(self, message: Message) -> str:
        if message.from_id != 391196432:
            return "Освобождение от налога доступно только главгаду"

        target_user, error = self._resolve_target_user_for_admin_action(message)
        if error:
            return error

        if target_user is None:
            return "Не удалось определить пользователя"

        target_id = target_user.user_id
        target_id_str = str(target_id)

        self.users[target_id_str].buffer_tax_free = True
        self._save_data()

        name = self._build_user_name(self.users[target_id_str])
        return f"Пользователь @id{target_id} ({name}) освобожден от налога для бафферов"

    async def handle_force_pay_tax_buffer(self, message: Message) -> str:
        if message.from_id != 391196432:
            return "Назначение налога доступно только главгаду"

        target_user, error = self._resolve_target_user_for_admin_action(message)
        if error:
            return error

        if target_user is None:
            return "Не удалось определить пользователя"

        target_id = target_user.user_id
        target_id_str = str(target_id)


        self.users[target_id_str].buffer_tax_free = False
        self._save_data()

        name = self._build_user_name(self.users[target_id_str])
        return f"Пользователь @id{target_id} ({name}) теперь платит налог для бафферов"

    async def handle_reduce_tax_gold(self, message: Message) -> str:
        """Списать голду — уменьшить золотой баланс пользователя."""
        if message.from_id != 391196432:
            return "Списание золота доступно только главгаду"

        target_user, error = self._resolve_target_user_for_admin_action(message)
        if error:
            return error

        if target_user is None:
            return "Не удалось определить пользователя"

        amount = self._parse_amount_from_command(message.text, "/списать_голду")
        if amount is None:
            return "Укажите целое число после команды, например: /списать_голду 100"

        target_id_str = str(target_user.user_id)
        self.users[target_id_str].user_tax_gold -= amount
        self._save_data()

        name = self._build_user_name(self.users[target_id_str])
        return f"Списано {amount} золота у @id{target_user.user_id} ({name}). Текущий баланс: {self.users[target_id_str].user_tax_gold}"

    async def handle_increase_tax_gold(self, message: Message) -> str:
        """Начислить голду — увеличить золотой баланс пользователя."""
        if message.from_id != 391196432:
            return "Начисление золота доступно только главгаду"

        target_user, error = self._resolve_target_user_for_admin_action(message)
        if error:
            return error

        if target_user is None:
            return "Не удалось определить пользователя"

        amount = self._parse_amount_from_command(message.text, "/начислить_голду")
        if amount is None:
            return "Укажите целое число после команды, например: /начислить_голду 100"

        target_id_str = str(target_user.user_id)
        self.users[target_id_str].user_tax_gold += amount
        self._save_data()

        name = self._build_user_name(self.users[target_id_str])
        return f"Начислено {amount} золота @id{target_user.user_id} ({name}). Текущий баланс: {self.users[target_id_str].user_tax_gold}"

    async def handle_reduce_tax_trophy(self, message: Message) -> str:
        """Списать трофы — уменьшить трофейный баланс пользователя."""
        if message.from_id != 391196432:
            return "Списание трофеев доступно только главгаду"

        target_user, error = self._resolve_target_user_for_admin_action(message)
        if error:
            return error

        if target_user is None:
            return "Не удалось определить пользователя"

        amount = self._parse_amount_from_command(message.text, "/списать_трофы")
        if amount is None:
            return "Укажите целое число после команды, например: /списать_трофы 50"

        target_id_str = str(target_user.user_id)
        self.users[target_id_str].user_tax_trophies -= amount
        self._save_data()

        name = self._build_user_name(self.users[target_id_str])
        return f"Списано {amount} трофеев у @id{target_user.user_id} ({name}). Текущий баланс: {self.users[target_id_str].user_tax_trophies}"

    async def handle_increase_tax_trophy(self, message: Message) -> str:
        """Начислить трофы — увеличить трофейный баланс пользователя."""
        if message.from_id != 391196432:
            return "Начисление трофеев доступно только главгаду"

        target_user, error = self._resolve_target_user_for_admin_action(message)
        if error:
            return error

        if target_user is None:
            return "Не удалось определить пользователя"

        amount = self._parse_amount_from_command(message.text, "/начислить_трофы")
        if amount is None:
            return "Укажите целое число после команды, например: /начислить_трофы 50"

        target_id_str = str(target_user.user_id)
        self.users[target_id_str].user_tax_trophies += amount
        self._save_data()

        name = self._build_user_name(self.users[target_id_str])
        return f"Начислено {amount} трофеев @id{target_user.user_id} ({name}). Текущий баланс: {self.users[target_id_str].user_tax_trophies}"

    @staticmethod
    def _parse_amount_from_command(text: str, command: str) -> Optional[int]:
        """Парсит целое положительное число после многословной команды.

        Учитывает, что команда может быть написана через пробелы (``/начислить голду 100``)
        или через подчёркивания (``/начислить_голду 100``).
        """
        if not text:
            return None

        # Убираем команду из начала строки
        rest = text
        for variant in (command, command.replace("_", " ")):
            if rest.lower().startswith(variant.lower()):
                rest = rest[len(variant):]
                break
        rest = rest.strip()

        if not rest:
            return None
        try:
            amount = int(rest)
        except (ValueError, TypeError):
            return None
        if amount <= 0:
            return None
        return amount

    async def get_chat_users(self, message: Message) -> List[dict]:
        if not message.ctx_api:
            return []

        try:
            await asyncio.sleep(1)
            response = await message.ctx_api.messages.get_conversation_members(
                peer_id=message.peer_id,
                fields=["is_bot", "first_name", "last_name"]
            )

            profiles = response.profiles
            items = response.items
            member_ids = {item.member_id for item in items}
            valid_users = []

            for user in profiles:
                if user.id not in member_ids:
                    continue
                if getattr(user, "is_bot", False):
                    continue
                if getattr(user, 'deactivated', None):
                    continue
                if user.id < 0:
                    continue

                valid_users.append({
                    'id': user.id,
                    'first_name': user.first_name,
                    'last_name': user.last_name
                })

            return valid_users

        except Exception as e:
            print(f"Error fetching chat users for peer {message.peer_id}: {e}")
            return []