from typing import List, Optional
from base_command_handler import BaseCommandHandler
from vkbottle.bot import Message

class WordGuesserHandler(BaseCommandHandler):
    def __init__(self):
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
            return f"Ответ: {res['possible_words'][0]}"
        else:
            return "Варианты:\n" + "\n".join(res["possible_words"])