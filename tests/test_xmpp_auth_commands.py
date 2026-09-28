import unittest
from dataclasses import dataclass

from xmpp_transport.adapters.xmpp.auth_commands import XmppAuthenticationCommands
from xmpp_transport.domain.identifiers import BackendId, BindingId
from xmpp_transport.ports.repositories import BindingRecord


class Bindings:
    def __init__(self) -> None:
        self.record = BindingRecord(BindingId("binding-1"), BackendId("max"))
        self.lookup = None

    async def binding_for_authentication(self, bare_jid, backend_id):  # type: ignore[no-untyped-def]
        self.lookup = (bare_jid, backend_id)
        return self.record


@dataclass(frozen=True)
class Attempt:
    public_url: str


class Attempts:
    def __init__(self) -> None:
        self.created = None

    async def create_attempt(self, binding_id, backend_id, public_base_url):  # type: ignore[no-untyped-def]
        self.created = (binding_id, backend_id, public_base_url)
        return Attempt("https://transport.example/auth/opaque")


class XmppAuthenticationCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_login_creates_attempt_for_owner_binding(self) -> None:
        bindings = Bindings()
        attempts = Attempts()
        commands = XmppAuthenticationCommands(
            BackendId("max"),
            "max.example.com",
            "https://transport.example",
            bindings,  # type: ignore[arg-type]
            attempts,
        )

        response = await commands.handle("user@example.com/device", "/login")

        self.assertTrue(commands.accepts("max.example.com"))
        self.assertFalse(commands.accepts("chat-1@max.example.com"))
        self.assertEqual(("user@example.com", BackendId("max")), bindings.lookup)
        self.assertEqual(
            (BindingId("binding-1"), BackendId("max"), "https://transport.example"),
            attempts.created,
        )
        self.assertIn("https://transport.example/auth/opaque", response)

    async def test_unknown_command_does_not_start_authentication(self) -> None:
        attempts = Attempts()
        commands = XmppAuthenticationCommands(
            BackendId("max"),
            "max.example.com",
            "https://transport.example",
            Bindings(),  # type: ignore[arg-type]
            attempts,
        )

        response = await commands.handle("user@example.com", "/status")

        self.assertIn("/login", response)
        self.assertIsNone(attempts.created)
