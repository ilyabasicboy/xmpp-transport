"""Safe reversible mapping between remote object IDs and component JIDs."""

import base64
import binascii
import re
from dataclasses import dataclass
from typing import Optional

from xmpp_transport.domain.identifiers import BackendId, BindingId, RemoteObjectId
from xmpp_transport.ports.repositories import BindingRepository


_LEGACY_NUMERIC_ID = re.compile(r"^-?[0-9]+$")
_DOMAIN_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class InvalidXmppAddress(ValueError):
    pass


@dataclass(frozen=True)
class DirectRoute:
    binding_id: BindingId
    conversation_id: RemoteObjectId
    owner_bare_jid: str


class ContactAddressCodec:
    """Preserve legacy ``chat-<number>`` JIDs and encode all other opaque IDs."""

    _CONTACT_PREFIX = "chat-"
    _ENCODED_PREFIX = "x-"
    _MAX_LOCALPART_BYTES = 1023

    def __init__(self, component_domain: str) -> None:
        self.component_domain = _normalize_domain(component_domain)

    def contact_jid(self, remote_id: RemoteObjectId) -> str:
        value = str(remote_id)
        if _LEGACY_NUMERIC_ID.fullmatch(value):
            local_id = value
        else:
            encoded = base64.b32encode(value.encode("utf-8")).decode("ascii")
            local_id = self._ENCODED_PREFIX + encoded.rstrip("=").lower()
        localpart = self._CONTACT_PREFIX + local_id
        if len(localpart.encode("utf-8")) > self._MAX_LOCALPART_BYTES:
            raise InvalidXmppAddress("remote identifier is too long for an XMPP localpart")
        return "{}@{}".format(localpart, self.component_domain)

    def remote_id(self, jid: str) -> RemoteObjectId:
        localpart, domain = _split_bare_jid(jid)
        if domain != self.component_domain:
            raise InvalidXmppAddress("JID does not belong to the configured component")
        if not localpart.startswith(self._CONTACT_PREFIX):
            raise InvalidXmppAddress("JID is not a transport contact")
        value = localpart[len(self._CONTACT_PREFIX) :]
        if _LEGACY_NUMERIC_ID.fullmatch(value):
            return RemoteObjectId(value)
        if not value.startswith(self._ENCODED_PREFIX):
            raise InvalidXmppAddress("transport contact localpart has an unknown encoding")
        payload = value[len(self._ENCODED_PREFIX) :]
        if not payload:
            raise InvalidXmppAddress("transport contact identifier is empty")
        padding = "=" * ((8 - len(payload) % 8) % 8)
        try:
            decoded = base64.b32decode((payload + padding).upper(), casefold=False)
            text = decoded.decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise InvalidXmppAddress("transport contact identifier is malformed") from exc
        canonical = self.contact_jid(RemoteObjectId(text))
        if canonical != "{}@{}".format(localpart, domain):
            raise InvalidXmppAddress("transport contact identifier is not canonical")
        return RemoteObjectId(text)


class DirectRouteResolver:
    def __init__(
        self,
        backend_id: BackendId,
        addresses: ContactAddressCodec,
        bindings: BindingRepository,
    ) -> None:
        self._backend_id = backend_id
        self._addresses = addresses
        self._bindings = bindings

    async def resolve(self, from_jid: str, to_jid: str) -> Optional[DirectRoute]:
        owner_bare_jid = bare_jid(from_jid)
        conversation_id = self._addresses.remote_id(to_jid)
        binding = await self._bindings.binding_for_xmpp_account(
            owner_bare_jid, self._backend_id
        )
        if binding is None:
            return None
        return DirectRoute(binding.binding_id, conversation_id, owner_bare_jid)

    async def resolve_group(
        self, owner_jid: str, conversation_id: RemoteObjectId
    ) -> Optional[DirectRoute]:
        owner_bare_jid = bare_jid(owner_jid)
        binding = await self._bindings.binding_for_xmpp_account(
            owner_bare_jid, self._backend_id
        )
        if binding is None:
            return None
        return DirectRoute(binding.binding_id, conversation_id, owner_bare_jid)


def bare_jid(jid: str) -> str:
    bare = jid.split("/", 1)[0]
    localpart, domain = _split_bare_jid(bare)
    return "{}@{}".format(localpart, domain)


def _split_bare_jid(jid: str) -> tuple:
    value = jid.strip()
    if "/" in value:
        raise InvalidXmppAddress("a bare component contact JID is required")
    if value.count("@") != 1:
        raise InvalidXmppAddress("JID must contain one localpart and domain")
    localpart, domain = value.split("@", 1)
    if not localpart or not domain or any(character.isspace() for character in value):
        raise InvalidXmppAddress("JID is malformed")
    return localpart, _normalize_domain(domain)


def _normalize_domain(domain: str) -> str:
    value = domain.strip().rstrip(".")
    if not value or "@" in value or "/" in value:
        raise InvalidXmppAddress("component domain is malformed")
    try:
        normalized = value.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise InvalidXmppAddress("component domain is malformed") from exc
    labels = normalized.split(".")
    if len(normalized) > 253 or any(
        _DOMAIN_LABEL.fullmatch(label) is None for label in labels
    ):
        raise InvalidXmppAddress("component domain is malformed")
    return normalized
