"""MAX plugin boundary for the shared transport runtime.

The network client is intentionally introduced behind this boundary so MAX
wire details do not leak into the application and domain packages.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Mapping, Optional, Protocol, Tuple, Type
from uuid import uuid4

from xmpp_transport.domain.auth import AuthChallenge, AuthResponse, AuthState
from xmpp_transport.domain.errors import BackendUnavailable, FeatureUnavailable, InvalidCommand
from xmpp_transport.domain.events import (
    AuthorizationLost,
    EventEnvelope,
    MessageReceived,
    SessionState,
    SessionStateChanged,
)
from xmpp_transport.domain.identifiers import BackendId, BindingId, EventId, RemoteObjectId
from xmpp_transport.domain.models import IncomingMessage, OutgoingMessage, ReplyReference
from xmpp_transport.ports.backend import MessageSender, SendResult
from xmpp_transport.ports.events import BackendEventSink

from .models import MaxAuthorizationError, MaxIncomingMessage


class MaxClient(Protocol):
    def set_message_handler(self, handler: Callable[[MaxIncomingMessage], object]) -> None:
        ...

    def set_authorization_lost_handler(
        self, handler: Callable[[MaxAuthorizationError], object]
    ) -> None:
        ...

    async def start(self) -> None:
        ...

    async def close(self) -> None:
        ...

    async def send_message(
        self,
        text: str,
        chat_id: Optional[str] = None,
        reply_to_message_id: Optional[str] = None,
    ) -> dict:
        ...


MaxClientFactory = Callable[["MaxCredentials"], MaxClient]


class MaxQrClient(Protocol):
    async def start(self):  # type: ignore[no-untyped-def]
        ...

    async def wait_for_credentials(self) -> Tuple[str, str, str]:
        ...

    async def submit_password(self, password: str) -> Tuple[str, str, str]:
        ...

    async def close(self) -> None:
        ...


@dataclass(frozen=True)
class MaxCredentials:
    token: str
    device_id: str
    account_id: str

    @classmethod
    def decode(cls, payload: bytes) -> "MaxCredentials":
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("MAX credentials must be a UTF-8 JSON object") from exc
        if not isinstance(value, dict):
            raise ValueError("MAX credentials must be a JSON object")
        fields = {}
        for name in ("token", "device_id", "account_id"):
            item = value.get(name)
            if not isinstance(item, str) or not item:
                raise ValueError("MAX credentials require a non-empty {}".format(name))
            fields[name] = item
        return cls(**fields)

    def encode(self) -> bytes:
        return json.dumps(
            {
                "token": self.token,
                "device_id": self.device_id,
                "account_id": self.account_id,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    def __repr__(self) -> str:
        return "MaxCredentials(token=<redacted>, device_id=<redacted>, account_id={!r})".format(
            self.account_id
        )


class MaxAuthenticationFlow:
    def __init__(
        self,
        client: MaxQrClient,
        password_required_error: Type[Exception],
        login_error: Type[Exception],
    ) -> None:
        self._client = client
        self._password_required_error = password_required_error
        self._login_error = login_error
        self._credentials: Optional[MaxCredentials] = None
        self._started = False
        self._closed = False

    async def start(self) -> AuthChallenge:
        if self._closed:
            raise RuntimeError("MAX authentication flow is closed")
        if self._started:
            raise InvalidCommand("MAX authentication flow is already started")
        self._started = True
        try:
            challenge = await self._client.start()
        except self._login_error as exc:
            return AuthChallenge(AuthState.FAILED, message=str(exc))
        return AuthChallenge(
            AuthState.WAITING_QR,
            expires_at=challenge.expires_at,
            public_url=challenge.qr_link,
            message="Scan the QR code with the MAX application",
        )

    async def respond(self, response: AuthResponse) -> AuthChallenge:
        if self._closed:
            raise RuntimeError("MAX authentication flow is closed")
        if not self._started:
            raise InvalidCommand("MAX authentication flow has not been started")
        try:
            if response.kind.value == "password":
                values = await self._client.submit_password(response.secret)
            else:
                values = await self._client.wait_for_credentials()
        except self._password_required_error:
            return AuthChallenge(
                AuthState.WAITING_PASSWORD,
                message="MAX requires a two-factor authentication password",
            )
        except self._login_error as exc:
            if response.kind.value == "password":
                return AuthChallenge(
                    AuthState.WAITING_PASSWORD,
                    message="MAX password was not accepted",
                )
            return AuthChallenge(AuthState.FAILED, message=str(exc))
        token, device_id, account_id = values
        self._credentials = MaxCredentials(token, device_id, account_id)
        return AuthChallenge(AuthState.CONNECTED, message="MAX authorization completed")

    def credentials(self) -> bytes:
        if self._credentials is None:
            raise InvalidCommand("MAX authentication has not completed")
        return self._credentials.encode()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._client.close()


class MaxBackendSession:
    def __init__(
        self,
        binding_id: BindingId,
        credentials: MaxCredentials,
        event_sink: BackendEventSink,
        client_factory: MaxClientFactory,
    ) -> None:
        self._binding_id = binding_id
        self._credentials = credentials
        self._event_sink = event_sink
        self._client = client_factory(credentials)
        self._started = False
        self._closed = False

    @property
    def binding_id(self) -> BindingId:
        return self._binding_id

    async def start(self) -> None:
        if self._closed:
            raise BackendUnavailable("MAX backend session is closed")
        if self._started:
            return
        self._client.set_message_handler(self._receive_message)
        self._client.set_authorization_lost_handler(self._authorization_lost)
        await self._publish_state(SessionState.STARTING)
        try:
            await self._client.start()
        except Exception as exc:
            await self._publish_state(SessionState.FAILED, str(exc))
            raise BackendUnavailable("MAX session failed to start") from exc
        self._started = True
        await self._publish_state(SessionState.CONNECTED)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._started = False
        await self._client.close()
        await self._publish_state(SessionState.STOPPED)

    def features(self) -> Mapping[type, object]:
        return {MessageSender: self}

    async def send_message(self, message: OutgoingMessage) -> SendResult:
        if not self._started or self._closed:
            raise BackendUnavailable("MAX backend session is not active")
        if message.binding_id != self._binding_id:
            raise InvalidCommand("message belongs to another binding")
        if message.media:
            raise FeatureUnavailable("MAX media sending is not connected yet")
        try:
            payload = await self._client.send_message(
                text=message.text or "",
                chat_id=str(message.conversation_id),
                reply_to_message_id=(
                    str(message.reply_to.message_id) if message.reply_to is not None else None
                ),
            )
        except Exception as exc:
            raise BackendUnavailable("MAX message send failed") from exc
        response_message = payload.get("message") or {}
        remote_id = response_message.get("id")
        if remote_id is None:
            raise BackendUnavailable("MAX send response did not contain a message ID")
        return SendResult(RemoteObjectId(str(remote_id)))

    async def _receive_message(self, message: MaxIncomingMessage) -> None:
        if message.is_self or not message.chat_id or not message.message_id:
            return
        reply = (
            ReplyReference(RemoteObjectId(message.reply_to_message_id))
            if message.reply_to_message_id
            else None
        )
        await self._event_sink.publish(
            MessageReceived(
                envelope=self._envelope(MessageReceived.EVENT_TYPE),
                message=IncomingMessage(
                    id=RemoteObjectId(message.message_id),
                    binding_id=self._binding_id,
                    conversation_id=RemoteObjectId(message.chat_id),
                    sender_id=RemoteObjectId(message.sender_id),
                    occurred_at=datetime.now(timezone.utc),
                    text=message.text or None,
                    reply_to=reply,
                    attributes={
                        "is_group": "true" if message.is_group else "false",
                        "sender_title": message.sender_title or "",
                        "chat_title": message.chat_title or "",
                    },
                ),
            )
        )

    async def _authorization_lost(self, exc: MaxAuthorizationError) -> None:
        self._started = False
        await self._event_sink.publish(
            AuthorizationLost(
                envelope=self._envelope(AuthorizationLost.EVENT_TYPE),
                reason=str(exc),
            )
        )

    async def _publish_state(self, state: SessionState, detail: Optional[str] = None) -> None:
        await self._event_sink.publish(
            SessionStateChanged(
                envelope=self._envelope(SessionStateChanged.EVENT_TYPE),
                state=state,
                detail=detail,
            )
        )

    def _envelope(self, event_type: str) -> EventEnvelope:
        return EventEnvelope(
            event_id=EventId(str(uuid4())),
            event_type=event_type,
            schema_version=1,
            backend_id=MaxBackendPlugin.backend_id,
            binding_id=self._binding_id,
            occurred_at=datetime.now(timezone.utc),
        )


class MaxBackendPlugin:
    backend_id = BackendId("max")

    def __init__(self, client_factory: Optional[MaxClientFactory] = None) -> None:
        self._client_factory = client_factory or self._create_client

    @staticmethod
    def _create_client(credentials: MaxCredentials) -> MaxClient:
        from .client import PersonalMaxBackend

        return PersonalMaxBackend(token=credentials.token, device_id=credentials.device_id)

    def create_authentication(self, binding_id: BindingId) -> MaxAuthenticationFlow:
        from .auth import MaxLoginError, MaxPasswordRequired, MaxQrAuthorizationFlow

        return MaxAuthenticationFlow(
            MaxQrAuthorizationFlow(),
            password_required_error=MaxPasswordRequired,
            login_error=MaxLoginError,
        )

    def create_session(
        self, binding_id: BindingId, credentials: bytes, event_sink: BackendEventSink
    ) -> MaxBackendSession:
        return MaxBackendSession(
            binding_id,
            MaxCredentials.decode(credentials),
            event_sink,
            self._client_factory,
        )
