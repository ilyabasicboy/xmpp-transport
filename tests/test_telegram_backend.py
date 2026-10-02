import unittest
from datetime import datetime, timezone

from xmpp_transport.adapters.backends.telegram import (
    TelegramAuthenticationFlow,
    TelegramBackendPlugin,
)
from xmpp_transport.domain.auth import AuthResponse, AuthResponseKind, AuthState
from xmpp_transport.domain.events import ContactChanged, MessageReceived, SessionState, SessionStateChanged
from xmpp_transport.domain.identifiers import BackendId, BindingId, RemoteObjectId
from xmpp_transport.domain.models import OutgoingMessage
from xmpp_transport.ports.backend import ContactSource, MessageSender


class PasswordRequired(RuntimeError):
    pass


class FakeSession:
    def __init__(self, value="stored-session") -> None:
        self.value = value

    def save(self):  # type: ignore[no-untyped-def]
        return self.value


class FakeQr:
    def __init__(self, password_required=False) -> None:  # type: ignore[no-untyped-def]
        self.url = "tg://login?token=opaque"
        self.expires = datetime.now(timezone.utc)
        self.password_required = password_required

    async def wait(self):  # type: ignore[no-untyped-def]
        if self.password_required:
            raise PasswordRequired("password required")
        return object()


class User:
    def __init__(self, user_id, first_name, last_name="", username=None):  # type: ignore[no-untyped-def]
        self.id = user_id
        self.first_name = first_name
        self.last_name = last_name
        self.username = username


class Dialog:
    def __init__(self, dialog_id, name, entity, is_group=False, is_channel=False):  # type: ignore[no-untyped-def]
        self.id = dialog_id
        self.name = name
        self.entity = entity
        self.is_group = is_group
        self.is_channel = is_channel


class FakeClient:
    def __init__(self, session_data=None, password_required=False):  # type: ignore[no-untyped-def]
        self.session = FakeSession(session_data or "stored-session")
        self.authorized = bool(session_data)
        self.password_required = password_required
        self.connected = 0
        self.disconnected = 0
        self.handler = None
        self.users = [User(100, "Alice", username="alice")]
        self.dialogs = [
            Dialog(200, "Test Bot", User(200, "Test Bot", username="test_bot")),
            Dialog(-300, "Group", User(-300, "Group"), is_group=True),
        ]
        self.sent = []

    async def connect(self):  # type: ignore[no-untyped-def]
        self.connected += 1

    async def disconnect(self):  # type: ignore[no-untyped-def]
        self.disconnected += 1

    async def is_user_authorized(self):  # type: ignore[no-untyped-def]
        return self.authorized

    async def qr_login(self):  # type: ignore[no-untyped-def]
        return FakeQr(self.password_required)

    async def sign_in(self, *, password):  # type: ignore[no-untyped-def]
        if password != "correct":
            raise ValueError("bad password")
        self.authorized = True
        return self.users[0]

    async def get_me(self):  # type: ignore[no-untyped-def]
        return self.users[0]

    async def __call__(self, request):  # type: ignore[no-untyped-def]
        return type("Contacts", (), {"users": self.users})()

    async def iter_dialogs(self):  # type: ignore[no-untyped-def]
        for dialog in self.dialogs:
            yield dialog

    async def send_message(self, entity, body, reply_to=None):  # type: ignore[no-untyped-def]
        self.sent.append((entity.id, body, reply_to))
        return type("Sent", (), {"id": 777})()

    def add_event_handler(self, handler, event_builder):  # type: ignore[no-untyped-def]
        self.handler = handler


class Sink:
    def __init__(self) -> None:
        self.events = []

    async def publish(self, event):  # type: ignore[no-untyped-def]
        self.events.append(event)


class TelegramAuthenticationTests(unittest.IsolatedAsyncioTestCase):
    async def test_qr_login_exports_string_session(self) -> None:
        client = FakeClient()
        flow = TelegramAuthenticationFlow(client, PasswordRequired)  # type: ignore[arg-type]

        challenge = await flow.start()
        connected = await flow.respond(
            AuthResponse(AuthResponseKind.CONFIRMATION, "confirmed")
        )

        self.assertEqual(AuthState.WAITING_QR, challenge.state)
        self.assertTrue(challenge.public_url.startswith("tg://login?"))
        self.assertEqual(AuthState.CONNECTED, connected.state)
        self.assertEqual(b"stored-session", flow.credentials())

    async def test_qr_login_continues_with_cloud_password(self) -> None:
        client = FakeClient(password_required=True)
        flow = TelegramAuthenticationFlow(client, PasswordRequired)  # type: ignore[arg-type]
        await flow.start()

        password = await flow.respond(
            AuthResponse(AuthResponseKind.CONFIRMATION, "confirmed")
        )
        connected = await flow.respond(
            AuthResponse(AuthResponseKind.PASSWORD, "correct")
        )

        self.assertEqual(AuthState.WAITING_PASSWORD, password.state)
        self.assertEqual(AuthState.CONNECTED, connected.state)


class TelegramBackendSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.client = FakeClient("stored-session")
        self.sink = Sink()
        plugin = TelegramBackendPlugin(lambda session: self.client)
        self.session = plugin.create_session(
            BindingId("binding-1"),
            b"stored-session",
            self.sink,  # type: ignore[arg-type]
        )

    async def test_syncs_contacts_and_sends_direct_text(self) -> None:
        await self.session.start()
        contacts = await self.session.features()[ContactSource].contacts()  # type: ignore[attr-defined]
        result = await self.session.features()[MessageSender].send_message(  # type: ignore[attr-defined]
            OutgoingMessage(
                "client-1",
                BindingId("binding-1"),
                RemoteObjectId("200"),
                text="hello",
            )
        )

        self.assertEqual(["Alice", "Test Bot"], [item.display_name for item in contacts])
        self.assertEqual(RemoteObjectId("777"), result.remote_message_id)
        self.assertEqual([(200, "hello", None)], self.client.sent)
        changes = [event for event in self.sink.events if isinstance(event, ContactChanged)]
        self.assertEqual(2, len(changes))
        self.assertTrue(all(event.force for event in changes))

    async def test_publishes_incoming_private_text(self) -> None:
        await self.session.start()
        event = type(
            "Event",
            (),
            {
                "out": False,
                "is_private": True,
                "raw_text": "hello from Telegram",
                "chat_id": 100,
                "sender_id": 100,
                "id": 55,
                "date": datetime.now(timezone.utc),
                "message": type("Message", (), {"reply_to_msg_id": 44})(),
            },
        )()

        await self.client.handler(event)

        received = [item for item in self.sink.events if isinstance(item, MessageReceived)]
        self.assertEqual(1, len(received))
        self.assertEqual("hello from Telegram", received[0].message.text)
        self.assertEqual(RemoteObjectId("44"), received[0].message.reply_to.message_id)

    async def test_lifecycle_is_idempotent(self) -> None:
        await self.session.start()
        await self.session.start()
        await self.session.close()
        await self.session.close()

        states = [
            event.state
            for event in self.sink.events
            if isinstance(event, SessionStateChanged)
        ]
        self.assertEqual(
            [SessionState.STARTING, SessionState.CONNECTED, SessionState.STOPPED],
            states,
        )
        self.assertEqual((1, 1), (self.client.connected, self.client.disconnected))


class TelegramPluginTests(unittest.TestCase):
    def test_identifies_and_validates_configuration(self) -> None:
        plugin = TelegramBackendPlugin()
        self.assertEqual(BackendId("telegram"), plugin.backend_id)
        with self.assertRaisesRegex(ValueError, "api_id"):
            plugin.configure({"api_id": "0", "api_hash": "hash"})
        with self.assertRaisesRegex(ValueError, "api_hash"):
            plugin.configure({"api_id": "123", "api_hash": ""})
        plugin.configure({"api_id": "123", "api_hash": "hash"})


if __name__ == "__main__":
    unittest.main()
