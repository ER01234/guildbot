import asyncio
import json
import logging
import time
import aiohttp

logger = logging.getLogger(__name__)

API_BASE = "https://welldungeon.online/api/v1"


class WdApiError(Exception):
    """Базовая ошибка WellDungeon API."""
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


class WdInsufficientVoicesError(WdApiError):
    """INSUFFICIENT_VOICES — не хватает Голосов Древних."""
    pass


class WdOnCooldownError(WdApiError):
    """ON_COOLDOWN — действие на кулдауне."""
    pass


class WdTargetNotFoundError(WdApiError):
    """TARGET_NOT_FOUND — целевой игрок не найден."""
    pass


class WdConditionsNotMetError(WdApiError):
    """CONDITIONS_NOT_MET — не выполнены условия (класс, уровень и т.д.)."""
    pass


class WdForbiddenScopeError(WdApiError):
    """FORBIDDEN_SCOPE — у токена нет права на метод."""
    pass


class WdInvalidTokenError(WdApiError):
    """INVALID_TOKEN — токен недействителен."""
    pass


class WdTooManyRequestsError(WdApiError):
    """TOO_MANY_REQUESTS — превышен лимит запросов."""
    pass


class WdInventoryError(WdApiError):
    """INVENTORY_ERROR — ошибка инвентаря."""
    pass


class WdInternalError(WdApiError):
    """INTERNAL_ERROR — внутренняя ошибка сервера."""
    pass


# Маппинг кодов ошибок API на исключения
ERROR_CODE_MAP: dict[str, type[WdApiError]] = {
    "INSUFFICIENT_VOICES": WdInsufficientVoicesError,
    "ON_COOLDOWN": WdOnCooldownError,
    "TARGET_NOT_FOUND": WdTargetNotFoundError,
    "CONDITIONS_NOT_MET": WdConditionsNotMetError,
    "FORBIDDEN_SCOPE": WdForbiddenScopeError,
    "INVALID_TOKEN": WdInvalidTokenError,
    "TOO_MANY_REQUESTS": WdTooManyRequestsError,
    "INVENTORY_ERROR": WdInventoryError,
    "INTERNAL_ERROR": WdInternalError,
}


class RateLimiter:
    """Простой rate limiter — 1 запрос в секунду."""
    def __init__(self):
        self._lock = asyncio.Lock()
        self._last_call: float = 0.0

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            wait = max(0.0, 1.0 - (now - self._last_call))
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()


class WdApiClient:
    """
    Асинхронный клиент для WellDungeon API.
    - Встроенный rate limiter (1 запрос/сек)
    - Автоматический разбор ошибок в исключения
    - Поддержка всех методов API
    """
    def __init__(self):
        self._rate_limiter = RateLimiter()
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        """Закрыть HTTP-сессию."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(self, method: str, params: dict) -> dict:
        """
        Базовый метод запроса к API.
        Применяет rate limiting, выполняет GET-запрос, разбирает ответ.
        """
        await self._rate_limiter.acquire()

        session = await self._get_session()
        url = f"{API_BASE}/{method}"

        logger.debug("WD API request: %s %s", method, params)

        async with session.get(url, params=params, ssl=False) as resp:
            text = await resp.text()
            data = json.loads(text)

        if data.get("result") == 0:
            error = data.get("error", {})
            code = error.get("code", "UNKNOWN")
            message = error.get("message", "Неизвестная ошибка")

            exc_cls = ERROR_CODE_MAP.get(code, WdApiError)
            raise exc_cls(code, message)

        return data

    async def apply_social_effect(
        self, token: str, player_id: int, effect_type: str
    ) -> dict:
        """
        Наложить социальный эффект на игрока.

        Args:
            token: API-токен персонажа
            player_id: VK ID целевого игрока
            effect_type: Тип эффекта (BlessOfLight, CurseOfPain и т.д.)

        Returns:
            {"result": 1, ...}

        Raises:
            WdInsufficientVoicesError — не хватает голосов
            WdOnCooldownError — кулдаун
            WdTargetNotFoundError — цель не найдена
            WdConditionsNotMetError — не подходит класс/уровень
            WdForbiddenScopeError — нет прав у токена
            WdInvalidTokenError — невалидный токен
        """
        return await self._request("ApplySocialEffectToPlayer", {
            "token": token,
            "player_id": player_id,
            "type": effect_type,
        })

    async def get_character_info(self, token: str) -> dict:
        """Получить информацию о персонаже токена."""
        return await self._request("GetCharacterInfo", {"token": token})

    async def get_token_info(self, token: str) -> dict:
        """Получить информацию о токене."""
        return await self._request("TokenInfo", {"token": token})


# Единый экземпляр клиента для всего приложения
wd_client = WdApiClient()
