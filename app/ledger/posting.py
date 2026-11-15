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

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Self

from sqlalchemy import event, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit
from app.chart.cari import get_or_create_cari
from app.core.locks import lock_document
from app.core.models import Belge, JournalEntry, JournalLine, Mukellef
from app.documents.extraction import InvoiceData, InvoiceLineData, PartyData
from app.learning.service import suggest_associations
from app.ledger.draft import JournalEntryDraft, LedgerValidationError
from app.ledger.draft import JournalLine as DraftLine
from app.ledger.rules.purchase_invoice import build_purchase_invoice_entry
from app.ledger.rules.sales_invoice import build_sales_invoice_entry
from app.ledger.service import persist_draft, replace_draft_content, validate_draft

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
    status: str  # draft_ready | needs_review | skipped | conflict
    reasons: list[str] = field(default_factory=list)
    entry_id: str | None = None
    confidence: Decimal | None = None
    direction: str | None = None
    account: str | None = None  # öğrenme için seçilen ana hesap (kod|ad)
    stale_entry_id: str | None = None  # korunan ama güncel olmayan taslak

    def metadata(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "status": self.status,
            "reasons": self.reasons,
            "entry_id": self.entry_id,
            "confidence": str(self.confidence) if self.confidence is not None else None,
            "direction": self.direction,
            "account": self.account,
        }
        if self.stale_entry_id is not None:
            data["stale_entry_id"] = self.stale_entry_id
        return data


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
        cari = await get_or_create_cari(
            session, buro_id=document.buro_id, mukellef_id=document.mukellef_id,
            main_account="320", unvan=counterparty.name, vkn_tckn=counterparty.tax_id,
        )
        if cari.reason():
            reasons.append(cari.reason())
        build = lambda: build_purchase_invoice_entry(  # noqa: E731
            invoice,
            expense_account_code=account[0],
            expense_account_name=account[1],
            vat_account_code=VAT_INPUT[0],
            vat_account_name=VAT_INPUT[1],
            supplier_account_code=cari.cari.hesap_kodu,
            supplier_account_name=cari.cari.unvan,
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
        cari = await get_or_create_cari(
            session, buro_id=document.buro_id, mukellef_id=document.mukellef_id,
            main_account="120", unvan=counterparty.name, vkn_tckn=counterparty.tax_id,
        )
        if cari.reason():
            reasons.append(cari.reason())
        build = lambda: build_sales_invoice_entry(  # noqa: E731
            invoice,
            revenue_account_code=account[0],
            revenue_account_name=account[1],
            vat_account_code=VAT_OUTPUT[0],
            vat_account_name=VAT_OUTPUT[1],
            settlement_account_code=cari.cari.hesap_kodu,
            settlement_account_name=cari.cari.unvan,
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


ACTIVE_STATUSES = ("draft", "pending_review", "approved")
_STATUS_ORDER = {"approved": 0, "pending_review": 1, "draft": 2}
_CENT = Decimal("0.01")


def is_reversal(entry: JournalEntry) -> bool:
    """Ters kayıt mı? Yeni kayıtlar `reversal_of_id` ile, eski kayıtlar `rule_name` ile tanınır.

    Eski ters kayıtların `rule_name` değeri `<orijinal>.reversal` veya `reversal` biçimindedir
    (app/ledger/reversal.py). Part 3a/3f sonrası `kayit_turu = 'ters_kayit'` de eklenecek.
    """
    if entry.reversal_of_id is not None:
        return True
    return (entry.rule_name or "").endswith("reversal")


@dataclass(frozen=True)
class DocumentEntries:
    """Bir belgeye bağlı fişler: aktif fişler ve ters kayıtlar ayrı, belirleyici sırayla."""

    active: tuple[JournalEntry, ...]
    reversals: tuple[JournalEntry, ...]

    @property
    def has_conflict(self) -> bool:
        return len(self.active) > 1

    @property
    def primary(self) -> JournalEntry | None:
        """Tek aktif fiş varsa odur; çakışmada da sıralamanın ilki (yalnızca gösterim için)."""
        return self.active[0] if self.active else None


async def document_entries(session: AsyncSession, document: Belge) -> DocumentEntries:
    """Belgenin tüm aktif fişleri ve ters kayıtları.

    Aktif: durum draft/pending_review/approved ve ters kayıt değil. Sıra: approved →
    pending_review → draft, ardından `created_at`, ardından `id` (eşit zaman damgalarında da
    belirleyici). Tek fiş varsayımı yapılmaz; çakışmayı çağıran değerlendirir.
    """
    rows = (
        await session.scalars(
            select(JournalEntry).where(
                JournalEntry.buro_id == document.buro_id,
                JournalEntry.mukellef_id == document.mukellef_id,
                JournalEntry.source_document_id == document.id,
            )
        )
    ).all()
    reversals = sorted((e for e in rows if is_reversal(e)), key=lambda e: (e.created_at, str(e.id)))
    active = sorted(
        (e for e in rows if not is_reversal(e) and e.status in ACTIVE_STATUSES),
        key=lambda e: (_STATUS_ORDER[e.status], e.created_at, str(e.id)),
    )
    return DocumentEntries(tuple(active), tuple(reversals))


def _amount(value: Decimal | int | None) -> Decimal:
    return Decimal(str(value or 0)).quantize(_CENT)


def line_fingerprint(entry_date: date, lines: Iterable[tuple[str, str, Any, Any]]) -> str:
    """Fiş tarihi + sıralı satırlar (hesap kodu, adı, borç, alacak) için SHA-256 özeti."""
    parts = [entry_date.isoformat()]
    parts.extend(
        f"{code}|{name}|{_amount(debit)}|{_amount(credit)}" for code, name, debit, credit in lines
    )
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _draft_lines(draft: JournalEntryDraft) -> list[tuple[str, str, Decimal, Decimal]]:
    return [(ln.account_code, ln.account_name, _amount(ln.debit), _amount(ln.credit))
            for ln in draft.lines]


async def _entry_lines(
    session: AsyncSession, entry: JournalEntry
) -> list[tuple[str, str, Decimal, Decimal]]:
    rows = await session.scalars(
        select(JournalLine)
        .where(
            JournalLine.journal_entry_id == entry.id,
            JournalLine.buro_id == entry.buro_id,
            JournalLine.mukellef_id == entry.mukellef_id,
        )
        .order_by(JournalLine.line_order)
    )
    return [(r.account_code, r.account_name, _amount(r.debit), _amount(r.credit)) for r in rows]


def _same_header(entry: JournalEntry, draft: JournalEntryDraft) -> bool:
    return (
        entry.entry_date == draft.entry_date
        and entry.description == draft.description
        and entry.rule_name == draft.rule_name
        and entry.rule_version == draft.rule_version
        and entry.confidence == draft.confidence
    )


def _target_review_status(document: Belge, result: PostingResult) -> str:
    if document.review_status == "approved":
        return "approved"
    return "needs_review" if result.status == "needs_review" else "pending"


class IslemSahipligiHatasi(RuntimeError):
    """`commit` parametresi ile oturumun transaction durumu uyuşmuyor."""


class _FlushTracker:
    """Bu çağrı sırasında veritabanına bir değişiklik flush edildi mi?"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session.sync_session
        self.wrote = False

    def _after_flush(self, session, flush_context) -> None:
        if session.new or session.dirty or session.deleted:
            self.wrote = True

    def __enter__(self) -> Self:
        event.listen(self._session, "after_flush", self._after_flush)
        return self

    def __exit__(self, *exc) -> None:
        event.remove(self._session, "after_flush", self._after_flush)


async def post_document(
    session: AsyncSession,
    document: Belge,
    *,
    account_override: tuple[str, str] | None = None,
    commit: bool = True,
    trigger: str = "otomatik_isleme",
) -> PostingResult:
    """Belgeden taslak fiş üretir veya mevcut taslağı yerinde günceller (D1a).

    Transaction sahipliği:

    - `commit=True` (varsayılan) — fonksiyon sahibidir. Çağrı anında oturumda açık transaction ve
      flush edilmemiş değişiklik OLMAMALIDIR (aksi halde `IslemSahipligiHatasi`, hiçbir işlem
      yapılmadan). Fonksiyon transaction'ı kendisi başlatır ve her dönüş yolunda kapatır:
      bir değişiklik yazıldıysa COMMIT, yazılmadıysa (çakışma, atlama, aynı içerik) ROLLBACK;
      hata halinde ROLLBACK ve hata yeniden fırlatılır. Dönüşte transaction yoktur; SQLite
      yazma kilidi (BEGIN IMMEDIATE) ve PostgreSQL satır kilidi bırakılmıştır.
      Not: ROLLBACK, SQLAlchemy'de oturumdaki nesneleri "expired" yapar; sonraki erişim yeni
      bir okuma (ve yeni bir transaction) başlatır.
    - `commit=False` — çağıran sahibidir. Çağrı anında açık transaction OLMALIDIR (aksi halde
      `IslemSahipligiHatasi`). Fonksiyon çağıranın transaction'ını asla commit/rollback etmez;
      kendi işini bir SAVEPOINT içinde yapar: başarıda SAVEPOINT bırakılır (değişiklikler
      çağıranın transaction'ında bekler), hatada yalnızca SAVEPOINT geri alınır (kısmi yazma
      kalmaz, çağıranın önceki değişiklikleri korunur) ve hata yeniden fırlatılır. Kilitler
      çağıran transaction'ı bitirene kadar tutulur.

    İş kuralları (`_post_document_locked`):
    - İlk iş belge satırını kilitlemektir; bütün kararlar kilit altında okunan veriye dayanır.
    - Birden fazla aktif fiş (çakışma): hiçbir şey yazılmaz, `conflict` döner.
    - Onaylı veya `pending_review` fiş: dokunulmaz, `skipped` döner.
    - Tek taslak: içerik aynıysa hiçbir şey yazılmaz; farklıysa aynı fiş kaydı (kimlik ve
      `created_at` korunarak) güncellenir ve eski/yeni satır parmak izi denetim kaydına yazılır.
    - Doğrulama hatası veya kural yok: mevcut taslak korunur (silinmez), belge `needs_review`
      olur ve taslak "güncel değil" (`stale_entry_id`) olarak işaretlenir.
    """
    if commit:
        if session.in_transaction():
            raise IslemSahipligiHatasi(
                "Oturumda çağırana ait açık bir transaction var; commit=False kullanın ve "
                "transaction'ı çağıran tarafta sonlandırın."
            )
        if session.new or session.dirty or session.deleted:
            raise IslemSahipligiHatasi("Oturumda flush edilmemiş değişiklikler var.")
        await session.begin()
        with _FlushTracker(session) as tracker:
            try:
                result = await _post_document_locked(session, document, account_override, trigger)
            except BaseException:
                await session.rollback()
                raise
        if tracker.wrote:
            await session.commit()
        else:
            await session.rollback()
        return result

    if not session.in_transaction():
        raise IslemSahipligiHatasi(
            "commit=False çağıranın açık transaction'ını gerektirir; transaction yok."
        )
    savepoint = await session.begin_nested()
    try:
        result = await _post_document_locked(session, document, account_override, trigger)
    except BaseException:
        await savepoint.rollback()
        raise
    await savepoint.commit()
    return result


async def _post_document_locked(
    session: AsyncSession,
    document: Belge,
    account_override: tuple[str, str] | None,
    trigger: str,
) -> PostingResult:
    """İş kuralları; transaction'ı asla başlatmaz veya sonlandırmaz (bkz. `post_document`)."""
    # Önceki bir ROLLBACK nesneyi "expired" yapmış olabilir; async ortamda gizli IO yerine açıkça
    # yeniden okunur. Bu okuma yalnızca kilit kapsamını (id/büro/mükellef) bulmak içindir; kararlar
    # kilit altındaki okumaya dayanır.
    if inspect(document).expired_attributes:
        await session.refresh(document)
    document = await lock_document(
        session, buro_id=document.buro_id, mukellef_id=document.mukellef_id,
        document_id=document.id,
    )
    if document.extraction_status != "completed":
        result = PostingResult("needs_review", ["Belge okunamadı; elle giriş gerekli."])
        return await _save(session, document, result)

    entries = await document_entries(session, document)
    if entries.has_conflict:
        return PostingResult(
            "conflict",
            [(f"Bu belgeye bağlı {len(entries.active)} aktif fiş var; çakışma çözülmeden "
              "otomatik işlem yapılmaz.")],
        )
    current = entries.primary
    if current is not None and current.status == "approved":
        return PostingResult("skipped", ["Bu belgenin fişi zaten onaylanmış."], str(current.id))
    if current is not None and current.status == "pending_review":
        return PostingResult("skipped", ["Bu belgenin fişi incelemede; değiştirilmedi."],
                             str(current.id))

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

    if draft is not None:
        try:
            await validate_draft(session, mukellef_id=document.mukellef_id, draft=draft)
        except LedgerValidationError as exc:
            result = PostingResult("needs_review", [*result.reasons, str(exc)],
                                   direction=result.direction)
            draft = None

    if draft is None:
        if current is not None:
            # Taslak silinmez; güncel olmadığı işaretlenir (onayı D1b'de engellenir).
            result.reasons.append("Mevcut taslak korundu ancak güncel değil; incelenmeli.")
            result.stale_entry_id = str(current.id)
        return await _save(session, document, result)

    if current is None:
        entry = await persist_draft(
            session, buro_id=document.buro_id, mukellef_id=document.mukellef_id, draft=draft
        )
        result.entry_id = str(entry.id)
        return await _save(session, document, result)

    result.entry_id = str(current.id)
    old_lines = await _entry_lines(session, current)
    new_lines = _draft_lines(draft)
    if old_lines == new_lines and _same_header(current, draft):
        stored = meta.get("posting") or {}
        if (
            stored.get("entry_id") == result.entry_id
            and stored.get("status") == result.status
            and not stored.get("stale_entry_id")
            and document.review_status == _target_review_status(document, result)
        ):
            return result  # aynı içerik: hiçbir yazma yok
        return await _save(session, document, result)

    old_fp = line_fingerprint(current.entry_date, old_lines)
    await replace_draft_content(session, entry=current, draft=draft)
    await audit.record(
        session, "entry.draft_regenerated",
        buro_id=document.buro_id, mukellef_id=document.mukellef_id,
        target_type="journal_entry", target_id=current.id,
        details={
            "document_id": str(document.id),
            "trigger": trigger,
            "rule_name": draft.rule_name,
            "old_fingerprint": old_fp,
            "new_fingerprint": line_fingerprint(draft.entry_date, new_lines),
            "old_line_count": len(old_lines),
            "new_line_count": len(new_lines),
        },
        commit=False,
    )
    return await _save(session, document, result)


async def _save(session: AsyncSession, document: Belge, result: PostingResult) -> PostingResult:
    """Belgeye sonucu yazar ve flush eder. Hiçbir zaman commit etmez (sahiplik çağırandadır)."""
    document.extraction_metadata = {**(document.extraction_metadata or {}),
                                    "posting": result.metadata()}
    document.review_status = _target_review_status(document, result)
    await session.flush()
    return result
