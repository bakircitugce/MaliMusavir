from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class ERPExportDocument:
    idempotency_key: str
    journal_entry_id: UUID
    payload: bytes

    def __post_init__(self) -> None:
        if not self.idempotency_key.strip():
            raise ValueError("ERP aktarım idempotency anahtarı boş olamaz.")
        if not self.payload:
            raise ValueError("ERP aktarım içeriği boş olamaz.")


@dataclass(frozen=True)
class ERPExportResult:
    idempotency_key: str
    external_reference: str
    exported_at: datetime
    was_duplicate: bool


class ERPExportAdapter(Protocol):
    async def export_entry(self, document: ERPExportDocument) -> ERPExportResult:
        ...
