"""Small transactional migration runner for packaged SQL migrations."""

from dataclasses import dataclass
from importlib import resources
from typing import Any, Iterable, List, Protocol, Sequence


@dataclass(frozen=True, order=True)
class Migration:
    version: int
    name: str
    sql: str


class MigrationConnection(Protocol):
    async def execute(self, query: str, *args: object) -> str:
        ...

    async def fetch(self, query: str, *args: object) -> Sequence[Any]:
        ...

    def transaction(self) -> Any:
        ...


class MigrationPool(Protocol):
    def acquire(self) -> Any:
        ...


def packaged_migrations() -> List[Migration]:
    directory = resources.files("xmpp_transport.adapters.postgres").joinpath("migrations")
    migrations = []
    for item in directory.iterdir():
        if not item.name.endswith(".sql"):
            continue
        prefix, separator, name = item.name.partition("_")
        if not separator or not prefix.isdigit():
            raise ValueError("invalid migration filename: {}".format(item.name))
        migrations.append(Migration(int(prefix), name[:-4], item.read_text(encoding="utf-8")))
    return sorted(migrations)


class MigrationRunner:
    """Apply each migration once while holding a PostgreSQL advisory lock."""

    _LOCK_ID = 7276947082477708308

    def __init__(
        self, pool: MigrationPool, migrations: Iterable[Migration] = ()
    ) -> None:
        self._pool = pool
        supplied = list(migrations)
        self._migrations = supplied if supplied else packaged_migrations()
        versions = [item.version for item in self._migrations]
        if len(versions) != len(set(versions)):
            raise ValueError("migration versions must be unique")

    async def run(self) -> None:
        async with self._pool.acquire() as connection:
            await connection.execute("SELECT pg_advisory_lock($1)", self._LOCK_ID)
            try:
                await self._prepare_tracking_table(connection)
                rows = await connection.fetch(
                    "SELECT version FROM transport_schema_migrations ORDER BY version"
                )
                applied = {int(row["version"]) for row in rows}
                for migration in self._migrations:
                    if migration.version in applied:
                        continue
                    async with connection.transaction():
                        await connection.execute(migration.sql)
                        await connection.execute(
                            "INSERT INTO transport_schema_migrations (version, name) VALUES ($1, $2)",
                            migration.version,
                            migration.name,
                        )
            finally:
                await connection.execute("SELECT pg_advisory_unlock($1)", self._LOCK_ID)

    @staticmethod
    async def _prepare_tracking_table(connection: MigrationConnection) -> None:
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS transport_schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

