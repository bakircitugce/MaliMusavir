from uuid import uuid4

import pytest

from app.erp import ERPExportDocument, InMemoryERPExportAdapter


def test_erp_export_document_validates_required_fields() -> None:
    with pytest.raises(ValueError, match="idempotency"):
        ERPExportDocument("", uuid4(), b"payload")
    with pytest.raises(ValueError, match="boş"):
        ERPExportDocument("ENTRY-001", uuid4(), b"")


@pytest.mark.asyncio
async def test_erp_fake_adapter_is_idempotent() -> None:
    adapter = InMemoryERPExportAdapter()
    document = ERPExportDocument("ENTRY-001", uuid4(), b"journal-payload")

    first = await adapter.export_entry(document)
    second = await adapter.export_entry(document)

    assert first.external_reference == second.external_reference
    assert first.was_duplicate is False
    assert second.was_duplicate is True
