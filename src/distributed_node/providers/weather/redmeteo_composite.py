"""Consulta de estaciones RedMeteo y composición meteorológica del nodo."""

from __future__ import annotations

from datetime import UTC, datetime
from statistics import fmean
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ...models import NodeConfig, Weather, WeatherData, WeatherLocation
from ...sonantia_protocol import isoformat_utc

SANTIAGO_TZ = "America/Santiago"
REDMETEO_JSON_BASE_URL = "https://redmeteo.cl/jsonemas"
REDMETEO_STATION_BASE_URL = "https://redmeteo.cl/estacion.html?codigo="
REDMETEO_AGGREGATE_PROVIDER = "redmeteo-station-aggregate"

# Para reemplazar una fuente basta cambiar su código. El nombre visible se toma
# desde ``metadatos.nombre`` y no queda acoplado al código Python.
REDMETEO_STATIONS: dict[str, dict[str, str]] = {
    "primary": {
        "code": "RMCL0098",
        "label": "Estación Meteorológica Principal",
    },
    "complementary": {
        "code": "RMCL0174",
        "label": "Estación complementaria",
    },
}

def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _pick(values: Any, index: int) -> Any:
    if isinstance(values, list):
        return values[index] if index < len(values) else None
    return values if index == 0 else None


def _pick_first(station: dict[str, Any], keys: tuple[str, ...], index: int = 0) -> Any:
    for key in keys:
        value = _pick(station.get(key), index)
        if value is not None and value != "":
            return value
    return None


def _format(value: Any, unit: str, *, decimals: int = 1) -> str:
    parsed = _number(value)
    if parsed is None:
        return "—"
    return f"{parsed:.{decimals}f} {unit}"


def _cardinal_direction(degrees: float) -> str:
    directions = (
        "N",
        "NNE",
        "NE",
        "ENE",
        "E",
        "ESE",
        "SE",
        "SSE",
        "S",
        "SSO",
        "SO",
        "OSO",
        "O",
        "ONO",
        "NO",
        "NNO",
    )
    return directions[round((degrees % 360) / 22.5) % 16]


def _format_direction(value: Any) -> str | None:
    if value is None:
        return None
    parsed = _number(value)
    if parsed is not None:
        return f"{_cardinal_direction(parsed)} · {parsed:.0f}°"
    text = str(value).strip().upper()
    return text or None


def _format_wind(speed: Any, direction: Any = None) -> str:
    parsed_speed = _number(speed)
    if parsed_speed is None:
        return "—"
    value = f"{parsed_speed * 3.6:.1f} km/h"
    direction_text = _format_direction(direction)
    return f"{value} · {direction_text}" if direction_text else value


def _local_datetime(value: str | None) -> str:
    if not value:
        return "—"
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return "—"
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return f"{timestamp.astimezone(ZoneInfo(SANTIAGO_TZ)):%Y-%m-%d %H:%M:%S}"


def _age_text(observed_at: str | None, moment: datetime) -> str:
    if not observed_at:
        return "sin referencia temporal"
    try:
        observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except ValueError:
        return "sin referencia temporal"
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    delta = max(0, int((moment.astimezone(UTC) - observed.astimezone(UTC)).total_seconds()))
    minutes = delta // 60
    if minutes < 1:
        return "menos de 1 minuto"
    if minutes == 1:
        return "1 minuto"
    if minutes < 60:
        return f"{minutes} minutos"
    hours = minutes // 60
    remaining_minutes = minutes % 60
    if hours == 1 and remaining_minutes == 0:
        return "1 hora"
    if remaining_minutes == 0:
        return f"{hours} horas"
    return f"{hours} h {remaining_minutes} min"


def _station_url(station_code: str) -> str:
    return f"{REDMETEO_STATION_BASE_URL}{station_code}"


def _json_url(station_code: str) -> str:
    return f"{REDMETEO_JSON_BASE_URL}/{station_code}.json"


def _fallback_station_name(station_code: str) -> str:
    return f"Estación {station_code}"


def unavailable_redmeteo_snapshot(
    moment: datetime,
    error: str | None = None,
    *,
    station_code: str = REDMETEO_STATIONS["primary"]["code"],
    label: str = REDMETEO_STATIONS["primary"]["label"],
) -> dict[str, Any]:
    station_name = _fallback_station_name(station_code)
    return {
        "status": "unavailable",
        "label": label,
        "source_label": "RedMeteo",
        "source_url": _station_url(station_code),
        "station_code": station_code,
        "station": station_name,
        "name": station_name,
        "generated_at": isoformat_utc(moment),
        "observed_at": None,
        "received_age": "no disponible",
        "updated_at_local": "—",
        "data": {},
        "metrics": [],
        "ephemerides": [],
        "error": error,
    }


def unavailable_redmeteo_snapshots(moment: datetime) -> dict[str, dict[str, Any]]:
    return {
        role: unavailable_redmeteo_snapshot(
            moment,
            station_code=config["code"],
            label=config["label"],
        )
        for role, config in REDMETEO_STATIONS.items()
    }


def _ephemerides(efemeride: dict[str, Any]) -> list[dict[str, str]]:
    labels = ("Hoy", "Ayer", "Anteayer")
    rows: list[dict[str, str]] = []
    for index, label in enumerate(labels):
        rows.append(
            {
                "label": label,
                "date": str(_pick(efemeride.get("fechas"), index) or "—"),
                "temperature": (
                    f"máx {_format(_pick(efemeride.get('tmax'), index), '°C')} / "
                    f"mín {_format(_pick(efemeride.get('tmin'), index), '°C')}"
                ),
                "dewpoint": (
                    f"máx {_format(_pick(efemeride.get('rocmax'), index), '°C')} / "
                    f"mín {_format(_pick(efemeride.get('rocmin'), index), '°C')}"
                ),
                "humidity": (
                    f"máx {_format(_pick(efemeride.get('hrmax'), index), '%')} / "
                    f"mín {_format(_pick(efemeride.get('hrmin'), index), '%')}"
                ),
                "wind": f"máx {_format_wind(_pick(efemeride.get('rachamax'), index))}",
                "pressure": (
                    f"máx {_format(_pick(efemeride.get('slpmax'), index), 'hPa')} / "
                    f"mín {_format(_pick(efemeride.get('slpmin'), index), 'hPa')}"
                ),
                "rain": f"{_format(_pick(efemeride.get('ppdiaria'), index), 'mm')} acum.",
                "radiation": (
                    f"máx {_format(_pick(efemeride.get('solarmax'), index), 'W/m²', decimals=0)}"
                ),
            }
        )
    return rows


def normalize_redmeteo_payload(
    payload: Any,
    moment: datetime,
    *,
    station_code: str = REDMETEO_STATIONS["primary"]["code"],
    label: str = REDMETEO_STATIONS["primary"]["label"],
) -> dict[str, Any]:
    station = payload[0] if isinstance(payload, list) and payload else payload
    if not isinstance(station, dict):
        raise ValueError("RedMeteo entregó una respuesta vacía o inválida")
    metadata = station.get("metadatos")
    if not isinstance(metadata, dict):
        raise ValueError("RedMeteo no entregó metadatos")
    observed_at = metadata.get("ultima_actualizacion")
    if not isinstance(observed_at, str):
        observed_at = None
    station_name = str(metadata.get("nombre") or _fallback_station_name(station_code))

    direction = _pick_first(
        station,
        ("vd", "dd", "dv", "wd", "wind_direction", "direccion", "dir_viento", "dir"),
    )
    solar_radiation = _pick_first(
        station,
        (
            "sw",
            "solar",
            "rs",
            "sr",
            "radiacion",
            "radiation",
            "solar_radiation",
            "rad",
        ),
    )
    raw_wind_speed = _number(_pick(station.get("vv"), 0))
    numeric_data = {
        "temperature_c": _number(_pick(station.get("t"), 0)),
        "relative_humidity_percent": _number(_pick(station.get("rh"), 0)),
        "pressure_hpa": _number(_pick(station.get("slp"), 0)),
        "wind_speed_kmh": raw_wind_speed * 3.6 if raw_wind_speed is not None else None,
        "wind_direction_degrees": _number(direction),
        "precipitation_mm": _number(_pick(station.get("ppd"), 0)),
        "solar_radiation_wm2": _number(solar_radiation),
    }

    return {
        "status": "available",
        "label": label,
        "source_label": "RedMeteo",
        "source_url": _station_url(station_code),
        "station_code": station_code,
        "station": station_name,
        "name": station_name,
        "generated_at": isoformat_utc(moment),
        "observed_at": observed_at,
        "received_age": _age_text(observed_at, moment),
        "updated_at_local": _local_datetime(observed_at),
        "data": numeric_data,
        "metrics": [
            {
                "icon": "🌡️",
                "label": "Temperatura",
                "value": _format(numeric_data["temperature_c"], "°C"),
            },
            {
                "icon": "💧",
                "label": "Humedad",
                "value": _format(numeric_data["relative_humidity_percent"], "%"),
            },
            {
                "icon": "⏱️",
                "label": "Presión",
                "value": _format(numeric_data["pressure_hpa"], "hPa"),
            },
            {
                "icon": "💨",
                "label": "Viento",
                "value": _format_wind(_pick(station.get("vv"), 0), direction),
            },
            {
                "icon": "🌧️",
                "label": "Lluvia hoy",
                "value": _format(numeric_data["precipitation_mm"], "mm"),
            },
            {
                "icon": "☀️",
                "label": "Radiación solar",
                "value": _format(numeric_data["solar_radiation_wm2"], "W/m²", decimals=0),
            },
        ],
        "ephemerides": _ephemerides(station.get("efemeride") or {}),
        "error": None,
    }


def fetch_redmeteo_station(
    moment: datetime,
    *,
    station_code: str,
    label: str,
    client: httpx.Client | None = None,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    owns_client = client is None
    active_client = client or httpx.Client()
    try:
        response = active_client.get(_json_url(station_code), timeout=timeout_seconds)
        response.raise_for_status()
        return normalize_redmeteo_payload(
            response.json(),
            moment,
            station_code=station_code,
            label=label,
        )
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        return unavailable_redmeteo_snapshot(
            moment,
            f"{type(exc).__name__}: {exc}",
            station_code=station_code,
            label=label,
        )
    finally:
        if owns_client:
            active_client.close()


def fetch_redmeteo_snapshots(
    moment: datetime,
    *,
    client: httpx.Client | None = None,
    timeout_seconds: float = 10.0,
) -> dict[str, dict[str, Any]]:
    owns_client = client is None
    active_client = client or httpx.Client()
    try:
        return {
            role: fetch_redmeteo_station(
                moment,
                station_code=config["code"],
                label=config["label"],
                client=active_client,
                timeout_seconds=timeout_seconds,
            )
            for role, config in REDMETEO_STATIONS.items()
        }
    finally:
        if owns_client:
            active_client.close()


def _available_values(
    snapshots: dict[str, dict[str, Any]],
    field: str,
) -> list[float]:
    values: list[float] = []
    for snapshot in snapshots.values():
        if snapshot.get("status") != "available":
            continue
        data = snapshot.get("data")
        if not isinstance(data, dict):
            continue
        value = _number(data.get(field))
        if value is not None:
            values.append(value)
    return values


def _average_field(
    snapshots: dict[str, dict[str, Any]],
    field: str,
) -> float | None:
    values = _available_values(snapshots, field)
    return round(fmean(values), 2) if values else None


def _latest_observed_at(snapshots: dict[str, dict[str, Any]]) -> str | None:
    candidates: list[datetime] = []
    for snapshot in snapshots.values():
        value = snapshot.get("observed_at")
        if not isinstance(value, str) or not value:
            continue
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        candidates.append(parsed.astimezone(UTC))
    return isoformat_utc(max(candidates)) if candidates else None


def build_composite_weather(
    node: NodeConfig,
    moment: datetime,
    snapshots: dict[str, dict[str, Any]],
    condition_weather: Weather,
) -> Weather:
    """Combina cada parámetro disponible sin hacer depender el mensaje del clima."""

    data = WeatherData(
        temperature_c=_average_field(snapshots, "temperature_c"),
        relative_humidity_percent=_average_field(
            snapshots,
            "relative_humidity_percent",
        ),
        precipitation_mm=_average_field(snapshots, "precipitation_mm"),
        pressure_hpa=_average_field(snapshots, "pressure_hpa"),
        wind_speed_kmh=None,
        solar_radiation_wm2=_average_field(snapshots, "solar_radiation_wm2"),
        condition_code=condition_weather.data.condition_code,
    )
    measurement_fields = (
        data.temperature_c,
        data.relative_humidity_percent,
        data.precipitation_mm,
        data.pressure_hpa,
        data.solar_radiation_wm2,
    )
    measurement_keys = (
        "temperature_c",
        "relative_humidity_percent",
        "precipitation_mm",
        "pressure_hpa",
        "solar_radiation_wm2",
    )
    source_codes = [
        str(snapshot["station_code"])
        for snapshot in snapshots.values()
        if snapshot.get("status") == "available"
        and snapshot.get("station_code")
        and isinstance(snapshot.get("data"), dict)
        and any(
            _number(snapshot["data"].get(key)) is not None
            for key in measurement_keys
        )
    ]

    return Weather(
        status=(
            "available"
            if any(value is not None for value in measurement_fields)
            else "unavailable"
        ),
        provider=REDMETEO_AGGREGATE_PROVIDER,
        requested_at=isoformat_utc(moment),
        observed_at=_latest_observed_at(snapshots),
        condition_provider=condition_weather.provider,
        condition_observed_at=(
            condition_weather.condition_observed_at
            or condition_weather.observed_at
        ),
        measurement_source_count=len(source_codes),
        measurement_source_codes=source_codes,
        location=WeatherLocation(
            latitude=node.logical_location.latitude,
            longitude=node.logical_location.longitude,
        ),
        data=data,
    )
