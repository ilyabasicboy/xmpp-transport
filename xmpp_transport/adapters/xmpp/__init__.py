"""XMPP protocol adapters and XML codecs."""

from .addressing import ContactAddressCodec, DirectRouteResolver, InvalidXmppAddress
from .auth_commands import XmppAuthenticationCommands
from .component import ComponentSettings, SlixmppComponentWire
from .gateway import XmppDirectMessageGateway, XmppMessageDelivery
from .message_codec import XmppMessageCodec, XmppMessageError

__all__ = [
    "ContactAddressCodec",
    "ComponentSettings",
    "DirectRouteResolver",
    "InvalidXmppAddress",
    "SlixmppComponentWire",
    "XmppDirectMessageGateway",
    "XmppMessageDelivery",
    "XmppMessageCodec",
    "XmppMessageError",
    "XmppAuthenticationCommands",
]
