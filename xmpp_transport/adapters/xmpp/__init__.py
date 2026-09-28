"""XMPP protocol adapters and XML codecs."""

from .addressing import ContactAddressCodec, DirectRouteResolver, InvalidXmppAddress
from .message_codec import XmppMessageCodec, XmppMessageError

__all__ = [
    "ContactAddressCodec",
    "DirectRouteResolver",
    "InvalidXmppAddress",
    "XmppMessageCodec",
    "XmppMessageError",
]
