"""Adaptador USGS para la región geológica cercana al nodo N02."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ...models import NodeConfig
from ...sonantia_protocol import isoformat_utc

PROVIDER_ID = "usgs-earthquakes"
PROVIDER_LABEL = "U.S. Geological Survey (USGS)"
REGION_LABEL = "Región geológica cercana a N02"
COUNTRY_CODE = "US"
USGS_QUERY_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
REGIONAL_WINDOW_HOURS = 168
EXTENDED_WINDOW_HOURS = 720
SEARCH_RADIUS_KM = 550


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
        "window_hours": REGIONAL_WINDOW_HOURS,
        "search_stage": "unavailable",
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


def normalize_usgs_payload(
    payload: dict[str, Any],
    node: NodeConfig,
    moment: datetime,
    *,
    source_url: str = USGS_QUERY_URL,
    region_label: str = REGION_LABEL,
    window_hours: int = REGIONAL_WINDOW_HOURS,
    search_stage: str = "regional-7d",
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
        "window_hours": window_hours,
        "search_stage": search_stage,
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

    def common_params(window_hours: int) -> dict[str, Any]:
        start = end - timedelta(hours=window_hours)
        return {
            "format": "geojson",
            "starttime": isoformat_utc(start),
            "endtime": isoformat_utc(end),
            "eventtype": "earthquake",
            "orderby": "time",
            "limit": 200,
        }

    searches = (
        {
            "window_hours": REGIONAL_WINDOW_HOURS,
            "region_label": REGION_LABEL,
            "search_stage": "regional-7d",
            "params": {
                **common_params(REGIONAL_WINDOW_HOURS),
                "latitude": node.logical_location.latitude,
                "longitude": node.logical_location.longitude,
                "maxradiuskm": SEARCH_RADIUS_KM,
            },
        },
        {
            "window_hours": EXTENDED_WINDOW_HOURS,
            "region_label": REGION_LABEL,
            "search_stage": "regional-30d",
            "params": {
                **common_params(EXTENDED_WINDOW_HOURS),
                "latitude": node.logical_location.latitude,
                "longitude": node.logical_location.longitude,
                "maxradiuskm": SEARCH_RADIUS_KM,
            },
        },
    )

    owns_client = client is None
    active_client = client or httpx.Client(follow_redirects=True)
    try:
        last_snapshot: dict[str, Any] | None = None
        for search in searches:
            payload, source_url = _request_usgs_payload(
                active_client,
                search["params"],
                timeout_seconds=timeout_seconds,
            )
            snapshot = normalize_usgs_payload(
                payload,
                node,
                moment,
                source_url=source_url,
                region_label=str(search["region_label"]),
                window_hours=int(search["window_hours"]),
                search_stage=str(search["search_stage"]),
            )
            last_snapshot = snapshot
            if snapshot["events"]:
                return snapshot

        return last_snapshot or unavailable_usgs_snapshot(moment)
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        return unavailable_usgs_snapshot(moment, f"{type(exc).__name__}: {exc}")
    finally:
        if owns_client:
            active_client.close()
