"""Provider-neutral XML codec for direct text messages.

This adapter accepts and returns stdlib XML elements. slixmpp stanza objects are
unwrapped only at the future component boundary, keeping them out of application
and domain layers.
"""

from datetime import timezone
from enum import Enum
from typing import TYPE_CHECKING, Optional
from xml.etree import ElementTree as ET

from xmpp_transport.domain.errors import InvalidCommand
from xmpp_transport.domain.identifiers import BindingId, RemoteObjectId
from xmpp_transport.domain.models import IncomingMessage, OutgoingMessage, ReplyReference

from .namespaces import (
    CLIENT_NS,
    DELAY_NS,
    FILES_NS,
    REPLY_NS,
    SID_NS,
    STANZAS_NS,
    XABBER_REFERENCES_NS,
)

if TYPE_CHECKING:
    from .auth_commands import ControlResponse


class XmppMessageError(str, Enum):
    BAD_REQUEST = "bad-request"
    FEATURE_NOT_IMPLEMENTED = "feature-not-implemented"
    SERVICE_UNAVAILABLE = "service-unavailable"


class XmppMessageCodec:
    MAX_BODY_LENGTH = 65536
    MAX_ID_LENGTH = 512

    def parse_outgoing(
        self,
        element: ET.Element,
        binding_id: BindingId,
        conversation_id: RemoteObjectId,
    ) -> OutgoingMessage:
        if _local_name(element.tag) != "message":
            raise InvalidCommand("expected an XMPP message stanza")
        message_type = element.attrib.get("type", "normal")
        if message_type not in ("chat", "normal"):
            raise InvalidCommand("unsupported XMPP message type")

        client_message_id = self._client_message_id(element)
        body = self._body(element)
        reply = self._reply(element)
        return OutgoingMessage(
            client_message_id=client_message_id,
            binding_id=binding_id,
            conversation_id=conversation_id,
            text=body,
            reply_to=reply,
        )

    def serialize_incoming(
        self,
        message: IncomingMessage,
        from_jid: str,
        to_jid: str,
    ) -> ET.Element:
        message_id = _bounded_id(str(message.id), self.MAX_ID_LENGTH)
        element = ET.Element(
            "message",
            {
                "from": _required_address(from_jid, "from_jid"),
                "to": _required_address(to_jid, "to_jid"),
                "type": "chat",
                "id": message_id,
            },
        )
        if message.text and len(message.text) > self.MAX_BODY_LENGTH:
            raise ValueError("incoming text message body is too large")
        if message.text:
            ET.SubElement(element, "body").text = message.text
        ET.SubElement(element, _tag(SID_NS, "origin-id"), {"id": message_id})
        if message.reply_to is not None:
            ET.SubElement(
                element,
                _tag(REPLY_NS, "reply"),
                {"id": _bounded_id(str(message.reply_to.message_id), self.MAX_ID_LENGTH)},
            )
        occurred_at = message.occurred_at
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=timezone.utc)
        stamp = occurred_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        ET.SubElement(element, _tag(DELAY_NS, "delay"), {"stamp": stamp})
        return element

    def error_reply(
        self,
        request: ET.Element,
        condition: XmppMessageError,
        public_text: Optional[str] = None,
    ) -> ET.Element:
        attributes = {"type": "error"}
        if request.attrib.get("to"):
            attributes["from"] = request.attrib["to"]
        if request.attrib.get("from"):
            attributes["to"] = request.attrib["from"]
        request_id = request.attrib.get("id")
        if request_id:
            attributes["id"] = _bounded_id(request_id, self.MAX_ID_LENGTH)
        response = ET.Element("message", attributes)
        error = ET.SubElement(response, "error", {"type": _error_type(condition)})
        ET.SubElement(error, _tag(STANZAS_NS, condition.value))
        if public_text:
            ET.SubElement(error, _tag(STANZAS_NS, "text")).text = public_text
        return response

    def text_reply(self, request: ET.Element, text: str) -> ET.Element:
        if not text or len(text) > self.MAX_BODY_LENGTH:
            raise ValueError("reply text is invalid")
        attributes = {"type": "chat"}
        if request.attrib.get("to"):
            attributes["from"] = request.attrib["to"]
        if request.attrib.get("from"):
            attributes["to"] = request.attrib["from"]
        request_id = request.attrib.get("id")
        if request_id:
            attributes["id"] = _bounded_id(request_id, self.MAX_ID_LENGTH)
        response = ET.Element("message", attributes)
        ET.SubElement(response, "body").text = text
        return response

    def control_reply(self, request: ET.Element, reply: "ControlResponse") -> ET.Element:
        body = reply.body
        media_ranges = []
        for media in reply.media:
            if body and not body.endswith("\n"):
                body += "\n"
            begin = len(body)
            body += media.name
            media_ranges.append((media, begin, len(body)))
        response = self.text_reply(request, body)
        for media, begin, end in media_ranges:
            reference = ET.SubElement(
                response,
                _tag(XABBER_REFERENCES_NS, "reference"),
                {"type": "mutable", "begin": str(begin), "end": str(end)},
            )
            sharing = ET.SubElement(reference, _tag(FILES_NS, "file-sharing"))
            file_element = ET.SubElement(sharing, "file")
            ET.SubElement(file_element, "media-type").text = media.mime_type
            ET.SubElement(file_element, "name").text = media.name
            ET.SubElement(file_element, "size").text = str(media.size)
            sources = ET.SubElement(sharing, "sources")
            ET.SubElement(sources, "uri").text = media.data_uri
        return response

    def _client_message_id(self, element: ET.Element) -> str:
        origin = element.find(_tag(SID_NS, "origin-id"))
        origin_id = origin.attrib.get("id") if origin is not None else None
        candidate = origin_id or element.attrib.get("id")
        if not candidate:
            raise InvalidCommand("message requires id or origin-id")
        return _bounded_id(candidate, self.MAX_ID_LENGTH)

    def _body(self, element: ET.Element) -> str:
        body_element = None
        for child in element:
            if _local_name(child.tag) == "body" and _namespace(child.tag) in ("", CLIENT_NS):
                body_element = child
                break
        body = "" if body_element is None else "".join(body_element.itertext())
        if not body.strip():
            raise InvalidCommand("text message body must not be empty")
        if len(body) > self.MAX_BODY_LENGTH:
            raise InvalidCommand("text message body is too large")
        return body

    def _reply(self, element: ET.Element) -> Optional[ReplyReference]:
        reply = element.find(_tag(REPLY_NS, "reply"))
        if reply is None:
            return None
        reply_id = reply.attrib.get("id")
        if not reply_id:
            raise InvalidCommand("reply element requires an id")
        return ReplyReference(RemoteObjectId(_bounded_id(reply_id, self.MAX_ID_LENGTH)))


def _tag(namespace: str, local_name: str) -> str:
    return "{{{}}}{}".format(namespace, local_name)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _namespace(tag: str) -> str:
    if not tag.startswith("{"):
        return ""
    return tag[1:].partition("}")[0]


def _bounded_id(value: str, maximum: int) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise InvalidCommand("message identifier is invalid")
    return normalized


def _required_address(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("{} must not be empty".format(field))
    return normalized


def _error_type(condition: XmppMessageError) -> str:
    if condition is XmppMessageError.BAD_REQUEST:
        return "modify"
    return "cancel"
