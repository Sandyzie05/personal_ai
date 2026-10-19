"""Bounded public-only TTL cache adapted from stock_predictor.runtime_cache."""

from collections import OrderedDict
from threading import RLock
from time import monotonic

from ..config import FORECAST_CACHE_MAX_ENTRIES, FORECAST_CACHE_TTL_SECONDS


class TTLCache:
    def __init__(self, max_entries=FORECAST_CACHE_MAX_ENTRIES,
                 ttl_seconds=FORECAST_CACHE_TTL_SECONDS, clock=monotonic):
        if max_entries < 1 or ttl_seconds <= 0:
            raise ValueError("positive cache bounds required")
        self.max_entries, self.ttl_seconds, self.clock = max_entries, ttl_seconds, clock
        self._items = OrderedDict()
        self._lock = RLock()

    def get(self, key):
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            expires, value = item
            if expires <= self.clock():
                self._items.pop(key)
                return None
            self._items.move_to_end(key)
            return value

    def set(self, key, value):
        with self._lock:
            now = self.clock()
            for expired in [k for k, (expires, _) in self._items.items() if expires <= now]:
                self._items.pop(expired)
            self._items[key] = (now + self.ttl_seconds, value)
            self._items.move_to_end(key)
            while len(self._items) > self.max_entries:
                self._items.popitem(last=False)
            return value
