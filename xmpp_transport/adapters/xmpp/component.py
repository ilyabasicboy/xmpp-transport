"""Thin, lazily imported slixmpp XEP-0114 wire client."""

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Optional
from xml.etree import ElementTree as ET

from .gateway import MessageHandler


log = logging.getLogger(__name__)
STANZAS_NS = "urn:ietf:params:xml:ns:xmpp-stanzas"


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
        # asyncio primitives bind to the current loop on Python 3.9. Runtime
        # composition is synchronous, so create the event lazily in start().
        self._ready: Optional[asyncio.Event] = None
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

        ready = asyncio.Event()
        self._ready = ready

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
                ready.wait(), timeout=self._settings.connect_timeout
            )
        except BaseException:
            self._client = None
            disconnect_result = client.disconnect()
            if inspect.isawaitable(disconnect_result):
                await disconnect_result
            raise

    async def send(self, element: ET.Element) -> None:
        client = self._client
        ready = self._ready
        if client is None or ready is None or not ready.is_set():
            raise ConnectionError("XMPP component is not connected")
        if element.tag.rsplit("}", 1)[-1] == "message":
            body = next(
                (
                    child
                    for child in element
                    if child.tag.rsplit("}", 1)[-1] == "body"
                ),
                None,
            )
            message = client.make_message(
                mfrom=element.attrib.get("from"),
                mto=element.attrib.get("to"),
                mtype=element.attrib.get("type"),
                mbody=body.text if body is not None else None,
            )
            for name, value in element.attrib.items():
                if name not in {"from", "to", "type"}:
                    message.xml.set(name, value)
            for child in element:
                if child is not body:
                    message.xml.append(child)
            message.send()
            return
        client.send_raw(ET.tostring(element, encoding="unicode"))

    async def request(self, element: ET.Element, timeout: float = 10.0) -> ET.Element:
        if element.tag.rsplit("}", 1)[-1] != "iq":
            raise ValueError("XMPP request must be an IQ stanza")
        client = self._client
        ready = self._ready
        if client is None or ready is None or not ready.is_set():
            raise ConnectionError("XMPP component is not connected")
        children = tuple(element)
        if len(children) != 1:
            raise ValueError("XMPP IQ request must contain one payload element")
        iq = client.make_iq_set(
            sub=children[0],
            ito=element.attrib.get("to"),
            ifrom=element.attrib.get("from"),
        )
        if element.attrib.get("id"):
            iq["id"] = element.attrib["id"]
        try:
            response = await iq.send(timeout=timeout)
        except Exception as exc:
            if type(exc).__name__ == "IqError":
                self._log_iq_error(exc, children[0])
            raise
        return response.xml

    @staticmethod
    def _log_iq_error(exc: Exception, payload: ET.Element) -> None:
        stanza = getattr(exc, "iq", None)
        xml = getattr(stanza, "xml", None)
        error = None if xml is None else xml.find("{jabber:client}error")
        if error is None and xml is not None:
            error = xml.find("error")
        code = error.attrib.get("code") if error is not None else None
        error_type = error.attrib.get("type") if error is not None else None
        condition = None
        server_text = None
        if error is not None:
            text_element = error.find("{{{}}}text".format(STANZAS_NS))
            server_text = text_element.text if text_element is not None else None
            for child in error:
                if child.tag == "{{{}}}text".format(STANZAS_NS):
                    continue
                if child.tag.startswith("{{{}}}".format(STANZAS_NS)):
                    condition = child.tag.rsplit("}", 1)[-1]
                    break
        log.error(
            "XMPP IQ request rejected payload=%s operation=%s code=%s "
            "error_type=%s condition=%s server_text=%s",
            payload.tag.rsplit("}", 1)[-1],
            payload.attrib.get("op"),
            code,
            error_type,
            condition,
            server_text,
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        ready = self._ready
        if ready is not None:
            ready.clear()
        client = self._client
        self._client = None
        if client is not None:
            result = client.disconnect()
            if inspect.isawaitable(result):
                await result

    async def _on_session_start(self, event: object) -> None:
        if self._ready is not None:
            self._ready.set()

    def _on_disconnected(self, event: object) -> None:
        if self._ready is not None:
            self._ready.clear()

    async def _on_message(self, stanza: Any) -> None:
        if self._handler is not None:
            await self._handler(stanza.xml)
