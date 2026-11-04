from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class HesapSeed:
    kod: str
    ad: str
    seviye: int
    ust_kod: str | None


def build_chart_seed(rows: Iterable[dict[str, str]]) -> list[HesapSeed]:
    result: list[HesapSeed] = []
    for row in rows:
        code = (row.get("kod") or "").strip()
        name = (row.get("ad") or row.get("hesap_adi") or "").strip()
        if not code or not name:
            continue
        parts = code.split(".")
        result.append(
            HesapSeed(
                kod=code,
                ad=name,
                seviye=len(parts),
                ust_kod=".".join(parts[:-1]) or None,
            )
        )
    return result
