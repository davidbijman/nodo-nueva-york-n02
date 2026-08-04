"""Referencia astronómica visual basada en NASA/JPL Horizons.

La consulta se usa como dato factual externo del ciclo. No almacena secretos,
no calcula posiciones localmente y no inventa valores cuando Horizons no
responde.
"""

from __future__ import annotations

import csv
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .messages import ObservationFact, SourceContribution
from .models import NodeConfig
from .sonantia_protocol import isoformat_utc

HORIZONS_API_URL = "https://ssd.jpl.nasa.gov/api/horizons.api"
HORIZONS_SOURCE_URL = "https://ssd.jpl.nasa.gov/horizons/"

# SITE_COORD usa el orden: longitud este, latitud y altitud en kilómetros.
OBSERVER_CENTER = "coord@399"


def _observer(node: NodeConfig) -> dict[str, Any]:
    location = node.logical_location
    return {
        "code": OBSERVER_CENTER,
        "name": f"{location.city} · coordenadas {node.node_id}",
        "longitude_deg": location.longitude,
        "latitude_deg": location.latitude,
        "elevation_km": location.elevation_m / 1000,
        "timezone": location.timezone,
    }



ASTRONOMY_TARGETS = (
    {"command": "10", "name": "Sol", "kind": "estrella", "icon": "☀️"},
    {"command": "301", "name": "Luna", "kind": "satélite", "icon": "🌙"},
    {"command": "199", "name": "Mercurio", "kind": "planeta", "icon": "☿"},
    {"command": "299", "name": "Venus", "kind": "planeta", "icon": "♀"},
    {"command": "499", "name": "Marte", "kind": "planeta", "icon": "♂"},
    {"command": "Ceres", "name": "Ceres", "kind": "planeta enano", "icon": "⚳"},
    {"command": "Pallas", "name": "Palas", "kind": "asteroide", "icon": "⚴"},
    {"command": "Vesta", "name": "Vesta", "kind": "asteroide", "icon": "⚶"},
    {"command": "Eros", "name": "Eros", "kind": "asteroide", "icon": "♦"},
    {"command": "Bennu", "name": "Bennu", "kind": "asteroide", "icon": "☄️"},
    {"command": "599", "name": "Júpiter", "kind": "planeta", "icon": "♃"},
    {"command": "501", "name": "Ío", "kind": "satélite", "icon": "○"},
    {"command": "502", "name": "Europa", "kind": "satélite", "icon": "❄"},
    {"command": "503", "name": "Ganímedes", "kind": "satélite", "icon": "🌕"},
    {"command": "504", "name": "Calisto", "kind": "satélite", "icon": "🌑"},
    {"command": "699", "name": "Saturno", "kind": "planeta", "icon": "♄"},
    {"command": "606", "name": "Titán", "kind": "satélite", "icon": "🌫️"},
    {"command": "799", "name": "Urano", "kind": "planeta", "icon": "♅"},
    {"command": "899", "name": "Neptuno", "kind": "planeta", "icon": "♆"},
    {"command": "801", "name": "Tritón", "kind": "satélite", "icon": "◌"},
    {"command": "999", "name": "Plutón", "kind": "planeta enano", "icon": "♇"},
)


def unavailable_astronomy_snapshot(
    moment: datetime,
    error: str | None = None,
    *,
    node: NodeConfig,
) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "source_url": HORIZONS_SOURCE_URL,
        "api_url": HORIZONS_API_URL,
        "generated_at": isoformat_utc(moment),
        "observer": _observer(node),
        "earth": None,
        "targets": [],
        "error": error,
    }


def _quoted(value: str) -> str:
    return f"'{value}'"


def _horizons_time(moment: datetime) -> str:
    return _quoted(moment.astimezone(ZoneInfo("UTC")).strftime("%Y-%b-%d %H:%M"))


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized or normalized.lower() in {"n.a.", "nan"}:
        return None
    try:
        return float(normalized)
    except ValueError:
        return None


def _format_number(value: float | None, suffix: str = "", *, decimals: int = 1) -> str:
    if value is None:
        return "—"
    return f"{value:.{decimals}f}{suffix}"


def _ephemeris_rows(result: str) -> list[list[str]]:
    start = result.find("$$SOE")
    end = result.find("$$EOE")
    if start == -1 or end == -1 or end <= start:
        return []
    body = result[start + len("$$SOE") : end]
    rows: list[list[str]] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parsed = next(csv.reader([line], skipinitialspace=True))
        if parsed:
            rows.append(parsed)
    return rows


def _parse_target_result(
    target: dict[str, str],
    result: str,
    moment: datetime,
    timezone_name: str,
) -> dict[str, Any]:
    rows = _ephemeris_rows(result)
    if not rows:
        return {
            **target,
            "status": "unavailable",
            "error": "Horizons no entregó tabla de efemérides",
        }
    row = rows[0]
    values = [item.strip() for item in row]
    azimuth = _to_float(values[5] if len(values) > 5 else None)
    elevation = _to_float(values[6] if len(values) > 6 else None)
    ra = _to_float(values[3] if len(values) > 3 else None)
    dec = _to_float(values[4] if len(values) > 4 else None)
    magnitude = _to_float(values[7] if len(values) > 7 else None)
    range_au = _to_float(values[9] if len(values) > 9 else None)
    range_rate = _to_float(values[10] if len(values) > 10 else None)
    visibility = (
        "sobre el horizonte"
        if elevation is not None and elevation >= 0
        else "bajo el horizonte"
        if elevation is not None
        else "sin elevación disponible"
    )
    local_moment = moment.astimezone(ZoneInfo(timezone_name))
    return {
        **target,
        "status": "available",
        "computed_at_local": f"{local_moment:%Y-%m-%d %H:%M}",
        "azimuth_deg": azimuth,
        "elevation_deg": elevation,
        "ra_deg": ra,
        "dec_deg": dec,
        "magnitude": magnitude,
        "range_au": range_au,
        "range_rate_km_s": range_rate,
        "visibility": visibility,
        "azimuth": _format_number(azimuth, "°"),
        "elevation": _format_number(elevation, "°"),
        "ra": _format_number(ra, "°", decimals=2),
        "dec": _format_number(dec, "°", decimals=2),
        "magnitude_text": _format_number(magnitude, decimals=2),
        "range_text": _format_number(range_au, " au", decimals=6),
        "range_rate_text": _format_number(range_rate, " km/s", decimals=3),
        "error": None,
    }


def _request_target(
    client: httpx.Client,
    target: dict[str, str],
    moment: datetime,
    timeout_seconds: float,
    *,
    observer: dict[str, Any],
) -> dict[str, Any]:
    quote = "'"
    params = {
        "format": "json",
        "COMMAND": target["command"],
        "OBJ_DATA": "NO",
        "MAKE_EPHEM": "YES",
        "EPHEM_TYPE": "OBSERVER",
        "CENTER": OBSERVER_CENTER,
        "COORD_TYPE": "GEODETIC",
        "SITE_COORD": _quoted(
            f"{observer['longitude_deg']},"
            f"{observer['latitude_deg']},"
            f"{observer['elevation_km']}"
        ),
        "TLIST": _horizons_time(moment),
        "TLIST_TYPE": "CAL",
        "TIME_TYPE": "UT",
        "QUANTITIES": f"{quote}1,4,9,20{quote}",
        "CSV_FORMAT": "YES",
        "ANG_FORMAT": "DEG",
    }
    response = client.get(HORIZONS_API_URL, params=params, timeout=timeout_seconds)
    response.raise_for_status()
    document = response.json()
    error = document.get("error")
    if error:
        return {**target, "status": "unavailable", "error": str(error)}
    return _parse_target_result(
        target,
        str(document.get("result") or ""),
        moment,
        str(observer["timezone"]),
    )


def _format_scientific(value: float | None, suffix: str) -> str:
    if value is None:
        return "—"
    return f"{value:.6e} {suffix}"


def _parse_earth_vector(
    result: str,
    moment: datetime,
    timezone_name: str,
) -> dict[str, Any] | None:
    rows = _ephemeris_rows(result)
    if not rows:
        return None
    values = [item.strip() for item in rows[0]]
    if len(values) < 8:
        return None
    jdut = _to_float(values[0])
    x = _to_float(values[2])
    y = _to_float(values[3])
    z = _to_float(values[4])
    vx = _to_float(values[5])
    vy = _to_float(values[6])
    vz = _to_float(values[7])
    local_moment = moment.astimezone(ZoneInfo(timezone_name))
    return {
        "status": "available",
        "frame": "Eclíptica J2000.0",
        "origin": "Sol",
        "center": "Tierra",
        "instant_utc": isoformat_utc(moment),
        "instant_local": f"{local_moment:%Y-%m-%d %H:%M:%S}",
        "julian_day": f"{jdut:.6f}" if jdut is not None else "—",
        "calendar_ut": values[1],
        "position": {
            "x_km": x,
            "y_km": y,
            "z_km": z,
            "x": _format_scientific(x, "km"),
            "y": _format_scientific(y, "km"),
            "z": _format_scientific(z, "km"),
        },
        "velocity": {
            "vx_km_s": vx,
            "vy_km_s": vy,
            "vz_km_s": vz,
            "vx": _format_scientific(vx, "km/s"),
            "vy": _format_scientific(vy, "km/s"),
            "vz": _format_scientific(vz, "km/s"),
        },
    }


def _request_earth_context(
    client: httpx.Client,
    moment: datetime,
    timeout_seconds: float,
    *,
    timezone_name: str,
) -> dict[str, Any] | None:
    params = {
        "format": "json",
        "COMMAND": "399",
        "OBJ_DATA": "NO",
        "MAKE_EPHEM": "YES",
        "EPHEM_TYPE": "VECTORS",
        "CENTER": "500@10",
        "TLIST": _horizons_time(moment),
        "TLIST_TYPE": "CAL",
        "TIME_TYPE": "UT",
        "CSV_FORMAT": "YES",
        "VEC_TABLE": "2",
    }
    response = client.get(HORIZONS_API_URL, params=params, timeout=timeout_seconds)
    response.raise_for_status()
    document = response.json()
    if document.get("error"):
        return None
    return _parse_earth_vector(
        str(document.get("result") or ""),
        moment,
        timezone_name,
    )


def fetch_astronomy_snapshot(
    node: NodeConfig,
    moment: datetime,
    *,
    client: httpx.Client | None = None,
    timeout_seconds: float = 12.0,
) -> dict[str, Any]:
    owns_client = client is None
    active_client = client or httpx.Client(follow_redirects=True)
    observer = _observer(node)
    targets: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        earth_context: dict[str, Any] | None = None
        try:
            earth_context = _request_earth_context(
                active_client,
                moment,
                timeout_seconds,
                timezone_name=node.logical_location.timezone,
            )
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            errors.append(f"Tierra: {type(exc).__name__}: {exc}")
        for target in ASTRONOMY_TARGETS:
            try:
                targets.append(
                    _request_target(
                        active_client,
                        target,
                        moment,
                        timeout_seconds,
                        observer=observer,
                    )
                )
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                errors.append(f"{target['name']}: {type(exc).__name__}: {exc}")
                targets.append(
                    {**target, "status": "unavailable", "error": str(exc)}
                )
        available_targets = [
            target for target in targets if target.get("status") == "available"
        ]
        if not available_targets:
            return unavailable_astronomy_snapshot(
                moment,
                "; ".join(errors) or None,
                node=node,
            )
        return {
            "status": "available",
            "source_url": HORIZONS_SOURCE_URL,
            "api_url": HORIZONS_API_URL,
            "generated_at": isoformat_utc(moment),
            "observer": _observer(node),
            "earth": earth_context,
            "targets": targets,
            "available_count": len(available_targets),
            "error": "; ".join(errors) or None,
        }
    finally:
        if owns_client:
            active_client.close()


def astronomy_contribution(snapshot: dict[str, Any]) -> SourceContribution | None:
    if snapshot.get("status") != "available":
        return None
    available_targets = [
        target
        for target in snapshot.get("targets", [])
        if isinstance(target, dict) and target.get("status") == "available"
    ]
    if not available_targets:
        return None
    facts = tuple(
        ObservationFact(
            str(target.get("name", "astro")).casefold(),
            (
                f"{target.get('name')} aparece {target.get('visibility')} "
                f"con elevación {target.get('elevation')} y azimut {target.get('azimuth')}"
            ),
        )
        for target in available_targets
        if target.get("name") and target.get("elevation") and target.get("azimuth")
    )
    if not facts:
        return None
    earth = snapshot.get("earth")
    if isinstance(earth, dict) and earth.get("status") == "available":
        facts = (
            ObservationFact(
                "tierra-vector",
                (
                    "la Tierra queda referenciada en la "
                    f"{earth.get('frame')} con origen en el {earth.get('origin')} "
                    f"en JD {earth.get('julian_day')}"
                ),
            ),
            *facts,
        )
    return SourceContribution(
        source_id="astronomy",
        provider="nasa-jpl-horizons",
        facts=facts,
        fact_count=1,
    )
