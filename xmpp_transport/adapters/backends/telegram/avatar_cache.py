"""Persistent Telegram avatar cache ported from the original transport."""

import asyncio
import hashlib
import json
import os
import time
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
        self._manifest_path = self.storage_dir / ".references.json"
        self._lock = asyncio.Lock()

    async def store(
        self,
        client: object,
        entity: object,
        owner_key: str,
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
        async with self._lock:
            references = self._read_references()
            references[self._reference_key(owner_key, peer_id)] = content_hash
            self._write_references(references)
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
        if not path.is_file():
            return None
        try:
            path.touch(exist_ok=True)
        except OSError:
            pass
        return path

    async def forget(self, owner_key: str, peer_id: int) -> None:
        async with self._lock:
            references = self._read_references()
            if references.pop(self._reference_key(owner_key, peer_id), None) is not None:
                self._write_references(references)

    async def cleanup_unreferenced(
        self, ttl_days: int, now: Optional[float] = None
    ) -> int:
        cutoff = (time.time() if now is None else now) - max(ttl_days, 0) * 86400
        removed = 0
        async with self._lock:
            referenced = set(self._read_references().values())
            if not self.storage_dir.is_dir():
                return 0
            for path in self.storage_dir.glob("*.jpg"):
                if path.stem in referenced:
                    continue
                try:
                    if path.stat().st_mtime >= cutoff:
                        continue
                    path.unlink()
                except FileNotFoundError:
                    continue
                except OSError:
                    continue
                removed += 1
        return removed

    def _read_references(self) -> dict[str, str]:
        try:
            value = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
        if not isinstance(value, dict):
            return {}
        return {
            str(key): str(content_hash)
            for key, content_hash in value.items()
            if isinstance(key, str) and self._valid_hash(content_hash)
        }

    def _write_references(self, references: dict[str, str]) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        temporary = self._manifest_path.with_name(
            f"{self._manifest_path.name}.tmp.{os.getpid()}"
        )
        try:
            temporary.write_text(
                json.dumps(references, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(str(temporary), str(self._manifest_path))
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _reference_key(owner_key: str, peer_id: int) -> str:
        return f"{owner_key}:{peer_id}"

    @staticmethod
    def _valid_hash(value: object) -> bool:
        return isinstance(value, str) and len(value) == 64 and all(
            character in "0123456789abcdef" for character in value
        )

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
