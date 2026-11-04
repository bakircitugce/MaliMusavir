from datetime import datetime, timezone

from app.erp.adapter import ERPExportAdapter, ERPExportDocument, ERPExportResult


class InMemoryERPExportAdapter(ERPExportAdapter):
    def __init__(self) -> None:
        self._exports: dict[str, ERPExportResult] = {}

    async def export_entry(self, document: ERPExportDocument) -> ERPExportResult:
        existing = self._exports.get(document.idempotency_key)
        if existing is not None:
            return ERPExportResult(
                idempotency_key=existing.idempotency_key,
                external_reference=existing.external_reference,
                exported_at=existing.exported_at,
                was_duplicate=True,
            )
        result = ERPExportResult(
            idempotency_key=document.idempotency_key,
            external_reference=f"FAKE-ERP-{len(self._exports) + 1:06d}",
            exported_at=datetime.now(timezone.utc),
            was_duplicate=False,
        )
        self._exports[document.idempotency_key] = result
        return result
