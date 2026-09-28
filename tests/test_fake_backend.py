import unittest

from xmpp_transport.adapters.backends.fake import FakeBackendPlugin
from xmpp_transport.domain.auth import AuthResponse, AuthResponseKind, AuthState
from xmpp_transport.domain.errors import BackendUnavailable, InvalidCommand
from xmpp_transport.domain.events import MessageReceived, SessionState, SessionStateChanged
from xmpp_transport.domain.identifiers import BindingId, RemoteObjectId
from xmpp_transport.domain.models import OutgoingMessage
from xmpp_transport.ports.backend import MessageSender
from xmpp_transport.testing.fakes import RecordingEventSink


class FakeBackendContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.plugin = FakeBackendPlugin()
        self.binding_id = BindingId("binding-1")
        self.events = RecordingEventSink()
        self.session = self.plugin.create_session(
            self.binding_id, b"opaque-credentials", self.events
        )

    async def test_start_and_close_are_idempotent(self) -> None:
        await self.session.start()
        await self.session.start()
        await self.session.close()
        await self.session.close()
        states = [
            event.state
            for event in self.events.events
            if isinstance(event, SessionStateChanged)
        ]
        self.assertEqual([SessionState.CONNECTED, SessionState.STOPPED], states)

    async def test_features_match_implemented_sender(self) -> None:
        self.assertIs(self.session, self.session.features()[MessageSender])

    async def test_send_emits_provider_neutral_echo_event(self) -> None:
        await self.session.start()
        message = OutgoingMessage(
            client_message_id="client-1",
            binding_id=self.binding_id,
            conversation_id=RemoteObjectId("opaque-contact"),
            text="hello",
        )
        result = await self.session.send_message(message)
        received = [
            event for event in self.events.events if isinstance(event, MessageReceived)
        ]
        self.assertEqual(1, len(received))
        self.assertEqual(result.remote_message_id, received[0].message.id)
        self.assertEqual(self.binding_id, received[0].envelope.binding_id)
        self.assertEqual(self.plugin.backend_id, received[0].envelope.backend_id)
        self.assertEqual("hello", received[0].message.text)

    async def test_remote_send_result_is_stable_for_retry_key(self) -> None:
        await self.session.start()
        message = OutgoingMessage(
            client_message_id="client-1",
            binding_id=self.binding_id,
            conversation_id=RemoteObjectId("contact-1"),
            text="hello",
        )
        first = await self.session.send_message(message)
        second = await self.session.send_message(message)
        self.assertEqual(first, second)

    async def test_send_rejects_other_binding(self) -> None:
        await self.session.start()
        with self.assertRaises(InvalidCommand):
            await self.session.send_message(
                OutgoingMessage(
                    client_message_id="client-1",
                    binding_id=BindingId("other-binding"),
                    conversation_id=RemoteObjectId("contact-1"),
                    text="hello",
                )
            )

    async def test_send_after_close_fails(self) -> None:
        await self.session.start()
        await self.session.close()
        with self.assertRaises(BackendUnavailable):
            await self.session.send_message(
                OutgoingMessage(
                    client_message_id="client-1",
                    binding_id=self.binding_id,
                    conversation_id=RemoteObjectId("contact-1"),
                    text="hello",
                )
            )

    async def test_authentication_is_immediately_connected(self) -> None:
        flow = self.plugin.create_authentication(self.binding_id)
        challenge = await flow.start()
        self.assertEqual(AuthState.CONNECTED, challenge.state)
        with self.assertRaises(InvalidCommand):
            await flow.respond(AuthResponse(AuthResponseKind.CONFIRMATION, "unused"))
        await flow.close()


if __name__ == "__main__":
    unittest.main()
