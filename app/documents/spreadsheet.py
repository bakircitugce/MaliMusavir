import csv
from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO, StringIO
from pathlib import PurePosixPath
from typing import Any

from openpyxl import load_workbook


class SpreadsheetReadError(ValueError):
    pass


@dataclass(frozen=True)
class SpreadsheetSheet:
    name: str
    headers: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class SpreadsheetData:
    sheets: tuple[SpreadsheetSheet, ...]


def _json_value(value: object) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _unique_headers(values: list[object]) -> tuple[str, ...]:
    headers: list[str] = []
    counts: dict[str, int] = {}
    for index, value in enumerate(values, start=1):
        base = str(value).strip() if value is not None else ""
        base = base or f"column_{index}"
        counts[base] = counts.get(base, 0) + 1
        headers.append(base if counts[base] == 1 else f"{base}_{counts[base]}")
    return tuple(headers)


def _rows_from_values(name: str, values: list[tuple[object, ...]]) -> SpreadsheetSheet:
    if not values:
        return SpreadsheetSheet(name=name, headers=(), rows=())
    width = max(len(row) for row in values)
    headers = _unique_headers(list(values[0]) + [None] * (width - len(values[0])))
    rows = []
    for raw_row in values[1:]:
        padded = list(raw_row) + [None] * (width - len(raw_row))
        rows.append({header: _json_value(padded[index]) for index, header in enumerate(headers)})
    return SpreadsheetSheet(name=name, headers=headers, rows=tuple(rows))


def read_csv(content: bytes, filename: str = "data.csv") -> SpreadsheetData:
    if not content:
        raise SpreadsheetReadError("CSV içeriği boş olamaz.")
    text: str | None = None
    for encoding in ("utf-8-sig", "cp1254"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise SpreadsheetReadError("CSV kodlaması okunamadı.")
    try:
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.reader(StringIO(text), dialect))
    except csv.Error as exc:
        raise SpreadsheetReadError("Geçersiz CSV dosyası.") from exc
    return SpreadsheetData(sheets=(_rows_from_values(PurePosixPath(filename).stem, rows),))


def read_xlsx(content: bytes) -> SpreadsheetData:
    if not content:
        raise SpreadsheetReadError("Excel içeriği boş olamaz.")
    try:
        workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    except (OSError, ValueError, KeyError) as exc:
        raise SpreadsheetReadError("Geçersiz Excel dosyası.") from exc
    sheets = []
    for worksheet in workbook.worksheets:
        values = [tuple(row) for row in worksheet.iter_rows(values_only=True)]
        sheets.append(_rows_from_values(worksheet.title, values))
    return SpreadsheetData(sheets=tuple(sheets))


def read_spreadsheet(content: bytes, filename: str) -> SpreadsheetData:
    suffix = PurePosixPath(filename.lower()).suffix
    if suffix == ".csv":
        return read_csv(content, filename)
    if suffix in {".xlsx", ".xlsm"}:
        return read_xlsx(content)
    raise SpreadsheetReadError("Desteklenmeyen tablo dosyası.")
