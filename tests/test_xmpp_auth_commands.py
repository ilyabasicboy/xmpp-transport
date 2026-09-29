import unittest

from xmpp_transport.adapters.xmpp.auth_commands import (
    XmppAuthenticationCommands,
    XmppAuthenticationNotices,
)
from xmpp_transport.adapters.xmpp.message_codec import XmppMessageCodec
from xmpp_transport.domain.auth import AuthChallenge, AuthState
from xmpp_transport.domain.identifiers import BackendId, BindingId
from xmpp_transport.ports.repositories import BindingRecord


class Bindings:
    def __init__(self) -> None:
        self.record = BindingRecord(BindingId("binding-1"), BackendId("max"))
        self.lookup = None
        self.ensured = None

    async def ensure_binding(self, bare_jid, backend_id):  # type: ignore[no-untyped-def]
        self.ensured = (bare_jid, backend_id)
        return self.record

    async def binding_for_authentication(self, bare_jid, backend_id):  # type: ignore[no-untyped-def]
        self.lookup = (bare_jid, backend_id)
        return self.record

    async def xmpp_account_for_binding(self, binding_id):  # type: ignore[no-untyped-def]
        return "user@example.com"


class Authentication:
    def __init__(self) -> None:
        self.begun = None
        self.responses = []

    async def begin(self, binding_id, backend_id):  # type: ignore[no-untyped-def]
        self.begun = (binding_id, backend_id)
        return AuthChallenge(AuthState.WAITING_QR, public_url="https://max.example/qr")

    async def respond(self, binding_id, backend_id, response):  # type: ignore[no-untyped-def]
        self.responses.append((binding_id, backend_id, response))
        return AuthChallenge(AuthState.CONNECTED)


class Wire:
    def __init__(self) -> None:
        self.sent = []

    async def send(self, stanza):  # type: ignore[no-untyped-def]
        self.sent.append(stanza)


class XmppAuthenticationCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_login_creates_attempt_for_owner_binding(self) -> None:
        bindings = Bindings()
        authentication = Authentication()
        commands = XmppAuthenticationCommands(
            BackendId("max"),
            "max.example.com",
            bindings,  # type: ignore[arg-type]
            authentication,  # type: ignore[arg-type]
        )

        response = await commands.handle("user@example.com/device", "/login")

        self.assertTrue(commands.accepts("bot@max.example.com"))
        self.assertTrue(commands.accepts("bot@max.example.com/mobile"))
        self.assertFalse(commands.accepts("max.example.com"))
        self.assertFalse(commands.accepts("chat-1@max.example.com"))
        self.assertEqual(("user@example.com", BackendId("max")), bindings.ensured)
        self.assertEqual(
            (BindingId("binding-1"), BackendId("max")), authentication.begun
        )
        self.assertNotIn("https://max.example/qr", response.body)
        self.assertNotIn("/continue", response.body)
        self.assertIn("сообщит о результате", response.body)
        self.assertEqual("image/svg+xml", response.media[0].mime_type)
        self.assertTrue(
            response.media[0].data_uri.startswith("data:image/svg+xml;base64,")
        )

    async def test_unknown_command_does_not_start_authentication(self) -> None:
        authentication = Authentication()
        commands = XmppAuthenticationCommands(
            BackendId("max"),
            "max.example.com",
            Bindings(),  # type: ignore[arg-type]
            authentication,  # type: ignore[arg-type]
        )

        response = await commands.handle("user@example.com", "/status")

        self.assertIn("/login", response.body)
        self.assertIsNone(authentication.begun)

    async def test_password_is_submitted_through_control_flow(self) -> None:
        authentication = Authentication()
        commands = XmppAuthenticationCommands(
            BackendId("max"),
            "max.example.com",
            Bindings(),  # type: ignore[arg-type]
            authentication,  # type: ignore[arg-type]
        )

        response = await commands.handle("user@example.com", "/password private")

        self.assertEqual("MAX успешно подключён.", response.body)
        self.assertEqual("private", authentication.responses[0][2].secret)

    async def test_background_password_notice_is_sent_from_control_jid(self) -> None:
        wire = Wire()
        notices = XmppAuthenticationNotices(
            "bot@max.example.com",
            Bindings(),  # type: ignore[arg-type]
            wire,
            XmppMessageCodec(),
        )

        await notices.deliver(
            BindingId("binding-1"), AuthChallenge(AuthState.WAITING_PASSWORD)
        )

        stanza = wire.sent[0]
        self.assertEqual("bot@max.example.com", stanza.attrib["from"])
        self.assertEqual("user@example.com", stanza.attrib["to"])
        self.assertIn("/password", stanza.findtext("body"))
