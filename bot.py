import asyncio
import logging
import os
import random
import time
from decimal import Decimal, ROUND_HALF_UP
from vkbottle.api import API
from vkbottle.bot import Bot, Message
from dev_aiohttp_client import DevAiohttpClient
from word_guesser_handler import WordGuesserHandler
from mystery_handler import MysteryHandler
from well_dungeon_app_handler import WellDungeonAppHandler
from crossroad_handler import CrossroadHandler
from search_command_handler import SearchCommandHandler
from user_handler import UserHandler
from chat_cleaner_handler import cleanup_answer
from token_storage import TokenStorage
from buffs_handler import BuffsHandler
from curses_handler import CursesHandler
from blesses_handler import BlessesHandler


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Watchdog для longpoll: если бот молчит дольше POLLING_STALE_SECONDS —
# перезапускаем polling. После MAX_STALE_KICKS неудачных перезапусков
# выходим из процесса, чтобы хостинг перезапустил бота.
POLLING_STALE_SECONDS = 600   # 10 минут без единого ответа longpoll = завис
POLLING_CHECK_SECONDS = 30    # проверка здоровья раз в 30 секунд
MAX_STALE_KICKS = 3           # 3 перезапуска подряд без восстановления

def main():
    api = API(token=TokenStorage.JibrillToken(), http_client=DevAiohttpClient())
    bot = Bot(api=api)
    word_guesser_handler = WordGuesserHandler()
    mystery_handler = MysteryHandler()
    app_handler = WellDungeonAppHandler()
    crossroad_handler = CrossroadHandler()
    user_handler = UserHandler()
    search_handler = SearchCommandHandler(user_handler=user_handler)
    buffs_handler = BuffsHandler()
    curses_handler = CursesHandler()
    blesses_handler = BlessesHandler()

    async def _safe_tax_scheduler():
        try:
            await user_handler.start_tax_scheduler()
        except Exception as e:
            logger.exception("Tax scheduler crashed: %s", e)

    async def _main():
        state = {"last_poll": time.time(), "polling_task": None, "kick_requested": False}

        # Сердцебиение: любой успешный ответ longpoll (даже пустой) = бот жив.
        # Штатно longpoll отвечает раз в ~25 сек, поэтому 10 минут тишины = завис.
        orig_get_event = bot.polling.get_event

        async def _watched_get_event(server):
            event = await orig_get_event(server)
            state["last_poll"] = time.time()
            return event

        bot.polling.get_event = _watched_get_event

        async def _watchdog():
            stale_kicks = 0
            while True:
                try:
                    await asyncio.sleep(POLLING_CHECK_SECONDS)
                    idle = time.time() - state["last_poll"]
                    if idle < POLLING_STALE_SECONDS:
                        stale_kicks = 0
                        continue

                    stale_kicks += 1
                    logger.error(
                        "Longpoll молчит %.0f сек (попытка %d/%d) — перезапускаю polling",
                        idle, stale_kicks, MAX_STALE_KICKS,
                    )
                    if stale_kicks >= MAX_STALE_KICKS:
                        logger.error("Polling не восстановился — выходим из процесса, хостинг перезапустит бота")
                        os._exit(1)

                    task = state["polling_task"]
                    if task is not None and not task.done():
                        state["kick_requested"] = True
                        task.cancel()
                except Exception:
                    logger.exception("Ошибка в polling watchdog")
                    await asyncio.sleep(POLLING_CHECK_SECONDS)

        asyncio.create_task(_safe_tax_scheduler())
        asyncio.create_task(_watchdog())

        # Цикл polling с автоперезапуском: зависший/упавший longpoll не убивает бота
        while True:
            task = asyncio.create_task(bot.run_polling())
            state["polling_task"] = task
            try:
                await task
            except asyncio.CancelledError:
                if not state.pop("kick_requested", False):
                    raise  # внешняя отмена (завершение) — выходим
                logger.warning("Polling отменён watchdog-ом — перезапуск через 3 сек")
            except Exception as e:
                logger.exception("Polling упал: %s — перезапуск через 3 сек", e)
            await asyncio.sleep(3)

    print("✅ Бот создан")

    @bot.on.message()
    async def handler(message: Message):
        text = message.text or ""
        text_lower = text.strip().lower()

        if text_lower == "ростик пидор":
            await cleanup_answer(message,"А может ты пидор?")
            return

        if text_lower == "рома программист":
            await cleanup_answer(message,"Рома любит яйца огра, а ещё спекаться по субботам")
            return

        if text_lower == "коля садовод":
            await cleanup_answer(message,"Да, он поливает ростки эссенциями")
            return

        if text_lower in ("жив?", "жив"):
            await cleanup_answer(message,"Жив, цел, орёл")
            return

        if text_lower == "/команды":
            help_text = (
                "Список команд:\n"
                "/регистрация — регистрация в боте\n"
                "Мой профиль — обновить профиль + кап статов\n"
                "/мой налог — текущий налог\n"
                "/налог — (ответом на соо) сдать налог золотом\n"
                "Репутация гильдии — обновить налог трофеями\n"
                "/я ем — ваш список \"ем\"+\"жру\"\n"
                "/ем+ <предмет> — добавить в \"ем\"\n"
                "/ем- <предмет> — удалить из \"ем\"\n"
                "/жру+ <предмет> — добавить в \"жру\"\n"
                "/жру- <предмет> — удалить из \"жру\"\n"
                "/кому — кто ест или жрет\n"
                "/список еды - выводит всех, кто что-то ест или жрет\n"
                "/баф <буквы> — бафы (у, а, з, ч, э, д, о, г, м)\n"
                "/неудачи|боли|добычи - проклятия\n"
                "/очищение|воскрешение|свет|огонь - благословения)\n"
                "/цена <предмет> — цена на аукционе\n"
                "/курс — курс осколков\n"
                "/чистыми <о/з> <сумма> — расчет суммы перевода (о=осколки, з=золото)\n"
                "/кубик — бросить кубик (1-20)\n"
            )
            await cleanup_answer(message,help_text)
            return

        res_user = await user_handler.handle(message)
        if res_user is not None:
            await cleanup_answer(message,res_user)
            return

        if text.startswith("/баф "):
            await buffs_handler.handle(message)
            return

        if text.startswith("/неудачи") or text.startswith("/боли") or text.startswith("/добычи"):
            await curses_handler.handle(message)
            return

        if text.startswith("/свет") or text.startswith("/огонь") or text.startswith("/воскрешение") or text.startswith("/очищение"):
            await blesses_handler.handle(message)
            return

        res_search = await search_handler.handle(message)
        if res_search is not None:
            await cleanup_answer(message,res_search)
            return

        if text.startswith("/кубик"):
            res = random.randint(1, 20)
            await cleanup_answer(message,f"{res}")

        if text.startswith("/чистыми"):
            parts = text.split()
            if len(parts) < 3:
                await cleanup_answer(message,"Использование: /чистыми <о/з> <сумма>")
                return

            p_type = parts[1].lower()
            try:
                amount = Decimal(parts[2])
            except ValueError:
                await cleanup_answer(message,"Сумма должна быть числом")
                return

            divisor = Decimal("1.0")
            if 'з' in p_type:
                divisor = Decimal("0.9")
            elif 'о' in p_type:
                divisor = Decimal("0.95")
            else:
                await cleanup_answer(message,"Должно быть о или з")
                return

            res = (amount / divisor).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            await cleanup_answer(message,f"{res}")
            return

        res_app = await app_handler.handle(message)
        if res_app is not None:
             await cleanup_answer(message,res_app)
             return

        if not message.fwd_messages:
            return

        res = await word_guesser_handler.handle(message)
        if res is not None:
            await cleanup_answer(message,res)
            return

        res_ist = await mystery_handler.handle(message)
        if res_ist is not None:
            await cleanup_answer(message,res_ist)
            return

        res_crossroad = await crossroad_handler.handle(message)
        if res_crossroad is not None:
            await cleanup_answer(message,res_crossroad)
            return

    print("🚀 Бот запущен")
    asyncio.run(_main())

if __name__ == "__main__":
    main()