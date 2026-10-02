"""Roster mutations through the privileged Xabber Server helper module."""

import hashlib
import hmac
import secrets
import time
from typing import Protocol, Sequence
from xml.etree import ElementTree as ET

from xmpp_transport.domain.identifiers import BindingId
from xmpp_transport.domain.models import Contact
from xmpp_transport.ports.repositories import BindingRepository

from .addressing import ContactAddressCodec
from .namespaces import PUBSUB_AVATAR_METADATA_NS, PUBSUB_EVENT_NS


class IqWire(Protocol):
    async def request(self, element: ET.Element, timeout: float = 10.0) -> ET.Element:
        ...


    async def send(self, element: ET.Element) -> None:
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
        iq_auth_secret: str = "",
    ) -> None:
        self._wire = wire
        self._bindings = bindings
        self._addresses = addresses
        self._component_domain = component_domain
        self._server_domain = server_domain
        self._namespace = namespace
        self._groups = tuple(groups)
        self._iq_auth_secret = iq_auth_secret

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
        if self._iq_auth_secret:
            self._sign_query(query, operation, fields)
        response = await self._wire.request(iq)
        result = response.find("{{{}}}query".format(self._namespace))
        if result is not None and result.attrib.get("status", "ok") not in {
            "ok", "updated", "unchanged", "removed"
        }:
            raise ConnectionError("XMPP roster helper did not complete operation")
        if operation != "remove-roster-contact" and contact.avatar is not None:
            await self._publish_avatar(owner_jid, contact)

    def _sign_query(self, query: ET.Element, operation: str, fields) -> None:  # type: ignore[no-untyped-def]
        timestamp = str(int(time.time()))
        nonce = secrets.token_hex(16)
        values = [
            "v1",
            timestamp,
            nonce,
            self._component_domain,
            self._server_domain,
            operation,
            str(len(fields)),
        ]
        for name in sorted(fields):
            values.extend((name, fields[name]))
        values.append(str(len(self._groups)))
        values.extend(self._groups)
        canonical = b"".join(self._canonical_part(value) for value in values)
        signature = hmac.new(
            self._iq_auth_secret.encode("utf-8"),
            canonical,
            hashlib.sha256,
        ).hexdigest()
        query.attrib.update(
            {
                "auth-timestamp": timestamp,
                "auth-nonce": nonce,
                "auth-signature": signature,
            }
        )

    @staticmethod
    def _canonical_part(value: str) -> bytes:
        encoded = str(value).encode("utf-8")
        return str(len(encoded)).encode("ascii") + b":" + encoded + b","

    async def _publish_avatar(self, owner_jid: str, contact: Contact) -> None:
        avatar = contact.avatar
        if avatar is None:
            return
        avatar_id = avatar.version or avatar.reference
        message = ET.Element(
            "message",
            {
                "from": self._addresses.contact_jid(contact.id),
                "to": owner_jid,
                "type": "headline",
            },
        )
        event = ET.SubElement(message, "{{{}}}event".format(PUBSUB_EVENT_NS))
        items = ET.SubElement(
            event,
            "{{{}}}items".format(PUBSUB_EVENT_NS),
            {"node": PUBSUB_AVATAR_METADATA_NS},
        )
        item = ET.SubElement(
            items, "{{{}}}item".format(PUBSUB_EVENT_NS), {"id": avatar_id}
        )
        metadata = ET.SubElement(
            item, "{{{}}}metadata".format(PUBSUB_AVATAR_METADATA_NS)
        )
        ET.SubElement(
            metadata,
            "info",
            {
                "bytes": str(max(avatar.size, 0)),
                "id": avatar_id,
                "type": avatar.content_type,
                "url": avatar.reference,
            },
        )
        await self._wire.send(message)
