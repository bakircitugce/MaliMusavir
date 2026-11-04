from uuid import uuid4

import pytest

from app.bank import (
    CounterpartyCandidate,
    find_counterparty_matches,
)


def test_counterparty_matching_prioritizes_exact_tax_id() -> None:
    first_id = uuid4()
    second_id = uuid4()
    matches = find_counterparty_matches(
        counterparty_name="ABC Ltd.",
        counterparty_tax_id="123 456 7890",
        candidates=[
            CounterpartyCandidate(first_id, "Başka Firma", "1234567890", "320.01.001"),
            CounterpartyCandidate(second_id, "ABC Ltd.", "9999999999", "320.01.002"),
        ],
    )

    assert matches[0].counterparty_id == first_id
    assert matches[0].score == 100
    assert matches[0].requires_review is False


def test_counterparty_matching_name_is_review_only_and_turkish_normalized() -> None:
    candidate_id = uuid4()
    matches = find_counterparty_matches(
        counterparty_name="Çözüm Gıda A.Ş.",
        counterparty_tax_id=None,
        candidates=[
            CounterpartyCandidate(
                candidate_id,
                "COZUM GIDA AS",
                None,
                "320.02.001",
            )
        ],
    )

    assert matches[0].counterparty_id == candidate_id
    assert matches[0].score < 100
    assert matches[0].requires_review is True


def test_counterparty_matching_validates_limit() -> None:
    with pytest.raises(ValueError, match="1 ile 100"):
        find_counterparty_matches(
            counterparty_name="Firma",
            counterparty_tax_id=None,
            candidates=[],
            limit=0,
        )
