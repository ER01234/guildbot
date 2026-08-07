from typing import List, Optional
from base_command_handler import BaseCommandHandler
from vkbottle.bot import Message

class WordGuesserHandler(BaseCommandHandler):
    def __init__(self, user_handler=None):
        self.user_handler = user_handler
        self.word_database = [
            "грязный удар", "удар вампира", "сила теней", "слепота",
            "берсеркер", "проклятие тьмы", "целебный огонь", "заражение",
            "слабое исцеление", "мощный удар", "расправа", "рассечение",
            "огонек надежды", "таран", "кровотечение", "раскол",
            "быстрое восстановление", "внимательность", "исследователь",
            "собиратель", "ошеломление", "неуязвимый", "бесстрашие",
            "феникс", "охотник за головами", "упорность", "расчетливость",
            "суеверность", "воздаяние", "прочность", "устрашение",
            "дробящий удар", "стойка сосредоточения", "картограф",
            "парирование", "незаметность", "устойчивость",
            "знания древних", "мародер", "инициативность", "ведьмак",
            "запасливость", "подвижность", "регенерация",
            "презрение к боли", "рыбак", "колющий удар",
            "режущий удар", "непоколебимый", "гладиатор",
            "ученик", "расторопность", "контратака",
            "защитная стойка", "водохлеб", "браконьер",
            "ловкость рук", "атлетика", "угроза",
            "книга адмов", "пещерный корень", "рыбий жир",
            "рыбий глаз", "адский гриб", "адский корень",
            "корень знаний", "болотник", "камнецвет",
            "сквернолист", "чернильник", "сверкающая чешуя",
            "необычная ракушка", "зелье отравления",
            "зелье травм", "зелье снятия травм",
            "зелье меткости", "зелье регенерации",
            "зелье характеристик", "кольцо зелий", "кольцо экипировки",
            "малое кольцо силы", "малое кольцо выносливости",
            "малое кольцо ловкости", "малое кольцо концентрации",
            "малое кольцо точности", "камень судьбы", "кольцо навыков",
            "еретик", "барьер"
        ]

    def guess_word(self, puzzle: str) -> List[str]:
        puzzle_parts = puzzle.split()

        revealed_chars = set()
        for part in puzzle_parts:
            for char in part:
                if char != '■':
                    revealed_chars.add(char.lower())

        result = []
        for word in self.word_database:
            word_parts = word.split()
            if len(puzzle_parts) != len(word_parts):
                continue
            ok = True
            for pp, wp in zip(puzzle_parts, word_parts):
                if len(pp) != len(wp):
                    ok = False
                    break
                for pc, wc in zip(pp, wp):
                    if pc == "■":
                        if wc.lower() in revealed_chars:
                            ok = False
                            break
                    elif pc.lower() != wc.lower():
                        ok = False
                        break
                if not ok:
                    break
            if ok:
                result.append(word.title())
        return result

    @staticmethod
    def analyze_puzzle(puzzle_text: str) -> dict:
        puzzle = puzzle_text.strip()
        possible = WordGuesserHandler().guess_word(puzzle)
        return {
            "puzzle": puzzle,
            "possible_words": possible,
            "possible_count": len(possible),
        }

    def build_food_report(self, word: str) -> str:
        """Список ем/жру для предмета: кто ест и кто жрёт этот предмет."""
        if self.user_handler is None:
            return ""

        item_name = self.user_handler.items_storage.get_item_name(word)
        if not item_name:
            return ""

        eaters = []
        devourers = []

        for user_data in self.user_handler.users.values():
            user_id = user_data.user_id
            full_name = self.user_handler._build_user_name(user_data)
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
            return ""

        return f"\n{item_name}\n" + "\n".join(response)

    @staticmethod
    def extract_puzzle_from_message(text) -> Optional[str]:
        if "■" in text:
            for line in text.split("\n"):
                if "■" in line:
                    return line
        return None

    async def handle(self, message: Message) -> Optional[str]:
        text = message.text or ""
        if message.fwd_messages:
            text = message.fwd_messages[0].text or ""

        if "%" in text:
            return None
        text = text.lower()
        puzzle = self.extract_puzzle_from_message(text)
        if not puzzle:
            return None
        res = self.analyze_puzzle(puzzle)
        if res["possible_count"] == 0:
            return f"Не найдено вариантов для {puzzle}"
        elif res["possible_count"] == 1:
            word = res["possible_words"][0]
            food_report = self.build_food_report(word)
            if food_report:
                return f"Ответ: {word}\n{food_report}"
            return f"Ответ: {word}"
        else:
            return "Варианты:\n" + "\n".join(res["possible_words"])