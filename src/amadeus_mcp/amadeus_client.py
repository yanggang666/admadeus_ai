# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import random
import time
from typing import Any

import httpx

from .config import Settings


class AmadeusClient:
    """Amadeus Self-Service API 통신을 담당하는 비동기 클라이언트."""

    def __init__(self, settings: Settings) -> None:
        """HTTP 클라이언트와 토큰 캐시 상태를 초기화한다."""
        self._settings = settings
        self._http = httpx.AsyncClient(base_url=settings.amadeus_host, timeout=settings.timeout_seconds)
        self._token_lock = asyncio.Lock()
        self._access_token: str | None = None
        self._token_expires_at = 0.0

    async def close(self) -> None:
        """내부 HTTP 세션을 정상 종료한다."""
        await self._http.aclose()

    async def _fetch_token(self) -> str:
        """OAuth 토큰을 새로 발급받아 캐시에 저장한다."""
        response = await self._http.post(
            "/v1/security/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self._settings.amadeus_client_id,
                "client_secret": self._settings.amadeus_client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if not response.is_success:
            raise RuntimeError(f"Token request failed ({response.status_code}): {response.text}")

        payload = response.json()
        token = payload.get("access_token")
        expires_in = int(payload.get("expires_in", 1800))
        if not token:
            raise RuntimeError("Token response did not include access_token.")

        self._access_token = token
        self._token_expires_at = time.time() + expires_in
        return token

    async def _get_token(self, force_refresh: bool = False) -> str:
        """유효한 토큰을 반환하며 필요 시 재발급한다."""
        async with self._token_lock:
            if not force_refresh and self._access_token and time.time() < self._token_expires_at - 30:
                return self._access_token
            return await self._fetch_token()

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        """에러 응답에서 사람이 읽기 쉬운 메시지를 추출한다."""
        try:
            payload = response.json()
        except Exception:
            return response.text

        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0]
            detail = first.get("detail") or first.get("title") or str(first)
            return str(detail)
        return str(payload)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        max_retries: int = 2,
    ) -> dict[str, Any]:
        """인증/재시도 로직을 포함해 Amadeus API를 호출한다."""
        transient_codes = {429, 500, 502, 503, 504}

        for attempt in range(max_retries + 1):
            token = await self._get_token(force_refresh=False)
            headers = {"Authorization": f"Bearer {token}"}

            response = await self._http.request(method=method, url=path, params=params, json=json_body, headers=headers)

            if response.status_code == 401 and attempt < max_retries:
                # 토큰 만료 가능성이 있으므로 강제 재발급 후 재시도한다.
                await self._get_token(force_refresh=True)
                continue

            if response.status_code in transient_codes and attempt < max_retries:
                # 일시적 장애/레이트리밋은 지수 백오프로 재시도한다.
                wait_s = (0.4 * (2**attempt)) + random.uniform(0, 0.25)
                await asyncio.sleep(wait_s)
                continue

            if not response.is_success:
                message = self._error_message(response)
                raise RuntimeError(f"Amadeus API error ({response.status_code}): {message}")

            return response.json()

        raise RuntimeError("Request failed after retries.")

    async def search_flight_offers(
        self,
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
        """항공권 오퍼 목록을 조회한다."""
        params: dict[str, Any] = {
            "originLocationCode": origin,
            "destinationLocationCode": destination,
            "departureDate": departure_date,
            "adults": adults,
            "nonStop": str(non_stop).lower(),
            "max": max_results,
        }
        if return_date:
            params["returnDate"] = return_date
        if travel_class:
            params["travelClass"] = travel_class
        if currency_code:
            params["currencyCode"] = currency_code

        return await self._request("GET", "/v2/shopping/flight-offers", params=params)

    async def price_flight_offer(self, flight_offer: dict[str, Any], currency_code: str | None = None) -> dict[str, Any]:
        """선택한 오퍼 1건을 재가격 조회하여 최신 운임을 확인한다."""
        body: dict[str, Any] = {
            "data": {
                "type": "flight-offers-pricing",
                "flightOffers": [flight_offer],
            }
        }
        if currency_code:
            body["data"]["currencyCode"] = currency_code

        return await self._request("POST", "/v1/shopping/flight-offers/pricing", json_body=body)
