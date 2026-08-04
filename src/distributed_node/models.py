"""Modelos estrictos de configuración y contexto de Sonantia Network 1.0."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class FlexibleModel(BaseModel):
    model_config = ConfigDict(extra="allow", strict=False)


class NetworkMembership(StrictModel):
    status: Literal["active", "inactive"]
    joined_at: str | None = None


class LogicalLocation(StrictModel):
    zone: str
    country: str
    country_code: str = Field(pattern=r"^[A-Z]{2}$")
    city: str
    timezone: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    elevation_m: float = Field(default=0.0, ge=-500, le=10000)


class Infrastructure(StrictModel):
    provider: str
    automation: str
    hosting: str
    region_status: str
    region_description: str


class ContextSourceConfig(StrictModel):
    enabled: bool = True
    provider: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    settings: dict[str, Any] = Field(default_factory=dict)


class WeatherSourceConfig(ContextSourceConfig):
    mode: Literal["single", "composite"] = "single"
    condition_provider: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )


class ContextSources(StrictModel):
    weather: WeatherSourceConfig
    astronomy: ContextSourceConfig
    economy: ContextSourceConfig
    geology: ContextSourceConfig


class Endpoints(StrictModel):
    home: str
    feed: str
    status: str
    interactions: str
    archive_index: str
    operator_message: str


class Capabilities(StrictModel):
    publish_original_messages: bool
    replicate_messages: bool
    poll_peers: bool
    query_weather: bool
    publish_html: bool
    publish_json: bool
    publish_operator_message: bool
    user_message_board: bool
    private_messages: bool
    content_types: list[str]


class Software(StrictModel):
    implementation: str
    implementation_version: str


class NodeConfig(StrictModel):
    protocol_version: Literal["1.0"] = "1.0"
    document_type: Literal["node"]
    node_id: str = Field(pattern=r"^N\d{2}$")
    display_name: str
    node_role: Literal["publisher-replicator"]
    network_membership: NetworkMembership
    logical_location: LogicalLocation
    infrastructure: Infrastructure
    context_sources: ContextSources
    public_url: str
    endpoints: Endpoints
    capabilities: Capabilities
    software: Software


class NetworkNodeConfig(StrictModel):
    node_id: str = Field(pattern=r"^N\d{2}$")
    display_name: str
    platform: str
    enabled: bool = False
    public_url: str
    feed_url: str
    status_url: str


class StorageConfig(StrictModel):
    archive_period: Literal["daily"] = "daily"
    own_feed_limit: int = Field(default=48, ge=1)
    relay_retention_hours: int = Field(default=168, ge=1)
    relay_limit_per_origin: int = Field(default=168, ge=1)
    interaction_limit: int = Field(default=200, ge=1)
    data_directory: str = "data/sonantia"
    active_public_directory: str = "public"


class SonantiaNetworkConfig(StrictModel):
    network_id: Literal["sonantia-network"]
    network_name: str
    protocol_name: str
    protocol_version: Literal["1.0"]
    network_epoch: str = Field(pattern=r"^SN1-\d{4}-\d{2}-\d{2}$")
    message_id_prefix: Literal["SN1"]
    implementation_state: Literal["active"]
    storage: StorageConfig
    nodes: list[NetworkNodeConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_nodes(self) -> SonantiaNetworkConfig:
        node_ids = [node.node_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("La topología contiene node_id duplicados")
        return self


class CatalogValue(StrictModel):
    value_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    enabled: bool = True
    weight: int = Field(default=1, ge=1)
    text: str = Field(min_length=1, max_length=2_000)
    requires: list[str] = Field(default_factory=list)


class PhraseCollection(StrictModel):
    collection_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    enabled: bool = True
    path: str = Field(pattern=r"^phrases/[a-z0-9_]+\.txt$")


class SourceTemplates(StrictModel):
    source_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    enabled: bool = True
    templates: list[CatalogValue] = Field(min_length=1, max_length=100)


class MessagePolicy(StrictModel):
    source_order: list[str] = Field(min_length=1, max_length=16)
    max_source_sections: int = Field(ge=1, le=16)
    weather_fact_count: Literal[2]
    max_message_characters: int = Field(ge=100, le=10_000)


class MessageCatalog(StrictModel):
    catalog_version: str = Field(min_length=1)
    generator_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    default_language: Literal["es"]
    selection_policy: Literal["deterministic_factorized"]
    recipient_names: list[str] = Field(default_factory=list, max_length=32)
    policy: MessagePolicy
    openings: list[CatalogValue] = Field(min_length=1, max_length=100)
    declarations: list[CatalogValue] = Field(min_length=1, max_length=100)
    fallback_messages: list[CatalogValue] = Field(min_length=1, max_length=100)
    source_templates: list[SourceTemplates] = Field(min_length=1, max_length=16)
    phrase_collections: list[PhraseCollection] = Field(min_length=1, max_length=32)

    @field_validator("recipient_names")
    @classmethod
    def validate_recipient_names(cls, names: list[str]) -> list[str]:
        normalized = [name.strip() for name in names]
        if any(not name or len(name) > 120 for name in normalized):
            raise ValueError("Cada destinatario debe tener entre 1 y 120 caracteres")
        if any(any(ord(character) < 32 for character in name) for name in normalized):
            raise ValueError("Los destinatarios no pueden contener caracteres de control")
        if len({name.casefold() for name in normalized}) != len(normalized):
            raise ValueError("La lista de destinatarios contiene nombres duplicados")
        return normalized

    @model_validator(mode="after")
    def validate_catalog(self) -> MessageCatalog:
        sections = [self.openings, self.declarations, self.fallback_messages]
        if any(not any(value.enabled for value in values) for values in sections):
            raise ValueError("Cada sección del catálogo requiere un valor habilitado")
        if not any(collection.enabled for collection in self.phrase_collections):
            raise ValueError("El catálogo requiere una colección de frases habilitada")
        if not any(source.enabled for source in self.source_templates):
            raise ValueError("El catálogo requiere una fuente habilitada")
        value_ids = [
            value.value_id
            for values in sections
            for value in values
        ]
        value_ids.extend(
            value.value_id
            for source in self.source_templates
            for value in source.templates
        )
        if len(value_ids) != len(set(value_ids)):
            raise ValueError("El catálogo contiene value_id duplicados")
        source_ids = [source.source_id for source in self.source_templates]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("El catálogo contiene source_id duplicados")
        collection_ids = [
            collection.collection_id for collection in self.phrase_collections
        ]
        if len(collection_ids) != len(set(collection_ids)):
            raise ValueError("El catálogo contiene collection_id duplicados")
        if len(self.policy.source_order) != len(set(self.policy.source_order)):
            raise ValueError("source_order contiene fuentes duplicadas")
        return self


class WeatherLocation(FlexibleModel):
    latitude: float | None = None
    longitude: float | None = None


class WeatherData(FlexibleModel):
    temperature_c: float | None = None
    relative_humidity_percent: float | None = None
    precipitation_mm: float | None = None
    pressure_hpa: float | None = None
    wind_speed_kmh: float | None = None
    solar_radiation_wm2: float | None = None
    condition_code: int | str | None = None


class Weather(FlexibleModel):
    status: str = "unavailable"
    provider: str = "unknown"
    requested_at: str = ""
    observed_at: str | None = None
    condition_provider: str = "unknown"
    condition_observed_at: str | None = None
    measurement_source_count: int = 0
    measurement_source_codes: list[str] = Field(default_factory=list)
    location: WeatherLocation = Field(default_factory=WeatherLocation)
    data: WeatherData = Field(default_factory=WeatherData)


class EconomyIndicator(FlexibleModel):
    label: str
    value: str
    group: str = "general"


class EconomySnapshot(FlexibleModel):
    status: str = "unavailable"
    provider: str
    provider_label: str
    region_label: str
    country_code: str = Field(pattern=r"^[A-Z]{2}$")
    generated_at: str
    observed_at: str | None = None
    source_url: str | None = None
    date: str | None = None
    indicators: list[EconomyIndicator] = Field(default_factory=list)
    inflation: list[EconomyIndicator] = Field(default_factory=list)
    error: str | None = None


class GeologyEvent(FlexibleModel):
    event_id: str
    occurred_at: str
    location: str
    magnitude: float | None = None
    depth_km: float | None = None
    latitude: float | None = None
    longitude: float | None = None


class GeologySnapshot(FlexibleModel):
    status: str = "unavailable"
    provider: str
    provider_label: str
    region_label: str
    country_code: str = Field(pattern=r"^[A-Z]{2}$")
    generated_at: str
    observed_at: str | None = None
    source_url: str | None = None
    window_hours: int = 24
    count: int = 0
    events: list[GeologyEvent] = Field(default_factory=list)
    error: str | None = None


class GeneratorSelectedValue(StrictModel):
    part_id: str
    value_id: str


class GeneratorMetadata(FlexibleModel):
    generator_id: str = "unknown"
    catalog_version: str = "unknown"
    catalog_hash: str = ""
    group_id: str = ""
    selection_policy: str = "unknown"
    selected_values: list[GeneratorSelectedValue] = Field(default_factory=list)


class OperatorMessage(StrictModel):
    operator_message_id: str
    title: str
    text: str
    created_at: str
    valid_from: str
    valid_until: str | None
    status: Literal["active", "inactive"]
