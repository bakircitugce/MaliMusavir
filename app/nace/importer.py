import csv
from collections.abc import Iterable
from dataclasses import dataclass
from io import TextIOBase
from pathlib import Path

from openpyxl import load_workbook


@dataclass(frozen=True)
class NaceRecord:
    kod: str
    tanim: str
    kaynak: str
    sektor_kodu: str | None = None
    sektor_tanim: str | None = None
    meslek_kodu: str | None = None
    meslek_tanim: str | None = None


def import_nace_csv(stream: TextIOBase, kaynak: str = "csv") -> list[NaceRecord]:
    reader = csv.DictReader(stream)
    records: list[NaceRecord] = []
    seen: set[str] = set()
    for row in reader:
        code = (row.get("kod") or row.get("NACE kodu") or "").strip()
        description = (row.get("tanim") or row.get("NACE tanımı") or "").strip()
        if not code or not description or code in seen:
            continue
        seen.add(code)
        records.append(NaceRecord(kod=code, tanim=description, kaynak=kaynak))
    return records


def validate_nace_codes(records: Iterable[NaceRecord]) -> None:
    for record in records:
        parts = record.kod.split(".")
        if len(parts) != 3 or any(len(part) != 2 or not part.isdigit() for part in parts):
            raise ValueError(f"Geçersiz NACE kodu: {record.kod}")


def import_nace_workbook(path: str | Path) -> tuple[list[NaceRecord], list[NaceRecord]]:
    """Read the workbook's related A-F table and independent G-H current list."""
    workbook = load_workbook(path, read_only=True, data_only=True)
    sheet = workbook.active
    related: list[NaceRecord] = []
    current: list[NaceRecord] = []
    seen_related: set[str] = set()
    seen_current: set[str] = set()

    for row in sheet.iter_rows(min_row=2, values_only=True):
        sector_code, sector_name, job_code, job_name, code, name, current_code, current_name = (
            list(row[:8]) + [None] * 8
        )[:8]
        if code and name and str(code).strip() not in seen_related:
            normalized_code = str(code).strip()
            seen_related.add(normalized_code)
            related.append(
                NaceRecord(
                    kod=normalized_code,
                    tanim=str(name).strip(),
                    kaynak="sektor_meslek",
                    sektor_kodu=str(sector_code).strip() if sector_code else None,
                    sektor_tanim=str(sector_name).strip() if sector_name else None,
                    meslek_kodu=str(job_code).strip() if job_code else None,
                    meslek_tanim=str(job_name).strip() if job_name else None,
                )
            )
        if current_code and current_name and str(current_code).strip() not in seen_current:
            normalized_current_code = str(current_code).strip()
            seen_current.add(normalized_current_code)
            current.append(
                NaceRecord(
                    kod=normalized_current_code,
                    tanim=str(current_name).strip(),
                    kaynak="guncel",
                )
            )

    validate_nace_codes(related)
    validate_nace_codes(current)
    return related, current
