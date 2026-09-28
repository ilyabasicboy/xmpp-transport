"""MAX plugin boundary for the shared transport runtime.

The network client is intentionally introduced behind this boundary so MAX
wire details do not leak into the application and domain packages.
"""

import json
from dataclasses import dataclass
from typing import Mapping

from xmpp_transport.domain.auth import AuthChallenge, AuthResponse, AuthState
from xmpp_transport.domain.errors import BackendUnavailable, FeatureUnavailable
from xmpp_transport.domain.identifiers import BackendId, BindingId
from xmpp_transport.ports.events import BackendEventSink


@dataclass(frozen=True)
class MaxCredentials:
    token: str
    device_id: str
    account_id: str

    @classmethod
    def decode(cls, payload: bytes) -> "MaxCredentials":
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("MAX credentials must be a UTF-8 JSON object") from exc
        if not isinstance(value, dict):
            raise ValueError("MAX credentials must be a JSON object")
        fields = {}
        for name in ("token", "device_id", "account_id"):
            item = value.get(name)
            if not isinstance(item, str) or not item:
                raise ValueError("MAX credentials require a non-empty {}".format(name))
            fields[name] = item
        return cls(**fields)

    def encode(self) -> bytes:
        return json.dumps(
            {
                "token": self.token,
                "device_id": self.device_id,
                "account_id": self.account_id,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    def __repr__(self) -> str:
        return "MaxCredentials(token=<redacted>, device_id=<redacted>, account_id={!r})".format(
            self.account_id
        )


class MaxAuthenticationFlow:
    """Placeholder boundary until the QR flow is moved behind the shared contract."""

    async def start(self) -> AuthChallenge:
        return AuthChallenge(
            AuthState.FAILED,
            message="MAX QR authorization is not connected to the unified runtime yet",
        )

    async def respond(self, response: AuthResponse) -> AuthChallenge:
        raise FeatureUnavailable("MAX QR authorization is not connected yet")

    async def close(self) -> None:
        return None


class MaxBackendSession:
    def __init__(
        self,
        binding_id: BindingId,
        credentials: MaxCredentials,
        event_sink: BackendEventSink,
    ) -> None:
        self._binding_id = binding_id
        self._credentials = credentials
        self._event_sink = event_sink

    @property
    def binding_id(self) -> BindingId:
        return self._binding_id

    async def start(self) -> None:
        raise BackendUnavailable("MAX network session adapter is not connected yet")

    async def close(self) -> None:
        return None

    def features(self) -> Mapping[type, object]:
        return {}


class MaxBackendPlugin:
    backend_id = BackendId("max")

    def create_authentication(self, binding_id: BindingId) -> MaxAuthenticationFlow:
        return MaxAuthenticationFlow()

    def create_session(
        self, binding_id: BindingId, credentials: bytes, event_sink: BackendEventSink
    ) -> MaxBackendSession:
        return MaxBackendSession(binding_id, MaxCredentials.decode(credentials), event_sink)
