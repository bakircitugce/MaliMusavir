"""Mükellefe özel cari hesap kırılımları.

Kural:
1. Aynı mükellef + ana hesap (120/320) + VKN/TCKN için cari kart varsa onun kodu kullanılır.
2. Yoksa mükellefin hesap planında (yüklenen mizan) aynı unvanlı bir alt hesap varsa,
   yeni kod açılmaz; cari kart o hesaba bağlanır.
3. O da yoksa, o mükellefin kendi numaralandırması tespit edilip kaldığı yerden ardışık
   yeni kod açılır (ör. 120.01.045 varsa → 120.01.046). Hiç alt hesap yoksa
   `<ana>.01.001` ile başlanır. Hesap planına da eklenir.
"""

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import Cari, Hesap

MAIN_ACCOUNT_NAMES = {
    "120": "ALICILAR",
    "320": "SATICILAR",
    "136": "DİĞER ÇEŞİTLİ ALACAKLAR",
    "336": "DİĞER ÇEŞİTLİ BORÇLAR",
}
DEFAULT_GROUP = "01"
DEFAULT_WIDTH = 3

# Yalnızca şirket türü / kalıp ifadeler atılır; sektör kelimeleri (inşaat, gıda...) kalır,
# aksi halde "ABC İnşaat" ile "ABC Gıda" aynı cari sanılırdı.
_COMPANY_SUFFIXES = re.compile(
    r"\b(ltd|sti|limited|sirketi|a s|as|anonim|kollektif|komandit|san|sanayi|tic|ticaret|ve)\b"
)


@dataclass
class CariResult:
    cari: Cari
    created_card: bool
    created_account: bool

    def reason(self) -> str | None:
        if self.created_account:
            return f"Yeni cari açıldı: {self.cari.hesap_kodu} {self.cari.unvan}."
        if self.created_card:
            return f"Cari, mevcut {self.cari.hesap_kodu} hesabıyla eşleştirildi."
        return None


def normalize_name(name: str | None) -> str:
    text = unicodedata.normalize("NFKD", (name or "").replace("ı", "i").replace("İ", "i"))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = _COMPANY_SUFFIXES.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _digits(value: str | None) -> str | None:
    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    return digits or None


def next_code(main: str, existing_codes: set[str]) -> str:
    """Ana hesap altında mükellefin numaralandırmasını izleyerek sıradaki kodu verir."""
    under = [c for c in existing_codes if c.startswith(main + ".")]
    leaves = [c for c in under if not any(o != c and o.startswith(c + ".") for o in under)]
    numeric = [c for c in leaves if re.fullmatch(r"\d+", c.rsplit(".", 1)[1])]
    if not numeric:
        candidate = f"{main}.{DEFAULT_GROUP}.{1:0{DEFAULT_WIDTH}d}"
    else:
        parents = Counter(c.rsplit(".", 1)[0] for c in numeric)
        top = max(parents.values())
        parent = sorted(p for p, n in parents.items() if n == top)[-1]
        siblings = [c.rsplit(".", 1)[1] for c in numeric if c.rsplit(".", 1)[0] == parent]
        width = max(len(s) for s in siblings)
        number = max(int(s) for s in siblings) + 1
        candidate = f"{parent}.{number:0{width}d}"
    while candidate in existing_codes:  # güvenlik: çakışmayı atla
        parent, last = candidate.rsplit(".", 1)
        candidate = f"{parent}.{int(last) + 1:0{len(last)}d}"
    return candidate


async def _ensure_parents(
    session: AsyncSession, buro_id, mukellef_id, code: str, existing: set[str]
) -> None:
    parts = code.split(".")
    for depth in range(1, len(parts)):
        parent = ".".join(parts[:depth])
        if parent in existing:
            continue
        session.add(
            Hesap(
                buro_id=buro_id,
                mukellef_id=mukellef_id,
                kod=parent,
                ad=MAIN_ACCOUNT_NAMES.get(parts[0], parts[0]),
                seviye=depth,
                ust_kod=".".join(parts[: depth - 1]) or None,
            )
        )
        existing.add(parent)


async def get_or_create_cari(
    session: AsyncSession,
    *,
    buro_id,
    mukellef_id,
    main_account: str,
    unvan: str | None,
    vkn_tckn: str | None,
) -> CariResult:
    vkn = _digits(vkn_tckn)
    title = (unvan or "").strip()[:250] or (f"VKN {vkn}" if vkn else "Cari")
    if vkn:
        existing_card = await session.scalar(
            select(Cari).where(
                Cari.buro_id == buro_id,
                Cari.mukellef_id == mukellef_id,
                Cari.ana_hesap == main_account,
                Cari.vkn_tckn == vkn,
            )
        )
        if existing_card is not None:
            return CariResult(existing_card, False, False)

    accounts = (
        await session.scalars(
            select(Hesap).where(
                Hesap.buro_id == buro_id,
                Hesap.mukellef_id == mukellef_id,
                Hesap.kod.like(f"{main_account}%"),
            )
        )
    ).all()
    linked_codes = set(
        (
            await session.scalars(
                select(Cari.hesap_kodu).where(
                    Cari.buro_id == buro_id, Cari.mukellef_id == mukellef_id
                )
            )
        ).all()
    )
    all_codes = {a.kod for a in accounts} | {c for c in linked_codes if c.startswith(main_account)}

    # 2) Mizandaki aynı unvanlı (henüz bir cariye bağlanmamış) alt hesap
    wanted = normalize_name(title)
    if wanted:
        for account in accounts:
            if (
                account.kod.startswith(main_account + ".")
                and account.kod not in linked_codes
                and not any(c != account.kod and c.startswith(account.kod + ".") for c in all_codes)
                and normalize_name(account.ad) == wanted
            ):
                card = Cari(
                    buro_id=buro_id, mukellef_id=mukellef_id, ana_hesap=main_account,
                    hesap_kodu=account.kod, unvan=account.ad, vkn_tckn=vkn, otomatik=False,
                )
                session.add(card)
                await session.flush()
                return CariResult(card, True, False)

    # 3) Kaldığı yerden ardışık yeni kod
    code = next_code(main_account, all_codes)
    existing_account_codes = {a.kod for a in accounts}
    await _ensure_parents(session, buro_id, mukellef_id, code, existing_account_codes)
    session.add(
        Hesap(
            buro_id=buro_id, mukellef_id=mukellef_id, kod=code, ad=title,
            seviye=len(code.split(".")), ust_kod=code.rsplit(".", 1)[0],
        )
    )
    card = Cari(
        buro_id=buro_id, mukellef_id=mukellef_id, ana_hesap=main_account,
        hesap_kodu=code, unvan=title, vkn_tckn=vkn, otomatik=True,
    )
    session.add(card)
    await session.flush()
    return CariResult(card, True, True)
