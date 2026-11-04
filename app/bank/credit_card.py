from dataclasses import replace
from datetime import date
from decimal import Decimal
from uuid import UUID

from app.bank.pos import build_pos_settlement_entry
from app.ledger.draft import JournalEntryDraft


def build_credit_card_settlement_entry(
    *,
    settlement_date: date,
    reference: str,
    gross_amount: Decimal,
    net_amount: Decimal,
    commission_amount: Decimal,
    bank_account_code: str,
    bank_account_name: str,
    commission_account_code: str,
    commission_account_name: str,
    credit_card_account_code: str,
    credit_card_account_name: str,
    source_document_id: UUID | None = None,
) -> JournalEntryDraft:
    entry = build_pos_settlement_entry(
        settlement_date=settlement_date,
        reference=reference,
        gross_amount=gross_amount,
        net_amount=net_amount,
        commission_amount=commission_amount,
        bank_account_code=bank_account_code,
        bank_account_name=bank_account_name,
        commission_account_code=commission_account_code,
        commission_account_name=commission_account_name,
        pos_account_code=credit_card_account_code,
        pos_account_name=credit_card_account_name,
        source_document_id=source_document_id,
    )
    return replace(
        entry,
        description=f"Kredi kartı mahsubu {reference}",
        rule_name="credit_card_settlement",
    )
