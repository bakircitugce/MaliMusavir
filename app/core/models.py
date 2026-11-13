from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    JSON,
    DateTime,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def uuid_column() -> Mapped[UUID]:
    return mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TenantModel(Base, TimestampMixin):
    __abstract__ = True
    id: Mapped[UUID] = uuid_column()
    buro_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)


class Buro(Base, TimestampMixin):
    __tablename__ = "burolar"

    id: Mapped[UUID] = uuid_column()
    unvan: Mapped[str] = mapped_column(String(200))


class User(Base, TimestampMixin):
    __tablename__ = "kullanicilar"

    id: Mapped[UUID] = uuid_column()
    buro_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    ad_soyad: Mapped[str] = mapped_column(String(200))
    rol: Mapped[str] = mapped_column(String(30), default="personel")
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    # Şifre değişikliği / çıkışta artırılır; eski token'lar geçersiz olur.
    token_version: Mapped[int] = mapped_column(default=0, server_default="0")
    failed_login_count: Mapped[int] = mapped_column(default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KullaniciMukellef(Base, TimestampMixin):
    """Personel ve mükellef rolündeki kullanıcıların erişebildiği mükellefler."""

    __tablename__ = "kullanici_mukellefler"
    __table_args__ = (
        UniqueConstraint("user_id", "mukellef_id", name="uq_kullanici_mukellef"),
    )

    id: Mapped[UUID] = uuid_column()
    buro_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("kullanicilar.id", ondelete="CASCADE"), index=True
    )
    mukellef_id: Mapped[UUID] = mapped_column(
        ForeignKey("mukellefler.id", ondelete="CASCADE"), index=True
    )


class Mukellef(TenantModel):
    __tablename__ = "mukellefler"

    vkn_tckn: Mapped[str] = mapped_column(String(11), index=True)
    unvan: Mapped[str] = mapped_column(String(250))
    vergi_dairesi: Mapped[str | None] = mapped_column(String(150))
    mukellef_turu: Mapped[str] = mapped_column(String(20))
    muhasebe_sistemi: Mapped[str] = mapped_column(String(3), default="7A")
    nace_kodlari: Mapped[list[str]] = mapped_column(JSON, default=list)
    defter_turu: Mapped[str | None] = mapped_column(String(50))
    e_fatura_durumu: Mapped[str | None] = mapped_column(String(30))
    e_arsiv_durumu: Mapped[str | None] = mapped_column(String(30))
    e_defter_durumu: Mapped[str | None] = mapped_column(String(30))


class TaxParameter(TenantModel):
    __tablename__ = "tax_parameters"

    anahtar: Mapped[str] = mapped_column(String(100), index=True)
    deger: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    baslangic_tarihi: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    bitis_tarihi: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class AccountingPeriod(TenantModel):
    __tablename__ = "muhasebe_donemleri"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'locked')",
            name="ck_muhasebe_donemi_status",
        ),
        UniqueConstraint(
            "mukellef_id",
            "start_date",
            "end_date",
            name="uq_muhasebe_donemi_mukellef_tarih",
        ),
    )

    mukellef_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="open")


class Hesap(TenantModel):
    __tablename__ = "hesaplar"
    __table_args__ = (UniqueConstraint("mukellef_id", "kod", name="uq_hesap_mukellef_kod"),)

    kod: Mapped[str] = mapped_column(String(30), index=True)
    mukellef_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    ad: Mapped[str] = mapped_column(String(250))
    seviye: Mapped[int] = mapped_column()
    ust_kod: Mapped[str | None] = mapped_column(String(30))
    acilis_borcu: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=0)
    acilis_alacagi: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=0)


class NaceCode(Base):
    __tablename__ = "nace_codes"
    __table_args__ = (UniqueConstraint("kod", "kaynak", name="uq_nace_kod_kaynak"),)

    id: Mapped[UUID] = uuid_column()
    kod: Mapped[str] = mapped_column(String(10), index=True)
    tanim: Mapped[str] = mapped_column(String(500))
    kaynak: Mapped[str] = mapped_column(String(30))


class Belge(TenantModel):
    __tablename__ = "belgeler"
    __table_args__ = (
        UniqueConstraint(
            "buro_id",
            "mukellef_id",
            "sha256",
            name="uq_belge_tenant_sha256",
        ),
    )

    mukellef_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    storage_key: Mapped[str] = mapped_column(String(500), unique=True)
    document_type: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), default="received")
    extraction_status: Mapped[str] = mapped_column(String(30), default="pending")
    extracted_text: Mapped[str | None] = mapped_column(String, nullable=True)
    extraction_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    classification_status: Mapped[str] = mapped_column(String(30), default="pending")
    document_category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    classification_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    classification_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    review_status: Mapped[str] = mapped_column(String(30), default="pending")
    review_note: Mapped[str | None] = mapped_column(String(2000), nullable=True)


class JournalEntry(TenantModel):
    __tablename__ = "yevmiye_fisleri"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'pending_review', 'approved', 'reversed')",
            name="ck_yevmiye_fisi_status",
        ),
    )

    mukellef_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    entry_date: Mapped[date] = mapped_column(Date)
    description: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(30), default="draft")
    source_document_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("belgeler.id", ondelete="SET NULL"), nullable=True, index=True
    )
    rule_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    rule_version: Mapped[str | None] = mapped_column(String(30), nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)


class JournalLine(TenantModel):
    __tablename__ = "yevmiye_satirlari"
    __table_args__ = (
        CheckConstraint(
            "(debit > 0 AND credit = 0) OR (debit = 0 AND credit > 0)",
            name="ck_yevmiye_satiri_one_side",
        ),
    )

    mukellef_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    journal_entry_id: Mapped[UUID] = mapped_column(
        ForeignKey("yevmiye_fisleri.id", ondelete="CASCADE"), index=True
    )
    line_order: Mapped[int] = mapped_column()
    account_code: Mapped[str] = mapped_column(String(30), index=True)
    account_name: Mapped[str] = mapped_column(String(250))
    debit: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=0)
    credit: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=0)


class ERPExport(TenantModel):
    __tablename__ = "erp_aktarimlari"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'exported', 'failed')",
            name="ck_erp_aktarimi_status",
        ),
        UniqueConstraint(
            "buro_id",
            "mukellef_id",
            "journal_entry_id",
            "idempotency_key",
            name="uq_erp_aktarimi_entry_key",
        ),
    )

    mukellef_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    journal_entry_id: Mapped[UUID] = mapped_column(
        ForeignKey("yevmiye_fisleri.id", ondelete="CASCADE"), index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    external_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)


class LearnedAssociation(TenantModel):
    __tablename__ = "ogrenilen_iliskiler"
    __table_args__ = (
        UniqueConstraint(
            "buro_id",
            "mukellef_id",
            "feature_type",
            "feature_hash",
            "label",
            name="uq_ogrenilen_iliski",
        ),
        CheckConstraint(
            "positive_count >= 0 AND negative_count >= 0",
            name="ck_ogrenilen_iliski_counts",
        ),
    )

    mukellef_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    feature_type: Mapped[str] = mapped_column(String(50), index=True)
    feature_hash: Mapped[str] = mapped_column(String(64), index=True)
    label: Mapped[str] = mapped_column(String(100), index=True)
    positive_count: Mapped[int] = mapped_column(default=0)
    negative_count: Mapped[int] = mapped_column(default=0)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=0)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class BankTransaction(TenantModel):
    __tablename__ = "banka_hareketleri"
    __table_args__ = (
        CheckConstraint(
            "(debit > 0 AND credit = 0) OR (debit = 0 AND credit > 0)",
            name="ck_banka_hareketi_one_side",
        ),
        CheckConstraint(
            "reconciliation_status IN ('unmatched', 'matched', 'ignored')",
            name="ck_banka_hareketi_reconciliation_status",
        ),
        UniqueConstraint(
            "buro_id",
            "mukellef_id",
            "bank_account_iban",
            "external_id",
            name="uq_banka_hareketi_external_id",
        ),
    )

    mukellef_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    bank_account_iban: Mapped[str] = mapped_column(String(34), index=True)
    external_id: Mapped[str] = mapped_column(String(150))
    transaction_date: Mapped[date] = mapped_column(Date)
    value_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    description: Mapped[str] = mapped_column(String(500))
    counterparty_name: Mapped[str | None] = mapped_column(String(250), nullable=True)
    counterparty_tax_id: Mapped[str | None] = mapped_column(String(11), nullable=True)
    debit: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=0)
    credit: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=0)
    balance: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    reconciliation_status: Mapped[str] = mapped_column(
        String(20), default="unmatched"
    )
    matched_journal_entry_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("yevmiye_fisleri.id", ondelete="SET NULL"), nullable=True, index=True
    )


class BankAccount(TenantModel):
    __tablename__ = "banka_hesaplari"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'inactive')",
            name="ck_banka_hesabi_status",
        ),
        UniqueConstraint(
            "buro_id",
            "mukellef_id",
            "iban",
            name="uq_banka_hesabi_tenant_iban",
        ),
    )

    mukellef_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    iban: Mapped[str] = mapped_column(String(34), index=True)
    bank_name: Mapped[str] = mapped_column(String(150))
    account_name: Mapped[str] = mapped_column(String(150))
    ledger_account_code: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="active")
