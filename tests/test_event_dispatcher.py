import unittest
from datetime import datetime, timezone
from typing import List

from xmpp_transport.adapters.events import EventDispatchError, InMemoryEventBus
from xmpp_transport.application.event_dispatcher import (
    BackendEventDispatcher,
    EventContractError,
    EventRegistrationError,
    UnhandledEventError,
)
from xmpp_transport.domain.events import (
    ContactChanged,
    EventEnvelope,
    MessageReceived,
    SessionState,
    SessionStateChanged,
)
from xmpp_transport.domain.identifiers import BackendId, BindingId, EventId, RemoteObjectId
from xmpp_transport.domain.models import Contact, IncomingMessage


def envelope(event_id: str, event_type: str) -> EventEnvelope:
    return EventEnvelope(
        event_id=EventId(event_id),
        event_type=event_type,
        schema_version=1,
        backend_id=BackendId("fake"),
        binding_id=BindingId("binding-1"),
        occurred_at=datetime.now(timezone.utc),
    )


class EventDispatcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_dispatches_registered_exact_event_type(self) -> None:
        received: List[str] = []

        async def handle(event):  # type: ignore[no-untyped-def]
            received.append(str(event.envelope.event_id))

        dispatcher = BackendEventDispatcher()
        dispatcher.register(SessionStateChanged, handle)
        await dispatcher.handle(
            SessionStateChanged(
                envelope("event-1", SessionStateChanged.EVENT_TYPE),
                SessionState.CONNECTED,
            )
        )
        self.assertEqual(["event-1"], received)

    async def test_unhandled_event_is_not_silently_ignored(self) -> None:
        dispatcher = BackendEventDispatcher()
        event = ContactChanged(
            envelope("event-1", ContactChanged.EVENT_TYPE),
            Contact(RemoteObjectId("contact-1"), "Contact"),
        )
        with self.assertRaises(UnhandledEventError):
            await dispatcher.handle(event)

    async def test_envelope_type_must_match_payload_contract(self) -> None:
        async def handle(event):  # type: ignore[no-untyped-def]
            raise AssertionError("invalid event must not reach handler")

        dispatcher = BackendEventDispatcher()
        dispatcher.register(SessionStateChanged, handle)
        event = SessionStateChanged(
            envelope("event-1", "contact.changed"),
            SessionState.CONNECTED,
        )
        with self.assertRaises(EventContractError) as context:
            await dispatcher.handle(event)
        self.assertNotIn("contact.changed", str(context.exception))

    async def test_duplicate_registration_is_rejected(self) -> None:
        async def handle(event):  # type: ignore[no-untyped-def]
            return None

        dispatcher = BackendEventDispatcher()
        dispatcher.register(SessionStateChanged, handle)
        with self.assertRaises(EventRegistrationError):
            dispatcher.register(SessionStateChanged, handle)

    async def test_dispatcher_connects_event_bus_to_message_receiver(self) -> None:
        received: List[MessageReceived] = []

        async def receive(event):  # type: ignore[no-untyped-def]
            received.append(event)
            return True

        dispatcher = BackendEventDispatcher.with_message_router(receive)
        bus = InMemoryEventBus(dispatcher)
        message = IncomingMessage(
            id=RemoteObjectId("message-1"),
            binding_id=BindingId("binding-1"),
            conversation_id=RemoteObjectId("conversation-1"),
            sender_id=RemoteObjectId("sender-1"),
            occurred_at=datetime.now(timezone.utc),
            text="hello",
        )
        await bus.publish(
            MessageReceived(
                envelope("event-1", MessageReceived.EVENT_TYPE),
                message,
            )
        )
        await bus.close()
        self.assertEqual([message], [event.message for event in received])

    async def test_dispatch_error_is_reported_by_event_bus(self) -> None:
        dispatcher = BackendEventDispatcher()
        bus = InMemoryEventBus(dispatcher)
        await bus.publish(
            SessionStateChanged(
                envelope("event-1", SessionStateChanged.EVENT_TYPE),
                SessionState.CONNECTED,
            )
        )
        with self.assertRaises(EventDispatchError) as context:
            await bus.close()
        self.assertEqual("UnhandledEventError", context.exception.failures[0].exception_type)


if __name__ == "__main__":
    unittest.main()
