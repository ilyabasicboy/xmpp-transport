"""aiohttp liveness and readiness endpoints."""

from typing import Any, Optional

from xmpp_transport.runtime.health import HealthState


class AiohttpHealthServer:
    def __init__(
        self,
        health: HealthState,
        host: str,
        port: int,
        web_module: Optional[Any] = None,
        media_handler: Optional[Any] = None,
        avatar_handler: Optional[Any] = None,
    ) -> None:
        self._health = health
        self._host = host
        self._port = port
        self._media_handler = media_handler
        self._avatar_handler = avatar_handler
        self._web = web_module
        self._runner: Optional[Any] = None

    async def start(self) -> None:
        if self._runner is not None:
            return
        web = self._web
        if web is None:
            from aiohttp import web as aiohttp_web

            web = aiohttp_web
            self._web = web
        application = web.Application()
        if self._media_handler is not None:
            application.router.add_get(
                "/media/{token}/{filename}", self._media_handler
            )
        if self._avatar_handler is not None:
            application.router.add_get(
                "/avatar/{filename}", self._avatar_handler
            )
        application.router.add_get("/live", self._live)
        application.router.add_get("/ready", self._ready)
        runner = web.AppRunner(application)
        await runner.setup()
        try:
            site = web.TCPSite(runner, self._host, self._port)
            await site.start()
        except BaseException:
            await runner.cleanup()
            raise
        self._runner = runner

    async def close(self) -> None:
        runner = self._runner
        self._runner = None
        if runner is not None:
            await runner.cleanup()

    async def _live(self, request: object) -> Any:
        snapshot = self._health.snapshot()
        return self._web.json_response(
            {"status": snapshot.status.value, "live": snapshot.live},
            status=200 if snapshot.live else 503,
        )

    async def _ready(self, request: object) -> Any:
        snapshot = self._health.snapshot()
        return self._web.json_response(
            {"status": snapshot.status.value, "ready": snapshot.ready},
            status=200 if snapshot.ready else 503,
        )

    async def __aenter__(self) -> "AiohttpHealthServer":
        await self.start()
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.close()
