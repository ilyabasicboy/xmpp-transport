"""Xabber Groups protocol adapter for transport-owned conversations."""

from xml.etree import ElementTree as ET

from xmpp_transport.domain.identifiers import BindingId
from xmpp_transport.domain.models import Conversation
from xmpp_transport.ports.repositories import BindingRepository

from .namespaces import GROUPS_NS, STANZAS_NS
from .roster import IqWire


class XmppGroupManager:
    def __init__(
        self,
        wire: IqWire,
        bindings: BindingRepository,
        component_domain: str,
        server_domain: str,
        control_localpart: str,
        group_localpart_prefix: str,
        provider_label: str,
    ) -> None:
        self._wire = wire
        self._bindings = bindings
        self._component_domain = component_domain
        self._server_domain = server_domain
        self._control_jid = "{}@{}".format(control_localpart, component_domain)
        self._group_localpart_prefix = group_localpart_prefix
        self._provider_label = provider_label

    async def ensure_group(
        self, binding_id: BindingId, conversation: Conversation
    ) -> None:
        owner_jid = await self._bindings.xmpp_account_for_binding(binding_id)
        if owner_jid is None:
            raise LookupError("active XMPP account not found for group synchronization")
        localpart = self._group_localpart(owner_jid, str(conversation.id))
        group_jid = "{}@{}".format(localpart, self._server_domain)
        create = ET.Element("{{{}}}create".format(GROUPS_NS))
        group = ET.SubElement(create, "group", {"privacy": "public"})
        ET.SubElement(group, "localpart").text = localpart
        info = ET.SubElement(group, "info")
        ET.SubElement(info, "name").text = conversation.title
        ET.SubElement(info, "description").text = "{} group {}".format(
            self._provider_label, conversation.id
        )
        settings = ET.SubElement(group, "settings")
        ET.SubElement(settings, "index").text = "none"
        ET.SubElement(settings, "membership").text = "private"
        try:
            await self._request(self._server_domain, create)
        except Exception as exc:
            if not self._is_conflict(exc):
                raise
        update = ET.Element("{{{}}}info".format(GROUPS_NS))
        ET.SubElement(update, "name").text = conversation.title
        await self._request(group_jid, update)

    async def _request(self, recipient: str, payload: ET.Element) -> ET.Element:
        iq = ET.Element(
            "iq",
            {"type": "set", "from": self._control_jid, "to": recipient},
        )
        iq.append(payload)
        return await self._wire.request(iq)

    def _group_localpart(self, owner_jid: str, conversation_id: str) -> str:
        token = "".join(
            character
            for character in conversation_id
            if character.isalnum() or character in "-_"
        ) or "unknown"
        return "{}-{}-{}".format(
            self._group_localpart_prefix,
            owner_jid.encode("utf-8").hex(),
            token,
        )

    @staticmethod
    def _is_conflict(exc: Exception) -> bool:
        iq = getattr(exc, "iq", None)
        xml = getattr(iq, "xml", None)
        return xml is not None and xml.find(
            ".//{{{}}}conflict".format(STANZAS_NS)
        ) is not None
