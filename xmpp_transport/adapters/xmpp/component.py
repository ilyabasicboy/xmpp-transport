"""Thin, lazily imported slixmpp XEP-0114 wire client."""

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any, Optional
from xml.etree import ElementTree as ET

from .gateway import MessageHandler


@dataclass(frozen=True)
class ComponentSettings:
    domain: str
    secret: str = field(repr=False)
    host: str = "127.0.0.1"
    port: int = 5347
    connect_timeout: float = 15.0

    def __post_init__(self) -> None:
        if not self.domain.strip() or not self.secret:
            raise ValueError("component domain and secret are required")
        if not self.host.strip() or not 1 <= self.port <= 65535:
            raise ValueError("component endpoint is invalid")
        if self.connect_timeout <= 0:
            raise ValueError("component connect_timeout must be positive")


class SlixmppComponentWire:
    def __init__(self, settings: ComponentSettings) -> None:
        self._settings = settings
        self._handler: Optional[MessageHandler] = None
        self._client: Optional[Any] = None
        self._ready = asyncio.Event()
        self._closed = False

    def set_message_handler(self, handler: MessageHandler) -> None:
        if self._client is not None:
            raise RuntimeError("message handler must be configured before start")
        self._handler = handler

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("XMPP component wire is closed")
        if self._client is not None:
            return
        if self._handler is None:
            raise RuntimeError("XMPP message handler is not configured")

        from slixmpp import ComponentXMPP

        client = ComponentXMPP(
            self._settings.domain,
            self._settings.secret,
            self._settings.host,
            self._settings.port,
        )
        client.add_event_handler("session_start", self._on_session_start)
        client.add_event_handler("disconnected", self._on_disconnected)
        client.add_event_handler("message", self._on_message)
        self._client = client
        try:
            connected = client.connect()
            if inspect.isawaitable(connected):
                connected = await connected
            if connected is False:
                raise ConnectionError("XMPP component connection was rejected")
            await asyncio.wait_for(
                self._ready.wait(), timeout=self._settings.connect_timeout
            )
        except BaseException:
            self._client = None
            disconnect_result = client.disconnect()
            if inspect.isawaitable(disconnect_result):
                await disconnect_result
            raise

    async def send(self, element: ET.Element) -> None:
        client = self._client
        if client is None or not self._ready.is_set():
            raise ConnectionError("XMPP component is not connected")
        client.send_raw(ET.tostring(element, encoding="unicode"))

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._ready.clear()
        client = self._client
        self._client = None
        if client is not None:
            result = client.disconnect()
            if inspect.isawaitable(result):
                await result

    async def _on_session_start(self, event: object) -> None:
        self._ready.set()

    def _on_disconnected(self, event: object) -> None:
        self._ready.clear()

    async def _on_message(self, stanza: Any) -> None:
        if self._handler is not None:
            await self._handler(stanza.xml)
