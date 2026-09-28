import asyncio
import unittest
from datetime import datetime, timezone
from typing import List

from xmpp_transport.adapters.events import EventDispatchError, InMemoryEventBus
from xmpp_transport.domain.events import BackendEvent, EventEnvelope, SessionState, SessionStateChanged
from xmpp_transport.domain.identifiers import BackendId, BindingId, EventId


def event(binding: str, sequence: int) -> SessionStateChanged:
    return SessionStateChanged(
        envelope=EventEnvelope(
            event_id=EventId("{}-{}".format(binding, sequence)),
            event_type="session.state_changed",
            schema_version=1,
            backend_id=BackendId("fake"),
            binding_id=BindingId(binding),
            occurred_at=datetime.now(timezone.utc),
        ),
        state=SessionState.CONNECTED,
        detail=str(sequence),
    )


class RecordingHandler:
    def __init__(self) -> None:
        self.received: List[str] = []

    async def handle(self, item: BackendEvent) -> None:
        await asyncio.sleep(0)
        self.received.append(str(item.envelope.event_id))


class EventBusTests(unittest.IsolatedAsyncioTestCase):
    async def test_preserves_order_within_each_binding(self) -> None:
        handler = RecordingHandler()
        bus = InMemoryEventBus(handler, queue_size=2)

        for sequence in range(5):
            await bus.publish(event("binding-a", sequence))
            await bus.publish(event("binding-b", sequence))
        await bus.close()

        self.assertEqual(
            ["binding-a-{}".format(index) for index in range(5)],
            [item for item in handler.received if item.startswith("binding-a-")],
        )
        self.assertEqual(
            ["binding-b-{}".format(index) for index in range(5)],
            [item for item in handler.received if item.startswith("binding-b-")],
        )

    async def test_different_bindings_are_processed_concurrently(self) -> None:
        both_started = asyncio.Event()
        started = set()

        class BarrierHandler:
            async def handle(self, item: BackendEvent) -> None:
                started.add(item.envelope.binding_id)
                if len(started) == 2:
                    both_started.set()
                await asyncio.wait_for(both_started.wait(), timeout=1)

        bus = InMemoryEventBus(BarrierHandler())
        await bus.publish(event("binding-a", 1))
        await bus.publish(event("binding-b", 1))
        await bus.close()
        self.assertEqual({BindingId("binding-a"), BindingId("binding-b")}, started)

    async def test_handler_failure_does_not_stop_later_events(self) -> None:
        handled: List[str] = []

        class FailingHandler:
            async def handle(self, item: BackendEvent) -> None:
                identifier = str(item.envelope.event_id)
                if identifier == "binding-a-1":
                    raise ValueError("private diagnostic must not be copied")
                handled.append(identifier)

        bus = InMemoryEventBus(FailingHandler())
        await bus.publish(event("binding-a", 1))
        await bus.publish(event("binding-a", 2))

        with self.assertRaises(EventDispatchError) as context:
            await bus.close()

        self.assertEqual(["binding-a-2"], handled)
        failure = context.exception.failures[0]
        self.assertEqual("ValueError", failure.exception_type)
        self.assertNotIn("private diagnostic", str(context.exception))

    async def test_publish_after_close_is_rejected(self) -> None:
        bus = InMemoryEventBus(RecordingHandler())
        await bus.close()
        with self.assertRaises(RuntimeError):
            await bus.publish(event("binding-a", 1))


if __name__ == "__main__":
    unittest.main()
