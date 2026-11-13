import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from decimal import Decimal
from xml.dom import minidom

from app.core.safety import stable_number
from app.erp.adapter import ERPExportAdapter, ERPExportDocument, ERPExportResult


class LogoTigerERPExportAdapter(ERPExportAdapter):
    """
    Logo Tiger / GO / Wings resmi GL_SLIPS XML formatı adaptörü.
    Kanonik yevmiye fişini Logo Tiger muhasebe fişi (GL_SLIP) XML standardına dönüştürür.
    """

    def __init__(self) -> None:
        self._exports: dict[str, ERPExportResult] = {}
        self._xml_store: dict[str, str] = {}

    def build_xml(self, payload: bytes) -> str:
        data = json.loads(payload.decode("utf-8"))
        entry_date_str = data["entry_date"]  # YYYY-MM-DD
        dt = datetime.strptime(entry_date_str, "%Y-%m-%d")
        formatted_date = dt.strftime("%d.%m.%Y")
        description = data.get("description", "Yevmiye Fişi")
        lines = data.get("lines", [])

        total_debit = Decimal("0.00")
        total_credit = Decimal("0.00")
        for line in lines:
            total_debit += Decimal(line.get("debit", "0"))
            total_credit += Decimal(line.get("credit", "0"))

        gl_slips = ET.Element("GL_SLIPS")
        gl_slip = ET.SubElement(gl_slips, "GL_SLIP", {"DBOP": "INS"})

        # TYPE: 4 = Mahsup Fişi, 1 = Tahsil, 2 = Tediye
        ET.SubElement(gl_slip, "TYPE").text = "4"
        slip_number = f"M{dt.strftime('%Y%m')}{stable_number(description, 90000) + 10000}"
        ET.SubElement(gl_slip, "NUMBER").text = slip_number
        ET.SubElement(gl_slip, "DATE").text = formatted_date
        ET.SubElement(gl_slip, "DOC_NUMBER").text = slip_number
        ET.SubElement(gl_slip, "EXPLANATION").text = description
        ET.SubElement(gl_slip, "CURR_TRANS").text = "0"
        ET.SubElement(gl_slip, "TOTAL_DEBIT").text = f"{total_debit:.2f}"
        ET.SubElement(gl_slip, "TOTAL_CREDIT").text = f"{total_credit:.2f}"

        lines_elem = ET.SubElement(gl_slip, "LINES")
        for line in lines:
            line_elem = ET.SubElement(lines_elem, "LINE")
            ET.SubElement(line_elem, "GL_CODE").text = line["account_code"]
            ET.SubElement(line_elem, "DESCRIPTION").text = line.get("account_name", "")

            debit = Decimal(line.get("debit", "0"))
            credit = Decimal(line.get("credit", "0"))
            if debit > 0:
                sign = "0"  # Borç
                amount = debit
            else:
                sign = "1"  # Alacak
                amount = credit

            ET.SubElement(line_elem, "SIGN").text = sign
            ET.SubElement(line_elem, "AMOUNT").text = f"{amount:.2f}"

        raw_xml = ET.tostring(gl_slips, encoding="utf-8")
        parsed = minidom.parseString(raw_xml)
        return parsed.toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")

    async def export_entry(self, document: ERPExportDocument) -> ERPExportResult:
        existing = self._exports.get(document.idempotency_key)
        if existing is not None:
            return ERPExportResult(
                idempotency_key=existing.idempotency_key,
                external_reference=existing.external_reference,
                exported_at=existing.exported_at,
                was_duplicate=True,
            )

        xml_content = self.build_xml(document.payload)
        ref_id = abs(hash(document.idempotency_key)) % 1000000
        ref = f"LOGO-GL-{ref_id:06d}"

        result = ERPExportResult(
            idempotency_key=document.idempotency_key,
            external_reference=ref,
            exported_at=datetime.now(timezone.utc),
            was_duplicate=False,
        )
        self._exports[document.idempotency_key] = result
        self._xml_store[document.idempotency_key] = xml_content
        return result

    def get_generated_xml(self, idempotency_key: str) -> str | None:
        return self._xml_store.get(idempotency_key)
