from typing import Protocol

from xmpp_transport.domain.identifiers import BindingId
from xmpp_transport.domain.models import Contact, IncomingMessage


class XmppMessageSink(Protocol):
    async def deliver_message(self, message: IncomingMessage) -> None:
        ...


class XmppRoster(Protocol):
    async def add_contact(self, binding_id: BindingId, contact: Contact) -> None:
        ...

    async def rename_contact(self, binding_id: BindingId, contact: Contact) -> None:
        ...

    async def remove_contact(self, binding_id: BindingId, contact: Contact) -> None:
        ...
