"""Commands accepted by the provider-neutral application layer."""

from dataclasses import dataclass

from .identifiers import BindingId, CorrelationId
from .models import OutgoingMessage


@dataclass(frozen=True)
class StartBinding:
    binding_id: BindingId
    correlation_id: CorrelationId


@dataclass(frozen=True)
class StopBinding:
    binding_id: BindingId
    correlation_id: CorrelationId


@dataclass(frozen=True)
class SendMessage:
    message: OutgoingMessage
    correlation_id: CorrelationId

