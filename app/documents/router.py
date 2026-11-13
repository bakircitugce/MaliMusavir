from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit
from app.auth.deps import get_current_user, require_approver
from app.auth.deps import tenant_context as tenant_headers
from app.core.db import get_session
from app.core.models import Belge, JournalLine, Mukellef, User
from app.core.safety import content_disposition
from app.documents.extractor import DocumentExtractionError, extract_document
from app.documents.queue import enqueue_extraction, process_after_extraction
from app.documents.review import DocumentReviewInput, apply_document_review
from app.documents.service import (
    CANONICAL_MEDIA_TYPES,
    MAX_FILE_SIZE,
    MAX_FILES_PER_UPLOAD,
    DuplicateDocumentError,
    receive_document,
)
from app.documents.storage import LocalStorage
from app.learning.service import record_feedback
from app.ledger.draft import LedgerValidationError
from app.ledger.posting import (
    PURCHASE_FEATURE,
    SALES_FEATURE,
    existing_entry,
    post_document,
)
from app.ledger.service import approve_entry

router = APIRouter(prefix="/documents", tags=["documents"])
storage = LocalStorage()




async def validate_tenant(
    session: AsyncSession,
    buro_id: UUID,
    mukellef_id: UUID,
) -> None:
    tenant = await session.scalar(
        select(Mukellef).where(Mukellef.id == mukellef_id, Mukellef.buro_id == buro_id)
    )
    if tenant is None:
        raise HTTPException(status_code=404, detail="Büro veya mükellef bulunamadı.")


def serialize_document(document: Belge) -> dict[str, object]:
    return {
        "id": str(document.id),
        "filename": document.original_filename,
        "content_type": document.content_type,
        "size_bytes": document.size_bytes,
        "sha256": document.sha256,
        "document_type": document.document_type,
        "status": document.status,
        "extraction_status": document.extraction_status,
        "classification_status": document.classification_status,
        "document_category": document.document_category,
        "classification_confidence": document.classification_confidence,
        "review_status": document.review_status,
    }


def serialize_document_detail(document: Belge) -> dict[str, object]:
    result = serialize_document(document)
    result.update(
        {
            "created_at": document.created_at.isoformat(),
            "updated_at": document.updated_at.isoformat(),
            "extracted_text": document.extracted_text,
            "extraction_metadata": document.extraction_metadata,
            "classification_reasons": document.classification_reasons,
            "review_note": document.review_note,
        }
    )
    return result


async def read_upload(upload: UploadFile) -> bytes:
    content = await upload.read(MAX_FILE_SIZE + 1)
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="Dosya boyutu 25 MB sınırını aşamaz.")
    return content


@router.post("/upload", status_code=201)
async def upload_document(
    request: Request,
    upload: UploadFile = File(...),
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict[str, object]:
    buro_id, mukellef_id = tenant
    await validate_tenant(session, buro_id, mukellef_id)
    content = await read_upload(upload)
    try:
        document = await receive_document(
            session,
            storage,
            buro_id,
            mukellef_id,
            upload.filename or "",
            upload.content_type or "application/octet-stream",
            content,
        )
    except DuplicateDocumentError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": str(exc), "document_id": str(exc.document_id)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await audit.record(
        session, "document.upload", request=request, user=user, mukellef_id=mukellef_id,
        target_type="document", target_id=document.id,
        details={"filename": document.original_filename, "sha256": document.sha256},
    )
    enqueue_extraction(document.id, buro_id, mukellef_id)
    return serialize_document(document)


@router.post("/upload-multi", status_code=201)
async def upload_documents(
    request: Request,
    uploads: list[UploadFile] = File(...),
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict[str, object]:
    buro_id, mukellef_id = tenant
    await validate_tenant(session, buro_id, mukellef_id)
    if len(uploads) > MAX_FILES_PER_UPLOAD:
        raise HTTPException(
            status_code=413,
            detail=f"Tek seferde en fazla {MAX_FILES_PER_UPLOAD} dosya yüklenebilir.",
        )
    documents: list[dict[str, object]] = []
    for upload in uploads:
        content = await read_upload(upload)
        try:
            document = await receive_document(
                session,
                storage,
                buro_id,
                mukellef_id,
                upload.filename or "",
                upload.content_type or "application/octet-stream",
                content,
            )
        except DuplicateDocumentError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"message": str(exc), "document_id": str(exc.document_id)},
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await audit.record(
            session, "document.upload", request=request, user=user, mukellef_id=mukellef_id,
            target_type="document", target_id=document.id,
            details={"filename": document.original_filename, "sha256": document.sha256},
        )
        enqueue_extraction(document.id, buro_id, mukellef_id)
        documents.append(serialize_document(document))
    return {"documents": documents}


@router.get("")
async def list_documents(
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    buro_id, mukellef_id = tenant
    await validate_tenant(session, buro_id, mukellef_id)
    documents = (
        await session.scalars(
            select(Belge)
            .where(Belge.buro_id == buro_id, Belge.mukellef_id == mukellef_id)
            .order_by(Belge.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return {"documents": [serialize_document(document) for document in documents]}


class CorrectionInput(BaseModel):
    account_code: str = Field(pattern=r"^[1-9][0-9]{2}(\.[0-9A-Za-z]{1,20}){0,3}$")
    account_name: str = Field(min_length=2, max_length=200)
    note: str | None = Field(default=None, max_length=2000)


def _invoice_summary(meta: dict) -> dict | None:
    invoice = meta.get("invoice")
    if not invoice:
        return None
    return {
        "invoice_id": invoice.get("invoice_id"),
        "issue_date": invoice.get("issue_date"),
        "invoice_type_code": invoice.get("invoice_type_code"),
        "supplier": invoice.get("supplier"),
        "customer": invoice.get("customer"),
        "tax_exclusive_amount": invoice.get("tax_exclusive_amount"),
        "tax_total": invoice.get("tax_total"),
        "payable_amount": invoice.get("payable_amount"),
    }


async def _queue_item(session: AsyncSession, document: Belge) -> dict[str, object]:
    meta = document.extraction_metadata or {}
    entry = await existing_entry(session, document)
    lines: list[dict[str, object]] = []
    if entry is not None:
        rows = await session.scalars(
            select(JournalLine)
            .where(JournalLine.journal_entry_id == entry.id, JournalLine.buro_id == document.buro_id)
            .order_by(JournalLine.line_order)
        )
        lines = [
            {"account_code": r.account_code, "account_name": r.account_name,
             "debit": str(r.debit), "credit": str(r.credit)}
            for r in rows.all()
        ]
    return {
        **serialize_document(document),
        "created_at": document.created_at.isoformat() if document.created_at else None,
        "invoice": _invoice_summary(meta),
        "posting": meta.get("posting"),
        "extraction_error": meta.get("error"),
        "entry": (
            {"id": str(entry.id), "status": entry.status, "description": entry.description,
             "entry_date": entry.entry_date.isoformat(),
             "confidence": str(entry.confidence) if entry.confidence is not None else None,
             "lines": lines}
            if entry is not None else None
        ),
    }


@router.get("/queue")
async def review_queue(
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
    include_approved: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=200),
) -> dict[str, object]:
    """İnceleme kuyruğu: belge + çıkarılan bilgiler + taslak yevmiye satırları + gerekçe."""
    buro_id, mukellef_id = tenant
    stmt = select(Belge).where(Belge.buro_id == buro_id, Belge.mukellef_id == mukellef_id)
    if not include_approved:
        stmt = stmt.where(Belge.review_status != "approved")
    documents = (await session.scalars(stmt.order_by(Belge.created_at.desc()).limit(limit))).all()
    items = [await _queue_item(session, d) for d in documents]
    return {"items": items, "count": len(items)}


async def _tenant_document(
    session: AsyncSession, document_id: UUID, buro_id: UUID, mukellef_id: UUID
) -> Belge:
    document = await session.scalar(
        select(Belge).where(
            Belge.id == document_id, Belge.buro_id == buro_id, Belge.mukellef_id == mukellef_id
        )
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Belge bulunamadı.")
    return document


async def _learn(session: AsyncSession, document: Belge, account_label: str) -> None:
    posting = (document.extraction_metadata or {}).get("posting") or {}
    invoice = (document.extraction_metadata or {}).get("invoice") or {}
    direction = posting.get("direction")
    party = invoice.get("supplier" if direction == "purchase" else "customer") or {}
    vkn = "".join(ch for ch in (party.get("tax_id") or "") if ch.isdigit())
    if direction not in ("purchase", "sales") or not vkn:
        return
    await record_feedback(
        session, buro_id=document.buro_id, mukellef_id=document.mukellef_id,
        feature_type=PURCHASE_FEATURE if direction == "purchase" else SALES_FEATURE,
        feature_value=vkn, label=account_label, accepted=True,
    )


@router.post("/{document_id}/approve", dependencies=[Depends(require_approver)])
async def approve_document(
    document_id: UUID,
    request: Request,
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict[str, object]:
    """Taslak fişi kesinleştirir (yalnızca müşavir/admin). Dönem kilidi ve denge kontrol edilir."""
    buro_id, mukellef_id = tenant
    document = await _tenant_document(session, document_id, buro_id, mukellef_id)
    entry = await existing_entry(session, document)
    if entry is None or entry.status != "draft":
        raise HTTPException(
            status_code=409,
            detail="Onaylanacak taslak fiş yok. Önce hesabı düzeltip taslak oluşturun.",
        )
    try:
        await approve_entry(session, buro_id=buro_id, mukellef_id=mukellef_id, entry_id=entry.id)
    except LedgerValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    document.review_status = "approved"
    document.classification_status = "classified"
    account = ((document.extraction_metadata or {}).get("posting") or {}).get("account")
    if account:
        await _learn(session, document, account)
    await audit.record(
        session, "document.approve", request=request, user=user, mukellef_id=mukellef_id,
        target_type="journal_entry", target_id=entry.id,
        details={"document_id": str(document.id)}, commit=False,
    )
    await session.commit()
    return await _queue_item(session, document)


@router.post("/{document_id}/correct")
async def correct_document(
    document_id: UUID,
    correction: CorrectionInput,
    request: Request,
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict[str, object]:
    """Ana hesabı (gider/stok veya gelir) değiştirir, taslağı yeniden üretir ve öğrenir."""
    buro_id, mukellef_id = tenant
    document = await _tenant_document(session, document_id, buro_id, mukellef_id)
    if document.review_status == "approved":
        raise HTTPException(status_code=409, detail="Onaylanmış belge düzeltilemez; ters kayıt kullanın.")
    if "invoice" not in (document.extraction_metadata or {}):
        raise HTTPException(status_code=422, detail="Bu belge türünde hesap düzeltme desteklenmiyor.")
    override = (correction.account_code, correction.account_name.strip())
    result = await post_document(session, document, account_override=override)
    if result.entry_id is None:
        raise HTTPException(status_code=422, detail=" ".join(result.reasons))
    document.review_note = correction.note
    await _learn(session, document, f"{override[0]}|{override[1]}"[:100])
    await audit.record(
        session, "document.correct", request=request, user=user, mukellef_id=mukellef_id,
        target_type="document", target_id=document.id,
        details={"account_code": override[0], "account_name": override[1]}, commit=False,
    )
    await session.commit()
    return await _queue_item(session, document)


@router.post("/{document_id}/later")
async def defer_document(
    document_id: UUID,
    request: Request,
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict[str, object]:
    """"Sonra": belgeyi onaylamadan incelemeye bırakır."""
    buro_id, mukellef_id = tenant
    document = await _tenant_document(session, document_id, buro_id, mukellef_id)
    if document.review_status == "approved":
        raise HTTPException(status_code=409, detail="Belge zaten onaylanmış.")
    document.review_status = "needs_review"
    await audit.record(
        session, "document.defer", request=request, user=user, mukellef_id=mukellef_id,
        target_type="document", target_id=document.id, commit=False,
    )
    await session.commit()
    return await _queue_item(session, document)


@router.get("/{document_id}")
async def get_document(
    document_id: UUID,
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    buro_id, mukellef_id = tenant
    await validate_tenant(session, buro_id, mukellef_id)
    document = await session.scalar(
        select(Belge).where(
            Belge.id == document_id,
            Belge.buro_id == buro_id,
            Belge.mukellef_id == mukellef_id,
        )
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Belge bulunamadı.")
    return serialize_document_detail(document)


@router.get("/{document_id}/content")
async def get_document_content(
    document_id: UUID,
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
) -> Response:
    buro_id, mukellef_id = tenant
    await validate_tenant(session, buro_id, mukellef_id)
    document = await session.scalar(
        select(Belge).where(
            Belge.id == document_id,
            Belge.buro_id == buro_id,
            Belge.mukellef_id == mukellef_id,
        )
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Belge bulunamadı.")
    try:
        content = await storage.get(document.storage_key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Belge içeriği bulunamadı.") from exc
    # Yükleyenin bildirdiği Content-Type'a güvenilmez: tür, doğrulanmış belge türünden
    # türetilir. Yalnızca PDF ve görseller tarayıcıda açılır, diğerleri indirilir.
    # sandbox CSP'si içerik tarayıcıda açılsa bile script çalışmasını engeller.
    if document.document_type == "image":
        media_type = "image/png" if content.startswith(b"\x89PNG") else "image/jpeg"
    else:
        media_type = CANONICAL_MEDIA_TYPES.get(document.document_type, "application/octet-stream")
    inline = document.document_type in {"pdf", "image"}
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": content_disposition(document.original_filename, inline=inline),
            "Content-Security-Policy": "sandbox; default-src 'none'; img-src 'self'",
            "X-Content-Type-Options": "nosniff",
            "X-Document-SHA256": document.sha256,
        },
    )


@router.post("/{document_id}/extract")
async def extract_document_endpoint(
    document_id: UUID,
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    buro_id, mukellef_id = tenant
    await validate_tenant(session, buro_id, mukellef_id)
    document = await session.scalar(
        select(Belge).where(
            Belge.id == document_id,
            Belge.buro_id == buro_id,
            Belge.mukellef_id == mukellef_id,
        )
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Belge bulunamadı.")
    try:
        document = await extract_document(session, storage, document)
    except DocumentExtractionError as exc:
        await process_after_extraction(session, document, storage)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await process_after_extraction(session, document, storage)
    await session.refresh(document)
    return serialize_document_detail(document)


@router.patch("/{document_id}/review")
async def review_document(
    document_id: UUID,
    review: DocumentReviewInput,
    request: Request,
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict[str, object]:
    buro_id, mukellef_id = tenant
    await validate_tenant(session, buro_id, mukellef_id)
    document = await session.scalar(
        select(Belge).where(
            Belge.id == document_id,
            Belge.buro_id == buro_id,
            Belge.mukellef_id == mukellef_id,
        )
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Belge bulunamadı.")
    apply_document_review(document, review)
    await audit.record(
        session, "document.review", request=request, user=user, mukellef_id=mukellef_id,
        target_type="document", target_id=document.id,
        details={"review_status": document.review_status}, commit=False,
    )
    await session.commit()
    await session.refresh(document)
    return serialize_document_detail(document)
