import unittest
from datetime import datetime, timezone

from xmpp_transport.adapters.backends.telegram import (
    TelegramAuthenticationFlow,
    TelegramBackendPlugin,
)
from xmpp_transport.domain.auth import AuthResponse, AuthResponseKind, AuthState
from xmpp_transport.domain.events import (
    ContactChanged,
    ConversationChanged,
    MessageReceived,
    SessionState,
    SessionStateChanged,
)
from xmpp_transport.domain.identifiers import BackendId, BindingId, RemoteObjectId
from xmpp_transport.domain.models import ConversationKind, Media, MediaKind, OutgoingMessage
from xmpp_transport.ports.backend import ConversationSource, ContactSource, MessageSender


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
            Dialog(-400, "News", User(-400, "News"), is_channel=True),
        ]
        self.sent = []
        self.sent_files = []
        self.downloaded_media = b"telegram-media"

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

    async def send_file(self, entity, file, **kwargs):  # type: ignore[no-untyped-def]
        self.sent_files.append((entity.id, file, kwargs))
        return type("Sent", (), {"id": 778})()

    async def download_media(self, message, file=bytes):  # type: ignore[no-untyped-def]
        return self.downloaded_media

    def add_event_handler(self, handler, event_builder):  # type: ignore[no-untyped-def]
        self.handler = handler


class GroupEvent:
    out = False
    is_private = False
    is_group = True
    is_channel = False
    raw_text = "hello group"
    chat_id = -300
    sender_id = 300
    id = 56
    date = datetime.now(timezone.utc)
    message = type("Message", (), {"reply_to_msg_id": None})()

    async def get_chat(self):  # type: ignore[no-untyped-def]
        return type("Chat", (), {"title": "Group"})()

    async def get_sender(self):  # type: ignore[no-untyped-def]
        return User(300, "Bob")


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
        self.plugin = TelegramBackendPlugin(lambda session: self.client)
        self.session = self.plugin.create_session(
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

    async def test_sends_image_and_voice_media(self) -> None:
        await self.session.start()
        sender = self.session.features()[MessageSender]
        image = Media(
            RemoteObjectId("image-1"),
            MediaKind.IMAGE,
            content_type="image/jpeg",
            file_name="photo.jpg",
            source_url="https://xabber.example/photo.jpg",
        )
        voice = Media(
            RemoteObjectId("voice-1"),
            MediaKind.AUDIO,
            content_type="audio/ogg",
            file_name="voice.ogg",
            source_url="https://xabber.example/voice.ogg",
            voice=True,
        )

        image_result = await sender.send_message(
            OutgoingMessage(
                "client-image", BindingId("binding-1"), RemoteObjectId("200"),
                text="caption", media=(image,),
            )
        )
        await sender.send_message(
            OutgoingMessage(
                "client-voice", BindingId("binding-1"), RemoteObjectId("200"),
                media=(voice,),
            )
        )

        self.assertEqual(RemoteObjectId("778"), image_result.remote_message_id)
        self.assertEqual("https://xabber.example/photo.jpg", self.client.sent_files[0][1])
        self.assertEqual("caption", self.client.sent_files[0][2]["caption"])
        self.assertTrue(self.client.sent_files[1][2]["voice_note"])

    async def test_maps_incoming_photo_to_media(self) -> None:
        await self.session.start()
        file_info = type(
            "File", (),
            {"mime_type": "image/jpeg", "name": "photo.jpg", "width": 640, "height": 480, "duration": None},
        )()
        message = type(
            "Message", (),
            {"reply_to_msg_id": None, "media": object(), "file": file_info, "photo": object(), "voice": None, "sticker": None},
        )()
        event = type(
            "Event", (),
            {"out": False, "is_private": True, "raw_text": "caption", "chat_id": 100, "sender_id": 100, "id": 57, "date": datetime.now(timezone.utc), "message": message},
        )()

        await self.client.handler(event)

        received = [item for item in self.sink.events if isinstance(item, MessageReceived)]
        media = received[-1].message.media[0]
        self.assertEqual(MediaKind.IMAGE, media.kind)
        self.assertEqual("photo.jpg", media.file_name)
        self.assertEqual((640, 480), (media.width, media.height))
        self.assertTrue(media.source_url.startswith("http://127.0.0.1:8080/media/"))
        token = media.source_url.split("/media/", 1)[1].split("/", 1)[0]
        request = type("Request", (), {"match_info": {"token": token}})()
        response = await self.plugin.media_handler(request)
        self.assertEqual(b"telegram-media", response.body)
        self.assertEqual("image/jpeg", response.headers["Content-Type"])

    async def test_syncs_groups_and_sends_group_text(self) -> None:
        await self.session.start()

        conversations = await self.session.features()[
            ConversationSource
        ].conversations()
        result = await self.session.features()[MessageSender].send_message(
            OutgoingMessage(
                "client-group",
                BindingId("binding-1"),
                RemoteObjectId("-300"),
                text="hello group",
            )
        )

        self.assertEqual(["Group", "News"], [item.title for item in conversations])
        self.assertEqual(ConversationKind.GROUP, conversations[0].kind)
        self.assertEqual(ConversationKind.CHANNEL, conversations[1].kind)
        self.assertEqual("100", conversations[0].attributes["owner_remote_id"])
        self.assertEqual(RemoteObjectId("777"), result.remote_message_id)
        self.assertEqual((-300, "hello group", None), self.client.sent[-1])
        echo = GroupEvent()
        echo.out = True
        echo.sender_id = 100
        echo.id = 777
        before = len(
            [item for item in self.sink.events if isinstance(item, MessageReceived)]
        )
        await self.client.handler(echo)
        after = len(
            [item for item in self.sink.events if isinstance(item, MessageReceived)]
        )
        self.assertEqual(before, after)

    async def test_publishes_incoming_group_message_and_sender(self) -> None:
        await self.session.start()

        await self.client.handler(GroupEvent())

        received = [item for item in self.sink.events if isinstance(item, MessageReceived)]
        message = received[-1].message
        self.assertEqual(RemoteObjectId("-300"), message.conversation_id)
        self.assertEqual(RemoteObjectId("300"), message.sender_id)
        self.assertEqual("true", message.attributes["is_group"])
        conversations = [
            item.conversation
            for item in self.sink.events
            if isinstance(item, ConversationChanged)
        ]
        self.assertEqual("Bob", conversations[-1].participants[0].display_name)

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
