# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _to_bool(value: str | None, default: bool) -> bool:
    """문자열 환경 변수를 bool로 안전하게 변환한다."""
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


@dataclass(slots=True)
class Settings:
    """MCP 서버 실행에 필요한 환경 설정 묶음."""

    amadeus_client_id: str
    amadeus_client_secret: str
    amadeus_host: str
    timeout_seconds: int
    cache_ttl_seconds: int
    price_cache_ttl_seconds: int
    max_requests_per_minute: int
    max_requests_per_day: int
    allow_stale_cache_on_limit: bool
    quota_state_file: Path


def load_settings() -> Settings:
    """`.env` 및 환경 변수에서 실행 설정을 읽어 반환한다."""
    # dotenv를 먼저 로드해 로컬 개발 환경에서도 동일한 방식으로 읽는다.
    load_dotenv()

    client_id = os.getenv("AMADEUS_CLIENT_ID", "").strip()
    client_secret = os.getenv("AMADEUS_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise ValueError("AMADEUS_CLIENT_ID and AMADEUS_CLIENT_SECRET are required.")

    host = os.getenv("AMADEUS_HOST", "https://test.api.amadeus.com").strip().rstrip("/")
    # 파일 경로를 절대 경로로 고정해 실행 위치가 달라도 동일 파일을 사용한다.
    quota_file = Path(os.getenv("QUOTA_STATE_FILE", ".quota_state.json")).resolve()

    return Settings(
        amadeus_client_id=client_id,
        amadeus_client_secret=client_secret,
        amadeus_host=host,
        timeout_seconds=max(5, int(os.getenv("AMADEUS_TIMEOUT_SECONDS", "20"))),
        cache_ttl_seconds=max(1, int(os.getenv("CACHE_TTL_SECONDS", "20"))),
        price_cache_ttl_seconds=max(1, int(os.getenv("PRICE_CACHE_TTL_SECONDS", "15"))),
        max_requests_per_minute=max(1, int(os.getenv("MAX_REQUESTS_PER_MINUTE", "12"))),
        max_requests_per_day=max(1, int(os.getenv("MAX_REQUESTS_PER_DAY", "250"))),
        allow_stale_cache_on_limit=_to_bool(os.getenv("ALLOW_STALE_CACHE_ON_LIMIT"), True),
        quota_state_file=quota_file,
    )
