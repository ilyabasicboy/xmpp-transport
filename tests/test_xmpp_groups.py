import unittest
from typing import Optional, Sequence
from xml.etree import ElementTree as ET

from xmpp_transport.adapters.xmpp.groups import XmppGroupManager
from xmpp_transport.adapters.xmpp.namespaces import GROUPS_NS
from xmpp_transport.domain.identifiers import BackendId, BindingId, RemoteObjectId
from xmpp_transport.domain.models import Conversation, ConversationKind, Participant
from xmpp_transport.ports.repositories import BindingRecord


class FakeBindings:
    async def xmpp_account_for_binding(self, binding_id: BindingId) -> Optional[str]:
        return "user@example.com"

    async def active_bindings(self) -> Sequence[BindingRecord]:
        return ()


class FakeWire:
    def __init__(self) -> None:
        self.requests = []
        self.sent = []

    async def request(self, element: ET.Element, timeout: float = 10.0) -> ET.Element:
        self.requests.append(element)
        return ET.Element("iq", {"type": "result"})

    async def send(self, element: ET.Element) -> None:
        self.sent.append(element)


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
                participants=(Participant(RemoteObjectId("7"), "Alice"),),
                attributes={"owner_remote_id": "100"},
            ),
        )

        self.assertEqual(5, len(wire.requests))
        create_iq, update_iq, transport_invite, owner_invite, member_invite = wire.requests
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
        invite_tag = "{{{}}}invite".format(GROUPS_NS)
        self.assertEqual(
            "bot@max.example.com",
            transport_invite.find(invite_tag + "/jid").text,
        )
        self.assertEqual("false", transport_invite.find(invite_tag + "/send").text)
        self.assertEqual(
            "user@example.com", owner_invite.find(invite_tag + "/jid").text
        )
        self.assertEqual("true", owner_invite.find(invite_tag + "/send").text)
        self.assertEqual(
            "chat-99@max.example.com",
            member_invite.find(invite_tag + "/jid").text,
        )
        self.assertEqual(4, len(wire.sent))
        self.assertEqual(
            ["subscribe", "subscribed", "subscribe", "subscribed"],
            [presence.attrib["type"] for presence in wire.sent],
        )
        self.assertEqual("bot@max.example.com", wire.sent[0].attrib["from"])
        self.assertEqual("chat-99@max.example.com", wire.sent[2].attrib["from"])


if __name__ == "__main__":
    unittest.main()
