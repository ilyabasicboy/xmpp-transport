"""Telethon-backed Telegram adapter preserving the original transport behavior."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Mapping, Optional, Protocol, Sequence
from uuid import uuid4

from xmpp_transport.domain.auth import AuthChallenge, AuthResponse, AuthResponseKind, AuthState
from xmpp_transport.domain.errors import AuthorizationRequired, BackendUnavailable, InvalidCommand
from xmpp_transport.domain.events import (
    AuthorizationLost,
    ContactChanged,
    EventEnvelope,
    MessageReceived,
    SessionState,
    SessionStateChanged,
)
from xmpp_transport.domain.identifiers import BackendId, BindingId, EventId, RemoteObjectId
from xmpp_transport.domain.models import Contact, IncomingMessage, OutgoingMessage, ReplyReference
from xmpp_transport.ports.backend import ContactSource, MessageSender, SendResult
from xmpp_transport.ports.events import BackendEventSink


log = logging.getLogger(__name__)


class TelegramClient(Protocol):
    session: object

    async def connect(self) -> None:
        ...

    async def disconnect(self) -> None:
        ...

    async def is_user_authorized(self) -> bool:
        ...

    async def qr_login(self):  # type: ignore[no-untyped-def]
        ...

    async def sign_in(self, *, password: str):  # type: ignore[no-untyped-def]
        ...

    async def get_me(self):  # type: ignore[no-untyped-def]
        ...

    async def __call__(self, request):  # type: ignore[no-untyped-def]
        ...

    def iter_dialogs(self):  # type: ignore[no-untyped-def]
        ...

    async def send_message(self, entity, body: str, reply_to=None):  # type: ignore[no-untyped-def]
        ...

    def add_event_handler(self, handler, event_builder) -> None:  # type: ignore[no-untyped-def]
        ...


TelegramClientFactory = Callable[[Optional[str]], TelegramClient]


class TelegramAuthenticationFlow:
    def __init__(
        self,
        client: TelegramClient,
        password_required_error: type,
    ) -> None:
        self._client = client
        self._password_required_error = password_required_error
        self._qr_login = None
        self._credentials: Optional[bytes] = None
        self._started = False
        self._closed = False

    async def start(self) -> AuthChallenge:
        if self._closed:
            raise RuntimeError("Telegram authentication flow is closed")
        if self._started:
            raise InvalidCommand("Telegram authentication flow is already started")
        self._started = True
        await self._client.connect()
        if await self._client.is_user_authorized():
            await self._complete()
            return AuthChallenge(AuthState.CONNECTED, message="Telegram authorization completed")
        self._qr_login = await self._client.qr_login()
        return AuthChallenge(
            AuthState.WAITING_QR,
            expires_at=self._qr_login.expires,
            public_url=self._qr_login.url,
            message="Scan the QR code with the Telegram application",
        )

    async def respond(self, response: AuthResponse) -> AuthChallenge:
        if self._closed or not self._started:
            raise InvalidCommand("Telegram authentication flow is not active")
        try:
            if response.kind is AuthResponseKind.PASSWORD:
                await self._client.sign_in(password=response.secret)
            else:
                if self._qr_login is None:
                    raise InvalidCommand("Telegram QR login is not active")
                await self._qr_login.wait()
        except self._password_required_error:
            return AuthChallenge(
                AuthState.WAITING_PASSWORD,
                message="Telegram requires a cloud password",
            )
        except asyncio.TimeoutError:
            return AuthChallenge(
                AuthState.EXPIRED,
                message="Telegram QR authorization expired",
            )
        except Exception:
            if response.kind is AuthResponseKind.PASSWORD:
                return AuthChallenge(
                    AuthState.WAITING_PASSWORD,
                    message="Telegram cloud password was rejected",
                )
            raise
        await self._complete()
        return AuthChallenge(AuthState.CONNECTED, message="Telegram authorization completed")

    def credentials(self) -> bytes:
        if self._credentials is None:
            raise InvalidCommand("Telegram authentication has not completed")
        return self._credentials

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._client.disconnect()

    async def _complete(self) -> None:
        session_data = self._client.session.save()  # type: ignore[attr-defined]
        if not isinstance(session_data, str) or not session_data:
            raise RuntimeError("Telegram did not return a serializable session")
        self._credentials = session_data.encode("utf-8")


class TelegramBackendSession:
    def __init__(
        self,
        binding_id: BindingId,
        session_data: str,
        event_sink: BackendEventSink,
        client_factory: TelegramClientFactory,
    ) -> None:
        self._binding_id = binding_id
        self._event_sink = event_sink
        self._client = client_factory(session_data)
        self._started = False
        self._closed = False

    @property
    def binding_id(self) -> BindingId:
        return self._binding_id

    async def start(self) -> None:
        if self._closed:
            raise BackendUnavailable("Telegram backend session is closed")
        if self._started:
            return
        await self._publish_state(SessionState.STARTING)
        try:
            await self._client.connect()
            if not await self._client.is_user_authorized():
                await self._event_sink.publish(
                    AuthorizationLost(
                        envelope=self._envelope(AuthorizationLost.EVENT_TYPE),
                        reason="Telegram session expired",
                    )
                )
                raise AuthorizationRequired("Telegram session expired")
            self._install_message_handler()
            self._started = True
            await self._synchronize_contacts()
        except Exception as exc:
            await self._publish_state(SessionState.FAILED, type(exc).__name__)
            raise BackendUnavailable("Telegram session failed to start") from exc
        await self._publish_state(SessionState.CONNECTED)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._started = False
        await self._client.disconnect()
        await self._publish_state(SessionState.STOPPED)

    def features(self) -> Mapping[type, object]:
        return {MessageSender: self, ContactSource: self}

    async def contacts(self) -> Sequence[Contact]:
        return tuple(await self._list_contacts())

    async def send_message(self, message: OutgoingMessage) -> SendResult:
        if not self._started or self._closed:
            raise BackendUnavailable("Telegram backend session is not active")
        if message.binding_id != self._binding_id:
            raise InvalidCommand("message belongs to another binding")
        peer_id = self._peer_id(message.conversation_id)
        entity = await self._resolve_direct_entity(peer_id)
        sent = await self._client.send_message(
            entity,
            message.text or "",
            reply_to=(
                int(message.reply_to.message_id) if message.reply_to is not None else None
            ),
        )
        message_id = getattr(sent, "id", None)
        if message_id is None:
            raise BackendUnavailable("Telegram send result did not contain a message ID")
        return SendResult(RemoteObjectId(str(message_id)))

    async def _synchronize_contacts(self) -> None:
        for contact in await self._list_contacts():
            await self._event_sink.publish(
                ContactChanged(
                    envelope=self._envelope(ContactChanged.EVENT_TYPE),
                    contact=contact,
                    force=True,
                )
            )

    async def _list_contacts(self) -> Sequence[Contact]:
        from telethon.tl.functions.contacts import GetContactsRequest

        contacts = {}
        result = await self._client(GetContactsRequest(hash=0))
        for user in getattr(result, "users", ()):
            peer_id = int(user.id)
            contacts[peer_id] = Contact(
                RemoteObjectId(str(peer_id)),
                self._user_title(user),
                username=getattr(user, "username", None),
            )
        async for dialog in self._client.iter_dialogs():
            if bool(getattr(dialog, "is_group", False)) or bool(
                getattr(dialog, "is_channel", False)
            ):
                continue
            peer_id = int(dialog.id)
            if peer_id not in contacts:
                contacts[peer_id] = Contact(
                    RemoteObjectId(str(peer_id)),
                    dialog.name or self._user_title(dialog.entity),
                    username=getattr(dialog.entity, "username", None),
                )
        return tuple(sorted(contacts.values(), key=lambda item: item.display_name.casefold()))

    async def _resolve_direct_entity(self, peer_id: int):  # type: ignore[no-untyped-def]
        from telethon.tl.functions.contacts import GetContactsRequest

        result = await self._client(GetContactsRequest(hash=0))
        for user in getattr(result, "users", ()):
            if int(user.id) == peer_id:
                return user
        async for dialog in self._client.iter_dialogs():
            if bool(getattr(dialog, "is_group", False)) or bool(
                getattr(dialog, "is_channel", False)
            ):
                continue
            if int(dialog.id) == peer_id:
                return dialog.entity
        raise BackendUnavailable(
            "Telegram direct chat is not available. Send /sync-contacts and try again."
        )

    def _install_message_handler(self) -> None:
        from telethon import events

        async def handle(event) -> None:  # type: ignore[no-untyped-def]
            try:
                await self._receive_message(event)
            except Exception:
                log.exception(
                    "Telegram incoming message handler failed binding_id=%s",
                    self._binding_id,
                )

        self._client.add_event_handler(handle, events.NewMessage())

    async def _receive_message(self, event) -> None:  # type: ignore[no-untyped-def]
        if getattr(event, "out", False) or not getattr(event, "is_private", False):
            return
        body = str(getattr(event, "raw_text", "") or "").strip()
        if not body:
            return
        peer_id = getattr(event, "chat_id", None) or getattr(event, "sender_id", None)
        message_id = getattr(event, "id", None)
        if peer_id is None or message_id is None:
            return
        reply_id = getattr(getattr(event, "message", None), "reply_to_msg_id", None)
        await self._event_sink.publish(
            MessageReceived(
                envelope=self._envelope(MessageReceived.EVENT_TYPE),
                message=IncomingMessage(
                    id=RemoteObjectId(str(message_id)),
                    binding_id=self._binding_id,
                    conversation_id=RemoteObjectId(str(peer_id)),
                    sender_id=RemoteObjectId(str(peer_id)),
                    occurred_at=self._event_date(event),
                    text=body,
                    reply_to=(
                        ReplyReference(RemoteObjectId(str(reply_id)))
                        if reply_id is not None
                        else None
                    ),
                ),
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
            backend_id=TelegramBackendPlugin.backend_id,
            binding_id=self._binding_id,
            occurred_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _event_date(event) -> datetime:  # type: ignore[no-untyped-def]
        value = getattr(event, "date", None) or getattr(
            getattr(event, "message", None), "date", None
        )
        if value is None:
            return datetime.now(timezone.utc)
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    @staticmethod
    def _peer_id(value: RemoteObjectId) -> int:
        try:
            return int(str(value))
        except ValueError:
            raise InvalidCommand("Telegram peer ID must be numeric") from None

    @staticmethod
    def _user_title(user) -> str:  # type: ignore[no-untyped-def]
        title = " ".join(
            item
            for item in (
                str(getattr(user, "first_name", "") or "").strip(),
                str(getattr(user, "last_name", "") or "").strip(),
            )
            if item
        )
        return title or str(getattr(user, "username", "") or getattr(user, "id", ""))


class TelegramBackendPlugin:
    backend_id = BackendId("telegram")

    def __init__(self, client_factory: Optional[TelegramClientFactory] = None) -> None:
        self._client_factory = client_factory
        self._api_id = 0
        self._api_hash = ""

    def configure(self, options: Mapping[str, str]) -> None:
        try:
            api_id = int(options.get("api_id", "0"))
        except ValueError:
            raise ValueError("api_id must be an integer") from None
        api_hash = options.get("api_hash", "").strip()
        if api_id <= 0:
            raise ValueError("api_id must be positive")
        if not api_hash:
            raise ValueError("api_hash must not be empty")
        self._api_id = api_id
        self._api_hash = api_hash

    def _create_client(self, session_data: Optional[str]) -> TelegramClient:
        if self._client_factory is not None:
            return self._client_factory(session_data)
        if self._api_id <= 0 or not self._api_hash:
            raise ValueError("Telegram plugin is not configured")
        from telethon import TelegramClient as TelethonClient
        from telethon.sessions import StringSession

        return TelethonClient(
            StringSession(session_data or ""),
            self._api_id,
            self._api_hash,
            catch_up=False,
        )

    def create_authentication(self, binding_id: BindingId) -> TelegramAuthenticationFlow:
        del binding_id
        from telethon.errors import SessionPasswordNeededError

        return TelegramAuthenticationFlow(
            self._create_client(None),
            password_required_error=SessionPasswordNeededError,
        )

    def create_session(
        self, binding_id: BindingId, credentials: bytes, event_sink: BackendEventSink
    ) -> TelegramBackendSession:
        try:
            session_data = credentials.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("Telegram credentials must be a UTF-8 StringSession") from None
        if not session_data:
            raise ValueError("Telegram StringSession must not be empty")
        return TelegramBackendSession(
            binding_id,
            session_data,
            event_sink,
            self._create_client,
        )
