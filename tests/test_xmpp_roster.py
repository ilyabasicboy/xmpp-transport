import unittest
from xml.etree import ElementTree as ET

from xmpp_transport.adapters.xmpp.addressing import ContactAddressCodec
from xmpp_transport.adapters.xmpp.roster import XmppServerRoster
from xmpp_transport.domain.identifiers import BindingId, RemoteObjectId
from xmpp_transport.domain.models import Avatar, Contact


NAMESPACE = "urn:xabber:transport:max:1"


class Wire:
    def __init__(self) -> None:
        self.requests = []
        self.sent = []

    async def request(self, element: ET.Element, timeout: float = 10.0) -> ET.Element:
        self.requests.append(element)
        response = ET.Element("iq", {"type": "result"})
        ET.SubElement(response, "{{{}}}query".format(NAMESPACE), {"status": "updated"})
        return response


    async def send(self, element: ET.Element) -> None:
        self.sent.append(element)

class Bindings:
    async def xmpp_account_for_binding(self, binding_id):  # type: ignore[no-untyped-def]
        return "user@example.com"


class XmppServerRosterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.wire = Wire()
        self.roster = XmppServerRoster(
            self.wire,
            Bindings(),  # type: ignore[arg-type]
            ContactAddressCodec("max.example.com"),
            "max.example.com",
            "example.com",
            NAMESPACE,
            ("MAX",),
        )
        self.contact = Contact(RemoteObjectId("42"), "Alice")

    async def test_add_contact_matches_server_module_contract(self) -> None:
        await self.roster.add_contact(BindingId("binding-1"), self.contact)

        iq = self.wire.requests[0]
        query = iq.find("{{{}}}query".format(NAMESPACE))
        self.assertIsNotNone(query)
        assert query is not None
        fields = {
            child.attrib["name"]: child.text
            for child in query
            if child.tag.rsplit("}", 1)[-1] == "field"
        }
        self.assertEqual("set", iq.attrib["type"])
        self.assertEqual("max.example.com", iq.attrib["from"])
        self.assertEqual("example.com", iq.attrib["to"])
        self.assertEqual("add-roster-contact", query.attrib["op"])
        self.assertEqual("user@example.com", fields["owner_jid"])
        self.assertEqual("chat-42@max.example.com", fields["contact_jid"])
        self.assertEqual("Alice", fields["name"])
        self.assertEqual("MAX", query.findtext("group"))

    async def test_remove_contact_omits_group(self) -> None:
        await self.roster.remove_contact(BindingId("binding-1"), self.contact)

        query = self.wire.requests[0].find("{{{}}}query".format(NAMESPACE))
        assert query is not None
        self.assertEqual("remove-roster-contact", query.attrib["op"])
        self.assertIsNone(query.find("group"))


    async def test_add_contact_publishes_external_avatar_metadata(self) -> None:
        contact = Contact(
            RemoteObjectId("42"),
            "Alice",
            avatar=Avatar(
                "https://max.example/avatar.jpg",
                "avatar-id",
                "image/jpeg",
                12345,
            ),
        )

        await self.roster.add_contact(BindingId("binding-1"), contact)

        message = self.wire.sent[0]
        self.assertEqual("chat-42@max.example.com", message.attrib["from"])
        self.assertEqual("user@example.com", message.attrib["to"])
        self.assertEqual("headline", message.attrib["type"])
        metadata = message.find(".//{urn:xmpp:avatar:metadata}metadata")
        assert metadata is not None
        info = metadata.find("info")
        self.assertIsNotNone(info)
        assert info is not None
        self.assertEqual(
            {
                "bytes": "12345",
                "id": "avatar-id",
                "type": "image/jpeg",
                "url": "https://max.example/avatar.jpg",
            },
            info.attrib,
        )

if __name__ == "__main__":
    unittest.main()
