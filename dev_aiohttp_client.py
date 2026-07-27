from contextlib import asynccontextmanager

import aiohttp

from vkbottle.http import SingleAiohttpClient


class DevAiohttpClient(SingleAiohttpClient):
    def _ensure_disabled_ssl_connector(self):
        if self._session_params.get("connector") is None:
            self._session_params["connector"] = aiohttp.TCPConnector(ssl=False)

    @asynccontextmanager
    async def request(self, url, method: str = "GET", data=None, **kwargs):
        self._ensure_disabled_ssl_connector()
        async with super().request(url, method, data, **kwargs) as response:
            yield response

    async def request_raw(self, url, method: str = "GET", data=None, **kwargs):
        kwargs.setdefault("ssl", False)
        self._ensure_disabled_ssl_connector()
        return await super().request_raw(url, method, data, **kwargs)
