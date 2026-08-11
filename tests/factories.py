from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from distributed_node.config import load_node_configuration
from distributed_node.models import NodeConfig, Weather, WeatherData, WeatherLocation

ROOT = Path(__file__).resolve().parents[1]
PROFILES_DIR = ROOT / "tests/profiles"
PROFILE_IDS = ("n01", "n02", "n03", "n04")
MOMENT = datetime(2026, 8, 1, 12, 6, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class NodeProfile:
    profile_id: str
    node_id: str
    display_name: str
    zone: str
    country: str
    country_code: str
    city: str
    timezone: str
    latitude: float
    longitude: float
    elevation_m: float
    platform: str
    automation: str
    hosting: str
    public_url: str
    weather_provider: str
    weather_mode: Literal["single", "composite"]
    condition_provider: str | None
    expected_weather_source_count: int
    astronomy_provider: str
    astronomy_enabled: bool
    economy_provider: str
    economy_enabled: bool
    geology_provider: str
    geology_enabled: bool


def load_node_profile(profile_id: str) -> NodeProfile:
    if profile_id not in PROFILE_IDS:
        raise ValueError(f"Perfil de prueba desconocido: {profile_id}")
    document = json.loads((PROFILES_DIR / f"{profile_id}.json").read_text(encoding="utf-8"))
    node = document["node"]
    weather = document["weather"]
    context = document["context"]
    return NodeProfile(
        profile_id=str(document["profile_id"]),
        node_id=str(node["node_id"]),
        display_name=str(node["display_name"]),
        zone=str(node["zone"]),
        country=str(node["country"]),
        country_code=str(node["country_code"]),
        city=str(node["city"]),
        timezone=str(node["timezone"]),
        latitude=float(node["latitude"]),
        longitude=float(node["longitude"]),
        elevation_m=float(node["elevation_m"]),
        platform=str(node["platform"]),
        automation=str(node["automation"]),
        hosting=str(node["hosting"]),
        public_url=str(node["public_url"]),
        weather_provider=str(weather["provider"]),
        weather_mode=str(weather["mode"]),
        condition_provider=weather.get("condition_provider"),
        expected_weather_source_count=int(weather["expected_source_count"]),
        astronomy_provider=str(context["astronomy_provider"]),
        astronomy_enabled=bool(context["astronomy_enabled"]),
        economy_provider=str(context["economy_provider"]),
        economy_enabled=bool(context["economy_enabled"]),
        geology_provider=str(context["geology_provider"]),
        geology_enabled=bool(context["geology_enabled"]),
    )


def make_node(
    *,
    node_id: str = "N99",
    display_name: str = "Nodo de prueba",
    city: str = "Ciudad de prueba",
    country: str = "País de prueba",
    country_code: str = "XX",
    timezone: str = "UTC",
    latitude: float = 0.0,
    longitude: float = 0.0,
    elevation_m: float = 0.0,
    platform: str = "test",
) -> NodeConfig:
    node = load_node_configuration(ROOT / "config").model_copy(deep=True)
    node.node_id = node_id
    node.display_name = display_name
    node.logical_location.zone = "test-zone"
    node.logical_location.city = city
    node.logical_location.country = country
    node.logical_location.country_code = country_code
    node.logical_location.timezone = timezone
    node.logical_location.latitude = latitude
    node.logical_location.longitude = longitude
    node.logical_location.elevation_m = elevation_m
    node.infrastructure.provider = platform
    node.public_url = f"https://example.invalid/{node_id.lower()}/"
    return node


def node_from_profile(profile_id: str) -> NodeConfig:
    profile = load_node_profile(profile_id)
    node = make_node(
        node_id=profile.node_id,
        display_name=profile.display_name,
        city=profile.city,
        country=profile.country,
        country_code=profile.country_code,
        timezone=profile.timezone,
        latitude=profile.latitude,
        longitude=profile.longitude,
        elevation_m=profile.elevation_m,
        platform=profile.platform,
    )
    node.logical_location.zone = profile.zone
    node.infrastructure.automation = profile.automation
    node.infrastructure.hosting = profile.hosting
    node.public_url = profile.public_url

    node.context_sources.weather.provider = profile.weather_provider
    node.context_sources.weather.mode = profile.weather_mode
    node.context_sources.weather.condition_provider = profile.condition_provider
    node.context_sources.astronomy.provider = profile.astronomy_provider
    node.context_sources.astronomy.enabled = profile.astronomy_enabled
    node.context_sources.economy.provider = profile.economy_provider
    node.context_sources.economy.enabled = profile.economy_enabled
    node.context_sources.geology.provider = profile.geology_provider
    node.context_sources.geology.enabled = profile.geology_enabled
    return NodeConfig.model_validate(node.model_dump(mode="json"))


def write_node_profile(config_dir: Path, profile_id: str) -> NodeConfig:
    node = node_from_profile(profile_id)
    (config_dir / "node.json").write_text(
        json.dumps(node.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return node


def write_project_profile(config_dir: Path, profile_id: str) -> NodeConfig:
    node = write_node_profile(config_dir, profile_id)
    network_path = config_dir / "sonantia-network.json"
    network = json.loads(network_path.read_text(encoding="utf-8"))
    for item in network["nodes"]:
        is_local = item["node_id"] == node.node_id
        item["enabled"] = is_local
        if is_local:
            item["display_name"] = node.display_name
            item["platform"] = node.infrastructure.provider
            item["public_url"] = node.public_url
            base_url = node.public_url.rstrip("/")
            item["feed_url"] = f"{base_url}/feed.json"
            item["status_url"] = f"{base_url}/sonantia-status.json"
    network_path.write_text(
        json.dumps(network, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return node


def available_weather(*, provider: str = "test-weather", sources: int = 1) -> Weather:
    return Weather(
        status="available",
        provider=provider,
        requested_at="2026-08-01T12:06:00Z",
        observed_at="2026-08-01T12:00:00Z",
        condition_provider=provider,
        condition_observed_at="2026-08-01T12:00:00Z",
        measurement_source_count=sources,
        measurement_source_codes=[f"SOURCE_{index + 1}" for index in range(sources)],
        location=WeatherLocation(latitude=0.0, longitude=0.0),
        data=WeatherData(
            temperature_c=18.2,
            relative_humidity_percent=61.0,
            precipitation_mm=0.0,
            pressure_hpa=1017.4,
            wind_speed_kmh=5.5,
            solar_radiation_wm2=220.0,
            condition_code=2,
        ),
    )
