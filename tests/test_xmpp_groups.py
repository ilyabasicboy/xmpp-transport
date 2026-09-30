import unittest
from typing import Optional, Sequence
from xml.etree import ElementTree as ET

from xmpp_transport.adapters.xmpp.groups import XmppGroupManager
from xmpp_transport.adapters.xmpp.namespaces import GROUPS_NS
from xmpp_transport.domain.identifiers import BackendId, BindingId, RemoteObjectId
from xmpp_transport.domain.models import Conversation, ConversationKind
from xmpp_transport.ports.repositories import BindingRecord


class FakeBindings:
    async def xmpp_account_for_binding(self, binding_id: BindingId) -> Optional[str]:
        return "user@example.com"

    async def active_bindings(self) -> Sequence[BindingRecord]:
        return ()


class FakeWire:
    def __init__(self) -> None:
        self.requests = []

    async def request(self, element: ET.Element, timeout: float = 10.0) -> ET.Element:
        self.requests.append(element)
        return ET.Element("iq", {"type": "result"})


class XmppGroupManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_creates_and_updates_transport_owned_group(self) -> None:
        wire = FakeWire()
        manager = XmppGroupManager(
            wire,  # type: ignore[arg-type]
            FakeBindings(),  # type: ignore[arg-type]
            "max.example.com",
            "example.com",
            "bot",
            "maxg",
            "MAX",
        )

        await manager.ensure_group(
            BindingId("binding-1"),
            Conversation(
                RemoteObjectId("-888"),
                ConversationKind.GROUP,
                "MAX Group",
            ),
        )

        self.assertEqual(2, len(wire.requests))
        create_iq, update_iq = wire.requests
        localpart = "maxg-75736572406578616d706c652e636f6d--888"
        self.assertEqual("bot@max.example.com", create_iq.attrib["from"])
        self.assertEqual("example.com", create_iq.attrib["to"])
        create = create_iq.find("{{{}}}create".format(GROUPS_NS))
        self.assertIsNotNone(create)
        assert create is not None
        self.assertEqual(localpart, create.find("group/localpart").text)
        self.assertEqual("MAX Group", create.find("group/info/name").text)
        self.assertEqual("public", create.find("group").attrib["privacy"])
        self.assertEqual("private", create.find("group/settings/membership").text)
        self.assertEqual("bot@max.example.com", update_iq.attrib["from"])
        self.assertEqual("{}@example.com".format(localpart), update_iq.attrib["to"])
        self.assertEqual(
            "MAX Group",
            update_iq.find("{{{}}}info/name".format(GROUPS_NS)).text,
        )


if __name__ == "__main__":
    unittest.main()
