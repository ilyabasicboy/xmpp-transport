import tempfile
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
            cached = await cache.store(Client(b"avatar"), object(), 42, "photo-1")

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

            cached = await cache.store(Client(b"avatar"), object(), 42, "photo-1")

            self.assertIsNone(cached)
            self.assertEqual([], list(Path(directory).glob("*")))


if __name__ == "__main__":
    unittest.main()
