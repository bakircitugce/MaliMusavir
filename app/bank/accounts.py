from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import BankAccount
from app.bank.identifiers import normalize_iban
from app.ledger.draft import LedgerValidationError


@dataclass(frozen=True)
class BankAccountInput:
    iban: str
    bank_name: str
    account_name: str
    ledger_account_code: str


async def create_bank_account(
    session: AsyncSession,
    *,
    buro_id: UUID,
    mukellef_id: UUID,
    account: BankAccountInput,
) -> BankAccount:
    iban = normalize_iban(account.iban)
    if not account.bank_name.strip() or not account.account_name.strip():
        raise LedgerValidationError("Banka ve hesap adı boş olamaz.")
    if not account.ledger_account_code.strip():
        raise LedgerValidationError("Banka muhasebe hesap kodu boş olamaz.")
    existing = await session.execute(
        select(BankAccount.id).where(
            BankAccount.buro_id == buro_id,
            BankAccount.mukellef_id == mukellef_id,
            BankAccount.iban == iban,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise LedgerValidationError("Bu IBAN için banka hesabı zaten kayıtlı.")
    bank_account = BankAccount(
        buro_id=buro_id,
        mukellef_id=mukellef_id,
        iban=iban,
        bank_name=account.bank_name,
        account_name=account.account_name,
        ledger_account_code=account.ledger_account_code,
        status="active",
    )
    session.add(bank_account)
    await session.flush()
    return bank_account


async def list_bank_accounts(
    session: AsyncSession,
    *,
    buro_id: UUID,
    mukellef_id: UUID,
    status: str | None = None,
) -> list[BankAccount]:
    statement = select(BankAccount).where(
        BankAccount.buro_id == buro_id,
        BankAccount.mukellef_id == mukellef_id,
    )
    if status is not None:
        if status not in {"active", "inactive"}:
            raise LedgerValidationError("Geçersiz banka hesabı durumu.")
        statement = statement.where(BankAccount.status == status)
    statement = statement.order_by(BankAccount.bank_name, BankAccount.account_name)
    result = await session.execute(statement)
    return list(result.scalars().all())


async def set_bank_account_status(
    session: AsyncSession,
    *,
    buro_id: UUID,
    mukellef_id: UUID,
    account_id: UUID,
    status: str,
) -> BankAccount:
    if status not in {"active", "inactive"}:
        raise LedgerValidationError("Geçersiz banka hesabı durumu.")
    result = await session.execute(
        select(BankAccount).where(
            BankAccount.id == account_id,
            BankAccount.buro_id == buro_id,
            BankAccount.mukellef_id == mukellef_id,
        )
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise LedgerValidationError("Banka hesabı bulunamadı.")
    account.status = status
    await session.flush()
    return account
