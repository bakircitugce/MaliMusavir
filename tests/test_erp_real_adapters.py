import json
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.erp import (
    ERPExportDocument,
    LogoTigerERPExportAdapter,
    UniversalCSVERPExportAdapter,
    erp_registry,
)


@pytest.mark.asyncio
async def test_logo_tiger_adapter_produces_valid_xml() -> None:
    adapter = LogoTigerERPExportAdapter()
    payload = json.dumps(
        {
            "entry_date": "2026-11-04",
            "description": "Satış Faturası Fişi",
            "lines": [
                {
                    "order": 1,
                    "account_code": "120.01.001",
                    "account_name": "Alıcılar",
                    "debit": "1200.00",
                    "credit": "0.00",
                },
                {
                    "order": 2,
                    "account_code": "600.01.001",
                    "account_name": "Yurtiçi Satışlar",
                    "debit": "0.00",
                    "credit": "1000.00",
                },
                {
                    "order": 3,
                    "account_code": "391.01.020",
                    "account_name": "Hesaplanan KDV %20",
                    "debit": "0.00",
                    "credit": "200.00",
                },
            ],
        }
    ).encode("utf-8")

    key = "TEST-TENANT:001"
    doc = ERPExportDocument(idempotency_key=key, journal_entry_id=uuid4(), payload=payload)

    first = await adapter.export_entry(doc)
    assert first.was_duplicate is False
    assert first.external_reference.startswith("LOGO-GL-")

    # Idempotency check
    second = await adapter.export_entry(doc)
    assert second.was_duplicate is True
    assert second.external_reference == first.external_reference

    # Verify generated XML
    xml = adapter.get_generated_xml(key)
    assert xml is not None
    assert "<GL_SLIPS>" in xml
    assert "<GL_CODE>120.01.001</GL_CODE>" in xml
    assert "<SIGN>0</SIGN>" in xml  # Borç
    assert "<GL_CODE>600.01.001</GL_CODE>" in xml
    assert "<SIGN>1</SIGN>" in xml  # Alacak
    assert "<TOTAL_DEBIT>1200.00</TOTAL_DEBIT>" in xml
    assert "<TOTAL_CREDIT>1200.00</TOTAL_CREDIT>" in xml


@pytest.mark.asyncio
async def test_universal_csv_adapter_produces_valid_csv() -> None:
    adapter = UniversalCSVERPExportAdapter()
    payload = json.dumps(
        {
            "entry_date": "2026-11-04",
            "description": "Banka Gideri",
            "lines": [
                {
                    "order": 1,
                    "account_code": "770.01.001",
                    "account_name": "Genel Yönetim",
                    "debit": "50.00",
                    "credit": "0.00",
                },
                {
                    "order": 2,
                    "account_code": "102.01.001",
                    "account_name": "Banka",
                    "debit": "0.00",
                    "credit": "50.00",
                },
            ],
        }
    ).encode("utf-8")

    key = "TEST-TENANT:CSV-001"
    doc = ERPExportDocument(idempotency_key=key, journal_entry_id=uuid4(), payload=payload)

    result = await adapter.export_entry(doc)
    assert result.was_duplicate is False
    assert result.external_reference.startswith("CSV-GL-")

    csv = adapter.get_generated_csv(key)
    assert csv is not None
    assert "TARIH;FIS_NO;HESAP_KODU;HESAP_ADI;ACIKLAMA;BORC;ALACAK" in csv
    assert "770.01.001;Genel Yönetim;Banka Gideri;50.00;0.00" in csv


def test_global_erp_registry_contains_real_adapters() -> None:
    providers = erp_registry.providers()
    assert "fake" in providers
    assert "logo" in providers
    assert "universal_csv" in providers

    logo_adapter = erp_registry.create("logo")
    assert isinstance(logo_adapter, LogoTigerERPExportAdapter)

    csv_adapter = erp_registry.create("universal_csv")
    assert isinstance(csv_adapter, UniversalCSVERPExportAdapter)
