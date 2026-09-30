import unittest
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

from xmpp_transport.adapters.xmpp.message_codec import XmppMessageCodec, XmppMessageError
from xmpp_transport.adapters.xmpp.namespaces import DELAY_NS, REPLY_NS, SID_NS, STANZAS_NS
from xmpp_transport.domain.errors import InvalidCommand
from xmpp_transport.domain.identifiers import BindingId, RemoteObjectId
from xmpp_transport.domain.models import IncomingMessage, ReplyReference


def tag(namespace: str, name: str) -> str:
    return "{{{}}}{}".format(namespace, name)


class ParseOutgoingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.codec = XmppMessageCodec()
        self.binding_id = BindingId("binding-1")
        self.conversation_id = RemoteObjectId("opaque-conversation")

    def test_parses_direct_text_and_prefers_origin_id(self) -> None:
        stanza = ET.fromstring(
            """
            <message xmlns='jabber:client' type='chat' id='server-copy-id'>
              <body>Hello</body>
              <origin-id xmlns='urn:xmpp:sid:0' id='client-stable-id'/>
            </message>
            """
        )
        message = self.codec.parse_outgoing(
            stanza, self.binding_id, self.conversation_id
        )
        self.assertEqual("client-stable-id", message.client_message_id)
        self.assertEqual("Hello", message.text)

    def test_parses_body_from_component_accept_stanza(self) -> None:
        stanza = ET.fromstring(
            """
            <message xmlns='jabber:component:accept'
                     from='user@example.com/device'
                     to='chat-123@max.example.com'
                     type='chat' id='client-component-1'>
              <body>Hello from Xabber</body>
            </message>
            """
        )
        message = self.codec.parse_outgoing(
            stanza, self.binding_id, self.conversation_id
        )
        self.assertEqual("client-component-1", message.client_message_id)
        self.assertEqual("Hello from Xabber", message.text)

    def test_parses_standard_reply_reference(self) -> None:
        stanza = ET.fromstring(
            """
            <message type='chat' id='message-1'>
              <body>Reply</body>
              <reply xmlns='urn:xmpp:reply:0' id='remote-message-1'/>
            </message>
            """
        )
        message = self.codec.parse_outgoing(
            stanza, self.binding_id, self.conversation_id
        )
        self.assertEqual(
            ReplyReference(RemoteObjectId("remote-message-1")), message.reply_to
        )

    def test_rejects_missing_stable_identifier(self) -> None:
        stanza = ET.fromstring("<message type='chat'><body>Hello</body></message>")
        with self.assertRaises(InvalidCommand):
            self.codec.parse_outgoing(stanza, self.binding_id, self.conversation_id)

    def test_rejects_empty_body(self) -> None:
        stanza = ET.fromstring("<message type='chat' id='1'><body> </body></message>")
        with self.assertRaises(InvalidCommand):
            self.codec.parse_outgoing(stanza, self.binding_id, self.conversation_id)

    def test_rejects_groupchat_on_direct_path(self) -> None:
        stanza = ET.fromstring(
            "<message type='groupchat' id='1'><body>Hello</body></message>"
        )
        with self.assertRaises(InvalidCommand):
            self.codec.parse_outgoing(stanza, self.binding_id, self.conversation_id)

    def test_rejects_oversized_body(self) -> None:
        stanza = ET.Element("message", {"type": "chat", "id": "1"})
        ET.SubElement(stanza, "body").text = "x" * (self.codec.MAX_BODY_LENGTH + 1)
        with self.assertRaises(InvalidCommand):
            self.codec.parse_outgoing(stanza, self.binding_id, self.conversation_id)


class SerializeIncomingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.codec = XmppMessageCodec()

    def test_serializes_body_origin_reply_and_utc_delay(self) -> None:
        message = IncomingMessage(
            id=RemoteObjectId("remote-message-1"),
            binding_id=BindingId("binding-1"),
            conversation_id=RemoteObjectId("conversation-1"),
            sender_id=RemoteObjectId("sender-1"),
            occurred_at=datetime(2026, 9, 28, 8, 30, tzinfo=timezone.utc),
            text="Hello",
            reply_to=ReplyReference(RemoteObjectId("remote-parent-1")),
        )
        stanza = self.codec.serialize_incoming(
            message, "contact@transport.example", "user@example.com"
        )
        self.assertEqual("chat", stanza.attrib["type"])
        self.assertEqual("Hello", stanza.find("body").text)
        self.assertEqual(
            "remote-message-1", stanza.find(tag(SID_NS, "origin-id")).attrib["id"]
        )
        self.assertEqual(
            "remote-parent-1", stanza.find(tag(REPLY_NS, "reply")).attrib["id"]
        )
        self.assertEqual(
            "2026-09-28T08:30:00Z", stanza.find(tag(DELAY_NS, "delay")).attrib["stamp"]
        )

    def test_rejects_missing_addresses(self) -> None:
        message = IncomingMessage(
            id=RemoteObjectId("remote-message-1"),
            binding_id=BindingId("binding-1"),
            conversation_id=RemoteObjectId("conversation-1"),
            sender_id=RemoteObjectId("sender-1"),
            occurred_at=datetime.now(timezone.utc),
            text="Hello",
        )
        with self.assertRaises(ValueError):
            self.codec.serialize_incoming(message, "", "user@example.com")

    def test_builds_client_safe_error_without_reflecting_body(self) -> None:
        request = ET.fromstring(
            """
            <message from='user@example.com/device' to='contact@transport.example'
                     id='message-1' type='chat'>
              <body>private message body</body>
            </message>
            """
        )
        response = self.codec.error_reply(
            request, XmppMessageError.SERVICE_UNAVAILABLE, "Transport unavailable"
        )
        xml = ET.tostring(response, encoding="unicode")
        self.assertEqual("error", response.attrib["type"])
        self.assertEqual("contact@transport.example", response.attrib["from"])
        self.assertEqual("user@example.com/device", response.attrib["to"])
        self.assertIsNotNone(
            response.find("error/" + tag(STANZAS_NS, "service-unavailable"))
        )
        self.assertNotIn("private message body", xml)


if __name__ == "__main__":
    unittest.main()
