from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID


CENT = Decimal("0.01")


class LedgerValidationError(ValueError):
    pass


def _money(value: Decimal) -> Decimal:
    if not isinstance(value, Decimal):
        raise LedgerValidationError("Yevmiye tutarları Decimal olmalıdır.")
    if value < 0:
        raise LedgerValidationError("Yevmiye tutarı negatif olamaz.")
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class JournalLine:
    account_code: str
    account_name: str
    debit: Decimal = Decimal("0.00")
    credit: Decimal = Decimal("0.00")

    def __post_init__(self) -> None:
        if not self.account_code.strip():
            raise LedgerValidationError("Hesap kodu boş olamaz.")
        if not self.account_name.strip():
            raise LedgerValidationError("Hesap adı boş olamaz.")
        debit = _money(self.debit)
        credit = _money(self.credit)
        if debit and credit:
            raise LedgerValidationError("Bir satırda hem borç hem alacak olamaz.")
        if not debit and not credit:
            raise LedgerValidationError("Bir satırda borç veya alacak bulunmalıdır.")
        object.__setattr__(self, "debit", debit)
        object.__setattr__(self, "credit", credit)


@dataclass(frozen=True)
class JournalEntryDraft:
    entry_date: date
    description: str
    lines: tuple[JournalLine, ...]
    source_document_id: UUID | None = None
    rule_name: str | None = None
    rule_version: str | None = None
    confidence: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise LedgerValidationError("Yevmiye açıklaması boş olamaz.")
        if len(self.lines) < 2:
            raise LedgerValidationError("Yevmiye fişi en az iki satır içermelidir.")
        if self.confidence is not None and not Decimal("0") <= self.confidence <= Decimal("1"):
            raise LedgerValidationError("Güven skoru 0 ile 1 arasında olmalıdır.")

    @property
    def total_debit(self) -> Decimal:
        return sum((line.debit for line in self.lines), Decimal("0.00")).quantize(CENT)

    @property
    def total_credit(self) -> Decimal:
        return sum((line.credit for line in self.lines), Decimal("0.00")).quantize(CENT)


def validate_balanced(entry: JournalEntryDraft) -> JournalEntryDraft:
    if entry.total_debit != entry.total_credit:
        raise LedgerValidationError(
            f"Yevmiye fişi dengede değil: borç={entry.total_debit}, "
            f"alacak={entry.total_credit}."
        )
    return entry
