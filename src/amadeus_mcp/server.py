# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from mcp.server.fastmcp import FastMCP

from .amadeus_client import AmadeusClient
from .cache import TTLCache
from .config import Settings, load_settings
from .formatters import offers_to_excel_rows, summarize_offer
from .quota import QuotaExceeded, QuotaGuard

mcp = FastMCP(name="amadeus-flight-mcp")


@dataclass(slots=True)
class Runtime:
    """서버 전역에서 재사용하는 런타임 객체 묶음."""

    settings: Settings
    client: AmadeusClient
    search_cache: TTLCache
    price_cache: TTLCache
    quota: QuotaGuard


_runtime: Runtime | None = None
_runtime_lock = asyncio.Lock()


def _stable_json(payload: dict[str, Any]) -> str:
    """캐시 키 생성을 위한 안정적인 JSON 문자열을 만든다."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _cache_key(prefix: str, payload: dict[str, Any]) -> str:
    """요청 페이로드를 해시해 충돌 가능성이 낮은 캐시 키를 만든다."""
    digest = hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"


def _normalize_search_args(
    *,
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str | None,
    adults: int,
    travel_class: str | None,
    currency_code: str | None,
    non_stop: bool,
    max_results: int,
) -> dict[str, Any]:
    """검색 파라미터를 정규화하여 API/캐시에 동일하게 사용한다."""
    return {
        "origin": origin.strip().upper(),
        "destination": destination.strip().upper(),
        "departure_date": departure_date.strip(),
        "return_date": return_date.strip() if return_date else None,
        "adults": max(1, adults),
        "travel_class": travel_class.strip().upper() if travel_class else None,
        "currency_code": currency_code.strip().upper() if currency_code else None,
        "non_stop": bool(non_stop),
        "max_results": min(20, max(1, max_results)),
    }


async def _get_runtime() -> Runtime:
    """런타임을 1회 초기화하고 이후에는 재사용한다."""
    global _runtime

    if _runtime is not None:
        return _runtime

    async with _runtime_lock:
        if _runtime is None:
            settings = load_settings()
            _runtime = Runtime(
                settings=settings,
                client=AmadeusClient(settings),
                search_cache=TTLCache(default_ttl_seconds=settings.cache_ttl_seconds),
                price_cache=TTLCache(default_ttl_seconds=settings.price_cache_ttl_seconds),
                quota=QuotaGuard(
                    state_file=settings.quota_state_file,
                    max_requests_per_minute=settings.max_requests_per_minute,
                    max_requests_per_day=settings.max_requests_per_day,
                ),
            )
    return _runtime


async def _cached_call(
    *,
    runtime: Runtime,
    cache: TTLCache,
    cache_key: str,
    ttl_seconds: int,
    fetcher: Callable[[], Awaitable[dict[str, Any]]],
) -> tuple[dict[str, Any], bool, bool, list[str]]:
    """캐시 우선 조회 후 필요 시 API 호출을 수행한다."""
    warnings: list[str] = []
    cached = await cache.get(cache_key, allow_stale=runtime.settings.allow_stale_cache_on_limit)

    if cached.hit and not cached.stale and isinstance(cached.value, dict):
        return cached.value, True, False, warnings

    try:
        await runtime.quota.acquire()
    except QuotaExceeded as exc:
        if cached.hit and cached.stale and isinstance(cached.value, dict):
            warnings.append(f"쿼터 초과로 만료 캐시를 반환했습니다. 재시도 가능 시간: {exc.retry_after_seconds}초")
            return cached.value, True, True, warnings
        raise RuntimeError(f"{exc}.")

    payload = await fetcher()
    await cache.set(cache_key, payload, ttl_seconds=ttl_seconds)
    return payload, False, False, warnings


def _extract_offers(payload: dict[str, Any], max_results: int) -> list[dict[str, Any]]:
    """응답에서 오퍼 배열을 안전하게 꺼내고 최대 개수를 제한한다."""
    offers = payload.get("data", [])
    if not isinstance(offers, list):
        return []
    return offers[: max(1, max_results)]


@mcp.tool()
async def quota_status() -> dict[str, Any]:
    """현재 분/일 사용량과 제한값을 조회한다."""
    runtime = await _get_runtime()
    usage = await runtime.quota.current_usage()
    return {
        "usage": usage,
        "cache_ttl_seconds": runtime.settings.cache_ttl_seconds,
        "price_cache_ttl_seconds": runtime.settings.price_cache_ttl_seconds,
        "allow_stale_cache_on_limit": runtime.settings.allow_stale_cache_on_limit,
        "host": runtime.settings.amadeus_host,
    }


@mcp.tool()
async def search_flight_offers(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str | None = None,
    adults: int = 1,
    travel_class: str | None = None,
    currency_code: str | None = None,
    non_stop: bool = False,
    max_results: int = 5,
    include_raw_offers: bool = False,
) -> dict[str, Any]:
    """항공권 오퍼를 조회하고 요약/원본 데이터를 선택적으로 반환한다."""
    runtime = await _get_runtime()
    args = _normalize_search_args(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        return_date=return_date,
        adults=adults,
        travel_class=travel_class,
        currency_code=currency_code,
        non_stop=non_stop,
        max_results=max_results,
    )
    key = _cache_key("search", args)

    async def _fetch() -> dict[str, Any]:
        """Amadeus 검색 API를 실제로 호출한다."""
        return await runtime.client.search_flight_offers(**args)

    payload, from_cache, stale_cache, warnings = await _cached_call(
        runtime=runtime,
        cache=runtime.search_cache,
        cache_key=key,
        ttl_seconds=runtime.settings.cache_ttl_seconds,
        fetcher=_fetch,
    )

    offers = _extract_offers(payload, args["max_results"])
    rows = offers_to_excel_rows(offers)
    usage = await runtime.quota.current_usage()

    result: dict[str, Any] = {
        "cached": from_cache,
        "stale_cache": stale_cache,
        "warnings": warnings,
        "offers_count": len(offers),
        "rows": rows,
        "usage": usage,
    }
    if include_raw_offers:
        result["offers"] = offers
    return result


@mcp.tool()
async def search_flights_excel_rows(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str | None = None,
    adults: int = 1,
    travel_class: str | None = None,
    currency_code: str | None = None,
    non_stop: bool = False,
    max_results: int = 10,
) -> dict[str, Any]:
    """Excel에 바로 붙여넣기 좋은 행 데이터만 반환한다."""
    result = await search_flight_offers(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        return_date=return_date,
        adults=adults,
        travel_class=travel_class,
        currency_code=currency_code,
        non_stop=non_stop,
        max_results=max_results,
        include_raw_offers=False,
    )
    return {
        "cached": result["cached"],
        "stale_cache": result["stale_cache"],
        "warnings": result["warnings"],
        "offers_count": result["offers_count"],
        "rows": result["rows"],
        "usage": result["usage"],
    }


@mcp.tool()
async def price_flight_offer(
    flight_offer: dict[str, Any],
    currency_code: str | None = None,
) -> dict[str, Any]:
    """오퍼 1건을 재가격 조회하여 최신 운임/가능 좌석 정보를 확인한다."""
    runtime = await _get_runtime()
    args = {
        "flight_offer": flight_offer,
        "currency_code": currency_code.strip().upper() if currency_code else None,
    }
    key = _cache_key("price", args)

    async def _fetch() -> dict[str, Any]:
        """Amadeus 재가격 API를 실제로 호출한다."""
        return await runtime.client.price_flight_offer(
            flight_offer=flight_offer,
            currency_code=args["currency_code"],
        )

    payload, from_cache, stale_cache, warnings = await _cached_call(
        runtime=runtime,
        cache=runtime.price_cache,
        cache_key=key,
        ttl_seconds=runtime.settings.price_cache_ttl_seconds,
        fetcher=_fetch,
    )

    priced_offers = _extract_offers(payload.get("data", {}), 1) if isinstance(payload.get("data"), dict) else []
    if not priced_offers:
        # 일부 응답은 data.flightOffers 구조가 아닌 data 배열로 올 수 있어 fallback 처리한다.
        data_obj = payload.get("data")
        if isinstance(data_obj, dict):
            flight_offers = data_obj.get("flightOffers", [])
            if isinstance(flight_offers, list):
                priced_offers = flight_offers[:1]

    summary = summarize_offer(priced_offers[0], 1) if priced_offers else {}
    usage = await runtime.quota.current_usage()

    return {
        "cached": from_cache,
        "stale_cache": stale_cache,
        "warnings": warnings,
        "summary_row": summary,
        "priced_offer": priced_offers[0] if priced_offers else None,
        "usage": usage,
    }


@mcp.tool()
async def search_and_price_top(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str | None = None,
    adults: int = 1,
    travel_class: str | None = None,
    currency_code: str | None = None,
    non_stop: bool = False,
    max_results: int = 5,
    top_n: int = 1,
) -> dict[str, Any]:
    """검색 후 상위 N개 오퍼만 재가격 조회한다 (비용 절감을 위해 N<=3 권장)."""
    normalized_top_n = min(3, max(1, top_n))
    search_result = await search_flight_offers(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        return_date=return_date,
        adults=adults,
        travel_class=travel_class,
        currency_code=currency_code,
        non_stop=non_stop,
        max_results=max(max_results, normalized_top_n),
        include_raw_offers=True,
    )

    raw_offers = search_result.get("offers", [])
    if not isinstance(raw_offers, list):
        raw_offers = []

    priced_rows: list[dict[str, Any]] = []
    priced_offers: list[dict[str, Any]] = []
    warnings = list(search_result.get("warnings", []))

    for offer in raw_offers[:normalized_top_n]:
        try:
            priced = await price_flight_offer(offer, currency_code=currency_code)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"재가격 실패: {exc}")
            continue

        summary_row = priced.get("summary_row")
        if isinstance(summary_row, dict):
            priced_rows.append(summary_row)
        priced_offer = priced.get("priced_offer")
        if isinstance(priced_offer, dict):
            priced_offers.append(priced_offer)
        warnings.extend(priced.get("warnings", []))

    runtime = await _get_runtime()
    usage = await runtime.quota.current_usage()
    return {
        "search_rows": search_result.get("rows", []),
        "priced_rows": priced_rows,
        "priced_offers": priced_offers,
        "warnings": warnings,
        "usage": usage,
    }


def main() -> None:
    """CLI에서 호출되는 MCP 서버 실행 진입점."""
    # 시작 전에 설정 누락을 빠르게 확인해 초기 실패를 명확히 한다.
    load_settings()
    mcp.run()


if __name__ == "__main__":
    main()
