"""Opaque, expiring HTTP continuation endpoints for authentication flows."""

import secrets
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlsplit

from xmpp_transport.application.authentication import AuthenticationCoordinator
from xmpp_transport.domain.auth import AuthChallenge, AuthResponse, AuthResponseKind, AuthState
from xmpp_transport.domain.identifiers import BackendId, BindingId


@dataclass(frozen=True)
class AuthAttempt:
    public_url: str
    challenge: AuthChallenge


@dataclass
class _AttemptState:
    binding_id: BindingId
    backend_id: BackendId
    challenge: AuthChallenge
    expires_at: float


class AiohttpAuthenticationApi:
    def __init__(
        self,
        coordinator: AuthenticationCoordinator,
        token_ttl: float = 300,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if token_ttl <= 0:
            raise ValueError("authentication token TTL must be positive")
        self._coordinator = coordinator
        self._token_ttl = token_ttl
        self._clock = clock
        self._attempts: Dict[str, _AttemptState] = {}
        self._web: Optional[Any] = None

    def register(self, application: Any, web_module: Any) -> None:
        self._web = web_module
        application.router.add_get("/auth/{token}", self._status)
        application.router.add_post("/auth/{token}", self._respond)

    async def create_attempt(
        self,
        binding_id: BindingId,
        backend_id: BackendId,
        public_base_url: str,
    ) -> AuthAttempt:
        base = public_base_url.rstrip("/")
        parsed_base = urlsplit(base)
        if parsed_base.scheme != "https" or not parsed_base.netloc:
            raise ValueError("authentication public base URL must use HTTPS")
        stale_tokens = tuple(
            token
            for token, attempt in self._attempts.items()
            if attempt.binding_id == binding_id
        )
        for token in stale_tokens:
            self._attempts.pop(token, None)
        challenge = await self._coordinator.begin(binding_id, backend_id)
        token = secrets.token_urlsafe(32)
        self._attempts[token] = _AttemptState(
            binding_id,
            backend_id,
            challenge,
            self._clock() + self._token_ttl,
        )
        return AuthAttempt("{}/auth/{}".format(base, token), challenge)

    async def close(self) -> None:
        attempts = tuple(self._attempts.values())
        self._attempts.clear()
        for attempt in attempts:
            await self._coordinator.cancel(attempt.binding_id)

    async def _status(self, request: Any) -> Any:
        attempt = await self._attempt(request.match_info.get("token", ""))
        if attempt is None:
            return self._json({"error": "authentication attempt not found"}, 404)
        challenge = attempt.challenge
        return self._json(
            {
                "state": challenge.state.value,
                "provider_url": challenge.public_url,
                "expires_at": (
                    challenge.expires_at.isoformat() if challenge.expires_at else None
                ),
                "message": challenge.message,
            },
            200,
        )

    async def _respond(self, request: Any) -> Any:
        token = request.match_info.get("token", "")
        attempt = await self._attempt(token)
        if attempt is None:
            return self._json({"error": "authentication attempt not found"}, 404)
        try:
            payload = await request.json()
        except Exception:
            return self._json({"error": "invalid JSON body"}, 400)
        kind_value = payload.get("kind") if isinstance(payload, dict) else None
        if kind_value == AuthResponseKind.PASSWORD.value:
            secret = payload.get("secret")
            if not isinstance(secret, str) or not secret:
                return self._json({"error": "password is required"}, 400)
            response = AuthResponse(AuthResponseKind.PASSWORD, secret)
        elif kind_value == AuthResponseKind.CONFIRMATION.value:
            response = AuthResponse(AuthResponseKind.CONFIRMATION, "confirmed")
        else:
            return self._json({"error": "unsupported response kind"}, 400)
        challenge = await self._coordinator.respond(
            attempt.binding_id,
            attempt.backend_id,
            response,
        )
        attempt.challenge = challenge
        if challenge.state in (AuthState.CONNECTED, AuthState.EXPIRED, AuthState.FAILED):
            self._attempts.pop(token, None)
        return self._json(
            {"state": challenge.state.value, "message": challenge.message},
            200,
        )

    async def _attempt(self, token: str) -> Optional[_AttemptState]:
        attempt = self._attempts.get(token)
        if attempt is None:
            return None
        if self._clock() < attempt.expires_at:
            return attempt
        self._attempts.pop(token, None)
        await self._coordinator.cancel(attempt.binding_id)
        return None

    def _json(self, payload: object, status: int) -> Any:
        if self._web is None:
            raise RuntimeError("authentication HTTP API is not registered")
        return self._web.json_response(payload, status=status)
