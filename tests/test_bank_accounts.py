from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bank import (
    BankAccountInput,
    create_bank_account,
    list_bank_accounts,
    set_bank_account_status,
)
from app.core.models import Base
from app.ledger import LedgerValidationError


@pytest.mark.asyncio
async def test_bank_account_list_and_status_are_tenant_scoped() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    buro_id = uuid4()
    mukellef_id = uuid4()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        account = await create_bank_account(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            account=BankAccountInput("TR00", "Demo", "Vadesiz", "102.01.001"),
        )
        assert len(await list_bank_accounts(
            session, buro_id=buro_id, mukellef_id=mukellef_id, status="active"
        )) == 1
        await set_bank_account_status(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            account_id=account.id,
            status="inactive",
        )
        assert await list_bank_accounts(
            session, buro_id=buro_id, mukellef_id=mukellef_id, status="active"
        ) == []
        with pytest.raises(LedgerValidationError, match="bulunamadı"):
            await set_bank_account_status(
                session,
                buro_id=uuid4(),
                mukellef_id=mukellef_id,
                account_id=account.id,
                status="active",
            )
    await engine.dispose()
