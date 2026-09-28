import unittest
from typing import Optional, Sequence

from xmpp_transport.adapters.xmpp.addressing import (
    ContactAddressCodec,
    DirectRouteResolver,
    InvalidXmppAddress,
    bare_jid,
)
from xmpp_transport.domain.identifiers import BackendId, BindingId, RemoteObjectId
from xmpp_transport.ports.repositories import BindingRecord


class FakeBindings:
    def __init__(self, record: Optional[BindingRecord]) -> None:
        self.record = record
        self.lookup = None

    async def binding_for_xmpp_account(self, bare: str, backend_id: BackendId):  # type: ignore[no-untyped-def]
        self.lookup = (bare, backend_id)
        return self.record

    async def active_bindings(self) -> Sequence[BindingRecord]:
        return ()

    async def encrypted_credentials(self, binding_id: BindingId) -> Optional[bytes]:
        return None

    async def save_encrypted_credentials(
        self, binding_id: BindingId, backend_id: BackendId, credentials: bytes
    ) -> None:
        return None


class ContactAddressCodecTests(unittest.TestCase):
    def setUp(self) -> None:
        self.codec = ContactAddressCodec("Telegram.Example.COM.")

    def test_preserves_legacy_numeric_contact_jid(self) -> None:
        jid = self.codec.contact_jid(RemoteObjectId("123456"))
        self.assertEqual("chat-123456@telegram.example.com", jid)
        self.assertEqual(RemoteObjectId("123456"), self.codec.remote_id(jid))

    def test_preserves_legacy_negative_numeric_id(self) -> None:
        jid = self.codec.contact_jid(RemoteObjectId("-100500"))
        self.assertEqual("chat--100500@telegram.example.com", jid)
        self.assertEqual(RemoteObjectId("-100500"), self.codec.remote_id(jid))

    def test_arbitrary_opaque_id_round_trips(self) -> None:
        remote_id = RemoteObjectId("user/Алиса@example:42")
        jid = self.codec.contact_jid(remote_id)
        self.assertTrue(jid.startswith("chat-x-"))
        self.assertNotIn("Алиса", jid)
        self.assertEqual(remote_id, self.codec.remote_id(jid))

    def test_wrong_component_domain_is_rejected(self) -> None:
        with self.assertRaises(InvalidXmppAddress):
            self.codec.remote_id("chat-123@other.example.com")

    def test_noncanonical_or_unknown_encoding_is_rejected(self) -> None:
        with self.assertRaises(InvalidXmppAddress):
            self.codec.remote_id("chat-user@example.com")

    def test_contact_resource_is_rejected(self) -> None:
        with self.assertRaises(InvalidXmppAddress):
            self.codec.remote_id("chat-123@telegram.example.com/resource")

    def test_malformed_component_domain_is_rejected(self) -> None:
        with self.assertRaises(InvalidXmppAddress):
            ContactAddressCodec("bad:domain")


class DirectRouteResolverTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolves_owner_resource_to_active_binding(self) -> None:
        backend_id = BackendId("telegram")
        record = BindingRecord(BindingId("binding-1"), backend_id)
        bindings = FakeBindings(record)
        addresses = ContactAddressCodec("telegram.example.com")
        resolver = DirectRouteResolver(backend_id, addresses, bindings)
        route = await resolver.resolve(
            "user@Example.COM/phone", "chat-123@telegram.example.com"
        )
        self.assertEqual(BindingId("binding-1"), route.binding_id)
        self.assertEqual(RemoteObjectId("123"), route.conversation_id)
        self.assertEqual("user@example.com", route.owner_bare_jid)
        self.assertEqual(("user@example.com", backend_id), bindings.lookup)

    async def test_missing_binding_returns_none(self) -> None:
        backend_id = BackendId("telegram")
        resolver = DirectRouteResolver(
            backend_id,
            ContactAddressCodec("telegram.example.com"),
            FakeBindings(None),
        )
        route = await resolver.resolve(
            "user@example.com/device", "chat-123@telegram.example.com"
        )
        self.assertIsNone(route)

    def test_bare_jid_requires_valid_account_address(self) -> None:
        self.assertEqual("user@example.com", bare_jid("user@Example.COM/device"))
        with self.assertRaises(InvalidXmppAddress):
            bare_jid("not-a-jid")


if __name__ == "__main__":
    unittest.main()
