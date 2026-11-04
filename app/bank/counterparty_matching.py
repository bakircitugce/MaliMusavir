from dataclasses import dataclass
import re
from typing import Iterable
from uuid import UUID


@dataclass(frozen=True)
class CounterpartyCandidate:
    counterparty_id: UUID
    name: str
    tax_id: str | None
    account_code: str


@dataclass(frozen=True)
class CounterpartyMatch:
    counterparty_id: UUID
    account_code: str
    score: int
    reason: str
    requires_review: bool


def _normalize_text(value: str) -> str:
    replacements = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    return re.sub(r"[^a-z0-9]+", " ", value.translate(replacements).lower()).strip()


def _normalize_tax_id(value: str | None) -> str | None:
    if value is None:
        return None
    digits = re.sub(r"\D", "", value)
    return digits or None


def find_counterparty_matches(
    *,
    counterparty_name: str | None,
    counterparty_tax_id: str | None,
    candidates: Iterable[CounterpartyCandidate],
    limit: int = 10,
) -> list[CounterpartyMatch]:
    if limit < 1 or limit > 100:
        raise ValueError("Cari aday limiti 1 ile 100 arasında olmalıdır.")
    normalized_name = _normalize_text(counterparty_name or "")
    normalized_tax_id = _normalize_tax_id(counterparty_tax_id)
    matches: list[CounterpartyMatch] = []
    for candidate in candidates:
        candidate_tax_id = _normalize_tax_id(candidate.tax_id)
        candidate_name = _normalize_text(candidate.name)
        if normalized_tax_id and candidate_tax_id == normalized_tax_id:
            matches.append(
                CounterpartyMatch(
                    counterparty_id=candidate.counterparty_id,
                    account_code=candidate.account_code,
                    score=100,
                    reason="VKN/TCKN tam eşleşti.",
                    requires_review=False,
                )
            )
            continue
        if normalized_name and candidate_name:
            source_words = set(normalized_name.split())
            candidate_words = set(candidate_name.split())
            overlap = len(source_words & candidate_words)
            if overlap:
                score = min(85, 50 + int(35 * overlap / max(len(source_words), 1)))
                matches.append(
                    CounterpartyMatch(
                        counterparty_id=candidate.counterparty_id,
                        account_code=candidate.account_code,
                        score=score,
                        reason="Unvan kelimeleri eşleşti; onay gerekli.",
                        requires_review=True,
                    )
                )
    matches.sort(key=lambda item: (-item.score, str(item.counterparty_id)))
    return matches[:limit]
