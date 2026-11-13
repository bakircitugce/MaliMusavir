"""Rol modeli (Faz 0.7).

- admin    : Büro yöneticisi. Kullanıcı yönetimi + müşavirin tüm yetkileri.
- musavir  : SMMM. Fiş onayı, ters kayıt, dönem kilidi, ERP/e-Fatura gönderimi.
- personel : Belge yükleme, çıkarım, inceleme, banka mutabakatı. Yalnızca atandığı mükellefler.
- mukellef : Yalnızca atandığı mükellefin verisini salt okuma.
"""

ADMIN = "admin"
MUSAVIR = "musavir"
PERSONEL = "personel"
MUKELLEF = "mukellef"

ALL_ROLES: frozenset[str] = frozenset({ADMIN, MUSAVIR, PERSONEL, MUKELLEF})

# Büronun tüm mükelleflerine erişen roller. Diğerleri atama tablosuna bağlıdır.
BURO_WIDE_ROLES: frozenset[str] = frozenset({ADMIN, MUSAVIR})
# Veri değiştirebilen roller (mükellef salt okumadır).
WRITE_ROLES: frozenset[str] = frozenset({ADMIN, MUSAVIR, PERSONEL})
# Muhasebe kararını kesinleştiren işlemler.
APPROVER_ROLES: frozenset[str] = frozenset({ADMIN, MUSAVIR})

_LEGACY_ROLE_MAP = {
    "yonetici": ADMIN,
    "kullanici": PERSONEL,
    "stajyer": PERSONEL,
}


def normalize_role(value: str | None) -> str | None:
    """Eski rol adlarını yeni modele eşler; bilinmeyen rol için None döner (yetki yok)."""
    if value is None:
        return None
    cleaned = value.strip().lower()
    cleaned = _LEGACY_ROLE_MAP.get(cleaned, cleaned)
    return cleaned if cleaned in ALL_ROLES else None
