"""JSON sütunlarını PostgreSQL'de JSONB'ye dönüştür (SQLite'ta no-op)

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-15 19:30:00.000000

Modelde JSON sütunları `JSON().with_variant(JSONB, "postgresql")` olarak tanımlıdır; bu migration
mevcut PostgreSQL veritabanlarındaki altı sütunun tipini `json` -> `jsonb`'ye taşır. SQLite'ta
JSON ile JSONB arasında tip ayrımı yoktur (değerler TEXT olarak saklanır), bu yüzden SQLite'ta
migration bilinçli olarak hiçbir şey yapmaz (no-op); şema yapısı ve veriler değişmez.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (tablo, sütun) — modeldeki JSONVariant sütunlarıyla birebir aynı.
JSON_COLUMNS: tuple[tuple[str, str], ...] = (
    ("denetim_kayitlari", "details"),
    ("mukellefler", "nace_kodlari"),
    ("tax_parameters", "metadata_json"),
    ("belgeler", "extraction_metadata"),
    ("belgeler", "classification_reasons"),
    ("ogrenilen_iliskiler", "metadata_json"),
)


def _is_postgresql() -> bool:
    # Online'da bağlantıdan, offline (--sql) modda context'ten lehçe adı alınır.
    return op.get_context().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgresql():
        return  # SQLite: JSON/JSONB ayrımı yok; no-op.
    for table, column in JSON_COLUMNS:
        op.alter_column(
            table,
            column,
            type_=postgresql.JSONB(),
            existing_type=sa.JSON(),
            existing_nullable=False,
            postgresql_using=f"{column}::jsonb",
        )


def downgrade() -> None:
    if not _is_postgresql():
        return  # SQLite: no-op.
    for table, column in JSON_COLUMNS:
        op.alter_column(
            table,
            column,
            type_=sa.JSON(),
            existing_type=postgresql.JSONB(),
            existing_nullable=False,
            postgresql_using=f"{column}::json",
        )
