# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class CacheEntry:
    """캐시 값과 만료 시각을 보관한다."""

    value: Any
    expires_at: float


@dataclass(slots=True)
class CacheLookup:
    """캐시 조회 결과(히트/만료 여부)를 표현한다."""

    hit: bool
    stale: bool
    value: Any | None


class TTLCache:
    """TTL(Time-To-Live) 기반의 간단한 비동기 메모리 캐시."""

    def __init__(self, default_ttl_seconds: int) -> None:
        """기본 TTL을 설정하고 내부 저장소를 초기화한다."""
        self._default_ttl = default_ttl_seconds
        self._entries: dict[str, CacheEntry] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str, allow_stale: bool = False) -> CacheLookup:
        """키를 조회하고 필요 시 만료된(stale) 값도 반환한다."""
        async with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return CacheLookup(hit=False, stale=False, value=None)

            now = time.time()
            if entry.expires_at >= now:
                return CacheLookup(hit=True, stale=False, value=entry.value)

            if allow_stale:
                return CacheLookup(hit=True, stale=True, value=entry.value)

            self._entries.pop(key, None)
            return CacheLookup(hit=False, stale=False, value=None)

    async def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        """키/값을 저장하고 TTL을 갱신한다."""
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        expires_at = time.time() + ttl
        async with self._lock:
            self._entries[key] = CacheEntry(value=value, expires_at=expires_at)
