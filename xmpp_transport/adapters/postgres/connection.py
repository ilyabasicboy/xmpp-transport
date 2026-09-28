"""Owned asyncpg pool lifecycle with migrations-before-readiness."""

from typing import Any, Awaitable, Callable, Optional

from xmpp_transport.runtime.config import DatabaseConfig

from .migrations import MigrationRunner


PoolFactory = Callable[..., Awaitable[Any]]
MigrationRunnerFactory = Callable[[Any], MigrationRunner]


async def _asyncpg_pool_factory(**kwargs: object) -> Any:
    import asyncpg

    return await asyncpg.create_pool(**kwargs)


class PostgresPoolManager:
    def __init__(
        self,
        config: DatabaseConfig,
        pool_factory: PoolFactory = _asyncpg_pool_factory,
        migration_runner_factory: MigrationRunnerFactory = MigrationRunner,
    ) -> None:
        self._config = config
        self._pool_factory = pool_factory
        self._migration_runner_factory = migration_runner_factory
        self._pool: Optional[Any] = None
        self._closed = False

    @property
    def pool(self) -> Any:
        if self._pool is None:
            raise RuntimeError("PostgreSQL pool is not started")
        return self._pool

    async def start(self) -> Any:
        if self._closed:
            raise RuntimeError("PostgreSQL pool manager is closed")
        if self._pool is not None:
            return self._pool
        pool = await self._pool_factory(
            dsn=self._config.dsn,
            min_size=self._config.min_pool_size,
            max_size=self._config.max_pool_size,
            command_timeout=self._config.command_timeout,
        )
        try:
            await self._migration_runner_factory(pool).run()
        except BaseException:
            await pool.close()
            raise
        self._pool = pool
        return pool

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        pool = self._pool
        self._pool = None
        if pool is not None:
            await pool.close()

    async def __aenter__(self) -> "PostgresPoolManager":
        await self.start()
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.close()
