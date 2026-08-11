"""Generación y persistencia directa de mensajes Sonantia Network 1.0."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .message_catalog import CompiledMessageCatalog
from .messages import GeneratedText, generate_sonantia_text
from .models import NodeConfig, SonantiaNetworkConfig, Weather
from .sonantia_activation import build_sonantia_storage
from .sonantia_protocol import build_sonantia_message
from .sonantia_storage import SonantiaStorageError


class SonantiaCycleError(RuntimeError):
    """El ciclo no pudo construir o persistir un mensaje canónico."""


@dataclass(frozen=True, slots=True)
class SonantiaCycleResult:
    status: str
    message_id: str
    sequence: int
    stored: str
    fallback: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "message_id": self.message_id,
            "sequence": self.sequence,
            "stored": self.stored,
            "fallback": self.fallback,
        }


def _compact_mapping(
    source: dict[str, Any] | None,
    fields: tuple[str, ...],
) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    return {field: deepcopy(source[field]) for field in fields if field in source}


def _compact_weather(weather: Weather) -> dict[str, Any]:
    document = weather.model_dump(mode="json")
    return _compact_mapping(
        document,
        (
            "status",
            "provider",
            "requested_at",
            "observed_at",
            "condition_provider",
            "condition_observed_at",
            "measurement_source_count",
            "measurement_source_codes",
            "location",
            "data",
        ),
    )


def _compact_astronomy(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(snapshot, dict):
        return None
    targets = snapshot.get("targets")
    target_list = targets if isinstance(targets, list) else []
    available = [
        target
        for target in target_list
        if isinstance(target, dict) and target.get("status") == "available"
    ]
    reference_target = None
    if available:
        reference_target = _compact_mapping(
            available[0],
            ("name", "kind", "icon", "azimuth_deg", "elevation_deg", "visibility"),
        )
    return {
        **_compact_mapping(
            snapshot,
            ("status", "provider", "provider_label", "generated_at", "observed_at", "observer"),
        ),
        "target_count": len(target_list),
        "available_target_count": len(available),
        "reference_target": reference_target,
    }


def _compact_geology(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(snapshot, dict):
        return None
    events = snapshot.get("events")
    event_list = events if isinstance(events, list) else []
    recent_event = None
    if event_list and isinstance(event_list[0], dict):
        recent_event = _compact_mapping(
            event_list[0],
            (
                "event_id",
                "occurred_at",
                "local_time",
                "location",
                "magnitude",
                "magnitude_text",
                "depth_km",
                "depth",
                "latitude",
                "longitude",
                "url",
            ),
        )
    return {
        **_compact_mapping(
            snapshot,
            (
                "status",
                "provider",
                "provider_label",
                "region_label",
                "country_code",
                "generated_at",
                "observed_at",
                "window_hours",
                "search_stage",
                "count",
                "source_url",
            ),
        ),
        "recent_event": recent_event,
    }


def _compact_economy(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(snapshot, dict):
        return None
    return _compact_mapping(
        snapshot,
        (
            "status",
            "provider",
            "provider_label",
            "region_label",
            "country_code",
            "generated_at",
            "observed_at",
            "date",
            "indicators",
            "inflation",
            "source_url",
        ),
    )


def build_sonantia_context(
    weather: Weather,
    *,
    node: NodeConfig,
    astronomy_snapshot: dict[str, Any] | None,
    geology_snapshot: dict[str, Any] | None,
    economy_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    """Construye el contexto factual canónico sin payloads externos completos."""
    return {
        "location": {
            "zone": node.logical_location.zone,
            "country": node.logical_location.country,
            "country_code": node.logical_location.country_code,
            "city": node.logical_location.city,
            "timezone": node.logical_location.timezone,
            "latitude": node.logical_location.latitude,
            "longitude": node.logical_location.longitude,
            "elevation_m": node.logical_location.elevation_m,
        },
        "weather": _compact_weather(weather),
        "astronomy": _compact_astronomy(astronomy_snapshot),
        "geology": _compact_geology(geology_snapshot),
        "economy": _compact_economy(economy_snapshot),
    }


def generate_and_store_sonantia_message(
    root: Path,
    *,
    node: NodeConfig,
    network: SonantiaNetworkConfig,
    catalog: CompiledMessageCatalog | None,
    weather: Weather,
    moment: datetime,
    astronomy_snapshot: dict[str, Any] | None,
    geology_snapshot: dict[str, Any] | None,
    economy_snapshot: dict[str, Any] | None,
    trailing_reference: str | None = None,
) -> SonantiaCycleResult:
    """Genera, valida y archiva un mensaje 1.0 de forma nativa."""
    now = moment.astimezone(UTC)
    try:
        storage = build_sonantia_storage(root, network, node)
        storage.ensure_core_consistency(moment=now)
        sequence = storage.next_own_sequence(moment=now)
        generator_state = storage.load_generator_state()
        generated: GeneratedText = generate_sonantia_text(
            node,
            catalog,
            weather,
            now,
            sequence,
            phrase_cursor=generator_state.get("phrase_cursor"),
            astronomy_snapshot=astronomy_snapshot,
            geology_snapshot=geology_snapshot,
            economy_snapshot=economy_snapshot,
            trailing_reference=trailing_reference,
        )
        message = build_sonantia_message(
            node_id=node.node_id,
            moment=now,
            sequence=sequence,
            text=generated.text,
            context=build_sonantia_context(
                weather,
                node=node,
                astronomy_snapshot=astronomy_snapshot,
                geology_snapshot=geology_snapshot,
                economy_snapshot=economy_snapshot,
            ),
            generator=generated.generator,
            language=generated.language,
            network_epoch=network.network_epoch,
        )
        stored = storage.append_own_message(message, stored_at=now)
        if generated.next_phrase_cursor is not None:
            storage.save_generator_state(
                {"phrase_cursor": generated.next_phrase_cursor},
                moment=now,
            )
        storage.append_interaction(
            event_type="message_generated",
            occurred_at=now,
            result="success" if stored == "stored" else "duplicate",
            message_id=str(message["message_id"]),
            details={
                "generator_id": generated.generator.get("generator_id"),
                "fallback": generated.fallback,
            },
        )
        return SonantiaCycleResult(
            status="success",
            message_id=str(message["message_id"]),
            sequence=sequence,
            stored=stored,
            fallback=generated.fallback,
        )
    except (KeyError, OSError, TypeError, ValueError, SonantiaStorageError) as exc:
        raise SonantiaCycleError(
            f"No se pudo generar el mensaje Sonantia: {type(exc).__name__}: {exc}"
        ) from exc
