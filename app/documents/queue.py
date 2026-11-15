import asyncio
import logging
from uuid import UUID

from sqlalchemy import inspect, select

from app.bank.statement_import import IbanRequiredError, import_statement
from app.bank.statement_parser import StatementParseError
from app.core.db import session_factory
from app.core.models import Belge
from app.documents.extractor import extract_document
from app.documents.pdf import PDFExtractionError
from app.documents.storage import LocalStorage, Storage
from app.ledger.draft import LedgerValidationError
from app.ledger.posting import IslemSahipligiHatasi, PostingResult, post_document

logger = logging.getLogger(__name__)

# Ekstre olabilecek belge türleri: önce banka ekstresi olarak okunmaya çalışılır.
STATEMENT_CANDIDATE_TYPES = {"spreadsheet", "pdf"}


async def _run_extraction(document_id: UUID, buro_id: UUID, mukellef_id: UUID) -> None:
    async with session_factory() as session:
        document = await session.scalar(
            select(Belge).where(
                Belge.id == document_id,
                Belge.buro_id == buro_id,
                Belge.mukellef_id == mukellef_id,
            )
        )
        if document is None:
            return
        storage = LocalStorage()
        try:
            await extract_document(session, storage, document)
        except ValueError:
            pass  # okunamayan belge de kuyruğa "elle giriş gerekli" olarak düşer
        await process_after_extraction(session, document, storage)


async def _try_bank_statement(session, document: Belge, storage: Storage) -> bool:
    """Belge bir banka ekstresiyse hareketleri içe aktarır. Ekstre değilse False döner.

    Transaction'ın sahibi değildir: asla commit veya rollback yapmaz. İçe aktarma denemesi bir
    SAVEPOINT içinde yapılır; deneme başarısız olursa yalnızca SAVEPOINT geri alınır (kısmen
    eklenmiş banka hesabı/hareketleri kalmaz), dış transaction açık kalır ve belge açıkça yeniden
    okunur (geri alma sonrası "expired" alanlara erişim async ortamda gizli IO'ya dönüşmesin).
    Not: `begin_nested()` bekleyen değişiklikleri SAVEPOINT'ten önce dış transaction'a flush eder;
    bunlar SAVEPOINT geri alınsa da korunur.
    """
    try:
        content = await storage.get(document.storage_key)
    except FileNotFoundError:
        return False
    savepoint = await session.begin_nested()
    try:
        result = await import_statement(
            session,
            buro_id=document.buro_id,
            mukellef_id=document.mukellef_id,
            content=content,
            filename=document.original_filename,
        )
    except IbanRequiredError as exc:
        await savepoint.rollback()
        await session.refresh(document)
        posting = PostingResult(
            "needs_review",
            [f"{exc} Banka & POS sekmesinden IBAN belirterek yükleyin."],
        )
        document.document_category = "bank_statement"
        document.extraction_metadata = {
            **(document.extraction_metadata or {}), "posting": posting.metadata()
        }
        document.review_status = "needs_review"
        return True
    except (StatementParseError, LedgerValidationError, PDFExtractionError):
        await savepoint.rollback()
        await session.refresh(document)
        return False
    except BaseException:
        await savepoint.rollback()
        raise
    await savepoint.commit()
    posting = PostingResult("bank_imported", [result.summary(), *result.warnings])
    document.document_category = "bank_statement"
    document.extraction_metadata = {
        **(document.extraction_metadata or {}),
        "posting": posting.metadata(),
        "bank_import": result.as_dict(),
    }
    # Hareketler Banka & POS ekranında eşleştirilir; belge onay kuyruğundan çıkar.
    document.review_status = "approved" if not result.balance_mismatches else "needs_review"
    return True


async def process_after_extraction(session, document: Belge, storage: Storage | None = None) -> None:
    """Okuma sonrası: ekstre ise bankaya aktar, değilse TDHP taslak yevmiye üret.

    Okuma sonrası işlemin transaction sahibi bu fonksiyondur; yardımcılar (`_try_bank_statement`,
    `post_document(commit=False)`) commit/rollback yapmaz. `extract_document` kendi yazmalarını
    commit eder ve en sonda belgeyi yeniden okur; bu yüzden çağrı anında yalnızca okuma yapmış bir
    transaction açık olabilir. Fonksiyon bu transaction'ı devralır ve her yolda kapatır: başarıda
    tek COMMIT, hatada ROLLBACK. Çağıranın flush edilmemiş değişikliği varsa (yanlışlıkla commit
    edilmesin diye) hiçbir işlem yapılmadan `IslemSahipligiHatasi` fırlatılır.

    Hata yutulur ve loglanır; logda ORM nesnesi yerine önceden saklanan belge kimliği kullanılır
    (geri alma sonrası nesne "expired" olabilir ve async ortamda okunamaz).
    """
    document_id = inspect(document).identity[0]
    if session.new or session.dirty or session.deleted:
        raise IslemSahipligiHatasi(
            "process_after_extraction çağrılırken oturumda flush edilmemiş değişiklik var "
            f"(belge {document_id}); bu değişiklikler sahibi tarafından sonlandırılmalı."
        )
    try:
        await session.refresh(document)
        meta = document.extraction_metadata or {}
        if (
            document.document_type in STATEMENT_CANDIDATE_TYPES
            and "invoice" not in meta
            and await _try_bank_statement(session, document, storage or LocalStorage())
        ):
            await session.commit()
            return
        await post_document(session, document, commit=False)
        await session.commit()
    except Exception:
        logger.exception("Belge sonrası işlem yapılamadı: %s", document_id)
        try:
            await session.rollback()
        except Exception:
            logger.exception("Belge sonrası işlemde rollback yapılamadı: %s", document_id)


def enqueue_extraction(document_id: UUID, buro_id: UUID, mukellef_id: UUID) -> None:
    """Run locally until the durable Celery/Redis worker is introduced."""
    asyncio.create_task(_run_extraction(document_id, buro_id, mukellef_id))
