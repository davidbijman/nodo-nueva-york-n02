"""Registro configurable de proveedores de contexto del nodo.

El ciclo consume dominios normalizados y no decide por identificador de nodo qué
adaptador debe ejecutar. La ubicación siempre llega desde ``NodeConfig``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from .astronomy import fetch_astronomy_snapshot, unavailable_astronomy_snapshot
from .models import EconomySnapshot, GeologySnapshot, NodeConfig, Weather
from .providers.economy.chile_bcentral import (
    fetch_bcentral_snapshot,
    unavailable_bcentral_snapshot,
)
from .providers.economy.us_economic_data import (
    fetch_us_economic_snapshot,
    unavailable_us_economic_snapshot,
)
from .providers.geology.chile_csn import (
    fetch_seismic_snapshot,
    unavailable_seismic_snapshot,
)
from .providers.geology.usgs_earthquakes import (
    fetch_usgs_snapshot,
    unavailable_usgs_snapshot,
)
from .providers.weather.redmeteo_composite import (
    build_composite_weather,
    fetch_redmeteo_snapshots,
    unavailable_redmeteo_snapshots,
)
from .sonantia_protocol import isoformat_utc
from .weather import (
    fetch_current_weather,
    open_meteo_source_snapshot,
    unavailable_current_weather,
    unavailable_weather,
)

WEATHER_REDMETEO_COMPOSITE = "redmeteo-composite"
WEATHER_OPEN_METEO_CURRENT = "open-meteo-current"
WEATHER_CONDITION_OPEN_METEO = "open-meteo-condition"
ASTRONOMY_HORIZONS = "nasa-jpl-horizons"
ECONOMY_CHILE_BCENTRAL = "chile-bcentral"
ECONOMY_US_DATA = "us-economic-data"
GEOLOGY_CHILE_CSN = "chile-csn"
GEOLOGY_USGS_EARTHQUAKES = "usgs-earthquakes"


class ProviderConfigurationError(ValueError):
    """La configuración solicita un proveedor no registrado o incompatible."""


@dataclass(frozen=True, slots=True)
class WeatherProviderResult:
    weather: Weather
    source_snapshots: dict[str, dict[str, Any]]


@dataclass(frozen=True, slots=True)
class NodeContext:
    weather: Weather
    condition_weather: Weather
    weather_sources: dict[str, dict[str, Any]]
    astronomy: dict[str, Any]
    economy: dict[str, Any]
    geology: dict[str, Any]


ProviderFetcher = Callable[..., Any]


class ProviderRegistry:
    """Registro pequeño por dominio, extensible sin condicionales por nodo."""

    def __init__(self) -> None:
        self._providers: dict[str, dict[str, ProviderFetcher]] = {
            "weather": {},
            "weather-condition": {},
            "astronomy": {},
            "economy": {},
            "geology": {},
        }

    def register(self, domain: str, provider_id: str, fetcher: ProviderFetcher) -> None:
        if domain not in self._providers:
            raise ProviderConfigurationError(f"Dominio de proveedor desconocido: {domain}")
        if provider_id in self._providers[domain]:
            raise ProviderConfigurationError(f"Proveedor duplicado para {domain}: {provider_id}")
        self._providers[domain][provider_id] = fetcher

    def resolve(self, domain: str, provider_id: str) -> ProviderFetcher:
        try:
            return self._providers[domain][provider_id]
        except KeyError as exc:
            raise ProviderConfigurationError(
                f"Proveedor no registrado para {domain}: {provider_id}"
            ) from exc

    def provider_ids(self, domain: str) -> tuple[str, ...]:
        if domain not in self._providers:
            raise ProviderConfigurationError(f"Dominio de proveedor desconocido: {domain}")
        return tuple(sorted(self._providers[domain]))


def _redmeteo_composite_provider(
    node: NodeConfig,
    moment: datetime,
    *,
    client: httpx.Client | None,
    condition_weather: Weather,
    enabled: bool,
    settings: dict[str, Any],
) -> WeatherProviderResult:
    del settings
    snapshots = (
        fetch_redmeteo_snapshots(moment, client=client)
        if enabled
        else unavailable_redmeteo_snapshots(moment)
    )
    return WeatherProviderResult(
        weather=build_composite_weather(
            node,
            moment,
            snapshots,
            condition_weather,
        ),
        source_snapshots=snapshots,
    )


def _open_meteo_current_provider(
    node: NodeConfig,
    moment: datetime,
    *,
    client: httpx.Client | None,
    condition_weather: Weather,
    enabled: bool,
    settings: dict[str, Any],
) -> WeatherProviderResult:
    del condition_weather, settings
    weather = (
        fetch_current_weather(node, moment, client=client)
        if enabled
        else unavailable_current_weather(node, isoformat_utc(moment))
    )
    return WeatherProviderResult(
        weather=weather,
        source_snapshots={WEATHER_OPEN_METEO_CURRENT: open_meteo_source_snapshot(weather, node)},
    )


def _horizons_provider(
    node: NodeConfig,
    moment: datetime,
    *,
    client: httpx.Client | None,
    enabled: bool,
    settings: dict[str, Any],
) -> dict[str, Any]:
    del settings
    return (
        fetch_astronomy_snapshot(node, moment, client=client)
        if enabled
        else unavailable_astronomy_snapshot(moment, node=node)
    )


def _chile_bcentral_provider(
    node: NodeConfig,
    moment: datetime,
    *,
    client: httpx.Client | None,
    enabled: bool,
    settings: dict[str, Any],
) -> dict[str, Any]:
    del settings
    if enabled and node.logical_location.country_code != "CL":
        raise ProviderConfigurationError(
            "chile-bcentral solo puede usarse en un perfil con country_code CL"
        )
    snapshot = (
        fetch_bcentral_snapshot(moment, client=client)
        if enabled
        else unavailable_bcentral_snapshot(moment)
    )
    return EconomySnapshot.model_validate(snapshot).model_dump(mode="json")


def _chile_csn_provider(
    node: NodeConfig,
    moment: datetime,
    *,
    client: httpx.Client | None,
    enabled: bool,
    settings: dict[str, Any],
) -> dict[str, Any]:
    del settings
    if enabled and node.logical_location.country_code != "CL":
        raise ProviderConfigurationError(
            "chile-csn solo puede usarse en un perfil con country_code CL"
        )
    snapshot = (
        fetch_seismic_snapshot(moment, client=client)
        if enabled
        else unavailable_seismic_snapshot(moment)
    )
    return GeologySnapshot.model_validate(snapshot).model_dump(mode="json")


def _us_economic_provider(
    node: NodeConfig,
    moment: datetime,
    *,
    client: httpx.Client | None,
    enabled: bool,
    settings: dict[str, Any],
) -> dict[str, Any]:
    del settings
    if enabled and node.logical_location.country_code != "US":
        raise ProviderConfigurationError(
            "us-economic-data solo puede usarse en un perfil con country_code US"
        )
    snapshot = (
        fetch_us_economic_snapshot(moment, client=client)
        if enabled
        else unavailable_us_economic_snapshot(moment)
    )
    return EconomySnapshot.model_validate(snapshot).model_dump(mode="json")


def _usgs_earthquakes_provider(
    node: NodeConfig,
    moment: datetime,
    *,
    client: httpx.Client | None,
    enabled: bool,
    settings: dict[str, Any],
) -> dict[str, Any]:
    del settings
    if enabled and node.logical_location.country_code != "US":
        raise ProviderConfigurationError(
            "usgs-earthquakes solo puede usarse en un perfil con country_code US"
        )
    snapshot = (
        fetch_usgs_snapshot(node, moment, client=client)
        if enabled
        else unavailable_usgs_snapshot(moment)
    )
    return GeologySnapshot.model_validate(snapshot).model_dump(mode="json")


def _provider_label(provider_id: str) -> str:
    return provider_id.replace("-", " ").strip().title() or "Proveedor no configurado"


def _disabled_astronomy_snapshot(
    node: NodeConfig,
    moment: datetime,
    provider_id: str,
) -> dict[str, Any]:
    location = node.logical_location
    return {
        "status": "unavailable",
        "provider": provider_id,
        "generated_at": isoformat_utc(moment),
        "observer": {
            "name": f"{location.city} · coordenadas {node.node_id}",
            "longitude_deg": location.longitude,
            "latitude_deg": location.latitude,
            "elevation_km": location.elevation_m / 1000,
            "timezone": location.timezone,
        },
        "earth": None,
        "targets": [],
        "error": "Proveedor deshabilitado para este perfil",
    }


def _disabled_economy_snapshot(
    node: NodeConfig,
    moment: datetime,
    provider_id: str,
) -> dict[str, Any]:
    return EconomySnapshot(
        provider=provider_id,
        provider_label=_provider_label(provider_id),
        region_label=node.logical_location.country,
        country_code=node.logical_location.country_code,
        generated_at=isoformat_utc(moment),
        error="Proveedor deshabilitado para este perfil",
    ).model_dump(mode="json")


def _disabled_geology_snapshot(
    node: NodeConfig,
    moment: datetime,
    provider_id: str,
) -> dict[str, Any]:
    return GeologySnapshot(
        provider=provider_id,
        provider_label=_provider_label(provider_id),
        region_label=node.logical_location.country,
        country_code=node.logical_location.country_code,
        generated_at=isoformat_utc(moment),
        error="Proveedor deshabilitado para este perfil",
    ).model_dump(mode="json")


def default_provider_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(
        "weather",
        WEATHER_REDMETEO_COMPOSITE,
        _redmeteo_composite_provider,
    )
    registry.register(
        "weather",
        WEATHER_OPEN_METEO_CURRENT,
        _open_meteo_current_provider,
    )
    registry.register(
        "weather-condition",
        WEATHER_CONDITION_OPEN_METEO,
        fetch_current_weather,
    )
    registry.register("astronomy", ASTRONOMY_HORIZONS, _horizons_provider)
    registry.register("economy", ECONOMY_CHILE_BCENTRAL, _chile_bcentral_provider)
    registry.register("economy", ECONOMY_US_DATA, _us_economic_provider)
    registry.register("geology", GEOLOGY_CHILE_CSN, _chile_csn_provider)
    registry.register("geology", GEOLOGY_USGS_EARTHQUAKES, _usgs_earthquakes_provider)
    return registry


def validate_context_provider_configuration(
    node: NodeConfig,
    *,
    registry: ProviderRegistry | None = None,
) -> None:
    providers = registry or default_provider_registry()
    sources = node.context_sources
    weather = sources.weather
    if weather.enabled:
        providers.resolve("weather", weather.provider)
        if weather.mode == "composite" and not weather.condition_provider:
            raise ProviderConfigurationError("El clima compuesto requiere condition_provider")
        if weather.mode == "single" and weather.condition_provider:
            raise ProviderConfigurationError(
                "El clima de fuente única no debe declarar condition_provider"
            )
        if weather.condition_provider:
            providers.resolve("weather-condition", weather.condition_provider)
    for domain in ("astronomy", "economy", "geology"):
        source = getattr(sources, domain)
        if source.enabled:
            providers.resolve(domain, source.provider)


def collect_node_context(
    node: NodeConfig,
    moment: datetime,
    *,
    registry: ProviderRegistry | None = None,
    weather_client: httpx.Client | None = None,
    redmeteo_client: httpx.Client | None = None,
    economy_client: httpx.Client | None = None,
    geology_client: httpx.Client | None = None,
    astronomy_client: httpx.Client | None = None,
    fetch_weather_sources: bool = True,
    fetch_economy: bool = True,
    fetch_geology: bool = True,
    fetch_astronomy: bool = True,
) -> NodeContext:
    """Consulta los proveedores declarados y entrega un contexto normalizado."""

    providers = registry or default_provider_registry()
    validate_context_provider_configuration(node, registry=providers)
    sources = node.context_sources
    weather_config = sources.weather

    if (
        weather_config.enabled
        and weather_config.mode == "composite"
        and weather_config.condition_provider
    ):
        condition_fetcher = providers.resolve(
            "weather-condition", weather_config.condition_provider
        )
        condition_weather = condition_fetcher(node, moment, client=weather_client)
    else:
        condition_weather = unavailable_weather(node, isoformat_utc(moment))

    weather_fetcher = providers.resolve("weather", weather_config.provider)
    source_client = redmeteo_client if weather_config.mode == "composite" else weather_client
    weather_result = weather_fetcher(
        node,
        moment,
        client=source_client,
        condition_weather=condition_weather,
        enabled=weather_config.enabled and fetch_weather_sources,
        settings=weather_config.settings,
    )
    weather = weather_result.weather
    if weather_config.mode == "single":
        condition_weather = weather

    astronomy_config = sources.astronomy
    if astronomy_config.enabled and fetch_astronomy:
        astronomy_fetcher = providers.resolve("astronomy", astronomy_config.provider)
        astronomy = astronomy_fetcher(
            node,
            moment,
            client=astronomy_client,
            enabled=True,
            settings=astronomy_config.settings,
        )
    else:
        astronomy = _disabled_astronomy_snapshot(node, moment, astronomy_config.provider)

    economy_config = sources.economy
    if economy_config.enabled and fetch_economy:
        economy_fetcher = providers.resolve("economy", economy_config.provider)
        economy = economy_fetcher(
            node,
            moment,
            client=economy_client,
            enabled=True,
            settings=economy_config.settings,
        )
    else:
        economy = _disabled_economy_snapshot(node, moment, economy_config.provider)

    geology_config = sources.geology
    if geology_config.enabled and fetch_geology:
        geology_fetcher = providers.resolve("geology", geology_config.provider)
        geology = geology_fetcher(
            node,
            moment,
            client=geology_client,
            enabled=True,
            settings=geology_config.settings,
        )
    else:
        geology = _disabled_geology_snapshot(node, moment, geology_config.provider)

    return NodeContext(
        weather=weather,
        condition_weather=condition_weather,
        weather_sources=weather_result.source_snapshots,
        astronomy=astronomy,
        economy=economy,
        geology=geology,
    )
