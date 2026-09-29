"""XMPP protocol adapters and XML codecs."""

from .addressing import ContactAddressCodec, DirectRouteResolver, InvalidXmppAddress
from .auth_commands import XmppAuthenticationCommands, XmppAuthenticationNotices
from .component import ComponentSettings, SlixmppComponentWire
from .gateway import XmppDirectMessageGateway, XmppMessageDelivery
from .message_codec import XmppMessageCodec, XmppMessageError
from .roster import XmppServerRoster

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
    "XmppServerRoster",
    "XmppAuthenticationCommands",
    "XmppAuthenticationNotices",
]
