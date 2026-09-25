from typing import AsyncIterator, Protocol

from xmpp_transport.domain.models import Media


class MediaReader(Protocol):
    def read(self, media: Media) -> AsyncIterator[bytes]:
        ...

