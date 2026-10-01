"""Provider-neutral XML codec for direct text messages.

This adapter accepts and returns stdlib XML elements. slixmpp stanza objects are
unwrapped only at the future component boundary, keeping them out of application
and domain layers.
"""

from datetime import timezone
from enum import Enum
from html import escape
import re
from typing import TYPE_CHECKING, Optional
from xml.etree import ElementTree as ET

from xmpp_transport.domain.errors import InvalidCommand
from xmpp_transport.domain.identifiers import BindingId, RemoteObjectId
from xmpp_transport.domain.models import IncomingMessage, MessageButton, OutgoingMessage, ReplyReference

from .namespaces import (
    CLIENT_NS,
    BOT_UI_NS,
    CHAT_MARKERS_NS,
    COMPONENT_ACCEPT_NS,
    DELAY_NS,
    DATA_FORMS_NS,
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
    BUTTON_COMMAND_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")

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
        body, button_range = self._body_with_buttons(message)
        if body and len(body) > self.MAX_BODY_LENGTH:
            raise ValueError("incoming text message body is too large")
        if body:
            ET.SubElement(element, "body").text = body
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
        if button_range is not None:
            self._append_message_keyboard(element, message.buttons, button_range)
        return element

    def _body_with_buttons(
        self, message: IncomingMessage
    ) -> tuple[str, Optional[tuple[int, int]]]:
        body = message.text or ""
        lines = []
        for row_index, row in enumerate(message.buttons):
            for button_index, button in enumerate(row):
                command = self._message_button_command(button, row_index, button_index)
                action = (
                    "/{}".format(command)
                    if self._message_button_type(button) == "callback"
                    else button.payload.strip() or "/{}".format(command)
                )
                if button.text.strip() and action:
                    lines.append("{} - {}".format(action, button.text.strip()))
        if not lines:
            return body, None
        result = body.rstrip()
        if result:
            result += "\n\n"
        begin = len(escape(result))
        fallback = "Команды кнопок:\n" + "\n".join(lines)
        return result + fallback, (begin, begin + len(escape(fallback)))

    def _append_message_keyboard(
        self,
        element: ET.Element,
        buttons,  # type: ignore[no-untyped-def]
        body_range: tuple[int, int],
    ) -> None:
        reference = ET.SubElement(
            element,
            _tag(XABBER_REFERENCES_NS, "reference"),
            {
                "type": "mutable",
                "begin": str(body_range[0]),
                "end": str(body_range[1]),
            },
        )
        keyboard = ET.SubElement(reference, _tag(BOT_UI_NS, "keyboard"), {"type": "inline"})
        for row_index, row in enumerate(buttons):
            row_element = ET.SubElement(keyboard, _tag(BOT_UI_NS, "row"))
            for button_index, button in enumerate(row):
                command = self._message_button_command(button, row_index, button_index)
                ET.SubElement(
                    row_element,
                    _tag(BOT_UI_NS, "button"),
                    {
                        "id": command,
                        "type": self._message_button_type(button),
                        "label": button.text.strip(),
                        "data": button.payload.strip() or "/{}".format(command),
                    },
                )

    @classmethod
    def _message_button_command(
        cls, button: MessageButton, row_index: int, button_index: int
    ) -> str:
        payload = button.payload.strip()
        if payload.startswith("/"):
            payload = payload[1:].strip()
        if cls.BUTTON_COMMAND_RE.fullmatch(payload):
            return payload
        return "button_{}_{}".format(row_index + 1, button_index + 1)

    @staticmethod
    def _message_button_type(button: MessageButton) -> str:
        kind = button.kind.strip().lower()
        if kind == "url":
            return "url"
        if kind in ("command", "webapp"):
            return kind
        return "callback" if kind or button.callback_id else "command"

    def serialize_group_message(
        self,
        message: IncomingMessage,
        from_jid: str,
        group_jid: str,
        transport_namespace: Optional[str] = None,
        fake_outgoing: bool = False,
    ) -> ET.Element:
        """Render an incoming provider message through the Xabber group protocol."""
        element = self.serialize_incoming(message, from_jid, group_jid)
        message_id = element.attrib["id"]
        ET.SubElement(element, _tag(CHAT_MARKERS_NS, "markable"))
        if fake_outgoing:
            if not transport_namespace:
                raise ValueError("transport namespace is required for fake outgoing")
            # Prevent a synthetic self-message from being sent back to the provider.
            ET.SubElement(element, _tag(transport_namespace, "fake-outgoing"))
        origin = element.find(_tag(SID_NS, "origin-id"))
        if origin is None:
            ET.SubElement(element, _tag(SID_NS, "origin-id"), {"id": message_id})
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
        button_range = None
        if reply.buttons:
            lines = []
            for row_index, row in enumerate(reply.buttons):
                for button_index, button in enumerate(row):
                    action = button.data or "/button_{}_{}".format(
                        row_index + 1, button_index + 1
                    )
                    lines.append("{} - {}".format(action, button.label))
            if lines:
                if body:
                    body = body.rstrip() + "\n\n"
                begin = len(escape(body))
                fallback = "Команды кнопок:\n" + "\n".join(lines)
                body += fallback
                button_range = (begin, begin + len(escape(fallback)))
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
        if button_range is not None:
            reference = ET.SubElement(
                response,
                _tag(XABBER_REFERENCES_NS, "reference"),
                {
                    "type": "mutable",
                    "begin": str(button_range[0]),
                    "end": str(button_range[1]),
                },
            )
            keyboard = ET.SubElement(reference, _tag(BOT_UI_NS, "keyboard"), {"type": "inline"})
            for row_index, row in enumerate(reply.buttons):
                row_element = ET.SubElement(keyboard, _tag(BOT_UI_NS, "row"))
                for button_index, button in enumerate(row):
                    data = button.data
                    command = data[1:].strip() if data.startswith("/") else data
                    if not command or any(character.isspace() for character in command):
                        command = "button_{}_{}".format(row_index + 1, button_index + 1)
                    ET.SubElement(
                        row_element,
                        _tag(BOT_UI_NS, "button"),
                        {
                            "id": command,
                            "type": button.type,
                            "label": button.label,
                            "data": data,
                        },
                    )
        for form in reply.forms:
            form_element = ET.SubElement(response, _tag(DATA_FORMS_NS, "x"), {"type": "form"})
            ET.SubElement(form_element, _tag(DATA_FORMS_NS, "title")).text = form.title
            ET.SubElement(form_element, _tag(DATA_FORMS_NS, "instructions")).text = form.instructions
            for field in form.fields:
                attributes = {"var": field.name, "type": field.type}
                if field.label:
                    attributes["label"] = field.label
                field_element = ET.SubElement(form_element, _tag(DATA_FORMS_NS, "field"), attributes)
                if field.value:
                    ET.SubElement(field_element, _tag(DATA_FORMS_NS, "value")).text = field.value
                if field.required:
                    ET.SubElement(field_element, _tag(DATA_FORMS_NS, "required"))
        return response

    def control_notice(
        self, from_jid: str, to_jid: str, reply: "ControlResponse"
    ) -> ET.Element:
        request = ET.Element("message", {"from": to_jid, "to": from_jid})
        return self.control_reply(request, reply)

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
            if _local_name(child.tag) == "body" and _namespace(child.tag) in (
                "",
                CLIENT_NS,
                COMPONENT_ACCEPT_NS,
            ):
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
