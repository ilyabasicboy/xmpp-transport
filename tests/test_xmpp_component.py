import asyncio
import unittest
from xml.etree import ElementTree as ET

from xmpp_transport.adapters.xmpp.component import ComponentSettings, SlixmppComponentWire
from xmpp_transport.adapters.xmpp.namespaces import FILES_NS, XABBER_REFERENCES_NS


class FakeStanza:
    def __init__(self, attributes, body):  # type: ignore[no-untyped-def]
        self.xml = ET.Element("{jabber:component:accept}message", attributes)
        if body is not None:
            ET.SubElement(self.xml, "{jabber:component:accept}body").text = body
        self.sent = False

    def send(self) -> None:
        self.sent = True


class FakeClient:
    def __init__(self) -> None:
        self.message = None
        self.raw = []

    def make_message(self, *, mfrom, mto, mtype, mbody):  # type: ignore[no-untyped-def]
        attributes = {"from": mfrom, "to": mto, "type": mtype}
        self.message = FakeStanza(attributes, mbody)
        return self.message

    def send_raw(self, value: str) -> None:
        self.raw.append(value)


class SlixmppComponentWireTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_message_through_slixmpp_stanza_api(self) -> None:
        wire = SlixmppComponentWire(ComponentSettings("max.example", "secret"))
        client = FakeClient()
        wire._client = client
        wire._ready = asyncio.Event()
        wire._ready.set()
        element = ET.Element(
            "message",
            {"from": "chat-1@max.example", "to": "group@example", "type": "chat", "id": "1"},
        )
        ET.SubElement(element, "body").text = "https://cdn.example/image.jpg"
        reference = ET.SubElement(
            element,
            "{{{}}}reference".format(XABBER_REFERENCES_NS),
            {"type": "mutable", "begin": "0", "end": "29"},
        )
        ET.SubElement(reference, "{{{}}}file-sharing".format(FILES_NS))

        await wire.send(element)

        self.assertIsNotNone(client.message)
        assert client.message is not None
        self.assertTrue(client.message.sent)
        self.assertEqual([], client.raw)
        self.assertEqual("{jabber:component:accept}message", client.message.xml.tag)
        self.assertEqual("1", client.message.xml.attrib["id"])
        self.assertEqual(
            "https://cdn.example/image.jpg",
            client.message.xml.findtext("{jabber:component:accept}body"),
        )
        self.assertIsNotNone(
            client.message.xml.find("{{{}}}reference".format(XABBER_REFERENCES_NS))
        )

    async def test_keeps_raw_path_for_non_message_stanza(self) -> None:
        wire = SlixmppComponentWire(ComponentSettings("max.example", "secret"))
        client = FakeClient()
        wire._client = client
        wire._ready = asyncio.Event()
        wire._ready.set()

        await wire.send(ET.Element("presence", {"to": "user@example"}))

        self.assertEqual(['<presence to="user@example" />'], client.raw)
        self.assertIsNone(client.message)


if __name__ == "__main__":
    unittest.main()
