import asyncio
import tempfile
import unittest
from pathlib import Path

from xmpp_transport.domain.identifiers import BackendId
from xmpp_transport.runtime.config import load_config
from xmpp_transport.runtime.lifecycle import TaskSupervisor
from xmpp_transport.runtime.registry import BackendRegistry


class FakePlugin:
    backend_id = BackendId("fake")


class RegistryTests(unittest.TestCase):
    def test_duplicate_backend_is_rejected(self) -> None:
        registry = BackendRegistry([FakePlugin()])  # type: ignore[list-item]
        with self.assertRaises(ValueError):
            registry.register(FakePlugin())  # type: ignore[arg-type]


class ConfigTests(unittest.TestCase):
    def test_reads_multiple_backend_sections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "transport.ini"
            path.write_text(
                "[backend:telegram]\ncomponent_domain=telegram.example.com\n"
                "[backend:max]\ncomponent_domain=max.example.com\n",
                encoding="utf-8",
            )
            config = load_config(path)
        self.assertEqual(["telegram", "max"], [item.name for item in config.backends])


class SupervisorTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_cancels_owned_tasks(self) -> None:
        cancelled = asyncio.Event()

        async def worker() -> None:
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        supervisor = TaskSupervisor()
        supervisor.create_task(worker(), name="test-worker")
        await asyncio.sleep(0)
        await supervisor.close()
        self.assertTrue(cancelled.is_set())


if __name__ == "__main__":
    unittest.main()

