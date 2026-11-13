import csv
import io
import json
from datetime import datetime, timezone
from decimal import Decimal

from app.core.safety import safe_csv_cell, stable_number
from app.erp.adapter import ERPExportAdapter, ERPExportDocument, ERPExportResult


class UniversalCSVERPExportAdapter(ERPExportAdapter):
    """
    Luca, Zirve, Orka vb. genel muhasebe yazılımları için standart CSV yevmiye fişi adaptörü.
    """

    def __init__(self) -> None:
        self._exports: dict[str, ERPExportResult] = {}
        self._csv_store: dict[str, str] = {}

    def build_csv(self, payload: bytes) -> str:
        data = json.loads(payload.decode("utf-8"))
        entry_date = data["entry_date"]
        description = data.get("description", "")
        lines = data.get("lines", [])

        output = io.StringIO()
        writer = csv.writer(output, delimiter=";", lineterminator="\n")
        writer.writerow(["TARIH", "FIS_NO", "HESAP_KODU", "HESAP_ADI", "ACIKLAMA", "BORC", "ALACAK"])

        slip_no = f"YV-{entry_date.replace('-', '')}"
        for line in lines:
            debit = Decimal(line.get("debit", "0"))
            credit = Decimal(line.get("credit", "0"))
            # Fatura açıklaması gibi dış kaynaklı metinler Excel'de formül olarak
            # çalışmasın diye temizlenir.
            writer.writerow([
                safe_csv_cell(entry_date),
                safe_csv_cell(slip_no),
                safe_csv_cell(line.get("account_code", "")),
                safe_csv_cell(line.get("account_name", "")),
                safe_csv_cell(description),
                f"{debit:.2f}",
                f"{credit:.2f}",
            ])

        return output.getvalue()

    async def export_entry(self, document: ERPExportDocument) -> ERPExportResult:
        existing = self._exports.get(document.idempotency_key)
        if existing is not None:
            return ERPExportResult(
                idempotency_key=existing.idempotency_key,
                external_reference=existing.external_reference,
                exported_at=existing.exported_at,
                was_duplicate=True,
            )

        csv_content = self.build_csv(document.payload)
        ref_id = stable_number(document.idempotency_key, 1_000_000)
        ref = f"CSV-GL-{ref_id:06d}"

        result = ERPExportResult(
            idempotency_key=document.idempotency_key,
            external_reference=ref,
            exported_at=datetime.now(timezone.utc),
            was_duplicate=False,
        )
        self._exports[document.idempotency_key] = result
        self._csv_store[document.idempotency_key] = csv_content
        return result

    def get_generated_csv(self, idempotency_key: str) -> str | None:
        return self._csv_store.get(idempotency_key)
