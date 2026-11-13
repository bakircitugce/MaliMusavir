"""Ekstre dosyasını (Excel/CSV/PDF) okuyup banka hareketi olarak içe aktarır."""

from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bank.accounts import BankAccountInput, create_bank_account
from app.bank.identifiers import normalize_iban
from app.bank.import_service import import_bank_transactions
from app.bank.statement_parser import ParsedStatement, StatementParseError, parse_statement
from app.bank.transactions import BankTransactionInput
from app.core.models import BankAccount


class IbanRequiredError(StatementParseError):
    """Ekstrede IBAN yok; kullanıcının hesabı belirtmesi gerekir."""

    def __init__(self, statement: ParsedStatement) -> None:
        super().__init__(
            f"Ekstre okundu ({len(statement.lines)} hareket) ancak dosyada IBAN yok. "
            "Hangi banka hesabına aktarılacağını belirtin."
        )
        self.statement = statement


@dataclass
class StatementImportResult:
    iban: str
    bank_name: str
    ledger_account_code: str
    account_created: bool
    source_format: str
    total: int
    created: int
    skipped: int
    inflow: Decimal
    outflow: Decimal
    period: tuple[str, str] | None
    balance_checked: int
    balance_mismatches: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "iban": self.iban,
            "bank_name": self.bank_name,
            "ledger_account_code": self.ledger_account_code,
            "account_created": self.account_created,
            "source_format": self.source_format,
            "total": self.total,
            "created": self.created,
            "skipped": self.skipped,
            "inflow": str(self.inflow),
            "outflow": str(self.outflow),
            "period": list(self.period) if self.period else None,
            "balance_checked": self.balance_checked,
            "balance_ok": self.balance_checked - len(self.balance_mismatches),
            "balance_mismatches": self.balance_mismatches[:20],
            "warnings": self.warnings,
        }

    def summary(self) -> str:
        text = (
            f"{self.bank_name} ({self.ledger_account_code}) ekstresinden {self.created} hareket "
            f"aktarıldı"
        )
        if self.skipped:
            text += f", {self.skipped} hareket zaten kayıtlıydı"
        if self.balance_checked:
            text += (
                f"; bakiye zinciri {self.balance_checked - len(self.balance_mismatches)}/"
                f"{self.balance_checked} doğrulandı"
            )
        return text + "."


async def _ensure_account(
    session: AsyncSession, buro_id: UUID, mukellef_id: UUID, iban: str, bank_name: str
) -> tuple[BankAccount, bool]:
    account = await session.scalar(
        select(BankAccount).where(
            BankAccount.buro_id == buro_id,
            BankAccount.mukellef_id == mukellef_id,
            BankAccount.iban == iban,
        )
    )
    if account is not None:
        return account, False
    count = await session.scalar(
        select(func.count(BankAccount.id)).where(
            BankAccount.buro_id == buro_id, BankAccount.mukellef_id == mukellef_id
        )
    )
    account = await create_bank_account(
        session,
        buro_id=buro_id,
        mukellef_id=mukellef_id,
        account=BankAccountInput(
            iban=iban,
            bank_name=bank_name,
            account_name="Vadesiz TL",
            ledger_account_code=f"102.{(count or 0) + 1:02d}",
        ),
    )
    return account, True


async def import_statement(
    session: AsyncSession,
    *,
    buro_id: UUID,
    mukellef_id: UUID,
    content: bytes,
    filename: str,
    iban: str | None = None,
) -> StatementImportResult:
    """Ekstreyi okur, hesabı (yoksa) oluşturur ve hareketleri mükerrersiz aktarır.

    Commit çağırana aittir.
    """
    statement = parse_statement(content, filename)
    target_iban = normalize_iban(iban) if iban else statement.iban
    if not target_iban:
        raise IbanRequiredError(statement)
    if statement.iban and iban and normalize_iban(iban) != statement.iban:
        raise StatementParseError(
            f"Seçilen IBAN ({normalize_iban(iban)}) ekstredeki IBAN ile ({statement.iban}) uyuşmuyor."
        )
    bank_name = statement.bank_name or "Banka"
    account, created_account = await _ensure_account(
        session, buro_id, mukellef_id, target_iban, bank_name
    )
    inputs = [
        BankTransactionInput(
            bank_account_iban=target_iban,
            external_id=line.external_id,
            transaction_date=line.transaction_date,
            description=line.description,
            debit=line.amount if line.amount > 0 else Decimal("0.00"),
            credit=-line.amount if line.amount < 0 else Decimal("0.00"),
            balance=line.balance,
        )
        for line in statement.lines
    ]
    result = await import_bank_transactions(
        session, buro_id=buro_id, mukellef_id=mukellef_id, transactions=inputs
    )
    period = statement.period
    return StatementImportResult(
        iban=target_iban,
        bank_name=account.bank_name,
        ledger_account_code=account.ledger_account_code,
        account_created=created_account,
        source_format=statement.source_format,
        total=len(inputs),
        created=result.created_count,
        skipped=result.skipped_count,
        inflow=sum((line.amount for line in statement.lines if line.amount > 0), Decimal("0")),
        outflow=sum((-line.amount for line in statement.lines if line.amount < 0), Decimal("0")),
        period=(period[0].isoformat(), period[1].isoformat()) if period else None,
        balance_checked=statement.balance_checked,
        balance_mismatches=statement.balance_mismatches,
        warnings=statement.warnings,
    )
