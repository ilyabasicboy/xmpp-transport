"""Provider-neutral inbound and outbound direct-message routing."""

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Hashable, Tuple

from xmpp_transport.domain.errors import FeatureUnavailable
from xmpp_transport.domain.events import MessageReceived
from xmpp_transport.domain.identifiers import BindingId
from xmpp_transport.domain.models import OutgoingMessage
from xmpp_transport.ports.backend import BackendFeatureProvider, MessageSender, SendResult
from xmpp_transport.ports.repositories import MessageMappingRepository
from xmpp_transport.ports.xmpp import XmppMessageSink


class _KeyedLocks:
    """Serialize duplicate operations without retaining every message key forever."""

    def __init__(self) -> None:
        self._guard = asyncio.Lock()
        self._entries: Dict[Hashable, Tuple[asyncio.Lock, int]] = {}

    @asynccontextmanager
    async def hold(self, key: Hashable) -> AsyncIterator[None]:
        async with self._guard:
            lock, users = self._entries.get(key, (asyncio.Lock(), 0))
            self._entries[key] = (lock, users + 1)
        await lock.acquire()
        try:
            yield
        finally:
            lock.release()
            async with self._guard:
                current_lock, users = self._entries[key]
                if users == 1:
                    del self._entries[key]
                else:
                    self._entries[key] = (current_lock, users - 1)


class MessageRouter:
    def __init__(
        self,
        features: BackendFeatureProvider,
        mappings: MessageMappingRepository,
        xmpp: XmppMessageSink,
    ) -> None:
        self._features = features
        self._mappings = mappings
        self._xmpp = xmpp
        self._locks = _KeyedLocks()

    async def send(self, message: OutgoingMessage) -> SendResult:
        """Send once per binding/client ID and persist the provider mapping."""
        key = ("out", message.binding_id, message.client_message_id)
        async with self._locks.hold(key):
            existing = await self._mappings.remote_id_for_client_message(
                message.binding_id, message.client_message_id
            )
            if existing is not None:
                return SendResult(existing)

            sender = await self._features.feature(message.binding_id, MessageSender)
            if sender is None:
                raise FeatureUnavailable(
                    "active binding does not provide message sending: {}".format(
                        message.binding_id
                    )
                )
            result = await sender.send_message(message)
            await self._mappings.save_mapping(
                message.binding_id,
                message.client_message_id,
                result.remote_message_id,
            )
            return result

    async def receive(self, event: MessageReceived) -> bool:
        """Deliver a remote message once; return False for an acknowledged duplicate."""
        message = event.message
        self._validate_envelope(event)
        key = ("in", message.binding_id, message.id)
        async with self._locks.hold(key):
            if await self._mappings.incoming_delivered(message.binding_id, message.id):
                return False
            await self._xmpp.deliver_message(message)
            await self._mappings.mark_incoming_delivered(message.binding_id, message.id)
            return True

    @staticmethod
    def _validate_envelope(event: MessageReceived) -> None:
        if event.envelope.binding_id != event.message.binding_id:
            raise ValueError("message binding does not match event envelope")
