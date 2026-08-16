from typing import List, Tuple


class TokenStorage:
    """
    Хранилище WellDungeon API токенов для разных типов эффектов.
    Каждый токен — строка вида wd1_live_64_random_chars...
    Второй элемент кортежа — строка-фильтр, какие эффекты доступны персонажу
    (для минимизации лишних API-вызовов).
    """

    @staticmethod
    def JibrillToken() -> str:
        """VK токен бота (не WD)."""
        return "vk1.a.uvPjLiil3CdUjXzVrpOcTyWe_lZpSA77TVgfa6fJDyAD1br8B3Dqv07LDoh5067VWqqAYth9QbHX8-DeKKw2lA62CbVTAYyXuf-Um0ji4t6E13pQH9zpvQaGx2brPBtydn89gdTzdl7st2fewIkA6-N1vendSp36-EUt6mzsjKP-qvzbutRHiw-bRvenYrvIytPIn1_jKRMMpeubi1TkrA"

    @staticmethod
    def BuffersTokens() -> List[Tuple[str, str]]:
        """
        Токены для бафов (BlessOfAttack, BlessOfDefense, BlessOfLuck, расовые).
        (wd_token, доступные_буквы)
        """
        return [
            ("wd1_live_WDORvPQ1S5FASXEeg0pr6OxEDUjbb2LXqRuHkdnbaJI5cEd8PK5G88M8KKgtQaJX", "эчуаз"),
            ("wd1_live_qnQ40yZAg8T4U7EkSI70dYuWP1TDA62lZDxvAyGdKbQUgFvcEKpuG6urBnyz7wWY", "эчуаз")
        ]

    @staticmethod
    def WarlocksTokens() -> List[str]:
        """Токены для проклятий (CurseOfPain, CurseOfLoot, CurseOfUnluck)."""
        return [
            "wd1_live_FLMx6xqxNWw1OHfbvyQ8iRQWrKEMBXN96g4onS8l7ftDAVSvfqtF4Ma9VrPBej9Q"
        ]

    @staticmethod
    def PaladinsTokens() -> List[Tuple[str, str]]:
        """Токены для благословений паладинов (свет, огонь, воскрешение, очищение)."""
        return [
            ("wd1_live_0UrGlZpRIf473q3F4VzFNqpOe62ZitoZiGHfE3nStGTV8jBCU5Yp08yDRcWBCR1N", "в")
        ]

    @staticmethod
    def AutopostToken() -> str:
        return ""