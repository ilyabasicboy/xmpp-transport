"""Strong identifiers used across application boundaries."""

from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class _Identifier:
    value: str

    def __post_init__(self) -> None:
        if not self.value or not self.value.strip():
            raise ValueError("identifier must not be empty")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, order=True)
class BackendId(_Identifier):
    """Stable backend implementation identifier, for example ``telegram``."""


@dataclass(frozen=True, order=True)
class BindingId(_Identifier):
    """A connection between one XMPP account and one remote account."""


@dataclass(frozen=True, order=True)
class RemoteObjectId(_Identifier):
    """Opaque provider-owned identifier; it must never be parsed by the core."""


@dataclass(frozen=True, order=True)
class EventId(_Identifier):
    """Globally unique event identifier."""


@dataclass(frozen=True, order=True)
class CorrelationId(_Identifier):
    """Identifier joining commands, events, and delivery results."""

