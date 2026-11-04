from dataclasses import dataclass
from pathlib import Path


FIXTURE_DIR = Path(__file__).parent


@dataclass(frozen=True)
class DocumentFixture:
    filename: str
    category: str

    @property
    def path(self) -> Path:
        return FIXTURE_DIR / self.filename

    def read_text(self) -> str:
        return self.path.read_text(encoding="utf-8")


DOCUMENT_FIXTURES = (
    DocumentFixture("z_report_001.txt", "z_report"),
    DocumentFixture("expense_slip_001.txt", "expense_slip"),
    DocumentFixture("smm_001.txt", "smm"),
    DocumentFixture("invoice_001.txt", "invoice"),
    DocumentFixture("bank_statement_001.txt", "bank_statement"),
)
