from io import BytesIO, StringIO
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bank.csv_import import parse_bank_csv, parse_bank_xlsx
from app.bank.identifiers import normalize_iban
from app.bank.import_service import BankImportResult, import_bank_transactions
from app.bank.mt940_import import parse_bank_mt940
from app.core.models import BankAccount
from app.ledger.draft import LedgerValidationError


async def import_bank_csv(
    session: AsyncSession,
    *,
    buro_id: UUID,
    mukellef_id: UUID,
    csv_text: str,
    bank_account_iban: str,
) -> BankImportResult:
    bank_account_iban = normalize_iban(bank_account_iban)
    account_result = await session.execute(
        select(BankAccount).where(
            BankAccount.buro_id == buro_id,
            BankAccount.mukellef_id == mukellef_id,
            BankAccount.iban == bank_account_iban,
            BankAccount.status == "active",
        )
    )
    if account_result.scalar_one_or_none() is None:
        raise LedgerValidationError("Aktif banka hesabı bulunamadı.")
    transactions = parse_bank_csv(
        StringIO(csv_text),
        bank_account_iban=bank_account_iban,
    )
    return await import_bank_transactions(
        session,
        buro_id=buro_id,
        mukellef_id=mukellef_id,
        transactions=transactions,
    )


async def import_bank_xlsx(
    session: AsyncSession,
    *,
    buro_id: UUID,
    mukellef_id: UUID,
    xlsx_bytes: bytes,
    bank_account_iban: str,
) -> BankImportResult:
    bank_account_iban = normalize_iban(bank_account_iban)
    account_result = await session.execute(
        select(BankAccount).where(
            BankAccount.buro_id == buro_id,
            BankAccount.mukellef_id == mukellef_id,
            BankAccount.iban == bank_account_iban,
            BankAccount.status == "active",
        )
    )
    if account_result.scalar_one_or_none() is None:
        raise LedgerValidationError("Aktif banka hesabı bulunamadı.")
    transactions = parse_bank_xlsx(
        BytesIO(xlsx_bytes),
        bank_account_iban=bank_account_iban,
    )
    return await import_bank_transactions(
        session,
        buro_id=buro_id,
        mukellef_id=mukellef_id,
        transactions=transactions,
    )


async def import_bank_mt940(
    session: AsyncSession,
    *,
    buro_id: UUID,
    mukellef_id: UUID,
    mt940_text: str,
    bank_account_iban: str,
) -> BankImportResult:
    bank_account_iban = normalize_iban(bank_account_iban)
    account_result = await session.execute(
        select(BankAccount).where(
            BankAccount.buro_id == buro_id,
            BankAccount.mukellef_id == mukellef_id,
            BankAccount.iban == bank_account_iban,
            BankAccount.status == "active",
        )
    )
    if account_result.scalar_one_or_none() is None:
        raise LedgerValidationError("Aktif banka hesabı bulunamadı.")
    transactions = parse_bank_mt940(
        mt940_text,
        bank_account_iban=bank_account_iban,
    )
    return await import_bank_transactions(
        session,
        buro_id=buro_id,
        mukellef_id=mukellef_id,
        transactions=transactions,
    )
