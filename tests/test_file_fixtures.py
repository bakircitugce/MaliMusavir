import pytest

from app.documents.classifier import classify_document
from app.documents.expense_slip import parse_expense_slip
from app.documents.smm import parse_smm
from app.documents.z_report import parse_z_report
from tests.fixtures.manifest import DOCUMENT_FIXTURES


@pytest.mark.parametrize("fixture", DOCUMENT_FIXTURES, ids=lambda item: item.filename)
def test_file_fixture_is_readable_and_classified(fixture) -> None:
    text = fixture.read_text()

    result = classify_document("text", text)

    assert result.category == fixture.category


def test_file_fixture_parsers_extract_specialized_documents() -> None:
    parsed_z = parse_z_report(DOCUMENT_FIXTURES[0].read_text())
    parsed_expense = parse_expense_slip(DOCUMENT_FIXTURES[1].read_text())
    parsed_smm = parse_smm(DOCUMENT_FIXTURES[2].read_text())

    assert parsed_z.review_status == "ready"
    assert parsed_expense.review_status == "ready"
    assert parsed_smm.review_status == "ready"
