"""Persistent Telegram avatar cache ported from the original transport."""

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class CachedAvatar:
    avatar_id: str
    url: str
    mime_type: str
    bytes_count: int
    content_hash: str


class TelegramAvatarCache:
    def __init__(self, storage_dir: str, base_url: str, max_bytes: int) -> None:
        self.storage_dir = Path(storage_dir)
        self.base_url = base_url.rstrip("/")
        self.max_bytes = max_bytes

    async def store(
        self,
        client: object,
        entity: object,
        peer_id: int,
        photo_id: str,
    ) -> Optional[CachedAvatar]:
        content = await client.download_profile_photo(  # type: ignore[attr-defined]
            entity, file=bytes, download_big=False
        )
        if not content or len(content) > self.max_bytes:
            return None
        content_hash = hashlib.sha256(content).hexdigest()
        filename = f"{content_hash}.jpg"
        self._write_once(self.storage_dir / filename, content)
        return CachedAvatar(
            avatar_id=f"telegram-{peer_id}-{photo_id}-{content_hash[:16]}",
            url=f"{self.base_url}/avatar/{filename}",
            mime_type="image/jpeg",
            bytes_count=len(content),
            content_hash=content_hash,
        )

    def path(self, filename: str) -> Optional[Path]:
        if not filename.endswith(".jpg"):
            return None
        content_hash = filename[:-4]
        if len(content_hash) != 64 or any(
            character not in "0123456789abcdef" for character in content_hash
        ):
            return None
        path = self.storage_dir / filename
        return path if path.is_file() else None

    @staticmethod
    def _write_once(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            return
        temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}")
        try:
            with temporary.open("xb") as handle:
                handle.write(content)
            os.replace(str(temporary), str(path))
        except FileExistsError:
            return
        finally:
            if temporary.exists():
                temporary.unlink()
