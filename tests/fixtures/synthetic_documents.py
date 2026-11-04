from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class SyntheticFixture:
    fixture_id: str
    category: str
    text: str
    expected_total: Decimal


def build_z_report_fixtures(count: int = 50) -> tuple[SyntheticFixture, ...]:
    return tuple(
        SyntheticFixture(
            fixture_id=f"z-report-{index:03d}",
            category="z_report",
            text=(
                f"Z RAPORU 2026-11-{(index % 28) + 1:02d} Z NO: {index} "
                "ÖKC SİCİL NO: DEMO123 TOPLAM: 1.200,00 "
                "NAKİT: 500,00 KREDİ KARTI: 700,00"
            ),
            expected_total=Decimal("1200.00"),
        )
        for index in range(1, count + 1)
    )


def build_expense_slip_fixtures(count: int = 50) -> tuple[SyntheticFixture, ...]:
    return tuple(
        SyntheticFixture(
            fixture_id=f"expense-slip-{index:03d}",
            category="expense_slip",
            text=(
                f"GİDER PUSULASI 2026-11-{(index % 28) + 1:02d} "
                f"SATICI: Örnek Hizmet Sağlayıcı {index} VKN: 1234567890 "
                "AÇIKLAMA: Nakliye MATRAH: 1.000,00 KDV: 200,00 TOPLAM: 1.200,00"
            ),
            expected_total=Decimal("1200.00"),
        )
        for index in range(1, count + 1)
    )


def build_smm_fixtures(count: int = 50) -> tuple[SyntheticFixture, ...]:
    return tuple(
        SyntheticFixture(
            fixture_id=f"smm-{index:03d}",
            category="smm",
            text=(
                f"SERBEST MESLEK MAKBUZ NO: S-{index} 2026-11-{(index % 28) + 1:02d} "
                f"DÜZENLEYEN: Örnek Mali Danışman {index} VKN: 1234567890 "
                "MÜŞTERİ: Örnek İşletme BRÜT: 1.000,00 STOPAJ: 200,00 "
                "KDV: 200,00 NET: 1.000,00"
            ),
            expected_total=Decimal("1000.00"),
        )
        for index in range(1, count + 1)
    )


def build_invoice_fixtures(count: int = 50) -> tuple[SyntheticFixture, ...]:
    return tuple(
        SyntheticFixture(
            fixture_id=f"invoice-{index:03d}",
            category="invoice",
            text=(
                f"E-FATURA FATURA NO: DEMO-{index:03d} 2026-11-{(index % 28) + 1:02d} "
                "SATICI: Örnek Tedarikçi MATRAH: 2.000,00 KDV: 400,00 TOPLAM: 2.400,00"
            ),
            expected_total=Decimal("2400.00"),
        )
        for index in range(1, count + 1)
    )


def build_bank_statement_fixtures(count: int = 50) -> tuple[SyntheticFixture, ...]:
    return tuple(
        SyntheticFixture(
            fixture_id=f"bank-statement-{index:03d}",
            category="bank_statement",
            text=(
                f"BANKA EKSTRESİ 2026-11-{(index % 28) + 1:02d} "
                "IBAN: TR00 0000 0000 0000 0000 0000 00 "
                "İŞLEM TARİHİ: 2026-11-03 AÇIKLAMA: Örnek hareket "
                "BAKİYE: 5.000,00"
            ),
            expected_total=Decimal("5000.00"),
        )
        for index in range(1, count + 1)
    )


def build_all_document_fixtures(count: int = 50) -> tuple[SyntheticFixture, ...]:
    return (
        build_z_report_fixtures(count)
        + build_expense_slip_fixtures(count)
        + build_smm_fixtures(count)
        + build_invoice_fixtures(count)
        + build_bank_statement_fixtures(count)
    )
