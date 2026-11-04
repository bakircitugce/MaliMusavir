import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar


T = TypeVar("T")


async def retry_export(
    operation: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    delay_seconds: float = 0,
) -> T:
    if max_attempts < 1 or max_attempts > 10:
        raise ValueError("ERP aktarım deneme sayısı 1 ile 10 arasında olmalıdır.")
    if delay_seconds < 0:
        raise ValueError("ERP aktarım bekleme süresi negatif olamaz.")
    last_error: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return await operation()
        except Exception as exc:
            last_error = exc
            if attempt == max_attempts - 1:
                raise
            if delay_seconds:
                await asyncio.sleep(delay_seconds)
    raise RuntimeError("ERP aktarımı beklenmeyen şekilde sonuçlanmadı.") from last_error
