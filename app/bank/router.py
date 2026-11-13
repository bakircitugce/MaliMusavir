from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit
from app.auth.deps import get_current_user
from app.auth.deps import tenant_context as tenant_headers
from app.bank import (
    BankAccountInput,
    ignore_bank_transaction,
    import_bank_csv,
    import_bank_mt940,
    import_bank_xlsx,
    list_bank_accounts,
    list_bank_transactions,
    list_reconciliation_candidates,
    list_reconciliation_group_candidates,
    list_reconciliation_queue,
    set_bank_account_status,
    summarize_pos_aging,
    summarize_reconciliation,
)
from app.bank.statement_import import IbanRequiredError, import_statement
from app.bank.statement_parser import StatementParseError
from app.core.db import get_session
from app.core.models import BankAccount, BankTransaction, Mukellef, User
from app.core.safety import UnsafeArchiveError, check_zip_archive
from app.ledger.draft import LedgerValidationError

router = APIRouter(prefix="/bank", tags=["bank"])


class BankAccountRequest(BaseModel):
    iban: str
    bank_name: str
    account_name: str
    ledger_account_code: str


class ReconcileRequest(BaseModel):
    journal_entry_id: UUID


def _serialize_import_result(result: object) -> dict[str, int]:
    return {
        "created_count": result.created_count,
        "skipped_count": result.skipped_count,
    }




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


def _serialize_account(account: BankAccount) -> dict[str, object]:
    return {
        "id": str(account.id),
        "iban": account.iban,
        "bank_name": account.bank_name,
        "account_name": account.account_name,
        "ledger_account_code": account.ledger_account_code,
        "status": account.status,
    }


def _serialize_transaction(transaction: BankTransaction) -> dict[str, object]:
    return {
        "id": str(transaction.id),
        "iban": transaction.bank_account_iban,
        "external_id": transaction.external_id,
        "transaction_date": transaction.transaction_date.isoformat(),
        "description": transaction.description,
        "debit": str(transaction.debit),
        "credit": str(transaction.credit),
        "balance": str(transaction.balance) if transaction.balance is not None else None,
        "reconciliation_status": transaction.reconciliation_status,
    }


@router.post("/accounts", status_code=status.HTTP_201_CREATED)
async def create_account(
    request: BankAccountRequest,
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    buro_id, mukellef_id = tenant
    await validate_tenant(session, buro_id, mukellef_id)
    try:
        from app.bank import create_bank_account

        account = await create_bank_account(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            account=BankAccountInput(**request.model_dump()),
        )
    except LedgerValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize_account(account)


@router.get("/accounts")
async def get_accounts(
    status_filter: str | None = Query(default=None, alias="status"),
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    buro_id, mukellef_id = tenant
    await validate_tenant(session, buro_id, mukellef_id)
    try:
        accounts = await list_bank_accounts(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            status=status_filter,
        )
    except LedgerValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"accounts": [_serialize_account(account) for account in accounts]}


@router.patch("/accounts/{account_id}/status")
async def update_account_status(
    account_id: UUID,
    status_value: str = Query(alias="status"),
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    buro_id, mukellef_id = tenant
    await validate_tenant(session, buro_id, mukellef_id)
    try:
        account = await set_bank_account_status(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            account_id=account_id,
            status=status_value,
        )
    except LedgerValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize_account(account)


@router.get("/transactions")
async def get_transactions(
    iban: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    reconciliation_status: str | None = None,
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    buro_id, mukellef_id = tenant
    await validate_tenant(session, buro_id, mukellef_id)
    try:
        transactions = await list_bank_transactions(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            bank_account_iban=iban,
            start_date=start_date,
            end_date=end_date,
            reconciliation_status=reconciliation_status,
        )
    except LedgerValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"transactions": [_serialize_transaction(item) for item in transactions]}


MAX_BANK_FILE_SIZE = 10 * 1024 * 1024


async def _read_bank_file(file: UploadFile) -> bytes:
    content = await file.read(MAX_BANK_FILE_SIZE + 1)
    if len(content) > MAX_BANK_FILE_SIZE:
        raise HTTPException(status_code=413, detail="Banka dosyası 10 MB sınırını aşamaz.")
    if not content:
        raise HTTPException(status_code=400, detail="Dosya boş olamaz.")
    return content


@router.post("/import")
async def import_any_statement(
    request: Request,
    file: UploadFile,
    iban: str | None = Query(default=None, max_length=40),
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict[str, object]:
    """Excel / CSV / PDF ekstreyi biçimini kendisi tanıyarak içe aktarır.

    IBAN dosyada yoksa `iban` parametresiyle verilmelidir (422 iban_required).
    Hesap yoksa otomatik açılır (102.NN). Aynı hareket ikinci kez aktarılmaz.
    """
    buro_id, mukellef_id = tenant
    content = await _read_bank_file(file)
    if content[:2] == b"PK":
        try:
            check_zip_archive(content)
        except UnsafeArchiveError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        result = await import_statement(
            session, buro_id=buro_id, mukellef_id=mukellef_id,
            content=content, filename=file.filename or "", iban=iban,
        )
    except IbanRequiredError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "iban_required", "message": str(exc),
                    "transactions": len(exc.statement.lines),
                    "bank_name": exc.statement.bank_name},
        ) from exc
    except (StatementParseError, LedgerValidationError) as exc:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await audit.record(
        session, "bank.import", request=request, user=user, mukellef_id=mukellef_id,
        target_type="bank_account", target_id=result.iban,
        details={"filename": file.filename, "created": result.created, "skipped": result.skipped},
        commit=False,
    )
    await session.commit()
    return {**result.as_dict(), "message": result.summary()}


@router.post("/import/csv")
async def import_csv(
    file: UploadFile,
    iban: str = Query(min_length=1),
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    buro_id, mukellef_id = tenant
    await validate_tenant(session, buro_id, mukellef_id)
    content = await _read_bank_file(file)
    try:
        result = await import_bank_csv(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            csv_text=content.decode("utf-8-sig"),
            bank_account_iban=iban,
        )
    except (UnicodeDecodeError, LedgerValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize_import_result(result)


@router.post("/import/xlsx")
async def import_xlsx(
    file: UploadFile,
    iban: str = Query(min_length=1),
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    buro_id, mukellef_id = tenant
    await validate_tenant(session, buro_id, mukellef_id)
    xlsx_bytes = await _read_bank_file(file)
    try:
        check_zip_archive(xlsx_bytes)
    except UnsafeArchiveError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        result = await import_bank_xlsx(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            xlsx_bytes=xlsx_bytes,
            bank_account_iban=iban,
        )
    except LedgerValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize_import_result(result)


@router.post("/import/mt940")
async def import_mt940(
    file: UploadFile,
    iban: str = Query(min_length=1),
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    buro_id, mukellef_id = tenant
    await validate_tenant(session, buro_id, mukellef_id)
    try:
        result = await import_bank_mt940(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            mt940_text=(await _read_bank_file(file)).decode("utf-8-sig"),
            bank_account_iban=iban,
        )
    except (UnicodeDecodeError, LedgerValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize_import_result(result)


@router.get("/reconciliation/queue")
async def get_reconciliation_queue(
    iban: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    buro_id, mukellef_id = tenant
    await validate_tenant(session, buro_id, mukellef_id)
    transactions = await list_reconciliation_queue(
        session,
        buro_id=buro_id,
        mukellef_id=mukellef_id,
        bank_account_iban=iban,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
    )
    return {"transactions": [_serialize_transaction(item) for item in transactions]}


@router.get("/reconciliation/summary")
async def get_reconciliation_summary(
    iban: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    buro_id, mukellef_id = tenant
    await validate_tenant(session, buro_id, mukellef_id)
    summary = await summarize_reconciliation(
        session,
        buro_id=buro_id,
        mukellef_id=mukellef_id,
        bank_account_iban=iban,
        start_date=start_date,
        end_date=end_date,
    )
    return {
        "transaction_count": summary.transaction_count,
        "unmatched_count": summary.unmatched_count,
        "matched_count": summary.matched_count,
        "ignored_count": summary.ignored_count,
        "unmatched_amount": str(summary.unmatched_amount),
        "matched_amount": str(summary.matched_amount),
    }


@router.get("/pos/aging")
async def get_pos_aging(
    as_of_date: date | None = None,
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    buro_id, mukellef_id = tenant
    await validate_tenant(session, buro_id, mukellef_id)
    summary = await summarize_pos_aging(
        session,
        buro_id=buro_id,
        mukellef_id=mukellef_id,
        as_of_date=as_of_date or date.today(),
    )
    return {
        "as_of_date": summary.as_of_date.isoformat(),
        "total_count": summary.total_count,
        "total_amount": str(summary.total_amount),
        "buckets": [
            {
                "label": bucket.label,
                "transaction_count": bucket.transaction_count,
                "amount": str(bucket.amount),
            }
            for bucket in summary.buckets
        ],
    }


@router.get("/transactions/{transaction_id}/candidates")
async def get_candidates(
    transaction_id: UUID,
    date_window: int = Query(default=3, ge=0, le=31),
    limit: int = Query(default=20, ge=1, le=100),
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    buro_id, mukellef_id = tenant
    await validate_tenant(session, buro_id, mukellef_id)
    try:
        candidates = await list_reconciliation_candidates(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            transaction_id=transaction_id,
            date_window=date_window,
            limit=limit,
        )
        groups = await list_reconciliation_group_candidates(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            transaction_id=transaction_id,
            date_window=date_window,
            limit=limit,
        )
    except (LedgerValidationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "single": [
            {
                "journal_entry_id": str(item.journal_entry_id),
                "entry_date": item.entry_date.isoformat(),
                "description": item.description,
                "amount": str(item.amount),
                "date_distance": item.date_distance,
                "score": item.score,
            }
            for item in candidates
        ],
        "groups": [
            {
                "journal_entry_ids": [str(entry_id) for entry_id in item.journal_entry_ids],
                "entry_dates": [entry_date.isoformat() for entry_date in item.entry_dates],
                "descriptions": list(item.descriptions),
                "amount": str(item.amount),
                "max_date_distance": item.max_date_distance,
                "score": item.score,
            }
            for item in groups
        ],
    }


@router.post("/transactions/{transaction_id}/reconcile")
async def reconcile_transaction(
    transaction_id: UUID,
    request: ReconcileRequest,
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    buro_id, mukellef_id = tenant
    await validate_tenant(session, buro_id, mukellef_id)
    from app.bank import reconcile_bank_transaction

    try:
        transaction = await reconcile_bank_transaction(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            transaction_id=transaction_id,
            journal_entry_id=request.journal_entry_id,
        )
    except LedgerValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize_transaction(transaction)


@router.post("/transactions/{transaction_id}/ignore")
async def ignore_transaction(
    transaction_id: UUID,
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    buro_id, mukellef_id = tenant
    await validate_tenant(session, buro_id, mukellef_id)
    try:
        transaction = await ignore_bank_transaction(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            transaction_id=transaction_id,
        )
    except LedgerValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize_transaction(transaction)
