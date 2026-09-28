"""Provider-neutral application orchestration."""

from .authentication import AuthenticationCoordinator
from .event_dispatcher import BackendEventDispatcher
from .message_router import MessageRouter
from .roster_sync import RosterSync
from .session_supervisor import SessionSupervisor

__all__ = [
    "AuthenticationCoordinator",
    "BackendEventDispatcher",
    "MessageRouter",
    "RosterSync",
    "SessionSupervisor",
]
