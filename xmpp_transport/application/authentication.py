"""Provider-neutral orchestration for short-lived authentication flows."""

import asyncio
from dataclasses import dataclass
from typing import Dict, Protocol

from xmpp_transport.domain.auth import AuthChallenge, AuthResponse, AuthState
from xmpp_transport.domain.errors import InvalidCommand
from xmpp_transport.domain.identifiers import BackendId, BindingId
from xmpp_transport.ports.backend import AuthenticationFlow, BackendPluginProvider
from xmpp_transport.ports.repositories import BindingRepository, CredentialCipher


class SessionStarter(Protocol):
    async def start(self, binding_id: BindingId, backend_id: BackendId) -> object:
        ...


@dataclass
class _ManagedFlow:
    backend_id: BackendId
    flow: AuthenticationFlow


class AuthenticationCoordinator:
    """Own auth flows and persist credentials only after successful completion."""

    def __init__(
        self,
        plugins: BackendPluginProvider,
        bindings: BindingRepository,
        cipher: CredentialCipher,
        sessions: SessionStarter,
    ) -> None:
        self._plugins = plugins
        self._bindings = bindings
        self._cipher = cipher
        self._sessions = sessions
        self._flows: Dict[BindingId, _ManagedFlow] = {}
        self._locks: Dict[BindingId, asyncio.Lock] = {}
        self._state_lock = asyncio.Lock()
        self._closed = False

    async def begin(self, binding_id: BindingId, backend_id: BackendId) -> AuthChallenge:
        lock = await self._lock_for(binding_id)
        async with lock:
            self._ensure_open()
            previous = self._flows.pop(binding_id, None)
            if previous is not None:
                await previous.flow.close()
            flow = self._plugins.get(backend_id).create_authentication(binding_id)
            self._flows[binding_id] = _ManagedFlow(backend_id, flow)
            try:
                return await flow.start()
            except BaseException:
                self._flows.pop(binding_id, None)
                await flow.close()
                raise

    async def respond(
        self,
        binding_id: BindingId,
        backend_id: BackendId,
        response: AuthResponse,
    ) -> AuthChallenge:
        lock = await self._lock_for(binding_id)
        async with lock:
            self._ensure_open()
            managed = self._flows.get(binding_id)
            if managed is None:
                raise InvalidCommand("authentication flow is not active")
            if managed.backend_id != backend_id:
                raise InvalidCommand("authentication flow belongs to another backend")
            flow = managed.flow
            challenge = await flow.respond(response)
            if challenge.state is AuthState.CONNECTED:
                plaintext = flow.credentials()
                encrypted = self._cipher.encrypt(plaintext)
                await self._bindings.save_encrypted_credentials(
                    binding_id, backend_id, encrypted
                )
                await self._sessions.start(binding_id, backend_id)
                self._flows.pop(binding_id, None)
                await flow.close()
            elif challenge.state in (AuthState.EXPIRED, AuthState.FAILED):
                self._flows.pop(binding_id, None)
                await flow.close()
            return challenge

    async def cancel(self, binding_id: BindingId) -> None:
        lock = await self._lock_for(binding_id)
        async with lock:
            flow = self._flows.pop(binding_id, None)
            if flow is not None:
                await flow.flow.close()

    async def close(self) -> None:
        async with self._state_lock:
            if self._closed:
                return
            self._closed = True
            flows = tuple(managed.flow for managed in self._flows.values())
            self._flows.clear()
        await asyncio.gather(*(flow.close() for flow in flows), return_exceptions=True)

    async def _lock_for(self, binding_id: BindingId) -> asyncio.Lock:
        async with self._state_lock:
            lock = self._locks.get(binding_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[binding_id] = lock
            return lock

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("authentication coordinator is closed")
