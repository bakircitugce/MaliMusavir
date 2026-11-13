"""Belge → TDHP taslak yevmiye (Faz 2 / P1).

Okunan belge, türüne göre ilgili kurala gönderilir ve taslak yevmiye fişi
oluşturulur. Fiş hiçbir zaman otomatik kesinleşmez; müşavir/admin onayı gerekir.

Hesap seçimi:
- Alış faturası: gider/stok hesabı (öğrenilmiş eşleşme yoksa 153 Ticari Mallar),
  191 İndirilecek KDV, 320.<VKN> Satıcılar.
- Satış faturası: 120.<VKN> Alıcılar, 600 Yurtiçi Satışlar (veya öğrenilmiş hesap),
  391 Hesaplanan KDV.
- Z raporu: 100 Kasa / 108 POS, 600 Yurtiçi Satışlar, 391 Hesaplanan KDV.

Güven skoru eşiğin altındaysa belge `needs_review` olur ve nedeni kaydedilir.
Kullanıcının düzelttiği hesap, aynı karşı taraf için öğrenme hafızasına yazılır.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import Belge, JournalEntry, JournalLine, Mukellef
from app.documents.extraction import InvoiceData, InvoiceLineData, PartyData
from app.learning.service import suggest_associations
from app.ledger.draft import JournalEntryDraft, LedgerValidationError
from app.ledger.draft import JournalLine as DraftLine
from app.ledger.rules.purchase_invoice import build_purchase_invoice_entry
from app.ledger.rules.sales_invoice import build_sales_invoice_entry
from app.ledger.service import persist_draft

REVIEW_THRESHOLD = Decimal("0.80")
CONFIDENCE_DEFAULT_ACCOUNT = Decimal("0.70")
CONFIDENCE_LEARNED_ACCOUNT = Decimal("0.92")
CONFIDENCE_STANDARD_SALES = Decimal("0.90")

PURCHASE_FEATURE = "purchase_account"
SALES_FEATURE = "sales_account"

DEFAULT_PURCHASE_ACCOUNT = ("153", "Ticari Mallar")
DEFAULT_SALES_ACCOUNT = ("600", "Yurtiçi Satışlar")
VAT_INPUT = ("191", "İndirilecek KDV")
VAT_OUTPUT = ("391", "Hesaplanan KDV")
CASH = ("100", "Kasa")
POS = ("108", "Diğer Hazır Değerler (POS)")

POSTABLE_INVOICE_TYPES = {None, "SATIS", "ISTISNA"}


@dataclass
class PostingResult:
    status: str  # draft_ready | needs_review | skipped
    reasons: list[str] = field(default_factory=list)
    entry_id: str | None = None
    confidence: Decimal | None = None
    direction: str | None = None
    account: str | None = None  # öğrenme için seçilen ana hesap (kod|ad)

    def metadata(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reasons": self.reasons,
            "entry_id": self.entry_id,
            "confidence": str(self.confidence) if self.confidence is not None else None,
            "direction": self.direction,
            "account": self.account,
        }


def encode_account(code: str, name: str) -> str:
    return f"{code}|{name}"[:100]


def decode_account(label: str) -> tuple[str, str]:
    code, _, name = label.partition("|")
    return code, name or code


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def invoice_from_metadata(meta: dict[str, Any]) -> InvoiceData:
    """Belge metadata'sındaki (JSON) fatura bilgisini InvoiceData'ya geri çevirir."""

    def party(data: dict[str, Any] | None) -> PartyData:
        data = data or {}
        return PartyData(data.get("name"), data.get("tax_id"), data.get("tax_id_scheme"))

    lines = tuple(
        InvoiceLineData(
            line_id=line.get("line_id"),
            description=line.get("description"),
            quantity=_decimal(line.get("quantity")),
            unit_code=line.get("unit_code"),
            line_extension_amount=_decimal(line.get("line_extension_amount")),
            tax_percent=_decimal(line.get("tax_percent")),
            item_code=line.get("item_code"),
        )
        for line in meta.get("lines", [])
    )
    return InvoiceData(
        invoice_id=meta["invoice_id"],
        issue_date=meta["issue_date"],
        invoice_type_code=meta.get("invoice_type_code"),
        profile_id=meta.get("profile_id"),
        currency=meta.get("currency"),
        supplier=party(meta.get("supplier")),
        customer=party(meta.get("customer")),
        tax_exclusive_amount=_decimal(meta.get("tax_exclusive_amount")),
        tax_inclusive_amount=_decimal(meta.get("tax_inclusive_amount")),
        payable_amount=_decimal(meta.get("payable_amount")),
        tax_total=_decimal(meta.get("tax_total")),
        lines=lines,
    )


def _norm_vkn(value: str | None) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


def invoice_direction(invoice: InvoiceData, mukellef: Mukellef) -> str | None:
    vkn = _norm_vkn(mukellef.vkn_tckn)
    if vkn and _norm_vkn(invoice.customer.tax_id) == vkn:
        return "purchase"
    if vkn and _norm_vkn(invoice.supplier.tax_id) == vkn:
        return "sales"
    return None


async def _learned_account(
    session: AsyncSession, document: Belge, feature: str, counterparty_vkn: str
) -> tuple[str, str] | None:
    if not counterparty_vkn:
        return None
    suggestions = await suggest_associations(
        session,
        buro_id=document.buro_id,
        mukellef_id=document.mukellef_id,
        feature_type=feature,
        feature_value=counterparty_vkn,
    )
    return decode_account(suggestions[0].label) if suggestions else None


def _with_confidence(draft: JournalEntryDraft, confidence: Decimal) -> JournalEntryDraft:
    return JournalEntryDraft(
        entry_date=draft.entry_date,
        description=draft.description,
        lines=draft.lines,
        source_document_id=draft.source_document_id,
        rule_name=draft.rule_name,
        rule_version=draft.rule_version,
        confidence=confidence,
    )


async def build_invoice_draft(
    session: AsyncSession,
    document: Belge,
    invoice: InvoiceData,
    mukellef: Mukellef,
    *,
    account_override: tuple[str, str] | None = None,
) -> tuple[JournalEntryDraft | None, PostingResult]:
    direction = invoice_direction(invoice, mukellef)
    if direction is None:
        return None, PostingResult(
            "needs_review",
            [
                "Fatura bu mükellefe ait görünmüyor: alıcı veya satıcı VKN'si mükellefin "
                f"VKN'si ({mukellef.vkn_tckn}) ile eşleşmedi."
            ],
        )
    if invoice.invoice_type_code not in POSTABLE_INVOICE_TYPES:
        return None, PostingResult(
            "needs_review",
            [f"'{invoice.invoice_type_code}' tipli fatura (iade/tevkifat vb.) elle incelenmeli."],
            direction=direction,
        )
    if invoice.currency not in (None, "TRY"):
        return None, PostingResult(
            "needs_review", [f"Döviz cinsi fatura ({invoice.currency}): kur bilgisi gerekli."],
            direction=direction,
        )

    reasons: list[str] = []
    if direction == "purchase":
        counterparty = invoice.supplier
        learned = await _learned_account(
            session, document, PURCHASE_FEATURE, _norm_vkn(counterparty.tax_id)
        )
        if account_override:
            account, confidence = account_override, CONFIDENCE_LEARNED_ACCOUNT
            reasons.append(f"Gider/stok hesabı kullanıcı tarafından seçildi: {account[0]}.")
        elif learned:
            account, confidence = learned, CONFIDENCE_LEARNED_ACCOUNT
            reasons.append(
                f"{counterparty.name} için daha önce onaylanan hesap kullanıldı: {account[0]} {account[1]}."
            )
        else:
            account, confidence = DEFAULT_PURCHASE_ACCOUNT, CONFIDENCE_DEFAULT_ACCOUNT
            reasons.append(
                f"{counterparty.name} için öğrenilmiş hesap yok; varsayılan 153 Ticari Mallar "
                "önerildi. Gider ise 770 vb. olarak düzeltin."
            )
        vkn = _norm_vkn(counterparty.tax_id)
        build = lambda: build_purchase_invoice_entry(  # noqa: E731
            invoice,
            expense_account_code=account[0],
            expense_account_name=account[1],
            vat_account_code=VAT_INPUT[0],
            vat_account_name=VAT_INPUT[1],
            supplier_account_code=f"320.{vkn}" if vkn else "320",
            supplier_account_name=f"Satıcılar - {counterparty.name or 'Satıcı'}",
            source_document_id=document.id,
        )
    else:
        counterparty = invoice.customer
        learned = await _learned_account(
            session, document, SALES_FEATURE, _norm_vkn(counterparty.tax_id)
        )
        if account_override:
            account, confidence = account_override, CONFIDENCE_LEARNED_ACCOUNT
            reasons.append(f"Gelir hesabı kullanıcı tarafından seçildi: {account[0]}.")
        elif learned:
            account, confidence = learned, CONFIDENCE_LEARNED_ACCOUNT
            reasons.append(f"{counterparty.name} için daha önce onaylanan gelir hesabı kullanıldı.")
        else:
            account, confidence = DEFAULT_SALES_ACCOUNT, CONFIDENCE_STANDARD_SALES
            reasons.append("Standart satış kaydı: 120 Alıcılar / 600 Yurtiçi Satışlar / 391 KDV.")
        vkn = _norm_vkn(counterparty.tax_id)
        build = lambda: build_sales_invoice_entry(  # noqa: E731
            invoice,
            revenue_account_code=account[0],
            revenue_account_name=account[1],
            vat_account_code=VAT_OUTPUT[0],
            vat_account_name=VAT_OUTPUT[1],
            settlement_account_code=f"120.{vkn}" if vkn else "120",
            settlement_account_name=f"Alıcılar - {counterparty.name or 'Alıcı'}",
            source_document_id=document.id,
        )

    try:
        draft = build()
    except LedgerValidationError as exc:
        return None, PostingResult("needs_review", [*reasons, str(exc)], direction=direction)

    rates = {line.tax_percent for line in invoice.lines if line.tax_percent is not None}
    if len(rates) > 1:
        reasons.append(f"Faturada birden fazla KDV oranı var ({', '.join(map(str, sorted(rates)))}).")
    status = "draft_ready" if confidence >= REVIEW_THRESHOLD else "needs_review"
    return _with_confidence(draft, confidence), PostingResult(
        status, reasons, confidence=confidence, direction=direction,
        account=encode_account(*account),
    )


def build_z_report_draft(document: Belge, meta: dict[str, Any]) -> tuple[JournalEntryDraft | None, PostingResult]:
    total = _decimal(meta.get("total"))
    cash = _decimal(meta.get("cash_total")) or Decimal("0")
    card = _decimal(meta.get("card_total")) or Decimal("0")
    breakdown = meta.get("vat_breakdown") or []
    if total is None:
        return None, PostingResult("needs_review", ["Z raporunda toplam okunamadı."])
    vat = sum((_decimal(item.get("tax")) or Decimal("0") for item in breakdown), Decimal("0"))
    if not breakdown or not vat:
        return None, PostingResult("needs_review", ["Z raporunda KDV dağılımı okunamadı."])
    if cash + card != total:
        return None, PostingResult(
            "needs_review", [f"Nakit+kart ({cash + card}) Z toplamıyla ({total}) eşleşmiyor."]
        )
    try:
        report_date = date.fromisoformat(meta.get("report_date") or "")
    except ValueError:
        return None, PostingResult("needs_review", ["Z raporu tarihi okunamadı."])
    lines = []
    if cash:
        lines.append(DraftLine(CASH[0], CASH[1], debit=cash))
    if card:
        lines.append(DraftLine(POS[0], POS[1], debit=card))
    lines.append(DraftLine(DEFAULT_SALES_ACCOUNT[0], DEFAULT_SALES_ACCOUNT[1], credit=total - vat))
    lines.append(DraftLine(VAT_OUTPUT[0], VAT_OUTPUT[1], credit=vat))
    draft = JournalEntryDraft(
        entry_date=report_date,
        description=f"Z raporu {meta.get('report_number') or ''}".strip(),
        lines=tuple(lines),
        source_document_id=document.id,
        rule_name="z_report",
        rule_version="1",
        confidence=CONFIDENCE_STANDARD_SALES,
    )
    return draft, PostingResult(
        "draft_ready", ["Z raporu: 100 Kasa / 108 POS, 600 Satışlar, 391 KDV."],
        confidence=CONFIDENCE_STANDARD_SALES, direction="sales",
    )


async def existing_entry(session: AsyncSession, document: Belge) -> JournalEntry | None:
    return await session.scalar(
        select(JournalEntry)
        .where(
            JournalEntry.buro_id == document.buro_id,
            JournalEntry.mukellef_id == document.mukellef_id,
            JournalEntry.source_document_id == document.id,
            JournalEntry.status.in_(("draft", "pending_review", "approved")),
        )
        .order_by(JournalEntry.created_at.desc())
    )


async def _discard_draft(session: AsyncSession, entry: JournalEntry) -> None:
    await session.execute(delete(JournalLine).where(JournalLine.journal_entry_id == entry.id))
    await session.delete(entry)
    await session.flush()


async def post_document(
    session: AsyncSession,
    document: Belge,
    *,
    account_override: tuple[str, str] | None = None,
) -> PostingResult:
    """Belgeden taslak fiş üretir (idempotent). Onaylanmış fişe dokunmaz."""
    if document.extraction_status != "completed":
        result = PostingResult("needs_review", ["Belge okunamadı; elle giriş gerekli."])
        return await _save(session, document, result)

    current = await existing_entry(session, document)
    if current is not None and current.status == "approved":
        return PostingResult("skipped", ["Bu belgenin fişi zaten onaylanmış."], str(current.id))

    meta = document.extraction_metadata or {}
    mukellef = await session.scalar(
        select(Mukellef).where(
            Mukellef.id == document.mukellef_id, Mukellef.buro_id == document.buro_id
        )
    )
    draft: JournalEntryDraft | None
    if "invoice" in meta:
        draft, result = await build_invoice_draft(
            session, document, invoice_from_metadata(meta["invoice"]), mukellef,
            account_override=account_override,
        )
    elif "z_report" in meta:
        draft, result = build_z_report_draft(document, meta["z_report"])
    else:
        draft, result = None, PostingResult(
            "needs_review",
            [f"'{document.document_category or document.document_type}' türü için otomatik "
             "kayıt kuralı yok; elle giriş gerekli."],
        )

    if current is not None:
        await _discard_draft(session, current)
    if draft is not None:
        try:
            entry = await persist_draft(
                session, buro_id=document.buro_id, mukellef_id=document.mukellef_id, draft=draft
            )
            result.entry_id = str(entry.id)
        except LedgerValidationError as exc:
            result = PostingResult("needs_review", [*result.reasons, str(exc)],
                                   direction=result.direction)
    return await _save(session, document, result)


async def _save(session: AsyncSession, document: Belge, result: PostingResult) -> PostingResult:
    document.extraction_metadata = {**(document.extraction_metadata or {}),
                                    "posting": result.metadata()}
    if document.review_status not in ("approved",):
        document.review_status = "needs_review" if result.status == "needs_review" else "pending"
    await session.commit()
    return result
