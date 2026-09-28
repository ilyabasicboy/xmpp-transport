import unittest
from datetime import datetime, timezone
from typing import List

from cryptography.fernet import Fernet

from xmpp_transport.adapters.events import InMemoryEventBus
from xmpp_transport.domain.events import BackendEvent, EventEnvelope, SessionState, SessionStateChanged
from xmpp_transport.domain.identifiers import BackendId, BindingId, EventId
from xmpp_transport.runtime.composition import EventSinkRelay, compose_single_backend
from xmpp_transport.runtime.config import BackendConfig, DatabaseConfig, RuntimeConfig


class RecordingHandler:
    def __init__(self) -> None:
        self.events: List[BackendEvent] = []

    async def handle(self, event: BackendEvent) -> None:
        self.events.append(event)


class FakePlugin:
    backend_id = BackendId("fake")

    def create_authentication(self, binding_id):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def create_session(self, binding_id, credentials, event_sink):  # type: ignore[no-untyped-def]
        raise NotImplementedError


class EventSinkRelayTests(unittest.IsolatedAsyncioTestCase):
    async def test_requires_one_binding_then_delegates(self) -> None:
        relay = EventSinkRelay()
        event = SessionStateChanged(
            EventEnvelope(
                event_id=EventId("event-1"),
                event_type=SessionStateChanged.EVENT_TYPE,
                schema_version=1,
                backend_id=BackendId("fake"),
                binding_id=BindingId("binding-1"),
                occurred_at=datetime.now(timezone.utc),
            ),
            SessionState.CONNECTED,
        )
        with self.assertRaises(RuntimeError):
            await relay.publish(event)
        handler = RecordingHandler()
        bus = InMemoryEventBus(handler)
        relay.bind(bus)
        with self.assertRaises(RuntimeError):
            relay.bind(bus)
        await relay.publish(event)
        await bus.close()
        self.assertEqual([event], handler.events)


class CompositionTests(unittest.TestCase):
    def test_builds_single_backend_without_importing_optional_runtime_drivers(self) -> None:
        key = Fernet.generate_key().decode("ascii")
        config = RuntimeConfig(
            backends=(
                BackendConfig(
                    "fake",
                    "fake.example.com",
                    {"component_secret_env": "FAKE_COMPONENT_SECRET"},
                ),
            ),
            database=DatabaseConfig("postgresql://user:private@db/transport"),
            credential_key_env="CREDENTIAL_KEY",
        )
        runtime = compose_single_backend(
            config,
            FakePlugin(),  # type: ignore[arg-type]
            {
                "FAKE_COMPONENT_SECRET": "component-secret",
                "CREDENTIAL_KEY": key,
            },
        )
        self.assertEqual("starting", runtime.health.snapshot().status.value)
        with self.assertRaisesRegex(RuntimeError, "not initialized"):
            _ = runtime.authentication

    def test_requires_database_and_component_secret(self) -> None:
        key = Fernet.generate_key().decode("ascii")
        without_database = RuntimeConfig(
            (BackendConfig("fake", "fake.example.com", {}),),
            credential_key_env="CREDENTIAL_KEY",
        )
        with self.assertRaises(ValueError):
            compose_single_backend(
                without_database,
                FakePlugin(),  # type: ignore[arg-type]
                {"CREDENTIAL_KEY": key},
            )


if __name__ == "__main__":
    unittest.main()
