from typing import Protocol

from xmpp_transport.domain.events import BackendEvent


class BackendEventSink(Protocol):
    async def publish(self, event: BackendEvent) -> None:
        """Publish an event without exposing the underlying queue or broker."""

