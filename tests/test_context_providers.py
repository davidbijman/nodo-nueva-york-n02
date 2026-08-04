from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from distributed_node.config import load_node_configuration
from distributed_node.context_providers import (
    ASTRONOMY_HORIZONS,
    ECONOMY_US_DATA,
    GEOLOGY_USGS_EARTHQUAKES,
    WEATHER_CONDITION_OPEN_METEO,
    WEATHER_OPEN_METEO_CURRENT,
    WEATHER_REDMETEO_COMPOSITE,
    ProviderConfigurationError,
    collect_node_context,
    default_provider_registry,
    validate_context_provider_configuration,
)
from tests.factories import node_from_profile

ROOT = Path(__file__).resolve().parents[1]
MOMENT = datetime(2026, 8, 1, 12, 6, tzinfo=UTC)


def test_configured_context_providers_are_registered_and_node_independent() -> None:
    node = load_node_configuration(ROOT / "config")
    registry = default_provider_registry()

    validate_context_provider_configuration(node, registry=registry)

    assert WEATHER_REDMETEO_COMPOSITE in registry.provider_ids("weather")
    assert WEATHER_OPEN_METEO_CURRENT in registry.provider_ids("weather")
    assert WEATHER_CONDITION_OPEN_METEO in registry.provider_ids("weather-condition")
    assert ASTRONOMY_HORIZONS in registry.provider_ids("astronomy")
    assert ECONOMY_US_DATA in registry.provider_ids("economy")
    assert GEOLOGY_USGS_EARTHQUAKES in registry.provider_ids("geology")

    invalid = node.model_copy(deep=True)
    invalid.context_sources.economy.provider = "provider-not-registered"
    with pytest.raises(ProviderConfigurationError, match="provider-not-registered"):
        validate_context_provider_configuration(invalid, registry=registry)


def test_single_weather_profile_uses_one_provider_for_data_and_condition() -> None:
    node = node_from_profile("n02")

    validate_context_provider_configuration(node)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "latitude": 40.7789,
                "longitude": -73.9692,
                "current": {
                    "time": "2026-08-01T08:00:00-04:00",
                    "temperature_2m": 24.4,
                    "relative_humidity_2m": 66.0,
                    "precipitation": 0.2,
                    "pressure_msl": 1014.8,
                    "wind_speed_10m": 12.6,
                    "shortwave_radiation": 315.0,
                    "weather_code": 2,
                },
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        context = collect_node_context(
            node,
            MOMENT,
            weather_client=client,
            fetch_economy=False,
            fetch_geology=False,
            fetch_astronomy=False,
        )

    assert context.weather.status == "available"
    assert context.weather.provider == WEATHER_OPEN_METEO_CURRENT
    assert context.condition_weather is context.weather
    assert context.weather.measurement_source_count == 1
    assert list(context.weather_sources) == [WEATHER_OPEN_METEO_CURRENT]
    assert context.weather.data.temperature_c == 24.4
    assert context.weather.data.condition_code == 2
    assert context.economy["provider"] == "us-economic-data"
    assert context.geology["provider"] == "usgs-earthquakes"

    invalid = node.model_copy(deep=True)
    invalid.context_sources.weather.condition_provider = WEATHER_CONDITION_OPEN_METEO
    with pytest.raises(
        ProviderConfigurationError,
        match="fuente única no debe declarar condition_provider",
    ):
        validate_context_provider_configuration(invalid)
