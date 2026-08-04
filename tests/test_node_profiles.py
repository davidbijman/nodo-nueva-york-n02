from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from distributed_node.astronomy import _observer
from distributed_node.context_providers import (
    collect_node_context,
    validate_context_provider_configuration,
)
from tests.factories import PROFILE_IDS, load_node_profile, node_from_profile

MOMENT = datetime(2026, 8, 1, 12, 6, tzinfo=UTC)


def open_meteo_client(latitude: float, longitude: float) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "latitude": latitude,
                    "longitude": longitude,
                    "current": {
                        "time": "2026-08-01T12:00:00Z",
                        "temperature_2m": 18.4,
                        "relative_humidity_2m": 64.0,
                        "precipitation": 0.1,
                        "pressure_msl": 1016.8,
                        "wind_speed_10m": 8.2,
                        "shortwave_radiation": 280.0,
                        "weather_code": 2,
                    },
                },
            )
        )
    )


def station_client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        code = request.url.path.rsplit("/", 1)[-1].removesuffix(".json")
        value_offset = 0.0 if code.endswith("0098") else 2.0
        return httpx.Response(
            200,
            json={
                "metadatos": {
                    "id_estacion": code,
                    "nombre": f"Fuente {code}",
                    "ultima_actualizacion": "2026-08-01T12:00:00Z",
                },
                "t": [14.0 + value_offset],
                "rh": [70.0 + value_offset],
                "slp": [1014.0 + value_offset],
                "vv": [1.0],
                "vd": [180],
                "ppd": [0.2 + value_offset / 10],
                "sw": [200.0 + value_offset * 50],
                "efemeride": {},
            },
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("profile_id", PROFILE_IDS)
def test_node_profiles_propagate_identity_location_and_weather_mode(
    profile_id: str,
) -> None:
    profile = load_node_profile(profile_id)
    node = node_from_profile(profile_id)

    validate_context_provider_configuration(node)

    assert node.node_id == profile.node_id
    assert node.display_name == profile.display_name
    assert node.logical_location.city == profile.city
    assert node.logical_location.timezone == profile.timezone
    assert node.infrastructure.provider == profile.platform
    assert node.context_sources.weather.provider == profile.weather_provider
    assert node.context_sources.weather.mode == profile.weather_mode
    assert (
        node.context_sources.weather.condition_provider
        == profile.condition_provider
    )

    observer = _observer(node)
    assert observer["name"] == f"{profile.city} · coordenadas {profile.node_id}"
    assert observer["latitude_deg"] == profile.latitude
    assert observer["longitude_deg"] == profile.longitude
    assert observer["timezone"] == profile.timezone


@pytest.mark.parametrize("profile_id", PROFILE_IDS)
def test_node_profiles_collect_the_expected_number_of_weather_sources(
    profile_id: str,
) -> None:
    profile = load_node_profile(profile_id)
    node = node_from_profile(profile_id)

    with open_meteo_client(profile.latitude, profile.longitude) as weather_client:
        redmeteo = station_client() if profile.weather_mode == "composite" else None
        try:
            context = collect_node_context(
                node,
                MOMENT,
                weather_client=weather_client,
                redmeteo_client=redmeteo,
                fetch_astronomy=False,
                fetch_economy=False,
                fetch_geology=False,
            )
        finally:
            if redmeteo is not None:
                redmeteo.close()

    assert context.weather.status == "available"
    assert (
        context.weather.measurement_source_count
        == profile.expected_weather_source_count
    )
    assert len(context.weather_sources) == profile.expected_weather_source_count
    assert context.weather.location.latitude == profile.latitude
    assert context.weather.location.longitude == profile.longitude
    assert context.astronomy["provider"] == profile.astronomy_provider
    assert context.economy["provider"] == profile.economy_provider
    assert context.geology["provider"] == profile.geology_provider


def test_unknown_node_profile_is_rejected() -> None:
    with pytest.raises(ValueError, match="Perfil de prueba desconocido"):
        load_node_profile("n99")
