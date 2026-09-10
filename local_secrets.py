"""Единая точка доступа к секретам проекта.

Приоритет источников:
1. переменная окружения с тем же именем (удобно для контейнера);
2. файл data/secrets.json — он в .gitignore, в репозиторий не попадает.

Файл ищется по нескольким путям (первый найденный выигрывает):
  <папка проекта>/data/secrets.json   — основной вариант
  <текущая папка>/data/secrets.json
  /app/data/secrets.json              — на случай запуска из другого cwd

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
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
_SECRETS_FILENAME = os.path.join("data", "secrets.json")

SECRETS_PATHS: List[str] = [
    os.path.join(_PROJECT_DIR, _SECRETS_FILENAME),
    os.path.join(os.getcwd(), _SECRETS_FILENAME),
    os.path.join("/app", _SECRETS_FILENAME),
]

_cache: Optional[Dict[str, Any]] = None
_loaded_from: Optional[str] = None


def _load_file() -> Dict[str, Any]:
    """Читает файл секретов один раз за процесс."""
    global _cache, _loaded_from
    if _cache is not None:
        return _cache

    data: Dict[str, Any] = {}
    for path in SECRETS_PATHS:
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8-sig") as fh:
                loaded = json.load(fh)
        except (OSError, ValueError) as exc:
            logger.error("Не удалось прочитать %s: %s", path, exc)
            continue
        if isinstance(loaded, dict):
            data = loaded
            _loaded_from = path
            logger.info("Секреты загружены из %s", path)
            break
        logger.error("%s: ожидался объект JSON, получено %s", path, type(loaded).__name__)

    if not data:
        logger.warning(
            "Секреты не найдены (искали: %s) — берём только переменные окружения",
            ", ".join(SECRETS_PATHS),
        )

    _cache = data
    return data


def get_secret(name: str, default: Any = None) -> Any:
    """Возвращает секрет по имени: сперва переменная окружения, затем файл."""
    from_env = os.getenv(name)
    if from_env:
        return from_env
    return _load_file().get(name, default)


def loaded_from() -> Optional[str]:
    """Путь к файлу, из которого реально прочитаны секреты (None — не найден)."""
    _load_file()
    return _loaded_from


def reload_secrets() -> None:
    """Сбрасывает кэш файла (нужно тестам и смене секретов на ходу)."""
    global _cache, _loaded_from
    _cache = None
    _loaded_from = None
