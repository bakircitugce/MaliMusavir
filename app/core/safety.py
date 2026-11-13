"""Ortak güvenlik yardımcıları (Faz 1)."""

import hashlib
import io
import re
import unicodedata
import zipfile
from urllib.parse import quote

# Excel/LibreOffice bu karakterlerle başlayan hücreyi formül olarak yorumlar.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")
_NUMERIC = re.compile(r"^-?\d+([.,]\d+)?$")

MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
MAX_ZIP_ENTRIES = 10_000


class UnsafeArchiveError(ValueError):
    pass


def safe_csv_cell(value: object) -> str:
    """CSV/formül enjeksiyonunu önler: formül gibi başlayan metne ' öneki ekler.

    Negatif sayılar (ör. -12,50) dokunulmadan bırakılır.
    """
    text = "" if value is None else str(value)
    if text.startswith(_FORMULA_PREFIXES) and not _NUMERIC.match(text):
        return "'" + text
    return text


def stable_number(text: str, modulo: int) -> int:
    """Süreçten bağımsız, deterministik sayı (Python ``hash()`` her açılışta değişir)."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % modulo


def check_zip_archive(
    content: bytes,
    *,
    max_uncompressed: int = MAX_UNCOMPRESSED_BYTES,
    max_ratio: int = MAX_COMPRESSION_RATIO,
    max_entries: int = MAX_ZIP_ENTRIES,
) -> None:
    """XLSX gibi ZIP tabanlı dosyalarda zip bombası ve bozuk arşiv kontrolü."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            infos = archive.infolist()
    except (zipfile.BadZipFile, ValueError) as exc:
        raise UnsafeArchiveError("Dosya geçerli bir XLSX/ZIP arşivi değil.") from exc
    if len(infos) > max_entries:
        raise UnsafeArchiveError("Arşivde çok fazla dosya var.")
    total = sum(info.file_size for info in infos)
    if total > max_uncompressed:
        raise UnsafeArchiveError("Arşivin açılmış boyutu sınırı aşıyor.")
    for info in infos:
        if info.compress_size and info.file_size / info.compress_size > max_ratio:
            raise UnsafeArchiveError("Arşivde şüpheli sıkıştırma oranı (zip bombası) tespit edildi.")
        name = info.filename.replace("\\", "/")
        if name.startswith("/") or ".." in name.split("/"):
            raise UnsafeArchiveError("Arşivde güvensiz dosya yolu var.")


def content_disposition(filename: str, *, inline: bool) -> str:
    """Başlık enjeksiyonuna kapalı Content-Disposition (RFC 6266 / 5987)."""
    cleaned = "".join(ch for ch in filename if unicodedata.category(ch)[0] != "C")
    cleaned = cleaned.replace("/", "_").replace("\\", "_").strip() or "belge"
    ascii_name = (
        unicodedata.normalize("NFKD", cleaned).encode("ascii", "ignore").decode("ascii")
    )
    ascii_name = re.sub(r'[^A-Za-z0-9._ -]', "_", ascii_name) or "belge"
    disposition = "inline" if inline else "attachment"
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(cleaned)}"
