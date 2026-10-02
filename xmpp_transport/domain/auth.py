"""Public authentication states; provider-specific data stays in adapters."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class AuthState(str, Enum):
    IDLE = "idle"
    WAITING_QR = "waiting_qr"
    WAITING_PASSWORD = "waiting_password"
    WAITING_CONFIRMATION = "waiting_confirmation"
    CONNECTED = "connected"
    EXPIRED = "expired"
    FAILED = "failed"


class AuthResponseKind(str, Enum):
    PASSWORD = "password"
    CONFIRMATION = "confirmation"


@dataclass(frozen=True)
class AuthChallenge:
    state: AuthState
    expires_at: Optional[datetime] = None
    public_url: Optional[str] = None
    message: Optional[str] = None

    def __post_init__(self) -> None:
        if self.public_url is not None and not self.public_url.startswith(("https://", "tg://")):
            raise ValueError("authentication public_url must use HTTPS or Telegram login scheme")


@dataclass(frozen=True)
class AuthResponse:
    kind: AuthResponseKind
    secret: str

    def __post_init__(self) -> None:
        if not self.secret:
            raise ValueError("authentication response must not be empty")

    def __repr__(self) -> str:
        # Avoid accidental secret disclosure in logs and tracebacks.
        return "AuthResponse(kind={!r}, secret=<redacted>)".format(self.kind)

