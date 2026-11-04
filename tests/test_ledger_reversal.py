from datetime import date
from decimal import Decimal

from app.ledger import JournalEntryDraft, JournalLine, reverse_entry_draft, validate_balanced


def test_reversal_swaps_debit_and_credit_without_mutating_original() -> None:
    original = JournalEntryDraft(
        entry_date=date(2026, 11, 3),
        description="Satış faturası S-1",
        lines=(
            JournalLine("120.01.001", "Alıcı", debit=Decimal("120.00")),
            JournalLine("600.03.001", "Satış", credit=Decimal("100.00")),
            JournalLine("391.03.001", "Hesaplanan KDV", credit=Decimal("20.00")),
        ),
        rule_name="sales_invoice",
        rule_version="1",
        confidence=Decimal("0.95"),
    )

    reversal = reverse_entry_draft(original)

    assert validate_balanced(original) is original
    assert reversal is not original
    assert reversal.description == "Ters kayıt: Satış faturası S-1"
    assert reversal.rule_name == "sales_invoice.reversal"
    assert [(line.debit, line.credit) for line in reversal.lines] == [
        (Decimal("0.00"), Decimal("120.00")),
        (Decimal("100.00"), Decimal("0.00")),
        (Decimal("20.00"), Decimal("0.00")),
    ]
