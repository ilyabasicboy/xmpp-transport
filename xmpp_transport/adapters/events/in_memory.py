"""Ordered in-process event delivery.

Each binding owns one worker and one bounded queue. This makes ordering an
explicit guarantee while allowing unrelated bindings to make progress in
parallel. The public port remains independent of ``asyncio.Queue`` so a durable
broker can replace this adapter later.
"""

import asyncio
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from xmpp_transport.domain.events import BackendEvent
from xmpp_transport.domain.identifiers import BindingId, EventId
from xmpp_transport.ports.events import BackendEventHandler


@dataclass(frozen=True)
class EventFailure:
    event_id: EventId
    event_type: str
    binding_id: BindingId
    exception_type: str


class EventDispatchError(Exception):
    """Raised after queued work finishes when one or more handlers failed."""

    def __init__(self, failures: Tuple[EventFailure, ...]) -> None:
        self.failures = failures
        super().__init__("{} event(s) failed during dispatch".format(len(failures)))


class InMemoryEventBus:
    def __init__(self, handler: BackendEventHandler, queue_size: int = 100) -> None:
        if queue_size < 1:
            raise ValueError("queue_size must be positive")
        self._handler = handler
        self._queue_size = queue_size
        self._queues: Dict[BindingId, asyncio.Queue[Optional[BackendEvent]]] = {}
        self._workers: Dict[BindingId, asyncio.Task[None]] = {}
        self._failures: List[EventFailure] = []
        self._lock = asyncio.Lock()
        self._closed = False

    async def publish(self, event: BackendEvent) -> None:
        """Enqueue an event, applying backpressure when a binding is busy."""
        binding_id = event.envelope.binding_id
        async with self._lock:
            if self._closed:
                raise RuntimeError("event bus is closed")
            queue = self._queues.get(binding_id)
            if queue is None:
                queue = asyncio.Queue(maxsize=self._queue_size)
                self._queues[binding_id] = queue
                self._workers[binding_id] = asyncio.create_task(
                    self._consume(queue),
                    name="events:{}".format(binding_id),
                )
            await queue.put(event)

    async def join(self) -> None:
        """Wait for all currently queued events and report handler failures."""
        async with self._lock:
            queues = tuple(self._queues.values())
        await asyncio.gather(*(queue.join() for queue in queues))
        self._raise_failures()

    async def close(self) -> None:
        """Drain accepted events, stop workers, and reject new publication."""
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            queues = tuple(self._queues.values())
            workers = tuple(self._workers.values())

        dispatch_error: Optional[EventDispatchError] = None
        await asyncio.gather(*(queue.join() for queue in queues))
        try:
            self._raise_failures()
        except EventDispatchError as exc:
            dispatch_error = exc

        for queue in queues:
            await queue.put(None)
        if workers:
            await asyncio.gather(*workers)
        self._queues.clear()
        self._workers.clear()

        if dispatch_error is not None:
            raise dispatch_error

    async def __aenter__(self) -> "InMemoryEventBus":
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.close()

    async def _consume(self, queue: asyncio.Queue[Optional[BackendEvent]]) -> None:
        while True:
            event = await queue.get()
            try:
                if event is None:
                    return
                try:
                    await self._handler.handle(event)
                except Exception as exc:  # A failed event must not kill its binding worker.
                    envelope = event.envelope
                    self._failures.append(
                        EventFailure(
                            event_id=envelope.event_id,
                            event_type=envelope.event_type,
                            binding_id=envelope.binding_id,
                            exception_type=type(exc).__name__,
                        )
                    )
            finally:
                queue.task_done()

    def _raise_failures(self) -> None:
        if not self._failures:
            return
        failures = tuple(self._failures)
        self._failures.clear()
        raise EventDispatchError(failures)
