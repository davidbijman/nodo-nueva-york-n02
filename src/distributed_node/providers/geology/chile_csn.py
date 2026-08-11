"""Adaptador geológico chileno basado en el Centro Sismológico Nacional."""

from __future__ import annotations

import html
import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import httpx

from ...sonantia_protocol import isoformat_utc

PROVIDER_ID = "chile-csn"
PROVIDER_LABEL = "Centro Sismológico Nacional"
REGION_LABEL = "Chile"
COUNTRY_CODE = "CL"

CSN_HOME_URL = "https://www.sismologia.cl/"
CSN_DAILY_URL = "https://www.sismologia.cl/sismicidad/sismos-por-dia.html"
CSN_CATALOG_URL_TEMPLATE = "https://www.sismologia.cl/sismicidad/catalogo/{year}/{month}/{day}.html"
SANTIAGO_TZ = "America/Santiago"


def _clean_text(value: str) -> str:
    decoded = html.unescape(re.sub(r"<[^>]+>", " ", value))
    return re.sub(r"\s+", " ", decoded).strip()


def _parse_local_datetime(value: str) -> datetime:
    parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    return parsed.replace(tzinfo=ZoneInfo(SANTIAGO_TZ))


def unavailable_seismic_snapshot(moment: datetime, error: str | None = None) -> dict[str, Any]:
    generated_at = isoformat_utc(moment)
    return {
        "status": "unavailable",
        "provider": PROVIDER_ID,
        "provider_label": PROVIDER_LABEL,
        "region_label": REGION_LABEL,
        "country_code": COUNTRY_CODE,
        "source_url": CSN_DAILY_URL,
        "generated_at": generated_at,
        "observed_at": None,
        "window_hours": 24,
        "count": 0,
        "events": [],
        "error": error,
    }


def _home_events(markup: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    row_pattern = re.compile(
        r"<tr(?P<attrs>[^>]*)>\s*"
        r"<td>\s*<a\s+href=\"(?P<href>[^\"]+)\">(?P<local_time>[^<]+)</a><br>\s*"
        r"(?P<location>.*?)</td>\s*"
        r"<td>(?P<depth>.*?)</td>\s*"
        r'<td class="magnitud">(?P<magnitude>.*?)</td>\s*'
        r"</tr>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in row_pattern.finditer(markup):
        events.append(
            {
                "local_time": _clean_text(match.group("local_time")),
                "location": _clean_text(match.group("location")),
                "depth": _clean_text(match.group("depth")),
                "magnitude": _clean_text(match.group("magnitude")),
                "felt": "percibido" in match.group("attrs"),
                "url": urljoin(CSN_HOME_URL, match.group("href")),
            }
        )
    return events


def _catalog_events(markup: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    row_pattern = re.compile(
        r"<tr(?P<attrs>[^>]*)>\s*"
        r"<td>\s*<a\s+href=\"(?P<href>[^\"]+)\">(?P<local_time>[^<]+)</a><br>\s*"
        r"(?P<location>.*?)</td>\s*"
        r"<td>.*?</td>\s*"
        r"<td>(?P<coordinates>.*?)</td>\s*"
        r"<td>(?P<depth>.*?)</td>\s*"
        r'<td class="magnitud">(?P<magnitude>.*?)</td>\s*'
        r"</tr>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in row_pattern.finditer(markup):
        events.append(
            {
                "local_time": _clean_text(match.group("local_time")),
                "location": _clean_text(match.group("location")),
                "coordinates": _clean_text(match.group("coordinates")),
                "depth": _clean_text(match.group("depth")),
                "magnitude": _clean_text(match.group("magnitude")),
                "felt": "percibido" in match.group("attrs"),
                "url": urljoin(CSN_HOME_URL, match.group("href")),
            }
        )
    return events


def normalize_seismic_events(
    raw_events: list[dict[str, Any]],
    moment: datetime,
    *,
    source_url: str,
) -> dict[str, Any]:
    local_moment = moment.astimezone(ZoneInfo(SANTIAGO_TZ))
    window_start = local_moment - timedelta(hours=24)
    events: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw_event in raw_events:
        local_time_text = str(raw_event.get("local_time") or "")
        try:
            local_time = _parse_local_datetime(local_time_text)
        except ValueError:
            continue
        if local_time < window_start or local_time > local_moment:
            continue
        key = (
            local_time_text,
            str(raw_event.get("location") or ""),
            str(raw_event.get("magnitude") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        coordinates = str(raw_event.get("coordinates") or "—")
        coordinate_match = re.search(r"(-?\d+(?:[.,]\d+)?)\s+(-?\d+(?:[.,]\d+)?)", coordinates)
        latitude = longitude = None
        if coordinate_match:
            latitude = float(coordinate_match.group(1).replace(",", "."))
            longitude = float(coordinate_match.group(2).replace(",", "."))
        depth_text = str(raw_event.get("depth") or "—")
        depth_match = re.search(r"-?\d+(?:[.,]\d+)?", depth_text)
        magnitude_text = str(raw_event.get("magnitude") or "—")
        magnitude_match = re.search(r"-?\d+(?:[.,]\d+)?", magnitude_text)
        events.append(
            {
                "event_id": f"{local_time:%Y%m%dT%H%M%S}-{len(events) + 1:03d}",
                "occurred_at": (
                    local_time.astimezone(ZoneInfo("UTC"))
                    .isoformat(timespec="seconds")
                    .replace("+00:00", "Z")
                ),
                "local_time": f"{local_time:%Y-%m-%d %H:%M:%S}",
                "location": str(raw_event.get("location") or "—"),
                "coordinates": coordinates,
                "latitude": latitude,
                "longitude": longitude,
                "depth": depth_text,
                "depth_km": float(depth_match.group(0).replace(",", ".")) if depth_match else None,
                "magnitude": (
                    float(magnitude_match.group(0).replace(",", ".")) if magnitude_match else None
                ),
                "magnitude_text": magnitude_text,
                "felt": bool(raw_event.get("felt")),
                "url": str(raw_event.get("url") or CSN_DAILY_URL),
            }
        )
    events.sort(key=lambda item: item["local_time"], reverse=True)
    generated_at = isoformat_utc(moment)
    return {
        "status": "available",
        "provider": PROVIDER_ID,
        "provider_label": PROVIDER_LABEL,
        "region_label": REGION_LABEL,
        "country_code": COUNTRY_CODE,
        "source_url": source_url,
        "generated_at": generated_at,
        "observed_at": (events[0]["occurred_at"] if events else generated_at),
        "window_hours": 24,
        "count": len(events),
        "events": events,
        "error": None,
    }


def normalize_seismic_home(markup: str, moment: datetime) -> dict[str, Any]:
    return normalize_seismic_events(_home_events(markup), moment, source_url=CSN_HOME_URL)


def _catalog_urls(moment: datetime) -> list[str]:
    utc_moment = moment.astimezone(ZoneInfo("UTC"))
    dates = [utc_moment.date(), (utc_moment - timedelta(days=1)).date()]
    return [
        CSN_CATALOG_URL_TEMPLATE.format(
            year=f"{item:%Y}",
            month=f"{item:%m}",
            day=f"{item:%Y%m%d}",
        )
        for item in dates
    ]


def fetch_seismic_snapshot(
    moment: datetime,
    *,
    client: httpx.Client | None = None,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    owns_client = client is None
    active_client = client or httpx.Client(follow_redirects=True)
    errors: list[str] = []
    try:
        catalog_events: list[dict[str, Any]] = []
        for url in _catalog_urls(moment):
            try:
                response = active_client.get(url, timeout=timeout_seconds)
                response.raise_for_status()
                catalog_events.extend(_catalog_events(response.text))
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                errors.append(f"{url}: {type(exc).__name__}: {exc}")
        if catalog_events:
            return normalize_seismic_events(
                catalog_events,
                moment,
                source_url=CSN_DAILY_URL,
            )
        response = active_client.get(CSN_HOME_URL, timeout=timeout_seconds)
        response.raise_for_status()
        return normalize_seismic_home(response.text, moment)
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        errors.append(f"home: {type(exc).__name__}: {exc}")
        return unavailable_seismic_snapshot(moment, "; ".join(errors))
    finally:
        if owns_client:
            active_client.close()
