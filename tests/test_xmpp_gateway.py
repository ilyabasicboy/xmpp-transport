import unittest
from datetime import datetime, timezone
from typing import Optional, Sequence
from xml.etree import ElementTree as ET

from xmpp_transport.adapters.xmpp.addressing import ContactAddressCodec, DirectRouteResolver
from xmpp_transport.adapters.xmpp.component import ComponentSettings
from xmpp_transport.adapters.xmpp.gateway import XmppDirectMessageGateway
from xmpp_transport.adapters.xmpp.message_codec import XmppMessageCodec
from xmpp_transport.adapters.xmpp.namespaces import STANZAS_NS
from xmpp_transport.domain.errors import FeatureUnavailable
from xmpp_transport.domain.identifiers import BackendId, BindingId, RemoteObjectId
from xmpp_transport.domain.models import IncomingMessage, OutgoingMessage
from xmpp_transport.ports.backend import SendResult
from xmpp_transport.ports.repositories import BindingRecord


class FakeWire:
    def __init__(self) -> None:
        self.handler = None
        self.sent = []
        self.started = 0
        self.closed = 0

    def set_message_handler(self, handler):  # type: ignore[no-untyped-def]
        self.handler = handler

    async def start(self) -> None:
        self.started += 1

    async def send(self, element: ET.Element) -> None:
        self.sent.append(element)

    async def close(self) -> None:
        self.closed += 1


class FakeBindings:
    def __init__(self) -> None:
        self.record: Optional[BindingRecord] = BindingRecord(
            BindingId("binding-1"), BackendId("telegram")
        )
        self.owner: Optional[str] = "user@example.com"

    async def binding_for_xmpp_account(self, bare_jid: str, backend_id: BackendId):  # type: ignore[no-untyped-def]
        return self.record

    async def xmpp_account_for_binding(self, binding_id: BindingId) -> Optional[str]:
        return self.owner

    async def active_bindings(self) -> Sequence[BindingRecord]:
        return ()

    async def encrypted_credentials(self, binding_id: BindingId) -> Optional[bytes]:
        return None

    async def save_encrypted_credentials(
        self, binding_id: BindingId, backend_id: BackendId, credentials: bytes
    ) -> None:
        return None


class FakeMessageRouter:
    def __init__(self) -> None:
        self.outgoing = []
        self.failure: Optional[Exception] = None

    async def send(self, message: OutgoingMessage) -> SendResult:
        if self.failure is not None:
            raise self.failure
        self.outgoing.append(message)
        return SendResult(RemoteObjectId("remote-result"))


def error_condition(stanza: ET.Element) -> str:
    error = stanza.find("error")
    assert error is not None
    for child in error:
        if child.tag != "{{{}}}text".format(STANZAS_NS):
            return child.tag.rsplit("}", 1)[-1]
    raise AssertionError("missing error condition")


class XmppGatewayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.wire = FakeWire()
        self.bindings = FakeBindings()
        self.router = FakeMessageRouter()
        self.addresses = ContactAddressCodec("telegram.example.com")
        self.gateway = XmppDirectMessageGateway(
            self.wire,
            DirectRouteResolver(BackendId("telegram"), self.addresses, self.bindings),
            self.addresses,
            self.bindings,
            self.router,  # type: ignore[arg-type]
            XmppMessageCodec(),
        )

    async def test_lifecycle_delegates_to_wire(self) -> None:
        await self.gateway.start()
        await self.gateway.close()
        self.assertEqual((1, 1), (self.wire.started, self.wire.closed))
        self.assertIsNotNone(self.wire.handler)

    async def test_routes_outgoing_stanza_to_message_router(self) -> None:
        stanza = ET.fromstring(
            """
            <message from='user@example.com/device'
                     to='chat-123@telegram.example.com' type='chat' id='client-1'>
              <body>Hello</body>
            </message>
            """
        )
        await self.gateway.handle_stanza(stanza)
        self.assertEqual(1, len(self.router.outgoing))
        message = self.router.outgoing[0]
        self.assertEqual(BindingId("binding-1"), message.binding_id)
        self.assertEqual(RemoteObjectId("123"), message.conversation_id)
        self.assertEqual([], self.wire.sent)

    async def test_missing_binding_returns_service_unavailable(self) -> None:
        self.bindings.record = None
        stanza = ET.fromstring(
            """
            <message from='user@example.com/device'
                     to='chat-123@telegram.example.com' type='chat' id='client-1'>
              <body>Hello</body>
            </message>
            """
        )
        await self.gateway.handle_stanza(stanza)
        self.assertEqual("service-unavailable", error_condition(self.wire.sent[0]))

    async def test_invalid_stanza_returns_bad_request_without_body_reflection(self) -> None:
        stanza = ET.fromstring(
            """
            <message from='user@example.com/device'
                     to='chat-123@telegram.example.com' type='chat' id='client-1'>
              <body>private body</body>
              <reply xmlns='urn:xmpp:reply:0'/>
            </message>
            """
        )
        await self.gateway.handle_stanza(stanza)
        response = self.wire.sent[0]
        self.assertEqual("bad-request", error_condition(response))
        self.assertNotIn("private body", ET.tostring(response, encoding="unicode"))

    async def test_backend_unavailable_is_normalized(self) -> None:
        self.router.failure = FeatureUnavailable("provider private detail")
        stanza = ET.fromstring(
            """
            <message from='user@example.com/device'
                     to='chat-123@telegram.example.com' type='chat' id='client-1'>
              <body>Hello</body>
            </message>
            """
        )
        await self.gateway.handle_stanza(stanza)
        xml = ET.tostring(self.wire.sent[0], encoding="unicode")
        self.assertEqual("service-unavailable", error_condition(self.wire.sent[0]))
        self.assertNotIn("provider private detail", xml)

    async def test_delivers_incoming_message_to_binding_owner(self) -> None:
        message = IncomingMessage(
            id=RemoteObjectId("remote-message-1"),
            binding_id=BindingId("binding-1"),
            conversation_id=RemoteObjectId("123"),
            sender_id=RemoteObjectId("123"),
            occurred_at=datetime.now(timezone.utc),
            text="Incoming",
        )
        await self.gateway.deliver_message(message)
        stanza = self.wire.sent[0]
        self.assertEqual("chat-123@telegram.example.com", stanza.attrib["from"])
        self.assertEqual("user@example.com", stanza.attrib["to"])

    async def test_incoming_message_requires_active_owner(self) -> None:
        self.bindings.owner = None
        message = IncomingMessage(
            id=RemoteObjectId("remote-message-1"),
            binding_id=BindingId("binding-1"),
            conversation_id=RemoteObjectId("123"),
            sender_id=RemoteObjectId("123"),
            occurred_at=datetime.now(timezone.utc),
            text="Incoming",
        )
        with self.assertRaises(LookupError):
            await self.gateway.deliver_message(message)


class ComponentSettingsTests(unittest.TestCase):
    def test_secret_is_hidden_from_repr(self) -> None:
        settings = ComponentSettings("telegram.example.com", "super-secret")
        self.assertNotIn("super-secret", repr(settings))


if __name__ == "__main__":
    unittest.main()
