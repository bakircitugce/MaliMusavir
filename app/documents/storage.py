from pathlib import Path
from typing import Protocol

from app.core.config import get_settings


class Storage(Protocol):
    async def put(self, key: str, content: bytes) -> None: ...

    async def delete(self, key: str) -> None: ...

    async def get(self, key: str) -> bytes: ...


class LocalStorage:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path(get_settings().storage_dir)

    async def put(self, key: str, content: bytes) -> None:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    async def delete(self, key: str) -> None:
        path = self.root / key
        if path.exists():
            path.unlink()

    async def get(self, key: str) -> bytes:
        path = self.root / key
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise FileNotFoundError("Belge depolamada bulunamadı.") from exc
