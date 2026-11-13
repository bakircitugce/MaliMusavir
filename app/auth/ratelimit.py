"""Giriş denemeleri için basit kayan pencere hız sınırı (Faz 0.6).

Süreç içinde tutulur: tek süreçli kurulum için yeterlidir. Çok süreçli / çok
sunuculu dağıtımda Redis tabanlı sayaçla değiştirilmelidir (Faz 3).
Hesap kilidi ise veritabanında tutulduğu için süreçler arası geçerlidir.
"""

import threading
import time
from collections import defaultdict, deque


class SlidingWindowLimiter:
    def __init__(self, max_events: int, window_seconds: float = 60.0) -> None:
        self.max_events = max_events
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str) -> bool:
        """Olayı kaydeder; sınır aşıldıysa False döner."""
        now = time.monotonic()
        with self._lock:
            events = self._events[key]
            while events and now - events[0] > self.window_seconds:
                events.popleft()
            if len(events) >= self.max_events:
                return False
            events.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._events.clear()
