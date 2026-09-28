"""In-process echo backend exercising the complete shared transport path."""

import hashlib
from datetime import datetime, timezone
from typing import Mapping
from uuid import uuid4

from xmpp_transport.domain.auth import AuthChallenge, AuthResponse, AuthState
from xmpp_transport.domain.errors import BackendUnavailable, InvalidCommand
from xmpp_transport.domain.events import (
    EventEnvelope,
    MessageReceived,
    SessionState,
    SessionStateChanged,
)
from xmpp_transport.domain.identifiers import BackendId, BindingId, EventId, RemoteObjectId
from xmpp_transport.domain.models import IncomingMessage, OutgoingMessage
from xmpp_transport.ports.backend import MessageSender, SendResult
from xmpp_transport.ports.events import BackendEventSink


class FakeAuthenticationFlow:
    def __init__(self) -> None:
        self._closed = False

    async def start(self) -> AuthChallenge:
        if self._closed:
            raise RuntimeError("authentication flow is closed")
        return AuthChallenge(AuthState.CONNECTED, message="Fake backend needs no authorization")

    async def respond(self, response: AuthResponse) -> AuthChallenge:
        raise InvalidCommand("fake backend does not accept authentication responses")

    async def close(self) -> None:
        self._closed = True


class FakeBackendSession:
    def __init__(self, binding_id: BindingId, event_sink: BackendEventSink) -> None:
        self._binding_id = binding_id
        self._event_sink = event_sink
        self._started = False
        self._closed = False

    @property
    def binding_id(self) -> BindingId:
        return self._binding_id

    async def start(self) -> None:
        if self._closed:
            raise BackendUnavailable("fake backend session is closed")
        if self._started:
            return
        self._started = True
        await self._publish_state(SessionState.CONNECTED)

    async def close(self) -> None:
        if self._closed:
            return
        was_started = self._started
        self._started = False
        self._closed = True
        if was_started:
            await self._publish_state(SessionState.STOPPED)

    def features(self) -> Mapping[type, object]:
        return {MessageSender: self}

    async def send_message(self, message: OutgoingMessage) -> SendResult:
        if not self._started or self._closed:
            raise BackendUnavailable("fake backend session is not active")
        if message.binding_id != self._binding_id:
            raise InvalidCommand("message belongs to another binding")
        remote_id = RemoteObjectId(self._remote_message_id(message.client_message_id))
        await self._event_sink.publish(
            MessageReceived(
                envelope=self._envelope(MessageReceived.EVENT_TYPE),
                message=IncomingMessage(
                    id=remote_id,
                    binding_id=self._binding_id,
                    conversation_id=message.conversation_id,
                    sender_id=message.conversation_id,
                    occurred_at=datetime.now(timezone.utc),
                    text=message.text,
                    media=message.media,
                    reply_to=message.reply_to,
                ),
            )
        )
        return SendResult(remote_id)

    async def _publish_state(self, state: SessionState) -> None:
        await self._event_sink.publish(
            SessionStateChanged(
                envelope=self._envelope(SessionStateChanged.EVENT_TYPE),
                state=state,
            )
        )

    def _envelope(self, event_type: str) -> EventEnvelope:
        return EventEnvelope(
            event_id=EventId(str(uuid4())),
            event_type=event_type,
            schema_version=1,
            backend_id=FakeBackendPlugin.backend_id,
            binding_id=self._binding_id,
            occurred_at=datetime.now(timezone.utc),
        )

    def _remote_message_id(self, client_message_id: str) -> str:
        digest = hashlib.sha256(
            "{}\0{}".format(self._binding_id, client_message_id).encode("utf-8")
        ).hexdigest()
        return "fake-{}".format(digest[:32])


class FakeBackendPlugin:
    backend_id = BackendId("fake")

    def create_authentication(self, binding_id: BindingId) -> FakeAuthenticationFlow:
        return FakeAuthenticationFlow()

    def create_session(
        self, binding_id: BindingId, credentials: bytes, event_sink: BackendEventSink
    ) -> FakeBackendSession:
        # Credentials are intentionally ignored, but the shared supervisor still
        # exercises encrypted credential loading exactly like production backends.
        return FakeBackendSession(binding_id, event_sink)
