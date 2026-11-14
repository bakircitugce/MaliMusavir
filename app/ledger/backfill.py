"""Eski onaylı fişlere geriye dönük fiş numarası (Faz 2 / Part 2c).

Varsayılan kullanım DRY-RUN'dır: plan çıkarılır, raporlanır, hiçbir şey yazılmaz.
Gerçek uygulama yalnızca dry-run'da incelenen planın parmak izi (`--onay`) verilirse ve plan
hâlâ birebir aynıysa yapılır; aksi halde reddedilir.

Kapsam: status IN ('approved', 'reversed') ve fis_no IS NULL olan fişler. Taslaklar dışarıda.

Numaralandırma sırası (mükellef + hesap dönemi içinde, belirleyici):
    1. entry_date (fiş tarihi)
    2. denetim kaydından kanıtlanmış onay zamanı (kanıt yoksa sona)
    3. created_at
    4. id

Bloklayan durumlar (plan uygulanamaz, tamamı ya da hiçbiri):
    - fiş tarihini kapsayan hesap dönemi yok (dönem OTOMATİK OLUŞTURULMAZ)
    - tarih birden fazla hesap dönemine düşüyor (çakışma)
    - hesap dönemi kapalı
    - fişte zaten farklı bir hesap_donemi_id var
    - aynı kapsamda numaralı fiş zaten var (eski fişleri araya sokmak mevcut numaraları
      kaydırmayı gerektirir; mevcut numaralar ASLA değiştirilmez)
    - sayaç 0'dan büyük ama numaralı fiş yok (tutarsızlık)
    - onaylı fiş dengesiz veya 2'den az satırlı
    - ters kayıt kanıtı çelişkili

Doldurulan alanlar yalnızca BOŞ olanlardır: hesap_donemi_id, fis_no (NumberingService ile),
approved_at / approved_by (yalnızca denetim kaydında kanıt varsa), reversal_of_id (yalnızca
ledger.reverse denetim kaydıyla kanıtlanırsa). yevmiye_no verilmez (ay kilidinde verilir).
"""

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import AuditLog, HesapDonemi, JournalEntry, JournalLine, NumaraSayaci
from app.ledger.numbering import SayacTuru, allocate

ELIGIBLE_STATUSES = ("approved", "reversed")
TARGET_COLUMNS = ("hesap_donemi_id", "fis_no", "approved_at", "approved_by", "reversal_of_id")


class BackfillError(RuntimeError):
    pass


@dataclass
class Issue:
    code: str
    message: str
    entry_id: UUID | None = None


@dataclass
class Assignment:
    entry_id: UUID
    buro_id: UUID
    mukellef_id: UUID
    hesap_donemi_id: UUID
    entry_date: date
    description: str
    fis_no: int
    approved_at: datetime | None
    approved_by: UUID | None
    reversal_of_id: UUID | None


@dataclass
class ScopePlan:
    buro_id: UUID
    mukellef_id: UUID
    hesap_donemi_id: UUID
    period: tuple[date, date]
    assignments: list[Assignment] = field(default_factory=list)


@dataclass
class BackfillPlan:
    scopes: list[ScopePlan]
    issues: list[Issue]
    skipped_numbered: int  # zaten numaralı (dokunulmayan) onaylı fiş sayısı
    eligible: int

    @property
    def assignments(self) -> list[Assignment]:
        return [a for s in self.scopes for a in s.assignments]

    @property
    def blocked(self) -> bool:
        return bool(self.issues)

    @property
    def fingerprint(self) -> str:
        payload = {
            "assignments": sorted(
                [
                    str(a.entry_id), str(a.hesap_donemi_id), a.fis_no,
                    a.approved_at.isoformat() if a.approved_at else None,
                    str(a.approved_by) if a.approved_by else None,
                    str(a.reversal_of_id) if a.reversal_of_id else None,
                ]
                for a in self.assignments
            ),
            "issues": sorted([i.code, str(i.entry_id)] for i in self.issues),
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()[:16]

    def report_lines(self) -> list[str]:
        lines = [
            f"Kapsamdaki numarasız onaylı fiş: {self.eligible} | zaten numaralı (dokunulmayacak): "
            f"{self.skipped_numbered}",
        ]
        for scope in self.scopes:
            a = scope.assignments
            if not a:
                continue
            evidence = sum(1 for x in a if x.approved_at)
            reversals = sum(1 for x in a if x.reversal_of_id)
            lines.append(
                f"- Mükellef {scope.mukellef_id} | hesap dönemi {scope.period[0]}–{scope.period[1]}: "
                f"{len(a)} fiş -> fiş no {a[0].fis_no}–{a[-1].fis_no} | onay kanıtı {evidence}/{len(a)} "
                f"| ters kayıt bağı {reversals}"
            )
            for x in a:
                lines.append(
                    f"    {x.fis_no:>5}  {x.entry_date}  {x.description[:60]}"
                    + ("  [onay kanıtı yok]" if not x.approved_at else "")
                )
        if self.issues:
            lines.append(f"BLOKLAYAN SORUNLAR ({len(self.issues)}): plan UYGULANAMAZ.")
            for issue in self.issues:
                ref = f" [fiş {issue.entry_id}]" if issue.entry_id else ""
                lines.append(f"  ! {issue.code}: {issue.message}{ref}")
        elif not self.assignments:
            lines.append("Yapılacak iş yok.")
        lines.append(f"Plan parmak izi: {self.fingerprint}")
        return lines


def _uuid(value: object) -> UUID | None:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (ValueError, TypeError):
        return None


async def _audit_evidence(session: AsyncSession, buro_ids: set[UUID]):
    """Onay zamanı/kişisi ve ters kayıt bağlantısı için kanıtlar (yalnızca denetim kaydı)."""
    approvals: dict[UUID, tuple[datetime, UUID | None]] = {}
    reversals: dict[UUID, list[UUID]] = defaultdict(list)  # ters fiş -> [orijinaller]
    rows = (
        await session.execute(
            select(AuditLog).where(
                AuditLog.action.in_(("document.approve", "ledger.reverse")),
                AuditLog.outcome == "success",
                AuditLog.buro_id.in_(buro_ids),
            ).order_by(AuditLog.created_at)
        )
    ).scalars().all()
    for row in rows:
        target = _uuid(row.target_id)
        if row.action == "document.approve" and target:
            approvals.setdefault(target, (row.created_at, row.user_id))
        elif row.action == "ledger.reverse" and target:
            reversal = _uuid((row.details or {}).get("reversal_entry_id"))
            if reversal:
                reversals[reversal].append(target)
                approvals.setdefault(reversal, (row.created_at, row.user_id))
    return approvals, reversals


async def build_plan(session: AsyncSession, *, mukellef_id: UUID | None = None) -> BackfillPlan:
    """Salt-okunur: hiçbir şey yazmaz."""
    base = select(JournalEntry).where(JournalEntry.status.in_(ELIGIBLE_STATUSES))
    if mukellef_id is not None:
        base = base.where(JournalEntry.mukellef_id == mukellef_id)
    entries = (await session.scalars(base)).all()
    pending = [e for e in entries if e.fis_no is None]
    skipped = len(entries) - len(pending)
    issues: list[Issue] = []
    if not pending:
        return BackfillPlan([], [], skipped, 0)

    buro_ids = {e.buro_id for e in pending}
    mukellef_ids = {e.mukellef_id for e in pending}
    periods = (
        await session.scalars(select(HesapDonemi).where(HesapDonemi.mukellef_id.in_(mukellef_ids)))
    ).all()
    approvals, reversals = await _audit_evidence(session, buro_ids)

    # Satır dengesi
    totals = {
        row[0]: (row[1] or Decimal(0), row[2] or Decimal(0), row[3])
        for row in (
            await session.execute(
                select(JournalLine.journal_entry_id, func.sum(JournalLine.debit),
                       func.sum(JournalLine.credit), func.count(JournalLine.id))
                .where(JournalLine.journal_entry_id.in_([e.id for e in pending]))
                .group_by(JournalLine.journal_entry_id)
            )
        ).all()
    }

    by_scope: dict[tuple[UUID, UUID, UUID], list[JournalEntry]] = defaultdict(list)
    period_by_id = {p.id: p for p in periods}
    for entry in pending:
        debit, credit, count = totals.get(entry.id, (Decimal(0), Decimal(0), 0))
        if count < 2 or debit != credit:
            issues.append(Issue("dengesiz_fis", f"Onaylı fiş dengesiz veya eksik satırlı "
                                f"(borç {debit}, alacak {credit}, satır {count}).", entry.id))
        matches = [
            p for p in periods
            if p.buro_id == entry.buro_id and p.mukellef_id == entry.mukellef_id
            and p.start_date <= entry.entry_date <= p.end_date
        ]
        if not matches:
            issues.append(Issue("donem_yok", f"{entry.entry_date} tarihini kapsayan hesap dönemi yok; "
                                "dönem açıkça oluşturulmalı (otomatik oluşturulmaz).", entry.id))
            continue
        if len(matches) > 1:
            issues.append(Issue("donem_cakisiyor", f"{entry.entry_date} birden fazla hesap dönemine "
                                "düşüyor.", entry.id))
            continue
        period = matches[0]
        if period.status != "open":
            issues.append(Issue("donem_kapali", f"Hesap dönemi ({period.start_date}–{period.end_date}) "
                                "kapalı.", entry.id))
            continue
        if entry.hesap_donemi_id is not None and entry.hesap_donemi_id != period.id:
            issues.append(Issue("farkli_donem", "Fişte zaten farklı bir hesap dönemi kayıtlı.", entry.id))
            continue
        by_scope[(entry.buro_id, entry.mukellef_id, period.id)].append(entry)

    # Ters kayıt kanıtı
    reversal_links: dict[UUID, UUID] = {}
    pending_ids = {e.id for e in pending}
    linked_originals = set(
        (await session.scalars(
            select(JournalEntry.reversal_of_id).where(JournalEntry.reversal_of_id.is_not(None))
        )).all()
    )
    all_entries = {e.id: e for e in entries}
    for reversal_id, originals in reversals.items():
        if reversal_id not in pending_ids or all_entries[reversal_id].reversal_of_id is not None:
            continue
        unique = set(originals)
        original = next(iter(unique)) if len(unique) == 1 else None
        if (
            original is None
            or original not in all_entries
            or all_entries[original].mukellef_id != all_entries[reversal_id].mukellef_id
            or original in linked_originals
            or original in reversal_links.values()
        ):
            issues.append(Issue("ters_kayit_celiskili", "Ters kayıt kanıtı tek ve tutarlı bir "
                                "orijinal fişi göstermiyor.", reversal_id))
            continue
        reversal_links[reversal_id] = original

    scopes: list[ScopePlan] = []
    for (buro_id, mk_id, period_id), scope_entries in sorted(by_scope.items(), key=lambda kv: str(kv[0])):
        period = period_by_id[period_id]
        numbered = await session.scalar(
            select(func.count(JournalEntry.id)).where(
                JournalEntry.mukellef_id == mk_id,
                JournalEntry.hesap_donemi_id == period_id,
                JournalEntry.fis_no.is_not(None),
            )
        )
        counter = await session.scalar(
            select(NumaraSayaci.son_numara).where(
                NumaraSayaci.mukellef_id == mk_id,
                NumaraSayaci.hesap_donemi_id == period_id,
                NumaraSayaci.sayac_turu == SayacTuru.FIS.value,
            )
        )
        if numbered:
            issues.append(Issue("numaralandirma_baslamis",
                                f"Mükellef {mk_id}, dönem {period.start_date}–{period.end_date}: "
                                f"{numbered} fiş zaten numaralı. Eski fişleri araya sokmak mevcut "
                                "numaraları değiştirmeyi gerektirir; yapılmaz."))
            continue
        if counter:
            issues.append(Issue("sayac_tutarsiz",
                                f"Mükellef {mk_id}, dönem {period.start_date}–{period.end_date}: sayaç "
                                f"{counter} ama numaralı fiş yok."))
            continue

        def sort_key(e: JournalEntry):
            evidence = approvals.get(e.id)
            approved_ts = evidence[0].isoformat() if evidence else "9999"
            return (e.entry_date, approved_ts, e.created_at.isoformat() if e.created_at else "", str(e.id))

        plan = ScopePlan(buro_id, mk_id, period_id, (period.start_date, period.end_date))
        for number, entry in enumerate(sorted(scope_entries, key=sort_key), start=1):
            evidence = approvals.get(entry.id) if entry.approved_at is None else None
            plan.assignments.append(
                Assignment(
                    entry_id=entry.id, buro_id=buro_id, mukellef_id=mk_id, hesap_donemi_id=period_id,
                    entry_date=entry.entry_date, description=entry.description, fis_no=number,
                    approved_at=evidence[0] if evidence else entry.approved_at,
                    approved_by=evidence[1] if evidence else entry.approved_by,
                    reversal_of_id=reversal_links.get(entry.id),
                )
            )
        scopes.append(plan)
    return BackfillPlan(scopes, issues, skipped, len(pending))


# --- Uygulama ve doğrulama ----------------------------------------------------


async def _snapshot(session: AsyncSession) -> dict:
    entries = (await session.scalars(select(JournalEntry))).all()
    lines = await session.scalar(select(func.count(JournalLine.id)))
    columns = [c.name for c in JournalEntry.__table__.columns]
    return {
        "lines": lines,
        "entries": {
            e.id: {c: getattr(e, c) for c in columns} for e in entries
        },
    }


def verify(before: dict, after: dict, plan: BackfillPlan) -> list[str]:
    """Uygulama sonrası bütünlük kontrolü; boş liste = sorun yok."""
    problems: list[str] = []
    if before["lines"] != after["lines"]:
        problems.append("Yevmiye satır sayısı değişti.")
    if set(before["entries"]) != set(after["entries"]):
        problems.append("Fiş kümesi değişti (ekleme/silme).")
        return problems
    targets = {a.entry_id: a for a in plan.assignments}
    for entry_id, old in before["entries"].items():
        new = after["entries"][entry_id]
        for column, value in old.items():
            if column == "updated_at":
                continue
            if entry_id in targets and column in TARGET_COLUMNS:
                if value is not None and new[column] != value:
                    problems.append(f"Mevcut değer değişti: {entry_id}.{column}")
                continue
            if new[column] != value:
                problems.append(f"Hedef dışı sütun değişti: {entry_id}.{column}")
    for scope in plan.scopes:
        numbers = sorted(after["entries"][a.entry_id]["fis_no"] for a in scope.assignments)
        if numbers != list(range(1, len(scope.assignments) + 1)):
            problems.append(f"Kapsam {scope.mukellef_id}/{scope.hesap_donemi_id}: numaralar 1..n değil.")
    return problems


async def apply_plan(session: AsyncSession, *, expected_fingerprint: str,
                     mukellef_id: UUID | None = None) -> BackfillPlan:
    """Planı yeniden hesaplar; parmak izi aynıysa tek transaction'da uygular ve doğrular.

    Commit çağırana aittir; doğrulama başarısızsa BackfillError fırlatılır (çağıran rollback eder).
    """
    plan = await build_plan(session, mukellef_id=mukellef_id)
    if plan.blocked:
        raise BackfillError("Bloklayan sorunlar var; plan uygulanamaz.")
    if plan.fingerprint != expected_fingerprint:
        raise BackfillError(
            f"Plan değişti (beklenen {expected_fingerprint}, güncel {plan.fingerprint}); "
            "önce dry-run'ı yeniden inceleyin."
        )
    if not plan.assignments:
        return plan
    before = await _snapshot(session)
    for scope in plan.scopes:
        numbers = await allocate(
            session, buro_id=scope.buro_id, mukellef_id=scope.mukellef_id,
            hesap_donemi_id=scope.hesap_donemi_id, kind=SayacTuru.FIS,
            count=len(scope.assignments),
        )
        if list(numbers) != [a.fis_no for a in scope.assignments]:
            raise BackfillError("Sayaçtan beklenmeyen numara aralığı döndü.")
        for assignment in scope.assignments:
            entry = await session.get(JournalEntry, assignment.entry_id)
            entry.hesap_donemi_id = entry.hesap_donemi_id or assignment.hesap_donemi_id
            entry.fis_no = assignment.fis_no
            if entry.approved_at is None and assignment.approved_at is not None:
                entry.approved_at = assignment.approved_at
                entry.approved_by = assignment.approved_by
            if entry.reversal_of_id is None and assignment.reversal_of_id is not None:
                entry.reversal_of_id = assignment.reversal_of_id
    await session.flush()
    session.expire_all()
    problems = verify(before, await _snapshot(session), plan)
    if problems:
        raise BackfillError("Bütünlük doğrulaması başarısız: " + "; ".join(problems[:5]))
    return plan
