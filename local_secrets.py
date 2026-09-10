"""Единая точка доступа к секретам проекта.

Приоритет источников:
1. переменная окружения с тем же именем (удобно для контейнера);
2. файл data/secrets.json — он в .gitignore, в репозиторий не попадает.

Формат data/secrets.json:
{
  "AI_STUDIO_API_KEY": "<ваш ключ>",
  "VK_JIBRILL_TOKEN": "vk1.a....",
  "WD_BUFFERS_TOKENS": [["wd1_live_...", "эчуаз"], ["wd1_live_...", "чоуаз"]],
  "WD_PALADINS_TOKENS": [["wd1_live_...", "в"]],
  "WD_WARLOCKS_TOKENS": [],
  "AUTOPOST_TOKEN": ""
}
"""
import json
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

SECRETS_FILE = "data/secrets.json"

_cache: Optional[Dict[str, Any]] = None


def _load_file() -> Dict[str, Any]:
    """Читает файл секретов один раз за процесс."""
    global _cache
    if _cache is not None:
        return _cache

    data: Dict[str, Any] = {}
    if os.path.exists(SECRETS_FILE):
        try:
            with open(SECRETS_FILE, encoding="utf-8-sig") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                data = loaded
            else:
                logger.error("%s: ожидался объект JSON, получено %s", SECRETS_FILE, type(loaded).__name__)
        except (OSError, ValueError) as exc:
            logger.error("Не удалось прочитать %s: %s", SECRETS_FILE, exc)
    else:
        logger.warning("%s не найден — секреты берутся только из переменных окружения", SECRETS_FILE)

    _cache = data
    return data


def get_secret(name: str, default: Any = None) -> Any:
    """Возвращает секрет по имени: сперва env, затем data/secrets.json."""
    from_env = os.getenv(name)
    if from_env:
        return from_env
    return _load_file().get(name, default)


def reload_secrets() -> None:
    """Сбрасывает кэш файла (нужно тестам и смене секретов на ходу)."""
    global _cache
    _cache = None
