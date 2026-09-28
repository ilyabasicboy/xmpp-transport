"""Direct-message boundary between XEP-0114 wire traffic and the application."""

import logging
from typing import Awaitable, Callable, Protocol
from xml.etree import ElementTree as ET

from xmpp_transport.application.message_router import MessageRouter
from xmpp_transport.domain.errors import AuthorizationRequired, FeatureUnavailable, InvalidCommand
from xmpp_transport.domain.models import IncomingMessage
from xmpp_transport.ports.repositories import BindingRepository

from .addressing import ContactAddressCodec, DirectRouteResolver, InvalidXmppAddress
from .message_codec import XmppMessageCodec, XmppMessageError


log = logging.getLogger(__name__)
MessageHandler = Callable[[ET.Element], Awaitable[None]]


class XmppWire(Protocol):
    def set_message_handler(self, handler: MessageHandler) -> None:
        ...

    async def start(self) -> None:
        ...

    async def send(self, element: ET.Element) -> None:
        ...

    async def close(self) -> None:
        ...


class XmppDirectMessageGateway:
    def __init__(
        self,
        wire: XmppWire,
        routes: DirectRouteResolver,
        addresses: ContactAddressCodec,
        bindings: BindingRepository,
        messages: MessageRouter,
        codec: XmppMessageCodec,
    ) -> None:
        self._wire = wire
        self._routes = routes
        self._addresses = addresses
        self._bindings = bindings
        self._messages = messages
        self._codec = codec
        self._wire.set_message_handler(self.handle_stanza)

    async def start(self) -> None:
        await self._wire.start()

    async def close(self) -> None:
        await self._wire.close()

    async def handle_stanza(self, stanza: ET.Element) -> None:
        try:
            route = await self._routes.resolve(
                stanza.attrib.get("from", ""), stanza.attrib.get("to", "")
            )
            if route is None:
                await self._send_error(stanza, XmppMessageError.SERVICE_UNAVAILABLE)
                return
            message = self._codec.parse_outgoing(
                stanza, route.binding_id, route.conversation_id
            )
            await self._messages.send(message)
        except (InvalidCommand, InvalidXmppAddress):
            await self._send_error(stanza, XmppMessageError.BAD_REQUEST)
        except (AuthorizationRequired, FeatureUnavailable, LookupError):
            await self._send_error(stanza, XmppMessageError.SERVICE_UNAVAILABLE)
        except Exception as exc:
            log.error(
                "XMPP direct message handling failed exception_type=%s",
                type(exc).__name__,
            )
            await self._send_error(stanza, XmppMessageError.SERVICE_UNAVAILABLE)

    async def deliver_message(self, message: IncomingMessage) -> None:
        owner_jid = await self._bindings.xmpp_account_for_binding(message.binding_id)
        if owner_jid is None:
            raise LookupError("active XMPP account not found for binding")
        sender_jid = self._addresses.contact_jid(message.conversation_id)
        stanza = self._codec.serialize_incoming(message, sender_jid, owner_jid)
        await self._wire.send(stanza)

    async def _send_error(
        self, request: ET.Element, condition: XmppMessageError
    ) -> None:
        response = self._codec.error_reply(request, condition)
        await self._wire.send(response)
