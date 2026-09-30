import unittest
from dataclasses import dataclass
from datetime import datetime, timezone

from xmpp_transport.adapters.backends.max import (
    MaxAuthenticationFlow,
    MaxBackendPlugin,
    MaxCredentials,
)
from xmpp_transport.adapters.backends.max.models import (
    MaxAuthorizationError,
    MaxChat,
    MaxContact,
    MaxIncomingMessage,
)
from xmpp_transport.domain.auth import AuthResponse, AuthResponseKind, AuthState
from xmpp_transport.domain.events import (
    AuthorizationLost,
    ContactChanged,
    ConversationChanged,
    MessageReceived,
    SessionStateChanged,
)
from xmpp_transport.domain.identifiers import BackendId, BindingId, RemoteObjectId
from xmpp_transport.domain.models import OutgoingMessage
from xmpp_transport.ports.backend import ContactSource, MessageSender


class EventSink:
    def __init__(self) -> None:
        self.events = []

    async def publish(self, event) -> None:  # type: ignore[no-untyped-def]
        self.events.append(event)


class MaxClient:
    def __init__(self) -> None:
        self.message_handler = None
        self.authorization_lost_handler = None
        self.chat_handler = None
        self.started = 0
        self.closed = 0
        self.sent = []

    def set_message_handler(self, handler) -> None:  # type: ignore[no-untyped-def]
        self.message_handler = handler

    def set_authorization_lost_handler(self, handler) -> None:  # type: ignore[no-untyped-def]
        self.authorization_lost_handler = handler

    def set_chat_handler(self, handler) -> None:  # type: ignore[no-untyped-def]
        self.chat_handler = handler

    async def start(self) -> None:
        self.started += 1

    async def close(self) -> None:
        self.closed += 1

    async def send_message(self, text, chat_id=None, reply_to_message_id=None):  # type: ignore[no-untyped-def]
        self.sent.append((text, chat_id, reply_to_message_id))
        return {"message": {"id": "sent-1"}}

    async def list_contacts(self):  # type: ignore[no-untyped-def]
        return [MaxContact("user-1", "Alice", "chat-1")]


class LoginError(RuntimeError):
    pass


class PasswordRequired(LoginError):
    pass


@dataclass(frozen=True)
class QrChallenge:
    qr_link: str
    expires_at: datetime


class QrClient:
    def __init__(self, password_required: bool = False) -> None:
        self.password_required = password_required
        self.closed = 0

    async def start(self) -> QrChallenge:
        return QrChallenge("https://max.example/qr/opaque", datetime.now(timezone.utc))

    async def wait_for_credentials(self):  # type: ignore[no-untyped-def]
        if self.password_required:
            raise PasswordRequired("password required")
        return "token", "device", "account"

    async def submit_password(self, password):  # type: ignore[no-untyped-def]
        if password != "correct":
            raise LoginError("invalid password")
        return "token", "device", "account"

    async def close(self) -> None:
        self.closed += 1


class MaxCredentialsTests(unittest.TestCase):
    def test_round_trips_credentials_without_exposing_secrets(self) -> None:
        credentials = MaxCredentials("secret-token", "secret-device", "42")

        self.assertEqual(credentials, MaxCredentials.decode(credentials.encode()))
        self.assertNotIn("secret-token", repr(credentials))
        self.assertNotIn("secret-device", repr(credentials))

    def test_rejects_missing_or_invalid_fields(self) -> None:
        invalid = (b"not-json", b"[]", b'{}', b'{"token":"x"}')
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                MaxCredentials.decode(payload)


class MaxBackendPluginTests(unittest.TestCase):
    def test_identifies_as_max_and_validates_session_credentials(self) -> None:
        plugin = MaxBackendPlugin()
        self.assertEqual(BackendId("max"), plugin.backend_id)

        with self.assertRaises(ValueError):
            plugin.create_session(BindingId("binding-1"), b"invalid", object())  # type: ignore[arg-type]

    def test_rejects_invalid_self_message_setting(self) -> None:
        plugin = MaxBackendPlugin()

        with self.assertRaisesRegex(ValueError, "test_self_messages"):
            plugin.configure({"test_self_messages": "sometimes"})


class MaxBackendSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.client = MaxClient()
        self.sink = EventSink()
        plugin = MaxBackendPlugin(lambda credentials: self.client)  # type: ignore[arg-type]
        credentials = MaxCredentials("token", "device", "account").encode()
        self.session = plugin.create_session(
            BindingId("binding-1"), credentials, self.sink  # type: ignore[arg-type]
        )

    async def test_starts_sends_text_and_closes_client(self) -> None:
        await self.session.start()
        sender = self.session.features()[MessageSender]
        result = await sender.send_message(  # type: ignore[attr-defined]
            OutgoingMessage(
                client_message_id="client-1",
                binding_id=BindingId("binding-1"),
                conversation_id=RemoteObjectId("chat-1"),
                text="hello",
            )
        )
        await self.session.close()

        self.assertEqual(RemoteObjectId("sent-1"), result.remote_message_id)
        self.assertEqual([("hello", "chat-1", None)], self.client.sent)
        self.assertEqual((1, 1), (self.client.started, self.client.closed))
        states = [event.state.value for event in self.sink.events if isinstance(event, SessionStateChanged)]
        self.assertEqual(["starting", "connected", "stopped"], states)

    async def test_publishes_incoming_message_and_authorization_loss(self) -> None:
        await self.session.start()
        await self.client.message_handler(  # type: ignore[misc]
            MaxIncomingMessage(
                sender_id="user-1",
                text="incoming",
                chat_id="chat-1",
                message_id="message-1",
            )
        )
        await self.client.authorization_lost_handler(  # type: ignore[misc]
            MaxAuthorizationError("expired", terminal=True)
        )

        messages = [event for event in self.sink.events if isinstance(event, MessageReceived)]
        lost = [event for event in self.sink.events if isinstance(event, AuthorizationLost)]
        self.assertEqual("incoming", messages[0].message.text)
        self.assertEqual(RemoteObjectId("chat-1"), messages[0].message.conversation_id)
        self.assertEqual("expired", lost[0].reason)

    async def test_ignores_self_messages_in_direct_chats(self) -> None:
        plugin = MaxBackendPlugin(
            lambda credentials: self.client,
            test_self_messages=True,
        )  # type: ignore[arg-type]
        session = plugin.create_session(
            BindingId("binding-1"),
            MaxCredentials("token", "device", "account").encode(),
            self.sink,  # type: ignore[arg-type]
        )
        await session.start()

        await self.client.message_handler(  # type: ignore[misc]
            MaxIncomingMessage(
                sender_id="account",
                text="direct self",
                chat_id="chat-1",
                message_id="self-direct-1",
                is_self=True,
            )
        )

        self.assertFalse(
            any(isinstance(event, MessageReceived) for event in self.sink.events)
        )

    async def test_publishes_group_self_message_when_enabled(self) -> None:
        plugin = MaxBackendPlugin(lambda credentials: self.client)  # type: ignore[arg-type]
        plugin.configure({"test_self_messages": "true"})
        session = plugin.create_session(
            BindingId("binding-1"),
            MaxCredentials("token", "device", "account").encode(),
            self.sink,  # type: ignore[arg-type]
        )
        await session.start()

        await self.client.message_handler(  # type: ignore[misc]
            MaxIncomingMessage(
                sender_id="account",
                text="group self",
                chat_id="group-1",
                message_id="self-group-1",
                is_self=True,
                is_group=True,
            )
        )

        messages = [
            event for event in self.sink.events if isinstance(event, MessageReceived)
        ]
        self.assertEqual(1, len(messages))
        self.assertEqual("true", messages[0].message.attributes["is_self"])
        self.assertEqual("true", messages[0].message.attributes["is_group"])

    async def test_ignores_group_self_message_when_disabled(self) -> None:
        await self.session.start()

        await self.client.message_handler(  # type: ignore[misc]
            MaxIncomingMessage(
                sender_id="account",
                text="group self",
                chat_id="group-1",
                message_id="self-group-1",
                is_self=True,
                is_group=True,
            )
        )

        self.assertFalse(
            any(isinstance(event, MessageReceived) for event in self.sink.events)
        )

    async def test_publishes_direct_snapshot_chat_as_roster_contact(self) -> None:
        await self.session.start()

        await self.client.chat_handler(  # type: ignore[misc]
            MaxChat("chat-42", "Alice", force_roster_sync=True)
        )

        contacts = [event for event in self.sink.events if isinstance(event, ContactChanged)]
        self.assertEqual(RemoteObjectId("chat-42"), contacts[0].contact.id)
        self.assertEqual("Alice", contacts[0].contact.display_name)
        self.assertTrue(contacts[0].force)

    async def test_publishes_group_snapshot_as_conversation(self) -> None:
        from xmpp_transport.adapters.backends.max.models import MaxChatMember

        await self.session.start()
        await self.client.chat_handler(  # type: ignore[misc]
            MaxChat(
                "group-42",
                "MAX Group",
                is_group=True,
                members=(MaxChatMember("user-7", "Alice"),),
            )
        )

        conversations = [
            event
            for event in self.sink.events
            if isinstance(event, ConversationChanged)
        ]
        self.assertEqual(1, len(conversations))
        self.assertEqual("MAX Group", conversations[0].conversation.title)
        self.assertEqual("Alice", conversations[0].conversation.participants[0].display_name)
        self.assertFalse(
            any(isinstance(event, ContactChanged) for event in self.sink.events)
        )

    async def test_exposes_address_book_through_contact_source(self) -> None:
        await self.session.start()

        source = self.session.features()[ContactSource]
        contacts = await source.contacts()  # type: ignore[attr-defined]

        self.assertEqual(RemoteObjectId("chat-1"), contacts[0].id)


class MaxAuthenticationFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_qr_flow_exports_credentials_after_connection(self) -> None:
        client = QrClient()
        flow = MaxAuthenticationFlow(client, PasswordRequired, LoginError)  # type: ignore[arg-type]

        challenge = await flow.start()
        connected = await flow.respond(
            AuthResponse(AuthResponseKind.CONFIRMATION, "confirmed")
        )

        self.assertEqual(AuthState.WAITING_QR, challenge.state)
        self.assertEqual(AuthState.CONNECTED, connected.state)
        self.assertEqual("account", MaxCredentials.decode(flow.credentials()).account_id)

    async def test_qr_flow_supports_two_factor_password(self) -> None:
        client = QrClient(password_required=True)
        flow = MaxAuthenticationFlow(client, PasswordRequired, LoginError)  # type: ignore[arg-type]

        await flow.start()
        password = await flow.respond(
            AuthResponse(AuthResponseKind.CONFIRMATION, "confirmed")
        )
        connected = await flow.respond(AuthResponse(AuthResponseKind.PASSWORD, "correct"))
        await flow.close()

        self.assertEqual(AuthState.WAITING_PASSWORD, password.state)
        self.assertEqual(AuthState.CONNECTED, connected.state)
        self.assertEqual(1, client.closed)

    async def test_rejected_two_factor_password_keeps_flow_active(self) -> None:
        client = QrClient(password_required=True)
        flow = MaxAuthenticationFlow(client, PasswordRequired, LoginError)  # type: ignore[arg-type]

        await flow.start()
        await flow.respond(AuthResponse(AuthResponseKind.CONFIRMATION, "confirmed"))
        rejected = await flow.respond(AuthResponse(AuthResponseKind.PASSWORD, "wrong"))
        connected = await flow.respond(AuthResponse(AuthResponseKind.PASSWORD, "correct"))

        self.assertEqual(AuthState.WAITING_PASSWORD, rejected.state)
        self.assertEqual(AuthState.CONNECTED, connected.state)


if __name__ == "__main__":
    unittest.main()
