"""Synchronize provider conversations through the XMPP group feature port."""

from xmpp_transport.domain.events import ConversationChanged
from xmpp_transport.domain.models import ConversationKind
from xmpp_transport.ports.xmpp import XmppGroupManager


class ConversationSync:
    def __init__(self, groups: XmppGroupManager) -> None:
        self._groups = groups

    async def handle(self, event: ConversationChanged) -> None:
        if event.conversation.kind not in (
            ConversationKind.GROUP,
            ConversationKind.CHANNEL,
        ):
            return
        await self._groups.ensure_group(
            event.envelope.binding_id,
            event.conversation,
        )
