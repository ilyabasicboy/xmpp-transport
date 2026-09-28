"""Provider-neutral application orchestration."""

from .event_dispatcher import BackendEventDispatcher
from .message_router import MessageRouter
from .roster_sync import RosterSync
from .session_supervisor import SessionSupervisor

__all__ = ["BackendEventDispatcher", "MessageRouter", "RosterSync", "SessionSupervisor"]
