"""Explicit routing from backend events to focused application handlers."""

from typing import Awaitable, Callable, Dict, Type

from xmpp_transport.domain.errors import TransportError
from xmpp_transport.domain.events import BackendEvent


EventHandler = Callable[[BackendEvent], Awaitable[object]]


class EventRegistrationError(ValueError):
    pass


class UnhandledEventError(TransportError):
    pass


class EventContractError(TransportError):
    pass


class BackendEventDispatcher:
    """Dispatch exact event classes; inheritance must not change routing silently."""

    def __init__(self) -> None:
        self._handlers: Dict[Type[object], EventHandler] = {}

    def register(self, event_class: Type[object], handler: EventHandler) -> None:
        if event_class in self._handlers:
            raise EventRegistrationError(
                "handler already registered for {}".format(event_class.__name__)
            )
        expected_type = getattr(event_class, "EVENT_TYPE", None)
        if not isinstance(expected_type, str) or not expected_type:
            raise EventRegistrationError(
                "event class has no stable EVENT_TYPE: {}".format(event_class.__name__)
            )
        self._handlers[event_class] = handler

    async def handle(self, event: BackendEvent) -> None:
        event_class = type(event)
        handler = self._handlers.get(event_class)
        if handler is None:
            raise UnhandledEventError(
                "no handler registered for {}".format(event_class.__name__)
            )
        expected_type = event_class.EVENT_TYPE
        if event.envelope.event_type != expected_type:
            raise EventContractError(
                "event type mismatch for {}: expected {}".format(
                    event_class.__name__, expected_type
                )
            )
        await handler(event)

    @classmethod
    def with_message_router(cls, receive: EventHandler) -> "BackendEventDispatcher":
        """Create a dispatcher with the first vertical-slice handler wired."""
        from xmpp_transport.domain.events import MessageReceived

        dispatcher = cls()
        dispatcher.register(MessageReceived, receive)
        return dispatcher
