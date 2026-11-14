"""Transaction güvenli fiş / yevmiye numarası üretimi (Faz 2 / Part 2b).

Sorumluluk yalnızca "bu mükellefin bu hesap dönemindeki sıradaki numarası"dır. Dönem kilidi,
onay ve yevmiye tarih sıralaması bu modülün işi değildir (PeriodService, sonraki parçalar).

Kurallar:
- Fiş numarası TEK seridir: mahsup, tahsil ve tediye aynı `fis` sayacını kullanır.
- Yevmiye madde numarası ayrı `yevmiye` sayacıdır.
- Fonksiyon ASLA commit etmez; çağıranın transaction'ı içinde çalışır. Sayaç artışı ile
  numaranın fişe yazılması aynı transaction'da olmalıdır. Rollback olursa ikisi birlikte
  geri alınır: numara kaybolmaz, çift numara oluşmaz.

Mekanizma: tek SQL cümlesiyle atomik artış
    UPDATE numara_sayaclari SET son_numara = son_numara + :n WHERE <kapsam> RETURNING son_numara
- PostgreSQL: UPDATE satırı commit'e kadar kilitler; aynı mükellefin eşzamanlı istekleri
  sıraya girer, farklı mükellefler birbirini beklemez.
- SQLite: transaction'lar `BEGIN IMMEDIATE` ile başlar (app.core.db); yazarlar veritabanı
  düzeyinde sıraya girer.
Sayaç satırı yoksa SAVEPOINT içinde oluşturulur; eşzamanlı iki ilk istekten biri tekil kısıta
takılırsa SAVEPOINT geri alınır ve artış tekrar denenir.
"""

from enum import StrEnum
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import HesapDonemi, NumaraSayaci
from app.ledger.draft import LedgerValidationError

MAX_BLOCK = 10_000


class SayacTuru(StrEnum):
    FIS = "fis"  # mahsup + tahsil + tediye tek seri
    YEVMIYE = "yevmiye"


class NumaralandirmaHatasi(LedgerValidationError):
    pass


async def _increment(
    session: AsyncSession,
    *,
    buro_id: UUID,
    mukellef_id: UUID,
    hesap_donemi_id: UUID,
    kind: SayacTuru,
    count: int,
) -> int | None:
    stmt = (
        update(NumaraSayaci)
        .where(
            NumaraSayaci.buro_id == buro_id,
            NumaraSayaci.mukellef_id == mukellef_id,
            NumaraSayaci.hesap_donemi_id == hesap_donemi_id,
            NumaraSayaci.sayac_turu == kind.value,
        )
        .values(son_numara=NumaraSayaci.son_numara + count, updated_at=func.now())
        .returning(NumaraSayaci.son_numara)
        .execution_options(synchronize_session=False)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def allocate(
    session: AsyncSession,
    *,
    buro_id: UUID,
    mukellef_id: UUID,
    hesap_donemi_id: UUID,
    kind: SayacTuru,
    count: int = 1,
) -> range:
    """Sıradaki `count` numarayı ayırır ve ardışık aralık olarak döner (commit etmez)."""
    kind = SayacTuru(kind)
    if not 1 <= count <= MAX_BLOCK:
        raise NumaralandirmaHatasi(f"Tek seferde 1 ile {MAX_BLOCK} arasında numara ayrılabilir.")

    period = await session.scalar(
        select(HesapDonemi).where(
            HesapDonemi.id == hesap_donemi_id,
            HesapDonemi.buro_id == buro_id,
            HesapDonemi.mukellef_id == mukellef_id,
        )
    )
    if period is None:
        raise NumaralandirmaHatasi("Hesap dönemi bulunamadı.")
    if period.status != "open":
        raise NumaralandirmaHatasi("Kapalı hesap dönemine numara verilemez.")

    scope = {
        "buro_id": buro_id,
        "mukellef_id": mukellef_id,
        "hesap_donemi_id": hesap_donemi_id,
        "kind": kind,
    }
    last = await _increment(session, count=count, **scope)
    if last is None:
        try:
            async with session.begin_nested():
                session.add(
                    NumaraSayaci(
                        buro_id=buro_id,
                        mukellef_id=mukellef_id,
                        hesap_donemi_id=hesap_donemi_id,
                        sayac_turu=kind.value,
                        son_numara=0,
                    )
                )
        except IntegrityError:
            pass  # eşzamanlı bir istek sayacı önce oluşturdu
        last = await _increment(session, count=count, **scope)
        if last is None:  # pragma: no cover - tekil kısıt nedeniyle beklenmez
            raise NumaralandirmaHatasi("Numara sayacı oluşturulamadı.")
    return range(last - count + 1, last + 1)
