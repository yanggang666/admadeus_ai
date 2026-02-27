# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass(slots=True)
class QuotaState:
    """일 단위 사용량 상태."""

    day_key: str
    used_today: int


class QuotaExceeded(Exception):
    """분/일 쿼터를 초과했을 때 던지는 예외."""

    def __init__(self, limit_type: str, retry_after_seconds: int, message: str) -> None:
        """초과 유형과 재시도 가능 시간을 함께 담아 초기화한다."""
        super().__init__(message)
        self.limit_type = limit_type
        self.retry_after_seconds = retry_after_seconds


class QuotaGuard:
    """분/일 단위 API 호출 제한을 관리한다."""

    def __init__(self, state_file: Path, max_requests_per_minute: int, max_requests_per_day: int) -> None:
        """저장 파일과 제한값을 받아 가드 상태를 초기화한다."""
        self._state_file = state_file
        self._max_per_minute = max_requests_per_minute
        self._max_per_day = max_requests_per_day
        self._lock = asyncio.Lock()
        self._minute_events: deque[float] = deque()
        self._state = self._load_state()

    def _today_key(self) -> str:
        """UTC 기준 오늘 날짜 키를 반환한다."""
        return datetime.now(timezone.utc).date().isoformat()

    def _load_state(self) -> QuotaState:
        """디스크에서 일 단위 카운터를 UTF-8로 읽어온다."""
        day_key = self._today_key()
        if not self._state_file.exists():
            return QuotaState(day_key=day_key, used_today=0)

        try:
            raw = json.loads(self._state_file.read_text(encoding="utf-8"))
            loaded_day = str(raw.get("day_key", day_key))
            loaded_used = int(raw.get("used_today", 0))
            if loaded_day != day_key:
                return QuotaState(day_key=day_key, used_today=0)
            return QuotaState(day_key=loaded_day, used_today=max(0, loaded_used))
        except Exception:
            return QuotaState(day_key=day_key, used_today=0)

    def _save_state(self) -> None:
        """일 단위 카운터를 UTF-8 JSON으로 저장한다."""
        payload = {"day_key": self._state.day_key, "used_today": self._state.used_today}
        self._state_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _rollover_day(self) -> None:
        """날짜가 바뀌면 일 카운터를 0으로 리셋한다."""
        day_key = self._today_key()
        if day_key != self._state.day_key:
            self._state = QuotaState(day_key=day_key, used_today=0)
            self._save_state()

    def _prune_minute_events(self, now: float) -> None:
        """1분보다 오래된 이벤트를 큐에서 제거한다."""
        while self._minute_events and now - self._minute_events[0] >= 60:
            self._minute_events.popleft()

    async def current_usage(self) -> dict[str, int]:
        """현재 분/일 사용량과 제한값을 반환한다."""
        async with self._lock:
            now = time.time()
            self._prune_minute_events(now)
            self._rollover_day()
            return {
                "used_last_minute": len(self._minute_events),
                "limit_per_minute": self._max_per_minute,
                "used_today": self._state.used_today,
                "limit_per_day": self._max_per_day,
            }

    async def acquire(self) -> None:
        """호출 1건을 예약하고 제한 초과 시 예외를 발생시킨다."""
        async with self._lock:
            now = time.time()
            self._prune_minute_events(now)
            self._rollover_day()

            if len(self._minute_events) >= self._max_per_minute:
                oldest = self._minute_events[0]
                retry_after = max(1, int(60 - (now - oldest)) + 1)
                raise QuotaExceeded(
                    limit_type="minute",
                    retry_after_seconds=retry_after,
                    message=f"Per-minute quota exceeded. Retry after {retry_after}s.",
                )

            if self._state.used_today >= self._max_per_day:
                utc_now = datetime.now(timezone.utc)
                tomorrow = (utc_now + timedelta(days=1)).date()
                next_reset = datetime.combine(tomorrow, datetime.min.time(), tzinfo=timezone.utc)
                retry_after = max(1, int((next_reset - utc_now).total_seconds()))
                raise QuotaExceeded(
                    limit_type="day",
                    retry_after_seconds=retry_after,
                    message=f"Daily quota exceeded. Retry after {retry_after}s.",
                )

            self._minute_events.append(now)
            self._state.used_today += 1
            self._save_state()
