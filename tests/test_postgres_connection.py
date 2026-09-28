import unittest
from typing import Any, Dict

from xmpp_transport.adapters.postgres.connection import PostgresPoolManager
from xmpp_transport.runtime.config import DatabaseConfig


class FakePool:
    def __init__(self) -> None:
        self.closed = 0

    async def close(self) -> None:
        self.closed += 1


class FakeMigrationRunner:
    def __init__(self, pool: FakePool, fail: bool = False) -> None:
        self.pool = pool
        self.fail = fail
        self.ran = 0

    async def run(self) -> None:
        self.ran += 1
        if self.fail:
            raise RuntimeError("migration failed")


class PostgresPoolManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_is_idempotent_and_runs_migrations_first(self) -> None:
        pool = FakePool()
        captured: Dict[str, object] = {}
        runner = FakeMigrationRunner(pool)

        async def factory(**kwargs: object) -> Any:
            captured.update(kwargs)
            return pool

        manager = PostgresPoolManager(
            DatabaseConfig("postgresql://private", 2, 7, 11),
            pool_factory=factory,
            migration_runner_factory=lambda value: runner,  # type: ignore[arg-type]
        )
        first = await manager.start()
        second = await manager.start()
        self.assertIs(first, second)
        self.assertEqual(1, runner.ran)
        self.assertEqual(2, captured["min_size"])
        self.assertEqual(7, captured["max_size"])
        await manager.close()
        await manager.close()
        self.assertEqual(1, pool.closed)

    async def test_migration_failure_closes_pool(self) -> None:
        pool = FakePool()
        runner = FakeMigrationRunner(pool, fail=True)

        async def factory(**kwargs: object) -> Any:
            return pool

        manager = PostgresPoolManager(
            DatabaseConfig("postgresql://private"),
            pool_factory=factory,
            migration_runner_factory=lambda value: runner,  # type: ignore[arg-type]
        )
        with self.assertRaises(RuntimeError):
            await manager.start()
        self.assertEqual(1, pool.closed)
        with self.assertRaises(RuntimeError):
            _ = manager.pool

    async def test_closed_manager_cannot_restart(self) -> None:
        pool = FakePool()

        async def factory(**kwargs: object) -> Any:
            return pool

        manager = PostgresPoolManager(
            DatabaseConfig("postgresql://private"),
            pool_factory=factory,
            migration_runner_factory=lambda value: FakeMigrationRunner(pool),  # type: ignore[arg-type]
        )
        await manager.close()
        with self.assertRaises(RuntimeError):
            await manager.start()


if __name__ == "__main__":
    unittest.main()
