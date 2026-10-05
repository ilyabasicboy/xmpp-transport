import os
import tempfile
import time
import unittest
from pathlib import Path

from xmpp_transport.adapters.backends.telegram.avatar_cache import TelegramAvatarCache


class Client:
    def __init__(self, content: bytes) -> None:
        self.content = content

    async def download_profile_photo(
        self, entity, file=bytes, download_big=False  # type: ignore[no-untyped-def]
    ) -> bytes:
        return self.content


class TelegramAvatarCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_stores_content_addressed_avatar_with_exact_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = TelegramAvatarCache(directory, "https://transport.example", 1024)
            cached = await cache.store(
                Client(b"avatar"), object(), "binding-1", 42, "photo-1"
            )

            self.assertIsNotNone(cached)
            assert cached is not None
            self.assertEqual(6, cached.bytes_count)
            self.assertTrue(cached.avatar_id.startswith("telegram-42-photo-1-"))
            filename = cached.url.rsplit("/", 1)[-1]
            self.assertEqual(b"avatar", (Path(directory) / filename).read_bytes())

            restarted = TelegramAvatarCache(
                directory, "https://transport.example", 1024
            )
            self.assertEqual(Path(directory) / filename, restarted.path(filename))

    async def test_rejects_oversized_avatar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = TelegramAvatarCache(directory, "https://transport.example", 5)

            cached = await cache.store(
                Client(b"avatar"), object(), "binding-1", 42, "photo-1"
            )

            self.assertIsNone(cached)
            self.assertEqual([], list(Path(directory).glob("*")))

    async def test_cleanup_removes_only_expired_replaced_avatar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = TelegramAvatarCache(directory, "https://transport.example", 1024)
            first = await cache.store(
                Client(b"first"), object(), "binding-1", 42, "photo-1"
            )
            second = await cache.store(
                Client(b"second"), object(), "binding-1", 42, "photo-2"
            )
            assert first is not None and second is not None
            first_path = Path(directory) / first.url.rsplit("/", 1)[-1]
            second_path = Path(directory) / second.url.rsplit("/", 1)[-1]
            old = time.time() - 8 * 86400
            os.utime(first_path, (old, old))
            os.utime(second_path, (old, old))

            removed = await cache.cleanup_unreferenced(7)

            self.assertEqual(1, removed)
            self.assertFalse(first_path.exists())
            self.assertTrue(second_path.exists())

    async def test_forget_makes_current_avatar_eligible_for_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = TelegramAvatarCache(directory, "https://transport.example", 1024)
            cached = await cache.store(
                Client(b"avatar"), object(), "binding-1", 42, "photo-1"
            )
            assert cached is not None
            path = Path(directory) / cached.url.rsplit("/", 1)[-1]
            old = time.time() - 1
            os.utime(path, (old, old))

            await cache.forget("binding-1", 42)
            removed = await cache.cleanup_unreferenced(0)

            self.assertEqual(1, removed)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
