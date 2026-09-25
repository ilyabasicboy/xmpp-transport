"""Python 3.9-compatible ownership and shutdown for background tasks."""

import asyncio
from typing import Awaitable, List, Optional


class TaskSupervisor:
    def __init__(self) -> None:
        self._tasks: List[asyncio.Task[None]] = []
        self._closed = False

    def create_task(self, operation: Awaitable[None], name: Optional[str] = None) -> None:
        if self._closed:
            raise RuntimeError("task supervisor is closed")
        self._tasks.append(asyncio.create_task(operation, name=name))

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def __aenter__(self) -> "TaskSupervisor":
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.close()

