"""Adaptador USGS para los sismos recientes del estado de Nueva York."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ...models import NodeConfig
from ...sonantia_protocol import isoformat_utc

PROVIDER_ID = "usgs-earthquakes"
PROVIDER_LABEL = "U.S. Geological Survey (USGS)"
REGION_LABEL = "Estado de Nueva York"
NEARBY_REGION_LABEL = "Nueva York y región cercana"
COUNTRY_CODE = "US"
USGS_QUERY_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
WINDOW_HOURS = 168
SEARCH_RADIUS_KM = 550

# Rectángulo geográfico que cubre el estado de Nueva York. El filtro textual
# posterior evita conservar eventos de estados o provincias vecinos incluidos
# por la forma rectangular de la consulta.
NEW_YORK_MIN_LATITUDE = 40.4774
NEW_YORK_MAX_LATITUDE = 45.0153
NEW_YORK_MIN_LONGITUDE = -79.7624
NEW_YORK_MAX_LONGITUDE = -71.7517


def unavailable_usgs_snapshot(
    moment: datetime,
    error: str | None = None,
) -> dict[str, Any]:
    generated_at = isoformat_utc(moment)
    return {
        "status": "unavailable",
        "provider": PROVIDER_ID,
        "provider_label": PROVIDER_LABEL,
        "region_label": REGION_LABEL,
        "country_code": COUNTRY_CODE,
        "source_url": USGS_QUERY_URL,
        "generated_at": generated_at,
        "observed_at": None,
        "window_hours": WINDOW_HOURS,
        "count": 0,
        "events": [],
        "error": error,
    }


def _utc_datetime_from_milliseconds(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(value) / 1000, tz=UTC)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_new_york_place(place: str) -> bool:
    normalized = place.casefold().strip()
    return "new york" in normalized or normalized.endswith(", ny")


def normalize_usgs_payload(
    payload: dict[str, Any],
    node: NodeConfig,
    moment: datetime,
    *,
    source_url: str = USGS_QUERY_URL,
    region_label: str = REGION_LABEL,
    restrict_to_new_york: bool = True,
) -> dict[str, Any]:
    features = payload.get("features")
    if not isinstance(features, list):
        raise ValueError("USGS no entregó una colección GeoJSON válida")

    events: list[dict[str, Any]] = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        properties = feature.get("properties")
        geometry = feature.get("geometry")
        if not isinstance(properties, dict) or not isinstance(geometry, dict):
            continue
        if str(properties.get("type") or "earthquake") != "earthquake":
            continue

        place = str(properties.get("place") or "").strip()
        if restrict_to_new_york and not _is_new_york_place(place):
            continue

        coordinates = geometry.get("coordinates")
        if not isinstance(coordinates, list) or len(coordinates) < 3:
            continue
        longitude = _optional_float(coordinates[0])
        latitude = _optional_float(coordinates[1])
        depth_km = _optional_float(coordinates[2])
        occurred = _utc_datetime_from_milliseconds(properties.get("time"))
        if occurred is None:
            continue

        magnitude = _optional_float(properties.get("mag"))
        local_time = occurred.astimezone(ZoneInfo(node.logical_location.timezone))
        event_id = str(feature.get("id") or properties.get("code") or "").strip()
        if not event_id:
            event_id = f"{occurred:%Y%m%dT%H%M%S}-{len(events) + 1:03d}"
        event_url = str(properties.get("url") or properties.get("detail") or source_url)
        events.append(
            {
                "event_id": event_id,
                "occurred_at": isoformat_utc(occurred),
                "local_time": f"{local_time:%Y-%m-%d %H:%M:%S}",
                "location": place or "Ubicación no informada",
                "magnitude": magnitude,
                "magnitude_text": "—" if magnitude is None else f"{magnitude:.1f}",
                "depth_km": depth_km,
                "depth": "—" if depth_km is None else f"{depth_km:.1f} km",
                "latitude": latitude,
                "longitude": longitude,
                "coordinates": (
                    "—"
                    if latitude is None or longitude is None
                    else f"{latitude:.4f}, {longitude:.4f}"
                ),
                "felt": bool(properties.get("felt")),
                "url": event_url,
            }
        )

    events.sort(key=lambda item: item["occurred_at"], reverse=True)
    generated_at = isoformat_utc(moment)
    return {
        "status": "available",
        "provider": PROVIDER_ID,
        "provider_label": PROVIDER_LABEL,
        "region_label": region_label,
        "country_code": COUNTRY_CODE,
        "source_url": source_url,
        "generated_at": generated_at,
        "observed_at": events[0]["occurred_at"] if events else generated_at,
        "window_hours": WINDOW_HOURS,
        "count": len(events),
        "events": events,
        "error": None,
    }


def _request_usgs_payload(
    active_client: httpx.Client,
    params: dict[str, Any],
    *,
    timeout_seconds: float,
) -> tuple[dict[str, Any], str]:
    response = active_client.get(
        USGS_QUERY_URL,
        params=params,
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    source_url = str(response.request.url)
    if response.status_code == 204 or not response.content:
        return {"features": []}, source_url
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("USGS entregó una respuesta JSON inválida")
    return payload, source_url


def fetch_usgs_snapshot(
    node: NodeConfig,
    moment: datetime,
    *,
    client: httpx.Client | None = None,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    end = moment.astimezone(UTC)
    start = end - timedelta(hours=WINDOW_HOURS)
    common_params = {
        "format": "geojson",
        "starttime": isoformat_utc(start),
        "endtime": isoformat_utc(end),
        "eventtype": "earthquake",
        "orderby": "time",
        "limit": 200,
    }
    state_params = {
        **common_params,
        "minlatitude": NEW_YORK_MIN_LATITUDE,
        "maxlatitude": NEW_YORK_MAX_LATITUDE,
        "minlongitude": NEW_YORK_MIN_LONGITUDE,
        "maxlongitude": NEW_YORK_MAX_LONGITUDE,
    }
    nearby_params = {
        **common_params,
        "latitude": node.logical_location.latitude,
        "longitude": node.logical_location.longitude,
        "maxradiuskm": SEARCH_RADIUS_KM,
    }

    owns_client = client is None
    active_client = client or httpx.Client(follow_redirects=True)
    try:
        payload, source_url = _request_usgs_payload(
            active_client,
            state_params,
            timeout_seconds=timeout_seconds,
        )
        state_snapshot = normalize_usgs_payload(
            payload,
            node,
            moment,
            source_url=source_url,
        )
        if state_snapshot["events"]:
            return state_snapshot

        payload, source_url = _request_usgs_payload(
            active_client,
            nearby_params,
            timeout_seconds=timeout_seconds,
        )
        return normalize_usgs_payload(
            payload,
            node,
            moment,
            source_url=source_url,
            region_label=NEARBY_REGION_LABEL,
            restrict_to_new_york=False,
        )
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        return unavailable_usgs_snapshot(moment, f"{type(exc).__name__}: {exc}")
    finally:
        if owns_client:
            active_client.close()
