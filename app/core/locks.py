"""Satır kilitleri (D1a): bir belgenin fişlerini okuyup değiştiren işlemleri sıraya sokar.

Kural: Bir belgenin fişleri hakkında karar veren her transaction, ilk kararından ÖNCE belge
satırını bu modülle kilitler ve işini aynı transaction içinde bitirir (arada commit yok).

Veritabanına göre garanti:
- PostgreSQL: `SELECT ... FOR UPDATE` satır kilidi; kilit transaction sonuna kadar tutulur.
- Dosya SQLite: SQLAlchemy'nin SQLite lehçesi `FOR UPDATE` ifadesini SQL'e yazmaz (sessizce
  atlar). Kilidin dayanağı, `app.core.db.configure_sqlite_transactions` ile her transaction'ın
  ilk SQL komutundan önce `BEGIN IMMEDIATE` göndermesidir (veritabanı yazma kilidi). Bu yapılandırma
  bağlantıda yoksa (gerçek DEFERRED) kilit garantisi olmadığından işlem reddedilir.
- Bellek içi SQLite: yalnızca test ortamında; eşzamanlılık garantisi iddia edilmez.

Kilit sorgusu `populate_existing` ile çalışır: aynı oturumda kilitten önce yüklenmiş nesne,
kilit altındaki güncel değerlerle yeniden doldurulur. Kilitten önce okunan değerlere dayanarak
karar verilmemelidir.

Kilit sırası (deadlock önlemi): belge(ler) kimliğe göre sıralı → hesap dönemi → muhasebe ayı →
fiş satırları → numara sayacı → cari.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import SQLITE_IMMEDIATE_INFO_KEY
from app.core.models import Belge


class KilitHatasi(RuntimeError):
    """Kilit alınamadı veya güvenli biçimde alınamaz."""


class KilitGarantisiYok(KilitHatasi):
    """Bağlantı, kilidin gerçekten sıraya sokacağını garanti etmiyor (ör. SQLite DEFERRED)."""


class KilitlenecekKayitYok(KilitHatasi, LookupError):
    """Kilitlenmek istenen kayıt bu büro/mükellef kapsamında yok."""


async def _ensure_lock_guarantee(session: AsyncSession) -> None:
    if not session.in_transaction():
        raise KilitHatasi(
            "Kilit açık bir transaction içinde alınmalıdır; aksi halde kilit hemen bırakılır."
        )
    connection = await session.connection()
    dialect = connection.dialect.name
    if dialect == "postgresql":
        return
    if dialect == "sqlite":
        database = connection.engine.url.database
        if database in (None, "", ":memory:"):
            if get_settings().app_env == "test":
                return
            raise KilitGarantisiYok("Bellek içi SQLite yalnızca test ortamında kullanılabilir.")
        if connection.sync_connection.info.get(SQLITE_IMMEDIATE_INFO_KEY):
            return
        raise KilitGarantisiYok(
            "SQLite bağlantısı BEGIN IMMEDIATE ile yapılandırılmamış (DEFERRED). Bu modda "
            "SELECT ... FOR UPDATE kilit sağlamaz; çift kayıt riskine karşı işlem reddedildi. "
            "Motor app.core.db.build_engine ile oluşturulmalıdır."
        )
    raise KilitGarantisiYok(f"'{dialect}' veritabanı için kilit garantisi tanımlı değil.")


async def lock_document(
    session: AsyncSession, *, buro_id: UUID, mukellef_id: UUID, document_id: UUID
) -> Belge:
    """Belge satırını kilitler ve güncel hâlini döner (transaction sonuna kadar kilitli)."""
    await _ensure_lock_guarantee(session)
    document = await session.scalar(
        select(Belge)
        .where(
            Belge.id == document_id,
            Belge.buro_id == buro_id,
            Belge.mukellef_id == mukellef_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if document is None:
        raise KilitlenecekKayitYok("Kilitlenecek belge bulunamadı.")
    return document
