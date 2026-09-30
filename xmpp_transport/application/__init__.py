"""Provider-neutral application orchestration."""

from .authentication import AuthenticationCoordinator
from .conversation_sync import ConversationSync
from .event_dispatcher import BackendEventDispatcher
from .message_router import MessageRouter
from .roster_sync import RosterSync
from .session_supervisor import SessionSupervisor

__all__ = [
    "AuthenticationCoordinator",
    "ConversationSync",
    "BackendEventDispatcher",
    "MessageRouter",
    "RosterSync",
    "SessionSupervisor",
]
