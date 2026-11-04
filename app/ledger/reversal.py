from app.ledger.draft import JournalEntryDraft, JournalLine, validate_balanced


def reverse_entry_draft(entry: JournalEntryDraft) -> JournalEntryDraft:
    reversed_entry = JournalEntryDraft(
        entry_date=entry.entry_date,
        description=f"Ters kayıt: {entry.description}",
        lines=tuple(
            JournalLine(
                account_code=line.account_code,
                account_name=line.account_name,
                debit=line.credit,
                credit=line.debit,
            )
            for line in entry.lines
        ),
        source_document_id=entry.source_document_id,
        rule_name=f"{entry.rule_name}.reversal" if entry.rule_name else "reversal",
        rule_version=entry.rule_version,
        confidence=entry.confidence,
    )
    return validate_balanced(reversed_entry)
