import pytest

from app.erp import retry_export


@pytest.mark.asyncio
async def test_retry_export_retries_then_returns() -> None:
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("temporary")
        return "ok"

    assert await retry_export(operation, max_attempts=3) == "ok"
    assert attempts == 3


@pytest.mark.asyncio
async def test_retry_export_raises_last_error_after_limit() -> None:
    async def operation() -> str:
        raise RuntimeError("permanent")

    with pytest.raises(RuntimeError, match="permanent"):
        await retry_export(operation, max_attempts=2)
