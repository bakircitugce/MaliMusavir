from hashlib import sha256
from pathlib import PurePosixPath
from uuid import UUID, uuid4

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import Belge
from app.core.safety import UnsafeArchiveError, check_zip_archive
from app.documents.storage import Storage

MAX_FILE_SIZE = 25 * 1024 * 1024
MAX_FILES_PER_UPLOAD = 20
# Depolama anahtarına yalnızca bu uzantılar yazılır (dosya adından gelen veri güvenilmez).
SAFE_SUFFIXES = {".xml", ".pdf", ".jpg", ".jpeg", ".png", ".csv", ".xls", ".xlsx", ".xlsm"}
CANONICAL_MEDIA_TYPES = {
    "pdf": "application/pdf",
    "ubl_xml": "application/xml",
    "spreadsheet": "application/octet-stream",
}
ALLOWED_TYPES = {
    "application/pdf": "pdf",
    "application/xml": "ubl_xml",
    "text/xml": "ubl_xml",
    "image/jpeg": "image",
    "image/png": "image",
    "text/csv": "spreadsheet",
    "application/vnd.ms-excel": "spreadsheet",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "spreadsheet",
}


def detect_document_type(filename: str, content_type: str) -> str:
    normalized_type = content_type.lower().split(";", 1)[0].strip()
    if normalized_type in ALLOWED_TYPES:
        return ALLOWED_TYPES[normalized_type]

    suffix = PurePosixPath(filename.lower()).suffix
    by_suffix = {
        ".xml": "ubl_xml",
        ".pdf": "pdf",
        ".jpg": "image",
        ".jpeg": "image",
        ".png": "image",
        ".csv": "spreadsheet",
        ".xls": "spreadsheet",
        ".xlsx": "spreadsheet",
    }
    if suffix in by_suffix:
        return by_suffix[suffix]
    raise ValueError("Desteklenmeyen belge türü.")


def validate_document_content(document_type: str, content: bytes) -> None:
    if document_type == "pdf" and not content.startswith(b"%PDF-"):
        raise ValueError("PDF içeriği doğrulanamadı.")
    if document_type == "image":
        signatures = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")
        if not content.startswith(signatures):
            raise ValueError("Görüntü içeriği doğrulanamadı.")
    if document_type == "ubl_xml":
        try:
            root = ElementTree.fromstring(content, forbid_dtd=True)
        except (ElementTree.ParseError, DefusedXmlException) as exc:
            raise ValueError("XML içeriği doğrulanamadı.") from exc
        if root.tag.rsplit("}", 1)[-1] != "Invoice":
            raise ValueError("XML belgesi UBL Invoice olmalıdır.")
    if document_type == "spreadsheet":
        if content[:2] != b"PK":
            raise ValueError("XLSX içeriği doğrulanamadı.")
        try:
            check_zip_archive(content)
        except UnsafeArchiveError as exc:
            raise ValueError(str(exc)) from exc


async def receive_document(
    session: AsyncSession,
    storage: Storage,
    buro_id: UUID,
    mukellef_id: UUID,
    filename: str,
    content_type: str,
    content: bytes,
) -> Belge:
    if not filename or len(filename) > 255:
        raise ValueError("Dosya adı geçersiz.")
    if not content:
        raise ValueError("Dosya boş olamaz.")
    if len(content) > MAX_FILE_SIZE:
        raise ValueError("Dosya boyutu 25 MB sınırını aşamaz.")

    document_type = detect_document_type(filename, content_type)
    if document_type != "spreadsheet" or PurePosixPath(filename.lower()).suffix in {".xlsx", ".xlsm"}:
        validate_document_content(document_type, content)
    digest = sha256(content).hexdigest()
    existing = await session.scalar(
        select(Belge).where(
            Belge.buro_id == buro_id,
            Belge.mukellef_id == mukellef_id,
            Belge.sha256 == digest,
        )
    )
    if existing is not None:
        raise DuplicateDocumentError(existing.id)

    document_id = uuid4()
    suffix = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
    storage_key = f"{buro_id}/{mukellef_id}/{document_id}{suffix if suffix in SAFE_SUFFIXES else ''}"
    await storage.put(storage_key, content)
    document = Belge(
        id=document_id,
        buro_id=buro_id,
        mukellef_id=mukellef_id,
        original_filename=filename,
        content_type=content_type,
        size_bytes=len(content),
        sha256=digest,
        storage_key=storage_key,
        document_type=document_type,
    )
    session.add(document)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        await storage.delete(storage_key)
        existing = await session.scalar(
            select(Belge).where(
                Belge.buro_id == buro_id,
                Belge.mukellef_id == mukellef_id,
                Belge.sha256 == digest,
            )
        )
        raise DuplicateDocumentError(existing.id if existing is not None else document_id) from exc
    await session.refresh(document)
    return document


class DuplicateDocumentError(ValueError):
    def __init__(self, document_id: UUID) -> None:
        super().__init__("Bu belge daha önce yüklendi.")
        self.document_id = document_id
