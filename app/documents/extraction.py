from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class PartyData:
    name: str | None
    tax_id: str | None
    tax_id_scheme: str | None


@dataclass(frozen=True)
class InvoiceLineData:
    line_id: str | None
    description: str | None
    quantity: Decimal | None
    unit_code: str | None
    line_extension_amount: Decimal | None
    tax_percent: Decimal | None
    item_code: str | None


@dataclass(frozen=True)
class InvoiceData:
    invoice_id: str
    issue_date: str
    invoice_type_code: str | None
    profile_id: str | None
    currency: str | None
    supplier: PartyData
    customer: PartyData
    tax_exclusive_amount: Decimal | None
    tax_inclusive_amount: Decimal | None
    payable_amount: Decimal | None
    tax_total: Decimal | None
    lines: tuple[InvoiceLineData, ...]
