import unittest
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from xmpp_transport.application.roster_sync import RosterSync, RosterSyncResult, contact_signature
from xmpp_transport.domain.events import ContactChanged, ContactChangeKind, EventEnvelope
from xmpp_transport.domain.identifiers import BackendId, BindingId, EventId, RemoteObjectId
from xmpp_transport.domain.models import Avatar, Contact


class FakeRosterRepository:
    def __init__(self) -> None:
        self.signatures: Dict[Tuple[BindingId, RemoteObjectId], str] = {}

    async def signature(
        self, binding_id: BindingId, remote_contact_id: RemoteObjectId
    ) -> Optional[str]:
        return self.signatures.get((binding_id, remote_contact_id))

    async def save_signature(
        self, binding_id: BindingId, remote_contact_id: RemoteObjectId, signature: str
    ) -> None:
        self.signatures[(binding_id, remote_contact_id)] = signature

    async def delete_signature(
        self, binding_id: BindingId, remote_contact_id: RemoteObjectId
    ) -> None:
        self.signatures.pop((binding_id, remote_contact_id), None)


class FakeXmppRoster:
    def __init__(self) -> None:
        self.operations = []
        self.fail = False

    async def add_contact(self, binding_id: BindingId, contact: Contact) -> None:
        self._record("add", binding_id, contact)

    async def rename_contact(self, binding_id: BindingId, contact: Contact) -> None:
        self._record("rename", binding_id, contact)

    async def remove_contact(self, binding_id: BindingId, contact: Contact) -> None:
        self._record("remove", binding_id, contact)

    def _record(self, operation: str, binding_id: BindingId, contact: Contact) -> None:
        if self.fail:
            raise ConnectionError("roster helper unavailable")
        self.operations.append((operation, binding_id, contact.id))


def contact_event(
    contact: Contact, change: ContactChangeKind = ContactChangeKind.UPSERT
) -> ContactChanged:
    binding_id = BindingId("binding-1")
    return ContactChanged(
        EventEnvelope(
            event_id=EventId("event-1"),
            event_type=ContactChanged.EVENT_TYPE,
            schema_version=1,
            backend_id=BackendId("fake"),
            binding_id=binding_id,
            occurred_at=datetime.now(timezone.utc),
        ),
        contact,
        change,
    )


class RosterSyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repository = FakeRosterRepository()
        self.xmpp = FakeXmppRoster()
        self.sync = RosterSync(self.repository, self.xmpp)
        self.contact = Contact(RemoteObjectId("contact-1"), "Alice")

    async def test_new_contact_is_added_and_signed(self) -> None:
        result = await self.sync.handle(contact_event(self.contact))
        self.assertEqual(RosterSyncResult.ADDED, result)
        self.assertEqual("add", self.xmpp.operations[0][0])
        self.assertEqual(
            contact_signature(self.contact),
            self.repository.signatures[(BindingId("binding-1"), self.contact.id)],
        )

    async def test_unchanged_contact_is_skipped(self) -> None:
        await self.sync.handle(contact_event(self.contact))
        result = await self.sync.handle(contact_event(self.contact))
        self.assertEqual(RosterSyncResult.UNCHANGED, result)
        self.assertEqual(1, len(self.xmpp.operations))

    async def test_forced_snapshot_contact_is_added_even_with_saved_signature(self) -> None:
        await self.sync.handle(contact_event(self.contact))
        event = contact_event(self.contact)
        forced = ContactChanged(
            envelope=event.envelope,
            contact=event.contact,
            change=event.change,
            force=True,
        )

        result = await self.sync.handle(forced)

        self.assertEqual(RosterSyncResult.ADDED, result)
        self.assertEqual(["add", "add"], [item[0] for item in self.xmpp.operations])

    async def test_changed_name_uses_explicit_rename(self) -> None:
        await self.sync.handle(contact_event(self.contact))
        changed = Contact(self.contact.id, "Alice Cooper")
        result = await self.sync.handle(contact_event(changed))
        self.assertEqual(RosterSyncResult.RENAMED, result)
        self.assertEqual("rename", self.xmpp.operations[-1][0])

    async def test_avatar_change_does_not_rewrite_roster_item(self) -> None:
        await self.sync.handle(contact_event(self.contact))
        changed = Contact(self.contact.id, "Alice", avatar=Avatar("avatar-2"))
        result = await self.sync.handle(contact_event(changed))
        self.assertEqual(RosterSyncResult.UNCHANGED, result)

    async def test_removed_contact_deletes_item_and_signature(self) -> None:
        await self.sync.handle(contact_event(self.contact))
        result = await self.sync.handle(
            contact_event(self.contact, ContactChangeKind.REMOVED)
        )
        self.assertEqual(RosterSyncResult.REMOVED, result)
        self.assertEqual("remove", self.xmpp.operations[-1][0])
        self.assertEqual({}, self.repository.signatures)

    async def test_unknown_removed_contact_is_idempotent(self) -> None:
        result = await self.sync.handle(
            contact_event(self.contact, ContactChangeKind.REMOVED)
        )
        self.assertEqual(RosterSyncResult.UNCHANGED, result)
        self.assertEqual([], self.xmpp.operations)

    async def test_failed_xmpp_operation_does_not_advance_signature(self) -> None:
        self.xmpp.fail = True
        with self.assertRaises(ConnectionError):
            await self.sync.handle(contact_event(self.contact))
        self.assertEqual({}, self.repository.signatures)


class ContactSignatureTests(unittest.TestCase):
    def test_signature_is_deterministic_and_does_not_contain_contact_data(self) -> None:
        contact = Contact(RemoteObjectId("opaque-id"), "Алиса")
        signature = contact_signature(contact)
        self.assertEqual(signature, contact_signature(contact))
        self.assertEqual(64, len(signature))
        self.assertNotIn("Алиса", signature)


if __name__ == "__main__":
    unittest.main()
