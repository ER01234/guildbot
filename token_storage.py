from typing import List, Tuple

from local_secrets import get_secret


def _pairs(name: str) -> List[Tuple[str, str]]:
    """Пары (wd_token, буквы-фильтр) из секретов."""
    raw = get_secret(name, []) or []
    pairs: List[Tuple[str, str]] = []
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            pairs.append((str(item[0]), str(item[1])))
    return pairs


class TokenStorage:
    """
    Хранилище токенов (VK и WellDungeon API).

    В коде значения НЕ хранятся: берутся из переменных окружения или из
    data/secrets.json (файл в .gitignore — в репозиторий не попадает).
    Второй элемент кортежа — строка-фильтр, какие эффекты доступны персонажу
    (для минимизации лишних API-вызовов).
    """

    @staticmethod
    def JibrillToken() -> str:
        """VK токен бота (не WD)."""
        return str(get_secret("VK_JIBRILL_TOKEN", "") or "")

    @staticmethod
    def BuffersTokens() -> List[Tuple[str, str]]:
        """Токены для бафов (BlessOfAttack, BlessOfDefense, BlessOfLuck, расовые)."""
        return _pairs("WD_BUFFERS_TOKENS")

    @staticmethod
    def WarlocksTokens() -> List[str]:
        """Токены для проклятий (CurseOfPain, CurseOfLoot, CurseOfUnluck)."""
        raw = get_secret("WD_WARLOCKS_TOKENS", []) or []
        return [str(token) for token in raw]

    @staticmethod
    def PaladinsTokens() -> List[Tuple[str, str]]:
        """Токены для благословений паладинов (свет, огонь, воскрешение, очищение)."""
        return _pairs("WD_PALADINS_TOKENS")

    @staticmethod
    def AutopostToken() -> str:
        """Токен автопоста (пустая строка/список = выключено)."""
        return str(get_secret("AUTOPOST_TOKEN", "") or "")
