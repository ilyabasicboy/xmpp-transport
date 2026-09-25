from typing import Optional, Protocol

from xmpp_transport.domain.identifiers import BackendId, BindingId, RemoteObjectId


class BindingRepository(Protocol):
    async def encrypted_credentials(self, binding_id: BindingId) -> Optional[bytes]:
        ...

    async def save_encrypted_credentials(
        self, binding_id: BindingId, backend_id: BackendId, credentials: bytes
    ) -> None:
        ...


class MessageMappingRepository(Protocol):
    async def remote_id_for_client_message(
        self, binding_id: BindingId, client_message_id: str
    ) -> Optional[RemoteObjectId]:
        ...

    async def save_mapping(
        self,
        binding_id: BindingId,
        client_message_id: str,
        remote_message_id: RemoteObjectId,
    ) -> None:
        ...

