"""Orquestación nativa y tolerante a fallos de un ciclo Sonantia 1.0."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from json import JSONDecodeError
from pathlib import Path
from typing import Any

import httpx

from .config import load_core_configuration, load_message_catalog
from .context_providers import collect_node_context
from .maintenance import remove_deprecated_artifacts
from .rendering import render_public_site
from .sonantia_activation import (
    build_sonantia_storage,
    publish_active_sonantia_surface,
)
from .sonantia_cycle import SonantiaCycleError, generate_and_store_sonantia_message
from .sonantia_peers import PeerPollingResult, poll_sonantia_peers
from .sonantia_protocol import isoformat_utc
from .validation import validate_message_flow

PAGE_NAMES = ["index.html", "sonantia.html"]


def utc_now() -> datetime:
    return datetime.now(UTC)


def _provider_component(
    name: str,
    snapshot: dict[str, Any],
    *,
    required: bool = False,
) -> dict[str, Any]:
    status = str(snapshot.get("status") or "unavailable")
    return {
        "component": name,
        "status": status,
        "required": required,
        "provider": snapshot.get("provider"),
        "error": snapshot.get("error"),
    }


def _weather_component(weather: Any, expected_sources: int) -> dict[str, Any]:
    source_count = int(getattr(weather, "measurement_source_count", 0) or 0)
    if weather.status != "available":
        status = "unavailable"
    elif expected_sources and source_count < expected_sources:
        status = "partial"
    else:
        status = "available"
    return {
        "component": "weather",
        "status": status,
        "provider": weather.provider,
        "source_count": source_count,
        "expected_sources": expected_sources,
    }


def _cycle_result(components: list[dict[str, Any]], *, fallback: bool) -> str:
    if fallback:
        return "degraded"
    degraded_states = {"unavailable", "partial", "unreachable", "failed"}
    return (
        "degraded"
        if any(str(item.get("status")) in degraded_states for item in components)
        else "success"
    )


def _load_relays(storage: Any, network: Any, moment: datetime) -> dict[str, dict[str, Any]]:
    return {
        peer.node_id: storage.load_relay(peer.node_id, moment=moment)
        for peer in network.nodes
        if peer.node_id != storage.node_id
    }


def cycle_due_status(
    root: Path,
    *,
    moment: datetime | None = None,
    max_age_minutes: int = 90,
) -> dict[str, Any]:
    """Informa si el nodo necesita generar un mensaje para recuperar continuidad."""
    check_moment = (moment or utc_now()).astimezone(UTC)
    node, network, _ = load_core_configuration(root / "config")
    storage = build_sonantia_storage(root, network, node)
    storage.ensure_core_consistency(moment=check_moment)
    age_minutes = storage.own_message_age_minutes(moment=check_moment)
    return {
        "due": storage.is_own_message_due(
            moment=check_moment,
            max_age_minutes=max_age_minutes,
        ),
        "age_minutes": age_minutes,
        "max_age_minutes": max_age_minutes,
        "last_message_id": storage.load_core().get("last_message_id"),
    }


def run_cycle_if_due(
    root: Path,
    *,
    moment: datetime | None = None,
    max_age_minutes: int = 90,
    **cycle_options: Any,
) -> dict[str, Any]:
    """Genera un mensaje solo si el flujo está vacío o excede la edad permitida."""
    check_moment = (moment or utc_now()).astimezone(UTC)
    due = cycle_due_status(
        root,
        moment=check_moment,
        max_age_minutes=max_age_minutes,
    )
    if due["due"]:
        return {
            "action": "cycle",
            **run_cycle(root, moment=check_moment, **cycle_options),
        }

    pages = render_existing(root, moment=check_moment)
    return {
        "action": "render",
        "status": "rendered",
        "page_count": len(pages),
        "message_age_minutes": due["age_minutes"],
        "last_message_id": due["last_message_id"],
    }


def run_cycle(
    root: Path,
    *,
    moment: datetime | None = None,
    weather_client: httpx.Client | None = None,
    peer_client: httpx.Client | None = None,
    redmeteo_client: httpx.Client | None = None,
    fetch_redmeteo: bool = True,
    economy_client: httpx.Client | None = None,
    fetch_economy: bool = True,
    geology_client: httpx.Client | None = None,
    fetch_geology: bool = True,
    astronomy_client: httpx.Client | None = None,
    fetch_astronomy: bool = True,
) -> dict[str, Any]:
    """Ejecuta un ciclo completo de forma nativa."""
    started_clock = time.perf_counter()
    cycle_moment = (moment or utc_now()).astimezone(UTC)
    node, network, operator_message = load_core_configuration(root / "config")
    remove_deprecated_artifacts(root, public_directory=network.storage.active_public_directory)
    storage = build_sonantia_storage(root, network, node)
    storage.ensure_core_consistency(moment=cycle_moment)
    storage.append_interaction(
        event_type="cycle_started",
        occurred_at=cycle_moment,
        result="success",
        details={"node_id": node.node_id},
    )

    catalog_error: str | None = None
    try:
        catalog = load_message_catalog(root / "config")
    except (JSONDecodeError, OSError, TypeError, ValueError) as exc:
        catalog = None
        catalog_error = f"{type(exc).__name__}: {exc}"

    peers: PeerPollingResult = poll_sonantia_peers(
        network.nodes,
        storage,
        cycle_moment,
        local_node_id=node.node_id,
        client=peer_client,
    )

    storage.append_interaction(
        event_type="context_requested",
        occurred_at=cycle_moment,
        result="success",
    )
    context = collect_node_context(
        node,
        cycle_moment,
        weather_client=weather_client,
        redmeteo_client=redmeteo_client,
        economy_client=economy_client,
        geology_client=geology_client,
        astronomy_client=astronomy_client,
        fetch_weather_sources=fetch_redmeteo,
        fetch_economy=fetch_economy,
        fetch_geology=fetch_geology,
        fetch_astronomy=fetch_astronomy,
    )

    expected_weather_sources = len(context.weather_sources)
    components = [
        _weather_component(context.weather, expected_weather_sources),
        _provider_component("astronomy", context.astronomy),
        _provider_component("economy", context.economy),
        _provider_component("geology", context.geology),
        *peers.components(),
    ]
    storage.append_interaction(
        event_type="context_collected",
        occurred_at=cycle_moment,
        result=(
            "degraded"
            if any(item["status"] in {"unavailable", "partial"} for item in components[:4])
            else "success"
        ),
        details={
            "weather_provider": context.weather.provider,
            "weather_source_count": context.weather.measurement_source_count,
        },
    )

    try:
        message_result = generate_and_store_sonantia_message(
            root,
            node=node,
            network=network,
            catalog=catalog,
            weather=context.weather,
            moment=cycle_moment,
            astronomy_snapshot=context.astronomy,
            geology_snapshot=context.geology,
            economy_snapshot=context.economy,
        )
    except SonantiaCycleError as exc:
        storage.append_interaction(
            event_type="cycle_failed",
            occurred_at=cycle_moment,
            result="failed",
            details={"error": str(exc)},
        )
        raise RuntimeError(str(exc)) from exc

    if catalog_error:
        components.append(
            {
                "component": "message-generator",
                "status": "unavailable",
                "error": catalog_error,
            }
        )
    result = _cycle_result(components, fallback=message_result.fallback)

    feed = storage.build_feed(generated_at=cycle_moment)
    own_messages = storage.load_recent_own_messages(limit=max(500, network.storage.own_feed_limit))
    archive_index = storage.load_archive_index(moment=cycle_moment)
    interactions = storage.load_interactions(moment=cycle_moment)
    relays = _load_relays(storage, network, cycle_moment)
    duration_ms = max(0, round((time.perf_counter() - started_clock) * 1000))
    status_context = {
        "status": result,
        "result": result,
        "node_id": node.node_id,
        "generated_at": isoformat_utc(cycle_moment),
        "duration_ms": duration_ms,
        "peer_status": peers.statuses,
        "components": components,
    }

    render_public_site(
        node=node,
        network=network,
        operator_message=operator_message,
        weather=context.weather,
        current_weather=context.condition_weather,
        feed=feed,
        own_messages=own_messages,
        archive_index=archive_index,
        interactions=interactions,
        relays=relays,
        peer_status=peers.statuses,
        economy_snapshot=context.economy,
        weather_source_snapshots=context.weather_sources,
        geology_snapshot=context.geology,
        astronomy_snapshot=context.astronomy,
        status=status_context,
        public_dir=root / network.storage.active_public_directory,
        template_dir=root / "src/distributed_node/templates",
        moment=cycle_moment,
    )
    written = publish_active_sonantia_surface(
        root,
        generated_at=cycle_moment,
        cycle_result=result,
        pages=PAGE_NAMES,
        components=components,
    )
    validate_message_flow(
        root,
        expected_message_id=message_result.message_id,
        require_message=True,
    )
    return {
        "status": result,
        "message_id": message_result.message_id,
        "sequence": message_result.sequence,
        "feed_message_count": int(feed["message_count"]),
        "archive_total_messages": int(archive_index.get("message_count", 0)),
        "publication_file_count": len(written),
        "peer_imported": peers.imported,
    }


def render_existing(root: Path, *, moment: datetime | None = None) -> list[str]:
    """Regenera HTML y JSON desde el estado Sonantia sin crear mensajes."""
    render_moment = (moment or utc_now()).astimezone(UTC)
    node, network, operator_message = load_core_configuration(root / "config")
    remove_deprecated_artifacts(root, public_directory=network.storage.active_public_directory)
    storage = build_sonantia_storage(root, network, node)
    storage.ensure_core_consistency(moment=render_moment)
    context = collect_node_context(node, render_moment)
    feed = storage.build_feed(generated_at=render_moment)
    own_messages = storage.load_recent_own_messages(limit=max(500, network.storage.own_feed_limit))
    archive_index = storage.load_archive_index(moment=render_moment)
    interactions = storage.load_interactions(moment=render_moment)
    relays = _load_relays(storage, network, render_moment)
    status_context = {
        "status": "rendered",
        "result": "rendered",
        "node_id": node.node_id,
        "generated_at": isoformat_utc(render_moment),
        "peer_status": [],
        "components": [],
    }
    pages = render_public_site(
        node=node,
        network=network,
        operator_message=operator_message,
        weather=context.weather,
        current_weather=context.condition_weather,
        feed=feed,
        own_messages=own_messages,
        archive_index=archive_index,
        interactions=interactions,
        relays=relays,
        peer_status=[],
        economy_snapshot=context.economy,
        weather_source_snapshots=context.weather_sources,
        geology_snapshot=context.geology,
        astronomy_snapshot=context.astronomy,
        status=status_context,
        public_dir=root / network.storage.active_public_directory,
        template_dir=root / "src/distributed_node/templates",
        moment=render_moment,
    )
    publish_active_sonantia_surface(root, generated_at=render_moment, pages=pages)
    validate_message_flow(
        root,
        require_message=int(storage.load_core()["last_own_sequence"]) > 0,
    )
    return pages


def rebuild_archive(root: Path, *, moment: datetime | None = None) -> dict[str, Any]:
    """Recupera el núcleo y republica índices desde los archivos diarios 1.0."""
    archive_moment = (moment or utc_now()).astimezone(UTC)
    node, network, _ = load_core_configuration(root / "config")
    remove_deprecated_artifacts(root, public_directory=network.storage.active_public_directory)
    storage = build_sonantia_storage(root, network, node)
    storage.recover_core_from_archives(moment=archive_moment)
    publish_active_sonantia_surface(root, generated_at=archive_moment)
    return storage.load_archive_index(moment=archive_moment)
