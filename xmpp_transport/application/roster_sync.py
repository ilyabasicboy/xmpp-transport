"""Idempotent synchronization of remote contacts into an XMPP roster."""

import hashlib
import json
from enum import Enum

from xmpp_transport.domain.events import ContactChanged, ContactChangeKind
from xmpp_transport.domain.models import Contact
from xmpp_transport.ports.repositories import RosterSyncRepository
from xmpp_transport.ports.xmpp import XmppRoster


class RosterSyncResult(str, Enum):
    ADDED = "added"
    RENAMED = "renamed"
    REMOVED = "removed"
    UNCHANGED = "unchanged"


def contact_signature(contact: Contact) -> str:
    """Hash only fields that affect the roster item managed by this service."""
    canonical = json.dumps(
        {
            "avatar": (
                {
                    "content_type": contact.avatar.content_type,
                    "reference": contact.avatar.reference,
                    "size": contact.avatar.size,
                    "version": contact.avatar.version,
                }
                if contact.avatar is not None
                else None
            ),
            "display_name": contact.display_name,
            "remote_id": str(contact.id),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class RosterSync:
    def __init__(self, repository: RosterSyncRepository, xmpp: XmppRoster) -> None:
        self._repository = repository
        self._xmpp = xmpp

    async def handle(self, event: ContactChanged) -> RosterSyncResult:
        binding_id = event.envelope.binding_id
        contact = event.contact
        current = await self._repository.signature(binding_id, contact.id)

        if event.change is ContactChangeKind.REMOVED:
            if current is None:
                return RosterSyncResult.UNCHANGED
            await self._xmpp.remove_contact(binding_id, contact)
            await self._repository.delete_signature(binding_id, contact.id)
            return RosterSyncResult.REMOVED

        desired = contact_signature(contact)
        if not event.force and current == desired:
            return RosterSyncResult.UNCHANGED
        if current is None or event.force:
            await self._xmpp.add_contact(binding_id, contact)
            result = RosterSyncResult.ADDED
        else:
            await self._xmpp.rename_contact(binding_id, contact)
            result = RosterSyncResult.RENAMED
        await self._repository.save_signature(binding_id, contact.id, desired)
        return result
