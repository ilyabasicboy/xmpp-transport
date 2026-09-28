"""PostgreSQL implementations of shared repository ports."""

from typing import Any, Optional, Protocol, Sequence

from xmpp_transport.domain.errors import DuplicateOperation
from xmpp_transport.domain.identifiers import BackendId, BindingId, RemoteObjectId
from xmpp_transport.ports.repositories import BindingRecord


class DatabasePool(Protocol):
    async def fetch(self, query: str, *args: object) -> Sequence[Any]:
        ...

    async def fetchrow(self, query: str, *args: object) -> Optional[Any]:
        ...

    async def fetchval(self, query: str, *args: object) -> Optional[Any]:
        ...

    async def execute(self, query: str, *args: object) -> str:
        ...


class AsyncpgBindingRepository:
    def __init__(self, pool: DatabasePool) -> None:
        self._pool = pool

    async def active_bindings(self) -> Sequence[BindingRecord]:
        rows = await self._pool.fetch(
            """
            SELECT binding_id, backend_id
            FROM backend_bindings
            WHERE status = 'active'
            ORDER BY binding_id
            """
        )
        return tuple(
            BindingRecord(
                binding_id=BindingId(str(row["binding_id"])),
                backend_id=BackendId(str(row["backend_id"])),
            )
            for row in rows
        )

    async def encrypted_credentials(self, binding_id: BindingId) -> Optional[bytes]:
        value = await self._pool.fetchval(
            """
            SELECT encrypted_credentials
            FROM backend_bindings
            WHERE binding_id = $1
            """,
            str(binding_id),
        )
        return bytes(value) if value is not None else None

    async def save_encrypted_credentials(
        self, binding_id: BindingId, backend_id: BackendId, credentials: bytes
    ) -> None:
        status = await self._pool.execute(
            """
            UPDATE backend_bindings
            SET encrypted_credentials = $3, updated_at = CURRENT_TIMESTAMP
            WHERE binding_id = $1 AND backend_id = $2
            """,
            str(binding_id),
            str(backend_id),
            credentials,
        )
        if status != "UPDATE 1":
            raise LookupError("binding not found for credential update: {}".format(binding_id))


class AsyncpgMessageMappingRepository:
    def __init__(self, pool: DatabasePool) -> None:
        self._pool = pool

    async def remote_id_for_client_message(
        self, binding_id: BindingId, client_message_id: str
    ) -> Optional[RemoteObjectId]:
        value = await self._pool.fetchval(
            """
            SELECT remote_message_id
            FROM message_mappings
            WHERE binding_id = $1 AND client_message_id = $2
            """,
            str(binding_id),
            client_message_id,
        )
        return RemoteObjectId(str(value)) if value is not None else None

    async def save_mapping(
        self,
        binding_id: BindingId,
        client_message_id: str,
        remote_message_id: RemoteObjectId,
    ) -> None:
        stored = await self._pool.fetchval(
            """
            WITH inserted AS (
                INSERT INTO message_mappings (
                    binding_id, client_message_id, remote_message_id
                ) VALUES ($1, $2, $3)
                ON CONFLICT DO NOTHING
                RETURNING remote_message_id
            )
            SELECT remote_message_id FROM inserted
            UNION ALL
            SELECT remote_message_id
            FROM message_mappings
            WHERE binding_id = $1 AND client_message_id = $2
            LIMIT 1
            """,
            str(binding_id),
            client_message_id,
            str(remote_message_id),
        )
        if stored is None or str(stored) != str(remote_message_id):
            raise DuplicateOperation(
                "client message already maps to a different remote message"
            )
