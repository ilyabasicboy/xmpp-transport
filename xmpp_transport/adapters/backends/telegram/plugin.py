"""Telethon-backed Telegram adapter preserving the original transport behavior."""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import shutil
from collections.abc import Awaitable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Callable, Optional, Protocol
from urllib.parse import quote
from uuid import uuid4

from xmpp_transport.domain.auth import AuthChallenge, AuthResponse, AuthResponseKind, AuthState
from xmpp_transport.domain.errors import AuthorizationRequired, BackendUnavailable, InvalidCommand
from xmpp_transport.domain.events import (
    AuthorizationLost,
    ContactChanged,
    ConversationChanged,
    EventEnvelope,
    MessageReceived,
    SessionState,
    SessionStateChanged,
)
from xmpp_transport.domain.identifiers import BackendId, BindingId, EventId, RemoteObjectId
from xmpp_transport.domain.models import (
    Avatar,
    Contact,
    Conversation,
    ConversationKind,
    ForwardReference,
    IncomingMessage,
    Media,
    MediaKind,
    OutgoingMessage,
    Participant,
    ReplyReference,
)
from xmpp_transport.ports.backend import (
    ContactSource,
    ConversationSource,
    MessageSender,
    SendResult,
)
from xmpp_transport.ports.events import BackendEventSink

from .avatar_cache import TelegramAvatarCache

log = logging.getLogger(__name__)

MEDIA_PROXY_CONNECT_TIMEOUT_SECONDS = 15
MEDIA_PROXY_LOOKUP_TIMEOUT_SECONDS = 15
XABBER_VOICE_MIME_TYPE = "audio/webm;codecs=opus"


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

    async def send_file(self, entity, file, **kwargs):  # type: ignore[no-untyped-def]
        ...

    async def forward_messages(self, entity, messages, from_peer):  # type: ignore[no-untyped-def]
        ...

    async def download_profile_photo(
        self, entity, file=bytes, download_big=False
    ):  # type: ignore[no-untyped-def]
        ...

    async def download_media(self, message, file=bytes):  # type: ignore[no-untyped-def]
        ...

    async def get_messages(self, entity, ids):  # type: ignore[no-untyped-def]
        ...

    def iter_download(
        self, media, *, request_size: int, file_size=None
    ):  # type: ignore[no-untyped-def]
        ...

    def add_event_handler(self, handler, event_builder) -> None:  # type: ignore[no-untyped-def]
        ...


TelegramClientFactory = Callable[[Optional[str]], TelegramClient]
TelegramAvatarStore = Callable[
    [BindingId, TelegramClient, object, int, str], Awaitable[Optional[Avatar]]
]
TelegramAvatarRemove = Callable[[BindingId, int], Awaitable[None]]


async def _resolve_telegram_entity(
    client: TelegramClient, peer_id: int
):  # type: ignore[no-untyped-def]
    from telethon.tl.functions.contacts import GetContactsRequest

    if peer_id >= 0:
        result = await client(GetContactsRequest(hash=0))
        for user in getattr(result, "users", ()):
            if int(user.id) == peer_id:
                return user
    async for dialog in client.iter_dialogs():
        if int(dialog.id) == peer_id:
            return dialog.entity
    raise BackendUnavailable(
        "Telegram chat is not available. Send /sync-contacts and try again."
    )


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
        media_register: Callable[[BindingId, int, int, str, str, Optional[int]], str],
        avatar_store: TelegramAvatarStore,
        avatar_remove: TelegramAvatarRemove,
        session_started: Callable[[BindingId, str], Awaitable[None]],
        session_stopped: Callable[[BindingId, str], Awaitable[None]],
    ) -> None:
        self._binding_id = binding_id
        self._event_sink = event_sink
        self._media_register = media_register
        self._avatar_store = avatar_store
        self._avatar_remove = avatar_remove
        self._session_started = session_started
        self._session_stopped = session_stopped
        self._session_data = session_data
        self._client = client_factory(session_data)
        self._started = False
        self._closed = False
        self._owner_id: Optional[int] = None
        self._conversations = {}
        self._sent_group_messages = set()
        self._listener_started_at: Optional[datetime] = None
        self._authorization_lost_published = False

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
            self._listener_started_at = datetime.now(timezone.utc)
            await self._client.connect()
            await self._ensure_authorized()
            owner = await self._client.get_me()
            self._owner_id = int(owner.id)
            self._install_message_handler()
            self._started = True
            # Incoming events may arrive as soon as the handler is installed. Register
            # the session with the stateless media proxy before the potentially slow
            # contact/avatar synchronization so media from those events is immediately
            # downloadable.
            await self._session_started(self._binding_id, self._session_data)
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
        await self._session_stopped(self._binding_id, self._session_data)
        await self._client.disconnect()
        await self._publish_state(SessionState.STOPPED)

    def features(self) -> Mapping[type, object]:
        return {
            MessageSender: self,
            ContactSource: self,
            ConversationSource: self,
        }

    async def contacts(self) -> Sequence[Contact]:
        return tuple(await self._list_contacts())

    async def conversations(self) -> Sequence[Conversation]:
        return tuple(await self._list_conversations())

    async def send_message(self, message: OutgoingMessage) -> SendResult:
        if not self._started or self._closed:
            raise BackendUnavailable("Telegram backend session is not active")
        if message.binding_id != self._binding_id:
            raise InvalidCommand("message belongs to another binding")
        await self._ensure_authorized()
        peer_id = self._peer_id(message.conversation_id)
        entity = await self._resolve_entity(peer_id)
        reply_to = int(str(message.reply_to.message_id)) if message.reply_to is not None else None
        if message.media:
            sent = await self._send_media(entity, message, reply_to)
        elif message.forwarded_from is not None and reply_to is None:
            try:
                sent = await self._send_forward(entity, message)
            except Exception:
                log.debug("Telegram native forward failed; using fallback", exc_info=True)
                sent = None
            if sent is None:
                sent = await self._client.send_message(
                    entity, self._forward_fallback(message)
                )
        else:
            sent = await self._client.send_message(
                entity,
                message.text or "",
                reply_to=reply_to,
            )
        if isinstance(sent, list):
            sent = sent[-1] if sent else None
        message_id = getattr(sent, "id", None)
        if message_id is None:
            raise BackendUnavailable("Telegram send result did not contain a message ID")
        if peer_id in self._conversations:
            self._sent_group_messages.add((peer_id, int(message_id)))
            if len(self._sent_group_messages) > 1024:
                self._sent_group_messages.pop()
        return SendResult(RemoteObjectId(str(message_id)))

    async def _send_forward(
        self, entity, message: OutgoingMessage
    ):  # type: ignore[no-untyped-def]
        reference = message.forwarded_from
        if reference is None or reference.source_message_id is None:
            return None
        source_peer_id = self._forward_source_peer_id(reference)
        if source_peer_id is None:
            return None
        source = await self._resolve_entity(source_peer_id)
        sent = await self._client.forward_messages(
            entity,
            int(str(reference.source_message_id)),
            from_peer=source,
        )
        if isinstance(sent, list):
            sent = sent[-1] if sent else None
        if message.text:
            await self._client.send_message(entity, message.text)
        return sent

    @classmethod
    def _forward_source_peer_id(
        cls, reference: ForwardReference
    ) -> Optional[int]:
        if reference.source_conversation_id is not None:
            try:
                return int(str(reference.source_conversation_id))
            except ValueError:
                pass
        for value in (reference.source_name, reference.source_recipient):
            localpart = str(value or "").split("@", 1)[0]
            if localpart.startswith("chat-"):
                try:
                    return int(localpart[5:])
                except ValueError:
                    continue
            if localpart.startswith("telegramg-"):
                payload = localpart[10:]
                _owner_hex, separator, chat_id = payload.partition("-")
                if not separator or not chat_id:
                    continue
                try:
                    return int(chat_id)
                except ValueError:
                    continue
        return None

    @staticmethod
    def _forward_fallback(message: OutgoingMessage) -> str:
        reference = message.forwarded_from
        parts = []
        if reference is not None:
            if reference.source_name:
                parts.append("Forwarded from {}".format(reference.source_name))
            if reference.body:
                parts.append(reference.body)
            parts.extend(item.source_url for item in reference.media if item.source_url)
        if message.text:
            parts.append(message.text)
        return "\n\n".join(parts)

    async def _send_media(
        self, entity, message: OutgoingMessage, reply_to
    ):  # type: ignore[no-untyped-def]
        media = tuple(
            item
            for item in message.media
            if (item.source_url or "").startswith(("http://", "https://"))
        )
        if not media:
            return await self._client.send_message(
                entity, message.text or "", reply_to=reply_to
            )
        files = [item.source_url for item in media]
        voice_note = len(media) == 1 and media[0].voice
        try:
            return await self._client.send_file(
                entity,
                files if len(files) > 1 else files[0],
                caption=message.text or None,
                reply_to=reply_to,
                voice_note=voice_note,
            )
        except Exception:
            log.debug(
                "Telegram URL upload failed; retrying downloaded media",
                exc_info=True,
            )
            downloaded = []
            try:
                downloaded = await self._download_media_files(media)
                return await self._client.send_file(
                    entity,
                    downloaded if len(downloaded) > 1 else downloaded[0],
                    caption=message.text or None,
                    reply_to=reply_to,
                    voice_note=voice_note,
                )
            except Exception:
                log.warning(
                    "Telegram media upload failed; sending links", exc_info=True
                )
                fallback = "\n".join(
                    item for item in ((message.text or "").strip(), *files) if item
                )
                return await self._client.send_message(
                    entity, fallback, reply_to=reply_to
                )
            finally:
                import os

                for path in downloaded:
                    try:
                        os.unlink(path)
                    except OSError:
                        log.debug("Could not remove Telegram upload file %s", path)



    async def _download_media_files(self, media: Sequence[Media]):  # type: ignore[no-untyped-def]
        import os
        import tempfile
        from urllib.parse import unquote, urlsplit

        import aiohttp

        downloaded = []
        async with aiohttp.ClientSession() as session:
            for item in media:
                suffix = os.path.splitext(
                    item.file_name or unquote(urlsplit(item.source_url or "").path)
                )[1]
                handle = tempfile.NamedTemporaryFile(
                    prefix="xmpp-telegram-upload-", suffix=suffix, delete=False
                )
                path = handle.name
                handle.close()
                size = 0
                try:
                    async with session.get(item.source_url) as response:
                        response.raise_for_status()
                        length = response.headers.get("Content-Length")
                        if length and int(length) > 50 * 1024 * 1024:
                            raise ValueError("Telegram upload exceeds 50 MiB")
                        with open(path, "wb") as output:
                            async for chunk in response.content.iter_chunked(65536):
                                size += len(chunk)
                                if size > 50 * 1024 * 1024:
                                    raise ValueError("Telegram upload exceeds 50 MiB")
                                output.write(chunk)
                except Exception:
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
                    raise
                downloaded.append(path)
        return downloaded

    async def _avatar(
        self, entity, peer_id: Optional[int] = None
    ) -> Optional[Avatar]:  # type: ignore[no-untyped-def]
        photo = getattr(entity, "photo", None)
        photo_id = getattr(photo, "photo_id", None)
        entity_id = getattr(entity, "id", peer_id)
        if entity_id is None:
            return None
        resolved_peer_id = int(entity_id)
        if photo_id is None:
            await self._avatar_remove(self._binding_id, resolved_peer_id)
            return None
        return await self._avatar_store(
            self._binding_id,
            self._client,
            entity,
            resolved_peer_id,
            str(photo_id),
        )

    async def _synchronize_contacts(self) -> None:
        for contact in await self._list_contacts():
            await self._event_sink.publish(
                ContactChanged(
                    envelope=self._envelope(ContactChanged.EVENT_TYPE),
                    contact=contact,
                )
            )

    async def _synchronize_conversations(self) -> None:
        for conversation in await self._list_conversations():
            await self._publish_conversation(conversation)

    async def _list_conversations(self) -> Sequence[Conversation]:
        conversations = []
        async for dialog in self._client.iter_dialogs():
            is_group = bool(getattr(dialog, "is_group", False))
            is_channel = bool(getattr(dialog, "is_channel", False))
            if not is_group and not is_channel:
                continue
            peer_id = int(dialog.id)
            conversation = self._conversations.get(peer_id)
            if conversation is None:
                conversation = Conversation(
                    id=RemoteObjectId(str(peer_id)),
                    kind=(
                        ConversationKind.CHANNEL
                        if is_channel
                        else ConversationKind.GROUP
                    ),
                    title=dialog.name or "Telegram group {}".format(peer_id),
                    avatar=await self._avatar(dialog.entity),
                    attributes=self._conversation_attributes(),
                )
                self._conversations[peer_id] = conversation
            conversations.append(conversation)
        return tuple(sorted(conversations, key=lambda item: item.title.casefold()))

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
                avatar=await self._avatar(user),
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
                    avatar=await self._avatar(dialog.entity),
                )
        return tuple(sorted(contacts.values(), key=lambda item: item.display_name.casefold()))

    async def _resolve_entity(self, peer_id: int):  # type: ignore[no-untyped-def]
        return await _resolve_telegram_entity(self._client, peer_id)

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
        if self._is_stale_event(event):
            return
        await self._ensure_authorized()
        is_group = self._is_group_event(event)
        if getattr(event, "out", False) and not is_group:
            return
        if is_group and self._is_avatar_update_event(event):
            peer_id = getattr(event, "chat_id", None)
            if peer_id is not None and int(peer_id) in self._conversations:
                sender_id = getattr(event, "sender_id", None) or self._owner_id or peer_id
                await self._update_group_from_event(
                    event, int(peer_id), int(sender_id)
                )
            return
        body = str(getattr(event, "raw_text", "") or "").strip()
        media = tuple(await self._incoming_media(event))
        forwarded = self._incoming_forward(event, body, media)
        if not body and not media:
            return
        peer_id = getattr(event, "chat_id", None) or getattr(event, "sender_id", None)
        message_id = getattr(event, "id", None)
        if peer_id is None or message_id is None:
            return
        echo_key = (int(peer_id), int(message_id))
        if getattr(event, "out", False) and is_group:
            if echo_key in self._sent_group_messages:
                self._sent_group_messages.discard(echo_key)
                return
        sender_id = self._owner_id if getattr(event, "out", False) else getattr(
            event, "sender_id", None
        )
        if sender_id is None:
            sender_id = peer_id
        if is_group:
            await self._update_group_from_event(event, int(peer_id), int(sender_id))
        reply_id = getattr(getattr(event, "message", None), "reply_to_msg_id", None)
        attributes = {}
        if is_group:
            attributes = {
                "is_group": "true",
                "is_self": (
                    "true" if getattr(event, "out", False) else "false"
                ),
            }
            if self._owner_id is not None:
                attributes["owner_remote_id"] = str(self._owner_id)
        await self._event_sink.publish(
            MessageReceived(
                envelope=self._envelope(MessageReceived.EVENT_TYPE),
                message=IncomingMessage(
                    id=RemoteObjectId(str(message_id)),
                    binding_id=self._binding_id,
                    conversation_id=RemoteObjectId(str(peer_id)),
                    sender_id=RemoteObjectId(str(sender_id)),
                    occurred_at=self._event_date(event),
                    text=None if forwarded is not None else body or None,
                    media=media,
                    forwarded_from=forwarded,
                    reply_to=(
                        ReplyReference(RemoteObjectId(str(reply_id)))
                        if reply_id is not None
                        else None
                    ),
                    attributes=attributes,
                ),
            )
        )

    async def _ensure_authorized(self) -> None:
        if await self._client.is_user_authorized():
            return
        if not self._authorization_lost_published:
            self._authorization_lost_published = True
            await self._event_sink.publish(
                AuthorizationLost(
                    envelope=self._envelope(AuthorizationLost.EVENT_TYPE),
                    reason="Telegram session expired",
                )
            )
        raise AuthorizationRequired("Telegram session expired")

    def _is_stale_event(self, event) -> bool:  # type: ignore[no-untyped-def]
        if self._listener_started_at is None:
            return False
        event_date = getattr(event, "date", None)
        if event_date is None:
            event_date = getattr(getattr(event, "message", None), "date", None)
        if event_date is None:
            return False
        if event_date.tzinfo is None:
            event_date = event_date.replace(tzinfo=timezone.utc)
        return event_date < self._listener_started_at


    def _incoming_forward(
        self, event, body: str, media: Sequence[Media]
    ) -> Optional[ForwardReference]:  # type: ignore[no-untyped-def]
        message = getattr(event, "message", None)
        header = getattr(message, "fwd_from", None) if message is not None else None
        if header is None:
            return None
        source_peer_id = self._forward_peer_id(header)
        source_name = str(getattr(header, "from_name", "") or "").strip() or None
        source_message_id = None
        for name in ("saved_from_msg_id", "channel_post"):
            value = getattr(header, name, None)
            if value is not None:
                source_message_id = RemoteObjectId(str(value))
                break
        forwarded_body = body or None
        if source_peer_id is None and source_name and forwarded_body:
            forwarded_body = "Forwarded from {}\n{}".format(
                source_name, forwarded_body
            )
        if source_peer_id is None and source_name is None:
            return None
        return ForwardReference(
            source_name=None,
            source_message_id=source_message_id,
            source_conversation_id=(
                RemoteObjectId(str(source_peer_id))
                if source_peer_id is not None
                else None
            ),
            sender_id=(
                RemoteObjectId(str(source_peer_id))
                if source_peer_id is not None and source_peer_id >= 0
                else None
            ),
            body=forwarded_body,
            media=tuple(media),
        )

    @classmethod
    def _forward_peer_id(cls, header) -> Optional[int]:  # type: ignore[no-untyped-def]
        for name in ("saved_from_peer", "from_id"):
            peer = getattr(header, name, None)
            if peer is None:
                continue
            if getattr(peer, "user_id", None) is not None:
                return int(peer.user_id)
            if getattr(peer, "chat_id", None) is not None:
                return -int(peer.chat_id)
            if getattr(peer, "channel_id", None) is not None:
                return int("-100{}".format(peer.channel_id))
        return None

    async def _incoming_media(self, event) -> Sequence[Media]:  # type: ignore[no-untyped-def]
        message = getattr(event, "message", None)
        media_value = getattr(message, "media", None) if message is not None else None
        if media_value is None:
            return ()
        if media_value.__class__.__name__ == "MessageMediaWebPage":
            return ()
        file_info = getattr(message, "file", None)
        mime_type = str(getattr(file_info, "mime_type", "") or "")
        is_voice = bool(getattr(message, "voice", None))
        is_sticker = bool(getattr(message, "sticker", None))
        if not mime_type:
            mime_type = (
                "image/jpeg"
                if getattr(message, "photo", None)
                else "application/octet-stream"
            )
        kind = self._media_kind(mime_type, is_sticker)
        if is_voice:
            mime_type = XABBER_VOICE_MIME_TYPE
        file_name = str(getattr(file_info, "name", "") or "") or "telegram-{}{}".format(
            getattr(event, "id", "media"), self._media_extension(mime_type)
        )
        source_url = self._media_register(
            self._binding_id,
            int(getattr(event, "chat_id")),
            int(getattr(event, "id")),
            mime_type,
            file_name,
            None if is_voice else getattr(file_info, "size", None),
        )
        return (
            Media(
                id=RemoteObjectId(
                    "{}:{}".format(
                        getattr(event, "chat_id", "chat"),
                        getattr(event, "id", "media"),
                    )
                ),
                kind=kind,
                content_type=mime_type,
                file_name=file_name,
                size=None if is_voice else getattr(file_info, "size", None),
                source_url=source_url,
                width=getattr(file_info, "width", None),
                height=getattr(file_info, "height", None),
                duration=getattr(file_info, "duration", None),
                voice=is_voice,
            ),
        )

    @staticmethod
    def _media_kind(mime_type: str, sticker: bool = False) -> MediaKind:
        if sticker:
            return MediaKind.STICKER
        major = mime_type.partition("/")[0].lower()
        return {
            "image": MediaKind.IMAGE,
            "video": MediaKind.VIDEO,
            "audio": MediaKind.AUDIO,
        }.get(major, MediaKind.FILE)

    @staticmethod
    def _media_extension(mime_type: str) -> str:
        return {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "video/mp4": ".mp4",
            "audio/ogg": ".ogg",
            XABBER_VOICE_MIME_TYPE: ".webm",
        }.get(mime_type.lower(), "")

    async def _update_group_from_event(
        self, event, peer_id: int, sender_id: int
    ) -> None:  # type: ignore[no-untyped-def]
        existing = self._conversations.get(peer_id)
        chat = await event.get_chat() if hasattr(event, "get_chat") else None
        sender = await event.get_sender() if hasattr(event, "get_sender") else None
        participants = {
            str(item.id): item for item in (existing.participants if existing else ())
        }
        participants[str(sender_id)] = Participant(
            RemoteObjectId(str(sender_id)),
            self._entity_title(sender, "Telegram user {}".format(sender_id)),
        )
        conversation = Conversation(
            id=RemoteObjectId(str(peer_id)),
            kind=(
                ConversationKind.CHANNEL
                if bool(getattr(event, "is_channel", False))
                else ConversationKind.GROUP
            ),
            title=self._entity_title(
                chat,
                existing.title if existing else "Telegram group {}".format(peer_id),
            ),
            participants=tuple(participants.values()),
            avatar=(
                await self._avatar(chat, peer_id)
                if chat is not None
                else (existing.avatar if existing else None)
            ),
            attributes=self._conversation_attributes(),
        )
        await self._publish_conversation(conversation)

    async def _publish_conversation(self, conversation: Conversation) -> None:
        self._conversations[int(str(conversation.id))] = conversation
        await self._event_sink.publish(
            ConversationChanged(
                envelope=self._envelope(ConversationChanged.EVENT_TYPE),
                conversation=conversation,
            )
        )

    def _conversation_attributes(self) -> Mapping[str, str]:
        if self._owner_id is None:
            return {}
        return {"owner_remote_id": str(self._owner_id)}

    @staticmethod
    def _is_avatar_update_event(event) -> bool:  # type: ignore[no-untyped-def]
        action = getattr(getattr(event, "message", None), "action", None)
        return action is not None and action.__class__.__name__ in {
            "MessageActionChatEditPhoto",
            "MessageActionChatDeletePhoto",
        }

    @staticmethod
    def _is_group_event(event) -> bool:  # type: ignore[no-untyped-def]
        if bool(getattr(event, "is_group", False)) or bool(
            getattr(event, "is_channel", False)
        ):
            return True
        if getattr(event, "is_private", None) is False:
            return True
        return isinstance(getattr(event, "chat_id", None), int) and event.chat_id < 0

    @classmethod
    def _entity_title(cls, entity, fallback: str) -> str:  # type: ignore[no-untyped-def]
        if entity is None:
            return fallback
        title = str(getattr(entity, "title", "") or "").strip()
        return title or cls._user_title(entity) or fallback

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
    supported_features = frozenset((MessageSender, ContactSource, ConversationSource))

    def __init__(self, client_factory: Optional[TelegramClientFactory] = None) -> None:
        self._client_factory = client_factory
        self._api_id = 0
        self._api_hash = ""
        self._media_base_url = "http://127.0.0.1:8080"
        self._media_url_secret = b""
        self._active_sessions: dict[BindingId, str] = {}
        self._media_stream_semaphores: dict[BindingId, asyncio.Semaphore] = {}
        self._media_stream_request_size = 524288
        self._web_module = None
        self._avatar_unreferenced_ttl_days = 7
        self._avatar_cleanup_interval_seconds = 86400
        self._avatar_cleanup_task: Optional[asyncio.Task] = None
        self._avatar_cache = TelegramAvatarCache(
            "data/avatars/telegram", self._media_base_url, 524288
        )

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
        media_url_secret = options.get("media_url_secret", "").strip()
        if len(media_url_secret) < 32:
            raise ValueError("media_url_secret must contain at least 32 characters")
        try:
            media_stream_request_size = int(
                options.get("media_stream_request_size", "524288")
            )
        except ValueError:
            raise ValueError("media_stream_request_size must be an integer") from None
        if media_stream_request_size <= 0:
            raise ValueError("media_stream_request_size must be positive")
        self._api_id = api_id
        self._media_base_url = options.get("media_base_url", self._media_base_url).strip()
        if not self._media_base_url.startswith(("http://", "https://")):
            raise ValueError("media_base_url must use HTTP or HTTPS")
        avatar_base_url = options.get("avatar_base_url", self._media_base_url).strip()
        if not avatar_base_url.startswith(("http://", "https://")):
            raise ValueError("avatar_base_url must use HTTP or HTTPS")
        avatar_storage_dir = options.get(
            "avatar_storage_dir", "data/avatars/telegram"
        ).strip()
        if not avatar_storage_dir:
            raise ValueError("avatar_storage_dir must not be empty")
        try:
            avatar_max_bytes = int(options.get("avatar_max_bytes", "524288"))
        except ValueError:
            raise ValueError("avatar_max_bytes must be an integer") from None
        if avatar_max_bytes <= 0:
            raise ValueError("avatar_max_bytes must be positive")
        try:
            avatar_unreferenced_ttl_days = int(
                options.get("avatar_unreferenced_ttl_days", "7")
            )
            avatar_cleanup_interval_seconds = int(
                options.get("avatar_cleanup_interval_seconds", "86400")
            )
        except ValueError:
            raise ValueError("Telegram avatar cleanup settings must be integers") from None
        if avatar_unreferenced_ttl_days < 0:
            raise ValueError("avatar_unreferenced_ttl_days must not be negative")
        if avatar_cleanup_interval_seconds <= 0:
            raise ValueError("avatar_cleanup_interval_seconds must be positive")
        self._avatar_cache = TelegramAvatarCache(
            avatar_storage_dir, avatar_base_url, avatar_max_bytes
        )
        self._api_hash = api_hash
        self._media_url_secret = media_url_secret.encode("utf-8")
        self._media_stream_request_size = media_stream_request_size
        self._avatar_unreferenced_ttl_days = avatar_unreferenced_ttl_days
        self._avatar_cleanup_interval_seconds = avatar_cleanup_interval_seconds

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
            self._register_media,
            self._store_avatar,
            self._remove_avatar,
            self._session_started,
            self._session_stopped,
        )

    def _register_media(
        self,
        binding_id: BindingId,
        peer_id: int,
        message_id: int,
        mime_type: str,
        file_name: str,
        bytes_count: Optional[int],
    ) -> str:
        payload = self._encode_url_part(
            json.dumps(
                {
                    "binding_id": str(binding_id),
                    "peer_id": peer_id,
                    "message_id": message_id,
                    "mime_type": mime_type,
                    "file_name": file_name,
                    "bytes_count": bytes_count,
                },
                separators=(",", ":"),
            ).encode("utf-8")
        )
        signature = self._encode_url_part(
            hmac.new(
                self._media_url_secret,
                payload.encode("ascii"),
                hashlib.sha256,
            ).digest()
        )
        token = "{}.{}".format(payload, signature)
        return "{}/media/{}/{}".format(
            self._media_base_url.rstrip("/"), token, quote(file_name)
        )

    async def _session_started(self, binding_id: BindingId, session_data: str) -> None:
        self._active_sessions[binding_id] = session_data
        if self._avatar_cleanup_task is None:
            self._avatar_cleanup_task = asyncio.create_task(self._avatar_cleanup_loop())

    async def _session_stopped(self, binding_id: BindingId, session_data: str) -> None:
        if self._active_sessions.get(binding_id) == session_data:
            self._active_sessions.pop(binding_id, None)
            self._media_stream_semaphores.pop(binding_id, None)
        if not self._active_sessions and self._avatar_cleanup_task is not None:
            self._avatar_cleanup_task.cancel()
            try:
                await self._avatar_cleanup_task
            except asyncio.CancelledError:
                pass
            self._avatar_cleanup_task = None

    async def _store_avatar(
        self,
        binding_id: BindingId,
        client: TelegramClient,
        entity,
        peer_id: int,
        photo_id: str,
    ) -> Optional[Avatar]:  # type: ignore[no-untyped-def]
        cached = await self._avatar_cache.store(
            client, entity, str(binding_id), peer_id, photo_id
        )
        if cached is None:
            return None
        return Avatar(
            reference=cached.url,
            version=cached.avatar_id,
            content_type=cached.mime_type,
            size=cached.bytes_count,
        )

    async def _remove_avatar(self, binding_id: BindingId, peer_id: int) -> None:
        await self._avatar_cache.forget(str(binding_id), peer_id)

    async def _avatar_cleanup_loop(self) -> None:
        while True:
            try:
                removed = await self._avatar_cache.cleanup_unreferenced(
                    self._avatar_unreferenced_ttl_days
                )
                if removed:
                    log.info("Removed %s unreferenced Telegram avatar(s)", removed)
                await asyncio.sleep(self._avatar_cleanup_interval_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Telegram avatar cache cleanup failed")
                await asyncio.sleep(self._avatar_cleanup_interval_seconds)

    async def avatar_handler(self, request):  # type: ignore[no-untyped-def]
        from aiohttp import web

        path = self._avatar_cache.path(request.match_info["filename"])
        if path is None:
            raise web.HTTPNotFound()
        return web.FileResponse(
            path,
            headers={
                "Content-Type": "image/jpeg",
                "Cache-Control": "public, max-age=31536000, immutable",
                "Access-Control-Allow-Origin": "*",
            },
        )

    async def media_handler(self, request):  # type: ignore[no-untyped-def]
        web = self._web_module
        if web is None:
            from aiohttp import web as aiohttp_web

            web = aiohttp_web

        try:
            value = self._decode_media_token(request.match_info["token"])
            binding_id = BindingId(str(value["binding_id"]))
            peer_id = int(value["peer_id"])
            message_id = int(value["message_id"])
            mime_type = str(value["mime_type"])
            file_name = str(value["file_name"])
            bytes_value = value.get("bytes_count")
            bytes_count = int(bytes_value) if bytes_value is not None else None
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            raise web.HTTPNotFound()
        session_data = self._active_sessions.get(binding_id)
        if session_data is None:
            log.warning(
                "Telegram media request has no active session binding_id=%s "
                "peer_id=%s message_id=%s",
                binding_id,
                peer_id,
                message_id,
            )
            raise web.HTTPNotFound()
        semaphore = self._media_stream_semaphores.setdefault(
            binding_id, asyncio.Semaphore(1)
        )
        await semaphore.acquire()
        client = self._create_client(session_data)
        response = None
        try:
            await asyncio.wait_for(
                client.connect(), timeout=MEDIA_PROXY_CONNECT_TIMEOUT_SECONDS
            )
            if not await client.is_user_authorized():
                log.warning(
                    "Telegram media session is not authorized binding_id=%s "
                    "peer_id=%s message_id=%s",
                    binding_id,
                    peer_id,
                    message_id,
                )
                raise web.HTTPNotFound()
            entity = await asyncio.wait_for(
                _resolve_telegram_entity(client, peer_id),
                timeout=MEDIA_PROXY_LOOKUP_TIMEOUT_SECONDS,
            )
            message = await asyncio.wait_for(
                client.get_messages(entity, ids=message_id),
                timeout=MEDIA_PROXY_LOOKUP_TIMEOUT_SECONDS,
            )
            media = getattr(message, "media", None) if message is not None else None
            if media is None:
                log.warning(
                    "Telegram media message is unavailable binding_id=%s "
                    "peer_id=%s message_id=%s",
                    binding_id,
                    peer_id,
                    message_id,
                )
                raise web.HTTPNotFound()
            safe_name = file_name.replace("\\", "_").replace('"', "_")
            safe_name = safe_name.replace("\r", "_").replace("\n", "_")
            headers = {
                "Content-Type": mime_type,
                "Content-Disposition": 'inline; filename="{}"'.format(safe_name),
                "Cache-Control": "private, max-age=300",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Expose-Headers": (
                    "Content-Length, Content-Type, Content-Disposition"
                ),
            }
            if bytes_count is not None and not self._is_xabber_voice(mime_type):
                headers["Content-Length"] = str(bytes_count)
            response = web.StreamResponse(status=200, headers=headers)
            await response.prepare(request)
            if self._is_xabber_voice(mime_type):
                await self._stream_voice(response, client, media, bytes_count)
            else:
                async for chunk in client.iter_download(
                    media,
                    request_size=self._media_stream_request_size,
                    file_size=bytes_count,
                ):
                    await response.write(bytes(chunk))
            await response.write_eof()
            return response
        except BackendUnavailable as exc:
            log.warning(
                "Telegram media peer is unavailable binding_id=%s peer_id=%s "
                "message_id=%s reason=%s",
                binding_id,
                peer_id,
                message_id,
                exc,
            )
            raise web.HTTPNotFound()
        except ConnectionResetError:
            return response if response is not None else web.Response(status=204)
        except asyncio.TimeoutError:
            raise web.HTTPGatewayTimeout()
        finally:
            try:
                await client.disconnect()
            finally:
                semaphore.release()

    async def _stream_voice(
        self, response, client: TelegramClient, media, bytes_count: Optional[int]
    ) -> None:  # type: ignore[no-untyped-def]
        web = self._web_module
        if web is None:
            from aiohttp import web as aiohttp_web

            web = aiohttp_web

        if shutil.which("ffmpeg") is None:
            raise web.HTTPInternalServerError(
                reason="ffmpeg is required to convert Telegram voice messages"
            )
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-c:a",
            "copy",
            "-f",
            "webm",
            "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        writer = asyncio.create_task(
            self._write_voice_input(process, client, media, bytes_count)
        )
        try:
            while True:
                chunk = await process.stdout.read(65536)
                if not chunk:
                    break
                await response.write(chunk)
            await writer
            stderr = await process.stderr.read()
            if await process.wait() != 0:
                log.warning("Telegram voice conversion failed: %s", stderr[:500])
                raise web.HTTPBadGateway(reason="Telegram voice conversion failed")
        finally:
            if not writer.done():
                writer.cancel()
                try:
                    await writer
                except asyncio.CancelledError:
                    pass
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def _write_voice_input(
        self, process, client: TelegramClient, media, bytes_count: Optional[int]
    ) -> None:  # type: ignore[no-untyped-def]
        try:
            async for chunk in client.iter_download(
                media,
                request_size=self._media_stream_request_size,
                file_size=bytes_count,
            ):
                process.stdin.write(bytes(chunk))
                await process.stdin.drain()
        finally:
            process.stdin.close()
            await process.stdin.wait_closed()

    @staticmethod
    def _is_xabber_voice(mime_type: str) -> bool:
        return mime_type.replace(" ", "").lower() == XABBER_VOICE_MIME_TYPE

    def _decode_media_token(self, token: str) -> Mapping[str, object]:
        payload, separator, supplied_signature = token.rpartition(".")
        if not separator or not payload or not supplied_signature:
            raise ValueError("invalid media token")
        expected_signature = self._encode_url_part(
            hmac.new(
                self._media_url_secret,
                payload.encode("ascii"),
                hashlib.sha256,
            ).digest()
        )
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise ValueError("invalid media signature")
        value = json.loads(self._decode_url_part(payload).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("invalid media payload")
        return value

    @staticmethod
    def _encode_url_part(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    @staticmethod
    def _decode_url_part(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        return base64.b64decode(value + padding, altchars=b"-_", validate=True)
