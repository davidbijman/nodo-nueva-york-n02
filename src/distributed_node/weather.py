"""Adaptadores Open-Meteo para condición y meteorología actual completa."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .models import NodeConfig, Weather, WeatherData, WeatherLocation
from .sonantia_protocol import isoformat_utc

OPEN_METEO_PROVIDER = "open-meteo-best-match"
OPEN_METEO_CURRENT_PROVIDER = "open-meteo-current"
OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_PUBLIC_URL = "https://open-meteo.com/"
OPEN_METEO_CONDITION_VARIABLES = "weather_code"
# Alias histórico: el adaptador original consultaba solo la condición actual.
OPEN_METEO_CURRENT_VARIABLES = OPEN_METEO_CONDITION_VARIABLES
OPEN_METEO_FULL_CURRENT_VARIABLES = ",".join(
    (
        "temperature_2m",
        "relative_humidity_2m",
        "precipitation",
        "pressure_msl",
        "wind_speed_10m",
        "shortwave_radiation",
        "weather_code",
    )
)

# Códigos WMO usados por Open-Meteo. Se conservan como códigos numéricos en el
# modelo y se traducen únicamente al presentar la información.
WMO_CONDITION_CODES = {
    0: "clear-sky",
    1: "mainly-clear",
    2: "partly-cloudy",
    3: "overcast",
    45: "fog",
    48: "rime-fog",
    51: "light-drizzle",
    53: "moderate-drizzle",
    55: "dense-drizzle",
    56: "light-freezing-drizzle",
    57: "dense-freezing-drizzle",
    61: "slight-rain",
    63: "moderate-rain",
    65: "heavy-rain",
    66: "light-freezing-rain",
    67: "heavy-freezing-rain",
    71: "slight-snowfall",
    73: "moderate-snowfall",
    75: "heavy-snowfall",
    77: "snow-grains",
    80: "slight-rain-showers",
    81: "moderate-rain-showers",
    82: "violent-rain-showers",
    85: "slight-snow-showers",
    86: "heavy-snow-showers",
    95: "thunderstorm",
    96: "thunderstorm-with-slight-hail",
    99: "thunderstorm-with-heavy-hail",
}


def condition_key(value: int | str | None) -> str | None:
    """Devuelve la clave textual compatible con registros nuevos y heredados."""

    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return WMO_CONDITION_CODES.get(value)
    if isinstance(value, float) and value.is_integer():
        return WMO_CONDITION_CODES.get(int(value))
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return WMO_CONDITION_CODES.get(int(text))
    return text


def _optional_number(values: dict[str, Any], key: str) -> float | None:
    value = values.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _observed_at(current: dict[str, Any]) -> str:
    value = current.get("time")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Open-Meteo no entregó hora de observación")

    observed = datetime.fromisoformat(value)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    return isoformat_utc(observed)


def _condition_code(current: dict[str, Any]) -> int | None:
    weather_code = current.get("weather_code")
    if isinstance(weather_code, bool) or not isinstance(weather_code, int | float):
        return None
    normalized = int(weather_code)
    return normalized if normalized in WMO_CONDITION_CODES else None


def normalize_response(
    payload: dict[str, Any],
    node: NodeConfig,
    requested_at: str,
) -> Weather:
    """Normaliza exclusivamente condición, hora y coordenadas de Open-Meteo."""

    current = payload.get("current")
    if not isinstance(current, dict):
        raise ValueError("Open-Meteo no entregó condiciones actuales")

    latitude = _optional_number(payload, "latitude")
    longitude = _optional_number(payload, "longitude")
    observed_at = _observed_at(current)

    return Weather(
        status="available",
        provider=OPEN_METEO_PROVIDER,
        requested_at=requested_at,
        observed_at=observed_at,
        condition_provider=OPEN_METEO_PROVIDER,
        condition_observed_at=observed_at,
        location=WeatherLocation(
            latitude=(
                latitude
                if latitude is not None
                else node.logical_location.latitude
            ),
            longitude=(
                longitude
                if longitude is not None
                else node.logical_location.longitude
            ),
        ),
        data=WeatherData(condition_code=_condition_code(current)),
    )


def unavailable_weather(
    node: NodeConfig,
    requested_at: str,
) -> Weather:
    """Construye una condición no disponible sin inventar valores."""

    return Weather(
        status="unavailable",
        provider=OPEN_METEO_PROVIDER,
        requested_at=requested_at,
        observed_at=None,
        condition_provider=OPEN_METEO_PROVIDER,
        condition_observed_at=None,
        location=WeatherLocation(
            latitude=node.logical_location.latitude,
            longitude=node.logical_location.longitude,
        ),
        data=WeatherData(),
    )


def fetch_weather(
    node: NodeConfig,
    moment: datetime,
    *,
    client: httpx.Client | None = None,
    attempts: int = 3,
    timeout_seconds: float = 10.0,
) -> Weather:
    """Consulta Open-Meteo; cualquier fallo termina en ``unavailable``."""

    if attempts < 1 or attempts > 3:
        raise ValueError("attempts debe estar entre 1 y 3")

    requested_at = isoformat_utc(moment)
    owns_client = client is None
    active_client = client or httpx.Client()
    params = {
        "latitude": node.logical_location.latitude,
        "longitude": node.logical_location.longitude,
        "current": OPEN_METEO_CONDITION_VARIABLES,
        "timezone": "UTC",
        "forecast_days": 1,
    }

    try:
        for _ in range(attempts):
            try:
                response = active_client.get(
                    OPEN_METEO_FORECAST_URL,
                    params=params,
                    timeout=timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError(
                        "Open-Meteo entregó una respuesta JSON inválida"
                    )
                return normalize_response(payload, node, requested_at)
            except (httpx.HTTPError, ValueError, TypeError):
                continue
    finally:
        if owns_client:
            active_client.close()

    return unavailable_weather(node, requested_at)


def normalize_current_weather_response(
    payload: dict[str, Any],
    node: NodeConfig,
    requested_at: str,
) -> Weather:
    """Normaliza un proveedor meteorológico único basado en Open-Meteo."""

    current = payload.get("current")
    if not isinstance(current, dict):
        raise ValueError("Open-Meteo no entregó condiciones actuales")

    latitude = _optional_number(payload, "latitude")
    longitude = _optional_number(payload, "longitude")
    observed_at = _observed_at(current)
    data = WeatherData(
        temperature_c=_optional_number(current, "temperature_2m"),
        relative_humidity_percent=_optional_number(
            current, "relative_humidity_2m"
        ),
        precipitation_mm=_optional_number(current, "precipitation"),
        pressure_hpa=_optional_number(current, "pressure_msl"),
        wind_speed_kmh=_optional_number(current, "wind_speed_10m"),
        solar_radiation_wm2=_optional_number(current, "shortwave_radiation"),
        condition_code=_condition_code(current),
    )
    measurement_values = (
        data.temperature_c,
        data.relative_humidity_percent,
        data.precipitation_mm,
        data.pressure_hpa,
        data.wind_speed_kmh,
        data.solar_radiation_wm2,
    )
    if not any(value is not None for value in measurement_values):
        raise ValueError("Open-Meteo no entregó mediciones meteorológicas actuales")

    return Weather(
        status="available",
        provider=OPEN_METEO_CURRENT_PROVIDER,
        requested_at=requested_at,
        observed_at=observed_at,
        condition_provider=OPEN_METEO_CURRENT_PROVIDER,
        condition_observed_at=observed_at,
        measurement_source_count=1,
        measurement_source_codes=[OPEN_METEO_CURRENT_PROVIDER],
        location=WeatherLocation(
            latitude=(
                latitude
                if latitude is not None
                else node.logical_location.latitude
            ),
            longitude=(
                longitude if longitude is not None else node.logical_location.longitude
            ),
        ),
        data=data,
    )


def unavailable_current_weather(
    node: NodeConfig,
    requested_at: str,
) -> Weather:
    """Representa un proveedor meteorológico único no disponible."""

    return Weather(
        status="unavailable",
        provider=OPEN_METEO_CURRENT_PROVIDER,
        requested_at=requested_at,
        observed_at=None,
        condition_provider=OPEN_METEO_CURRENT_PROVIDER,
        condition_observed_at=None,
        measurement_source_count=0,
        measurement_source_codes=[],
        location=WeatherLocation(
            latitude=node.logical_location.latitude,
            longitude=node.logical_location.longitude,
        ),
        data=WeatherData(),
    )


def fetch_current_weather(
    node: NodeConfig,
    moment: datetime,
    *,
    client: httpx.Client | None = None,
    attempts: int = 3,
    timeout_seconds: float = 10.0,
) -> Weather:
    """Consulta un conjunto meteorológico completo para nodos de fuente única."""

    if attempts < 1 or attempts > 3:
        raise ValueError("attempts debe estar entre 1 y 3")

    requested_at = isoformat_utc(moment)
    owns_client = client is None
    active_client = client or httpx.Client()
    params = {
        "latitude": node.logical_location.latitude,
        "longitude": node.logical_location.longitude,
        "current": OPEN_METEO_FULL_CURRENT_VARIABLES,
        "timezone": "UTC",
        "forecast_days": 1,
    }

    try:
        for _ in range(attempts):
            try:
                response = active_client.get(
                    OPEN_METEO_FORECAST_URL,
                    params=params,
                    timeout=timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError(
                        "Open-Meteo entregó una respuesta JSON inválida"
                    )
                return normalize_current_weather_response(
                    payload, node, requested_at
                )
            except (httpx.HTTPError, ValueError, TypeError):
                continue
    finally:
        if owns_client:
            active_client.close()

    return unavailable_current_weather(node, requested_at)


def _format_metric(value: float | None, unit: str, *, decimals: int = 1) -> str:
    if value is None:
        return "—"
    return f"{value:.{decimals}f} {unit}"


def open_meteo_source_snapshot(weather: Weather, node: NodeConfig) -> dict[str, Any]:
    """Proyecta ``Weather`` a la tarjeta genérica de una fuente meteorológica."""

    updated_at_local = "—"
    if weather.observed_at:
        try:
            observed = datetime.fromisoformat(
                weather.observed_at.replace("Z", "+00:00")
            )

            updated_at_local = (
                observed.astimezone(ZoneInfo(node.logical_location.timezone))
                .isoformat(timespec="seconds")
                .replace("T", " ")
            )
        except (ValueError, TypeError):
            updated_at_local = "—"

    metrics = [
        {
            "label": "Temperatura",
            "icon": "🌡️",
            "icon_label": "Temperatura",
            "value": _format_metric(weather.data.temperature_c, "°C"),
        },
        {
            "label": "Humedad",
            "icon": "💧",
            "icon_label": "Humedad relativa",
            "value": _format_metric(
                weather.data.relative_humidity_percent,
                "%",
            ),
        },
        {
            "label": "Presión",
            "icon": "⏱️",
            "icon_label": "Presión atmosférica",
            "value": _format_metric(weather.data.pressure_hpa, "hPa"),
        },
        {
            "label": "Viento",
            "icon": "💨",
            "icon_label": "Velocidad del viento",
            "value": _format_metric(weather.data.wind_speed_kmh, "km/h"),
        },
        {
            "label": "Precipitación",
            "icon": "🌧️",
            "icon_label": "Precipitación",
            "value": _format_metric(weather.data.precipitation_mm, "mm"),
        },
        {
            "label": "Radiación",
            "icon": "☀️",
            "icon_label": "Radiación solar",
            "value": _format_metric(weather.data.solar_radiation_wm2, "W/m²"),
        },
    ]
    available_metrics = [
        metric for metric in metrics if metric["value"] != "—"
    ]
    return {
        "label": "Proveedor meteorológico principal",
        "source_label": "Open-Meteo",
        "source_url": OPEN_METEO_PUBLIC_URL,
        "station_code": OPEN_METEO_CURRENT_PROVIDER,
        "station": "Central Park - Nueva York",
        "name": "Central Park - Nueva York",
        "status": weather.status,
        "observed_at": weather.observed_at,
        "updated_at_local": updated_at_local,
        "data": weather.data.model_dump(mode="json"),
        "metrics": available_metrics,
        "ephemerides": [],
        "error": None if weather.status == "available" else "unavailable",
    }
