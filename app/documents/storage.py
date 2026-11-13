from pathlib import Path
from typing import Protocol

from app.core.config import get_settings


class Storage(Protocol):
    async def put(self, key: str, content: bytes) -> None: ...

    async def delete(self, key: str) -> None: ...

    async def get(self, key: str) -> bytes: ...


class StorageKeyError(ValueError):
    pass


class LocalStorage:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path(get_settings().storage_dir)

    def _path(self, key: str) -> Path:
        """Anahtarın depolama kökü dışına çıkmasını (path traversal) engeller."""
        root = self.root.resolve()
        path = (root / key).resolve()
        if root != path and root not in path.parents:
            raise StorageKeyError("Geçersiz depolama anahtarı.")
        return path

    async def put(self, key: str, content: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    async def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise FileNotFoundError("Belge depolamada bulunamadı.") from exc
