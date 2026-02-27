# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any


def _safe_get_price(offer: dict[str, Any]) -> tuple[str, str]:
    """오퍼에서 통화와 총액 문자열을 안전하게 추출한다."""
    price = offer.get("price", {}) if isinstance(offer.get("price"), dict) else {}
    return str(price.get("currency", "")), str(price.get("total", ""))


def _segment_chain(itinerary: dict[str, Any]) -> list[dict[str, Any]]:
    """여정(itinerary)의 세그먼트 목록을 반환한다."""
    segments = itinerary.get("segments", [])
    return segments if isinstance(segments, list) else []


def summarize_offer(offer: dict[str, Any], rank: int) -> dict[str, Any]:
    """오퍼 1건을 Excel 친화적인 단일 행 데이터로 요약한다."""
    itineraries = offer.get("itineraries", [])
    first_itinerary = itineraries[0] if itineraries else {}
    segments = _segment_chain(first_itinerary)

    first_segment = segments[0] if segments else {}
    last_segment = segments[-1] if segments else {}

    dep = first_segment.get("departure", {}) if isinstance(first_segment.get("departure"), dict) else {}
    arr = last_segment.get("arrival", {}) if isinstance(last_segment.get("arrival"), dict) else {}

    currency, total = _safe_get_price(offer)
    total_stops = sum(max(0, len(_segment_chain(it)) - 1) for it in itineraries if isinstance(it, dict))
    seats = offer.get("numberOfBookableSeats")
    validating = offer.get("validatingAirlineCodes", [])

    # Excel에서 바로 표로 쓰기 쉽도록 평평한(flat) 구조로 반환한다.
    return {
        "rank": rank,
        "carrier": first_segment.get("carrierCode", ""),
        "flight_number": first_segment.get("number", ""),
        "origin": dep.get("iataCode", ""),
        "destination": arr.get("iataCode", ""),
        "departure_at": dep.get("at", ""),
        "arrival_at": arr.get("at", ""),
        "duration": first_itinerary.get("duration", ""),
        "stops": total_stops,
        "bookable_seats": seats if seats is not None else "",
        "currency": currency,
        "total_price": total,
        "last_ticketing_date": offer.get("lastTicketingDate", ""),
        "validating_airline_codes": ",".join(validating) if isinstance(validating, list) else "",
    }


def offers_to_excel_rows(offers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """오퍼 목록 전체를 Excel 행 목록으로 변환한다."""
    rows: list[dict[str, Any]] = []
    for idx, offer in enumerate(offers, start=1):
        rows.append(summarize_offer(offer, idx))
    return rows
