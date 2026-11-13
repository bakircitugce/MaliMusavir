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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit
from app.auth.deps import get_current_user
from app.auth.deps import tenant_context as tenant_headers
from app.core.db import get_session
from app.core.models import Belge, Mukellef, User
from app.core.safety import content_disposition
from app.documents.extractor import DocumentExtractionError, extract_document
from app.documents.queue import enqueue_extraction
from app.documents.review import DocumentReviewInput, apply_document_review
from app.documents.service import (
    CANONICAL_MEDIA_TYPES,
    MAX_FILE_SIZE,
    MAX_FILES_PER_UPLOAD,
    DuplicateDocumentError,
    receive_document,
)
from app.documents.storage import LocalStorage

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
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
