import pytest

from app.documents.classifier import classify_document
from app.documents.expense_slip import parse_expense_slip
from app.documents.smm import parse_smm
from app.documents.z_report import parse_z_report
from tests.fixtures.synthetic_documents import (
    build_all_document_fixtures,
    build_expense_slip_fixtures,
    build_smm_fixtures,
    build_z_report_fixtures,
)


@pytest.mark.parametrize("fixture", build_z_report_fixtures(), ids=lambda item: item.fixture_id)
def test_synthetic_fixture_has_expected_z_report_total(fixture) -> None:
    result = parse_z_report(fixture.text)

    assert result.review_status == "ready"
    assert result.total == fixture.expected_total


@pytest.mark.parametrize(
    "fixture",
    build_all_document_fixtures(),
    ids=lambda item: item.fixture_id,
)
def test_all_synthetic_documents_are_classified(fixture) -> None:
    result = classify_document("text", fixture.text)

    assert result.category == fixture.category
    assert result.status == "classified"


@pytest.mark.parametrize("fixture", build_expense_slip_fixtures(), ids=lambda item: item.fixture_id)
def test_synthetic_expense_slip_is_balanced(fixture) -> None:
    result = parse_expense_slip(fixture.text)

    assert result.review_status == "ready"
    assert result.total_amount == fixture.expected_total


@pytest.mark.parametrize("fixture", build_smm_fixtures(), ids=lambda item: item.fixture_id)
def test_synthetic_smm_is_balanced(fixture) -> None:
    result = parse_smm(fixture.text)

    assert result.review_status == "ready"
    assert result.net_amount == fixture.expected_total
