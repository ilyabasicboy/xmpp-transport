"""Typed events emitted by backend sessions."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import ClassVar, Optional, Union

from .identifiers import BackendId, BindingId, CorrelationId, EventId
from .models import Contact, Conversation, IncomingMessage


class SessionState(str, Enum):
    STARTING = "starting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True)
class EventEnvelope:
    event_id: EventId
    event_type: str
    schema_version: int
    backend_id: BackendId
    binding_id: BindingId
    occurred_at: datetime
    correlation_id: Optional[CorrelationId] = None

    def __post_init__(self) -> None:
        if self.schema_version < 1:
            raise ValueError("schema_version must be positive")
        if not self.event_type:
            raise ValueError("event_type must not be empty")


@dataclass(frozen=True)
class MessageReceived:
    EVENT_TYPE: ClassVar[str] = "message.received"
    envelope: EventEnvelope
    message: IncomingMessage


@dataclass(frozen=True)
class MessageChanged:
    EVENT_TYPE: ClassVar[str] = "message.changed"
    envelope: EventEnvelope
    message: IncomingMessage


@dataclass(frozen=True)
class ConversationChanged:
    EVENT_TYPE: ClassVar[str] = "conversation.changed"
    envelope: EventEnvelope
    conversation: Conversation


@dataclass(frozen=True)
class ContactChanged:
    EVENT_TYPE: ClassVar[str] = "contact.changed"
    envelope: EventEnvelope
    contact: Contact


@dataclass(frozen=True)
class AuthorizationLost:
    EVENT_TYPE: ClassVar[str] = "authorization.lost"
    envelope: EventEnvelope
    reason: str


@dataclass(frozen=True)
class SessionStateChanged:
    EVENT_TYPE: ClassVar[str] = "session.state_changed"
    envelope: EventEnvelope
    state: SessionState
    detail: Optional[str] = None


BackendEvent = Union[
    MessageReceived,
    MessageChanged,
    ConversationChanged,
    ContactChanged,
    AuthorizationLost,
    SessionStateChanged,
]
