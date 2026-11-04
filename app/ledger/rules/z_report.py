from decimal import Decimal
from uuid import UUID

from app.documents.z_report import ZReportData
from app.ledger.draft import JournalEntryDraft, JournalLine, LedgerValidationError, validate_balanced


def build_z_report_entry(
    report: ZReportData,
    *,
    revenue_account_code: str,
    revenue_account_name: str,
    vat_account_code: str,
    vat_account_name: str,
    revenue_base: Decimal,
    vat_amount: Decimal,
    cash_account_code: str | None = None,
    cash_account_name: str = "Merkez Kasa",
    card_account_code: str | None = None,
    card_account_name: str = "POS",
    source_document_id: UUID | None = None,
) -> JournalEntryDraft:
    if report.total is None:
        raise LedgerValidationError("Z raporunda toplam bulunmalıdır.")
    if report.cash_total is None and report.card_total is None:
        raise LedgerValidationError("Z raporunda ödeme dağılımı bulunmalıdır.")
    if revenue_base < 0 or vat_amount < 0:
        raise LedgerValidationError("Z raporu matrah ve KDV tutarı negatif olamaz.")
    if revenue_base + vat_amount != report.total:
        raise LedgerValidationError(
            f"Z raporu matrah ve KDV toplamla eşleşmiyor: "
            f"matrah+KDV={revenue_base + vat_amount}, toplam={report.total}."
        )

    payment_total = sum(
        (amount for amount in (report.cash_total, report.card_total) if amount is not None),
        Decimal("0.00"),
    )
    if payment_total != report.total:
        raise LedgerValidationError(
            f"Z raporu ödeme dağılımı toplamla eşleşmiyor: "
            f"ödemeler={payment_total}, toplam={report.total}."
        )
    if report.cash_total and not cash_account_code:
        raise LedgerValidationError("Nakit ödeme için kasa hesabı bulunmalıdır.")
    if report.card_total and not card_account_code:
        raise LedgerValidationError("Kart ödemesi için POS hesabı bulunmalıdır.")

    lines: list[JournalLine] = []
    if report.cash_total:
        lines.append(
            JournalLine(cash_account_code or "", cash_account_name, debit=report.cash_total)
        )
    if report.card_total:
        lines.append(
            JournalLine(card_account_code or "", card_account_name, debit=report.card_total)
        )
    lines.extend(
        (
            JournalLine(revenue_account_code, revenue_account_name, credit=revenue_base),
            JournalLine(vat_account_code, vat_account_name, credit=vat_amount),
        )
    )
    entry = JournalEntryDraft(
        entry_date=_report_date(report),
        description=f"Z raporu {report.report_number or ''}".strip(),
        lines=tuple(lines),
        source_document_id=source_document_id,
        rule_name="z_report",
        rule_version="1",
        confidence=Decimal("0.95"),
    )
    return validate_balanced(entry)


def _report_date(report: ZReportData):
    from datetime import date

    if report.report_date is None:
        raise LedgerValidationError("Z raporunda tarih bulunmalıdır.")
    try:
        return date.fromisoformat(report.report_date)
    except ValueError as exc:
        raise LedgerValidationError("Z raporu tarihi ISO formatında olmalıdır.") from exc
