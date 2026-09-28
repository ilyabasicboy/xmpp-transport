import unittest
from datetime import datetime, timezone

from xmpp_transport.domain.auth import AuthResponse, AuthResponseKind, AuthChallenge, AuthState
from xmpp_transport.domain.events import EventEnvelope, MessageReceived
from xmpp_transport.domain.identifiers import BackendId, BindingId, EventId, RemoteObjectId
from xmpp_transport.domain.models import OutgoingMessage


class IdentifierTests(unittest.TestCase):
    def test_identifiers_reject_blank_values(self) -> None:
        with self.assertRaises(ValueError):
            BackendId("  ")

    def test_identifier_types_are_not_equal(self) -> None:
        self.assertNotEqual(BackendId("same"), BindingId("same"))


class ModelTests(unittest.TestCase):
    def test_event_payload_has_stable_type_name(self) -> None:
        self.assertEqual("message.received", MessageReceived.EVENT_TYPE)

    def test_authentication_url_must_use_https(self) -> None:
        with self.assertRaises(ValueError):
            AuthChallenge(AuthState.WAITING_QR, public_url="http://example.com/auth")

    def test_authentication_secret_is_redacted_from_repr(self) -> None:
        response = AuthResponse(AuthResponseKind.PASSWORD, "do-not-log-me")
        self.assertNotIn("do-not-log-me", repr(response))

    def test_outgoing_message_requires_content(self) -> None:
        with self.assertRaises(ValueError):
            OutgoingMessage(
                client_message_id="client-1",
                binding_id=BindingId("binding-1"),
                conversation_id=RemoteObjectId("opaque-conversation"),
            )

    def test_event_schema_version_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            EventEnvelope(
                event_id=EventId("event-1"),
                event_type="message.received",
                schema_version=0,
                backend_id=BackendId("telegram"),
                binding_id=BindingId("binding-1"),
                occurred_at=datetime.now(timezone.utc),
            )


if __name__ == "__main__":
    unittest.main()
