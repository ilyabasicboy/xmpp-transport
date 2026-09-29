"""Roster mutations through the privileged Xabber Server helper module."""

from typing import Protocol, Sequence
from xml.etree import ElementTree as ET

from xmpp_transport.domain.identifiers import BindingId
from xmpp_transport.domain.models import Contact
from xmpp_transport.ports.repositories import BindingRepository

from .addressing import ContactAddressCodec


class IqWire(Protocol):
    async def request(self, element: ET.Element, timeout: float = 10.0) -> ET.Element:
        ...


class XmppServerRoster:
    def __init__(
        self,
        wire: IqWire,
        bindings: BindingRepository,
        addresses: ContactAddressCodec,
        component_domain: str,
        server_domain: str,
        namespace: str,
        groups: Sequence[str],
    ) -> None:
        self._wire = wire
        self._bindings = bindings
        self._addresses = addresses
        self._component_domain = component_domain
        self._server_domain = server_domain
        self._namespace = namespace
        self._groups = tuple(groups)

    async def add_contact(self, binding_id: BindingId, contact: Contact) -> None:
        await self._mutate("add-roster-contact", binding_id, contact)

    async def rename_contact(self, binding_id: BindingId, contact: Contact) -> None:
        await self._mutate("rename-roster-contact", binding_id, contact)

    async def remove_contact(self, binding_id: BindingId, contact: Contact) -> None:
        await self._mutate("remove-roster-contact", binding_id, contact)

    async def _mutate(
        self, operation: str, binding_id: BindingId, contact: Contact
    ) -> None:
        owner_jid = await self._bindings.xmpp_account_for_binding(binding_id)
        if owner_jid is None:
            raise LookupError("active XMPP account not found for roster synchronization")
        iq = ET.Element(
            "iq",
            {
                "type": "set",
                "from": self._component_domain,
                "to": self._server_domain,
            },
        )
        query = ET.SubElement(
            iq, "{{{}}}query".format(self._namespace), {"op": operation}
        )
        fields = {
            "owner_jid": owner_jid,
            "contact_jid": self._addresses.contact_jid(contact.id),
            "name": contact.display_name,
        }
        for name, value in fields.items():
            field = ET.SubElement(query, "field", {"name": name})
            field.text = value
        if operation != "remove-roster-contact":
            for group_name in self._groups:
                ET.SubElement(query, "group").text = group_name
        response = await self._wire.request(iq)
        result = response.find("{{{}}}query".format(self._namespace))
        if result is not None and result.attrib.get("status", "ok") != "ok":
            raise ConnectionError("XMPP roster helper did not complete operation")
