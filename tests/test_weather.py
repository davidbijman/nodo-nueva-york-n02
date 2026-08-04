from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from distributed_node.models import NodeConfig, Weather, WeatherData, WeatherLocation
from distributed_node.providers.weather.redmeteo_composite import (
    REDMETEO_AGGREGATE_PROVIDER,
    build_composite_weather,
    normalize_redmeteo_payload,
)
from distributed_node.weather import (
    OPEN_METEO_CURRENT_PROVIDER,
    OPEN_METEO_CURRENT_VARIABLES,
    OPEN_METEO_FULL_CURRENT_VARIABLES,
    OPEN_METEO_PROVIDER,
    fetch_current_weather,
    fetch_weather,
    normalize_current_weather_response,
    normalize_response,
    open_meteo_source_snapshot,
)

ROOT = Path(__file__).resolve().parents[1]
MOMENT = datetime(2026, 8, 1, 12, 6, tzinfo=UTC)


def node() -> NodeConfig:
    return NodeConfig.model_validate_json(
        (ROOT / "config/node.json").read_text(encoding="utf-8")
    )


def open_meteo_payload(
    *, code: int = 2, include_coordinates: bool = False
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "current": {
            "time": "2026-08-01T09:00:00-03:00",
            "interval": 900,
            "weather_code": code,
        }
    }
    if include_coordinates:
        payload.update({"latitude": -33.45, "longitude": -70.67})
    return payload



def open_meteo_full_payload() -> dict[str, Any]:
    return {
        "latitude": 40.71,
        "longitude": -74.01,
        "current": {
            "time": "2026-08-01T08:00:00-04:00",
            "interval": 900,
            "temperature_2m": 24.4,
            "relative_humidity_2m": 66.0,
            "precipitation": 0.2,
            "pressure_msl": 1014.8,
            "wind_speed_10m": 12.6,
            "shortwave_radiation": 315.0,
            "weather_code": 2,
        },
    }


def condition_weather(*, code: int | None = 2) -> Weather:
    return Weather(
        status="available",
        provider=OPEN_METEO_PROVIDER,
        requested_at="2026-08-01T12:06:00Z",
        observed_at="2026-08-01T12:00:00Z",
        condition_provider=OPEN_METEO_PROVIDER,
        condition_observed_at="2026-08-01T12:00:00Z",
        location=WeatherLocation(latitude=-33.45, longitude=-70.67),
        data=WeatherData(condition_code=code),
    )


def test_open_meteo_condition_adapter_is_minimal_and_failure_safe() -> None:
    configured_node = node()
    requests: list[httpx.Request] = []

    def success(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=open_meteo_payload())

    with httpx.Client(transport=httpx.MockTransport(success)) as client:
        weather = fetch_weather(configured_node, MOMENT, client=client, attempts=1)

    assert weather.status == "available"
    assert weather.observed_at == "2026-08-01T12:00:00Z"
    assert weather.location.latitude == configured_node.logical_location.latitude
    assert weather.location.longitude == configured_node.logical_location.longitude
    assert requests[0].url.params["current"] == OPEN_METEO_CURRENT_VARIABLES
    assert OPEN_METEO_CURRENT_VARIABLES == "weather_code"
    assert weather.data.model_dump() == {
        "temperature_c": None,
        "relative_humidity_percent": None,
        "precipitation_mm": None,
        "pressure_hpa": None,
        "wind_speed_kmh": None,
        "solar_radiation_wm2": None,
        "condition_code": 2,
    }

    unknown = normalize_response(
        open_meteo_payload(code=999, include_coordinates=True),
        configured_node,
        "2026-08-01T12:06:00Z",
    )
    assert unknown.data.condition_code is None

    invalid_payloads: list[tuple[str, httpx.MockTransport]] = [
        (
            "missing-time",
            httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={"current": {"weather_code": 2}},
                )
            ),
        ),
        (
            "invalid-json",
            httpx.MockTransport(
                lambda request: httpx.Response(200, content=b"{")
            ),
        ),
    ]

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("sin respuesta", request=request)

    invalid_payloads.append(("timeout", httpx.MockTransport(timeout)))

    for name, transport in invalid_payloads:
        with httpx.Client(transport=transport) as client:
            unavailable = fetch_weather(
                configured_node,
                MOMENT,
                client=client,
                attempts=1,
            )
        assert unavailable.status == "unavailable", name
        assert all(
            value is None
            for value in unavailable.data.model_dump().values()
        ), name


def test_station_payloads_are_normalized_and_aggregated_per_parameter() -> None:
    configured_node = node()
    first = normalize_redmeteo_payload(
        {
            "metadatos": {
                "id_estacion": "SOURCE_A",
                "nombre": "Fuente A",
                "ultima_actualizacion": "2026-08-01T12:00:00Z",
            },
            "t": [10.1],
            "rh": [74.9],
            "slp": [1020.04],
            "vv": [1.0],
            "vd": [180],
            "ppd": [0.1],
            "sw": [125.1],
            "efemeride": {},
        },
        MOMENT,
        station_code="SOURCE_A",
        label="Fuente principal",
    )
    second = normalize_redmeteo_payload(
        {
            "metadatos": {
                "id_estacion": "SOURCE_B",
                "nombre": "Fuente B",
                "ultima_actualizacion": "2026-08-01T12:01:00Z",
            },
            "t": [10.2],
            "rh": [75.0],
            "slp": [1020.10],
            "vv": [2.0],
            "vd": [90],
            "ppd": [0.2],
            "sw": [125.2],
            "efemeride": {},
        },
        MOMENT,
        station_code="SOURCE_B",
        label="Fuente complementaria",
    )

    assert first["station"] == "Fuente A"
    assert first["data"]["solar_radiation_wm2"] == 125.1
    assert first["data"]["wind_speed_kmh"] == 3.6
    assert first["data"]["wind_direction_degrees"] == 180.0

    composite = build_composite_weather(
        configured_node,
        MOMENT,
        {"first": first, "second": second},
        condition_weather(),
    )
    assert composite.status == "available"
    assert composite.provider == REDMETEO_AGGREGATE_PROVIDER
    assert composite.measurement_source_count == 2
    assert composite.data.temperature_c == 10.15
    assert composite.data.relative_humidity_percent == 74.95
    assert composite.data.pressure_hpa == 1020.07
    assert composite.data.precipitation_mm == 0.15
    assert composite.data.solar_radiation_wm2 == 125.15
    assert composite.data.wind_speed_kmh is None
    assert composite.data.condition_code == 2

    single_source = build_composite_weather(
        configured_node,
        MOMENT,
        {
            "first": first,
            "second": {
                "status": "unavailable",
                "station_code": "SOURCE_B",
                "observed_at": None,
                "data": {},
            },
        },
        condition_weather(code=None),
    )
    assert single_source.measurement_source_count == 1
    assert single_source.data.temperature_c == 10.1
    assert single_source.data.solar_radiation_wm2 == 125.1


def test_open_meteo_current_provider_normalizes_a_single_complete_source() -> None:
    configured_node = node().model_copy(deep=True)
    configured_node.node_id = "N02"
    configured_node.display_name = "Nodo Nueva York"
    configured_node.logical_location.city = "Nueva York"
    configured_node.logical_location.country = "Estados Unidos"
    configured_node.logical_location.country_code = "US"
    configured_node.logical_location.timezone = "America/New_York"
    configured_node.logical_location.latitude = 40.7128
    configured_node.logical_location.longitude = -74.0060

    requests: list[httpx.Request] = []

    def success(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=open_meteo_full_payload())

    with httpx.Client(transport=httpx.MockTransport(success)) as client:
        weather = fetch_current_weather(
            configured_node,
            MOMENT,
            client=client,
            attempts=1,
        )

    assert weather.status == "available"
    assert weather.provider == OPEN_METEO_CURRENT_PROVIDER
    assert weather.condition_provider == OPEN_METEO_CURRENT_PROVIDER
    assert weather.measurement_source_count == 1
    assert weather.measurement_source_codes == [OPEN_METEO_CURRENT_PROVIDER]
    assert weather.data.temperature_c == 24.4
    assert weather.data.relative_humidity_percent == 66.0
    assert weather.data.precipitation_mm == 0.2
    assert weather.data.pressure_hpa == 1014.8
    assert weather.data.wind_speed_kmh == 12.6
    assert weather.data.solar_radiation_wm2 == 315.0
    assert weather.data.condition_code == 2
    assert requests[0].url.params["current"] == OPEN_METEO_FULL_CURRENT_VARIABLES

    snapshot = open_meteo_source_snapshot(weather, configured_node)
    assert snapshot["status"] == "available"
    assert snapshot["station_code"] == OPEN_METEO_CURRENT_PROVIDER
    assert snapshot["name"] == "Central Park - Nueva York"
    assert len(snapshot["metrics"]) == 6

    invalid = open_meteo_full_payload()
    invalid["current"] = {
        "time": "2026-08-01T08:00:00-04:00",
        "weather_code": 2,
    }
    with pytest.raises(
        ValueError,
        match="no entregó mediciones meteorológicas actuales",
    ):
        normalize_current_weather_response(
            invalid,
            configured_node,
            "2026-08-01T12:06:00Z",
        )
