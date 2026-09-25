from datetime import datetime
from typing import List

from xmpp_transport.domain.events import BackendEvent


class FakeClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def now(self) -> datetime:
        return self.current


class RecordingEventSink:
    def __init__(self) -> None:
        self.events: List[BackendEvent] = []

    async def publish(self, event: BackendEvent) -> None:
        self.events.append(event)

