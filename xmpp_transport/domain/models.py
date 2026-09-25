"""Canonical models exchanged between adapters and application services."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Mapping, Optional, Sequence

from .identifiers import BindingId, RemoteObjectId


class ConversationKind(str, Enum):
    DIRECT = "direct"
    GROUP = "group"
    CHANNEL = "channel"


class MediaKind(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    FILE = "file"
    STICKER = "sticker"


@dataclass(frozen=True)
class RemoteAccount:
    id: RemoteObjectId
    display_name: str
    username: Optional[str] = None


@dataclass(frozen=True)
class Avatar:
    reference: str
    version: Optional[str] = None


@dataclass(frozen=True)
class Contact:
    id: RemoteObjectId
    display_name: str
    username: Optional[str] = None
    avatar: Optional[Avatar] = None


@dataclass(frozen=True)
class Participant:
    id: RemoteObjectId
    display_name: str
    is_admin: bool = False


@dataclass(frozen=True)
class Conversation:
    id: RemoteObjectId
    kind: ConversationKind
    title: str
    participants: Sequence[Participant] = field(default_factory=tuple)
    avatar: Optional[Avatar] = None


@dataclass(frozen=True)
class Media:
    id: RemoteObjectId
    kind: MediaKind
    content_type: Optional[str] = None
    file_name: Optional[str] = None
    size: Optional[int] = None


@dataclass(frozen=True)
class ReplyReference:
    message_id: RemoteObjectId


@dataclass(frozen=True)
class ForwardReference:
    source_name: Optional[str] = None
    source_message_id: Optional[RemoteObjectId] = None


@dataclass(frozen=True)
class IncomingMessage:
    id: RemoteObjectId
    binding_id: BindingId
    conversation_id: RemoteObjectId
    sender_id: RemoteObjectId
    occurred_at: datetime
    text: Optional[str] = None
    media: Sequence[Media] = field(default_factory=tuple)
    reply_to: Optional[ReplyReference] = None
    forwarded_from: Optional[ForwardReference] = None
    attributes: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class OutgoingMessage:
    client_message_id: str
    binding_id: BindingId
    conversation_id: RemoteObjectId
    text: Optional[str] = None
    media: Sequence[Media] = field(default_factory=tuple)
    reply_to: Optional[ReplyReference] = None

    def __post_init__(self) -> None:
        if not self.client_message_id:
            raise ValueError("client_message_id must not be empty")
        if not self.text and not self.media:
            raise ValueError("an outgoing message must contain text or media")

