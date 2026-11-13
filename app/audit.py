"""Denetim kaydı (Faz 1 / G8): kim, ne zaman, hangi kaydı onayladı/ters kaydetti/değiştirdi.

Kayıtlar append-only'dir: ORM seviyesinde UPDATE ve DELETE engellenir. PostgreSQL'e
geçişte (Faz 1 migration) aynı kural veritabanı trigger'ı ile de uygulanmalıdır.
"""

from typing import Any
from uuid import UUID

from fastapi import Request
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.models import AuditLog, User


class AuditImmutableError(RuntimeError):
    pass


@event.listens_for(Session, "before_flush")
def _forbid_audit_mutation(session: Session, flush_context, instances) -> None:
    for obj in list(session.dirty) + list(session.deleted):
        if isinstance(obj, AuditLog):
            raise AuditImmutableError("Denetim kayıtları değiştirilemez veya silinemez.")


def client_ip(request: Request | None) -> str | None:
    if request is None or request.client is None:
        return None
    return request.client.host


async def record(
    session: AsyncSession,
    action: str,
    *,
    request: Request | None = None,
    user: User | None = None,
    buro_id: UUID | None = None,
    mukellef_id: UUID | None = None,
    target_type: str | None = None,
    target_id: object | None = None,
    outcome: str = "success",
    details: dict[str, Any] | None = None,
    commit: bool = True,
) -> None:
    session.add(
        AuditLog(
            buro_id=buro_id or (user.buro_id if user is not None else None),
            user_id=user.id if user is not None else None,
            mukellef_id=mukellef_id,
            action=action,
            outcome=outcome,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            ip_address=client_ip(request),
            details=details or {},
        )
    )
    if commit:
        await session.commit()
