"""Publicación y reinicio controlado de Sonantia Network 1.0."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import load_network_configuration, load_node_configuration
from .maintenance import remove_deprecated_artifacts
from .models import NodeConfig, SonantiaNetworkConfig
from .sonantia_protocol import NETWORK_ID, PROTOCOL_VERSION, isoformat_utc
from .sonantia_storage import (
    SonantiaStorage,
    SonantiaStorageError,
    SonantiaStorageSettings,
    atomic_copy_file,
    atomic_write_json,
)


class SonantiaActivationError(RuntimeError):
    """La publicación o inicialización Sonantia no pudo completarse."""


def load_sonantia_configuration(root: Path) -> SonantiaNetworkConfig:
    try:
        return load_network_configuration(root / "config")
    except (OSError, TypeError, ValueError) as exc:
        raise SonantiaActivationError("No se pudo cargar config/sonantia-network.json") from exc


def load_local_node_configuration(root: Path) -> NodeConfig:
    try:
        return load_node_configuration(root / "config")
    except (OSError, TypeError, ValueError) as exc:
        raise SonantiaActivationError("No se pudo cargar config/node.json") from exc


def _network_nodes(
    configuration: SonantiaNetworkConfig,
    local_node: NodeConfig,
) -> list[dict[str, Any]]:
    nodes = [node.model_dump(mode="json") for node in configuration.nodes]
    local = next(
        (item for item in nodes if item["node_id"] == local_node.node_id),
        None,
    )
    if local is None:
        raise SonantiaActivationError(
            f"El nodo local {local_node.node_id} no está definido en la topología"
        )
    local.update(
        {
            "display_name": local_node.display_name,
            "platform": local_node.infrastructure.provider,
            "public_url": local_node.public_url,
            "feed_url": f"{local_node.public_url.rstrip('/')}/feed.json",
            "status_url": f"{local_node.public_url.rstrip('/')}/sonantia-status.json",
            "enabled": True,
        }
    )
    return nodes


def build_sonantia_storage(
    root: Path,
    configuration: SonantiaNetworkConfig,
    local_node: NodeConfig | None = None,
) -> SonantiaStorage:
    node = local_node or load_local_node_configuration(root)
    settings = SonantiaStorageSettings.from_mapping(configuration.storage.model_dump(mode="json"))
    known_node_ids = tuple(item.node_id for item in configuration.nodes)
    return SonantiaStorage(
        root,
        node_id=node.node_id,
        known_node_ids=known_node_ids,
        network_epoch=configuration.network_epoch,
        settings=settings,
        data_relative_path=configuration.storage.data_directory,
    )


def _empty_archive_index(
    configuration: SonantiaNetworkConfig,
    local_node: NodeConfig,
    *,
    generated_at: datetime,
) -> dict[str, Any]:
    return {
        "network_id": NETWORK_ID,
        "protocol_version": PROTOCOL_VERSION,
        "network_epoch": configuration.network_epoch,
        "document_type": "archive-index",
        "origin_node_id": local_node.node_id,
        "generated_at": isoformat_utc(generated_at),
        "message_count": 0,
        "first_sequence": None,
        "last_sequence": None,
        "months": [],
    }


def _network_document(
    configuration: SonantiaNetworkConfig,
    local_node: NodeConfig,
    *,
    generated_at: datetime,
) -> dict[str, Any]:
    return {
        "network_id": NETWORK_ID,
        "network_name": configuration.network_name,
        "protocol_name": configuration.protocol_name,
        "protocol_version": PROTOCOL_VERSION,
        "network_epoch": configuration.network_epoch,
        "document_type": "network",
        "generated_at": isoformat_utc(generated_at),
        "reference_node_id": local_node.node_id,
        "reference_node_name": local_node.display_name,
        "platform": local_node.infrastructure.provider,
        "implementation_state": configuration.implementation_state,
        "nodes": _network_nodes(configuration, local_node),
    }


def _status_document(
    configuration: SonantiaNetworkConfig,
    local_node: NodeConfig,
    storage: SonantiaStorage,
    *,
    generated_at: datetime,
    publication_file_count: int,
    result: str,
    components: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    feed = storage.build_feed(generated_at=generated_at)
    inventory = storage.build_inventory(generated_at=generated_at)
    return {
        "network_id": NETWORK_ID,
        "protocol_version": PROTOCOL_VERSION,
        "network_epoch": configuration.network_epoch,
        "document_type": "network-status",
        "node_id": local_node.node_id,
        "generated_at": isoformat_utc(generated_at),
        "implementation_state": configuration.implementation_state,
        "result": result,
        "feed_message_count": int(feed["message_count"]),
        "archive_message_count": int(storage.load_core()["last_own_sequence"]),
        "publication_file_count": publication_file_count,
        "origins": inventory["origins"],
        "components": list(components or []),
    }


def publish_active_sonantia_surface(
    root: Path,
    *,
    generated_at: datetime,
    cycle_result: str | None = None,
    pages: list[str] | None = None,
    components: list[dict[str, Any]] | None = None,
) -> list[Path]:
    """Materializa todas las vistas JSON públicas desde el estado Sonantia."""
    now = generated_at.astimezone(UTC)
    configuration = load_sonantia_configuration(root)
    local_node = load_local_node_configuration(root)

    try:
        storage = build_sonantia_storage(root, configuration, local_node)
        storage.initialize(moment=now)
        if cycle_result is not None:
            storage.append_interaction(
                event_type="site_generated",
                occurred_at=now,
                result="success",
                details={"pages": list(pages or [])},
            )
            storage.append_interaction(
                event_type="cycle_completed",
                occurred_at=now,
                result="degraded" if cycle_result == "degraded" else "success",
                details={"cycle_result": cycle_result},
            )

        target = root / configuration.storage.active_public_directory
        written = storage.publish_surface(generated_at=now, destination=target)

        source_index = target / "archive" / storage.node_id / "index.json"
        public_index = target / "archive" / "index.json"
        if source_index.exists():
            atomic_copy_file(source_index, public_index)
        else:
            atomic_write_json(
                public_index,
                _empty_archive_index(configuration, local_node, generated_at=now),
            )
        written.append(public_index)

        network_path = target / "network.json"
        atomic_write_json(
            network_path,
            _network_document(configuration, local_node, generated_at=now),
        )
        written.append(network_path)

        status_path = target / "sonantia-status.json"
        atomic_write_json(
            status_path,
            _status_document(
                configuration,
                local_node,
                storage,
                generated_at=now,
                publication_file_count=len(set(written)) + 1,
                result=cycle_result or "rendered",
                components=components,
            ),
        )
        written.append(status_path)
        return sorted(set(written))
    except (OSError, TypeError, ValueError, SonantiaStorageError) as exc:
        raise SonantiaActivationError(
            f"No se pudo publicar la superficie Sonantia: {type(exc).__name__}: {exc}"
        ) from exc


def initialize_sonantia_v1(
    root: Path,
    *,
    moment: datetime | None = None,
    force: bool = False,
) -> list[Path]:
    """Reinicia el estado Sonantia y publica una superficie 1.0 vacía."""
    now = (moment or datetime.now(UTC)).astimezone(UTC)
    configuration = load_sonantia_configuration(root)
    local_node = load_local_node_configuration(root)
    remove_deprecated_artifacts(
        root, public_directory=configuration.storage.active_public_directory
    )

    data_root = root / configuration.storage.data_directory
    if data_root.exists() and any(data_root.rglob("*.json")) and not force:
        raise SonantiaActivationError(
            "Ya existe estado Sonantia. Repita con --force para reiniciarlo"
        )

    active_root = root / configuration.storage.active_public_directory
    for path in (
        data_root,
        active_root / "relay",
        active_root / "archive" / local_node.node_id,
        active_root / "interactions",
    ):
        if path.exists():
            shutil.rmtree(path)
    for path in (
        active_root / "feed.json",
        active_root / "inventory.json",
        active_root / "network.json",
        active_root / "sonantia-status.json",
        active_root / "archive" / "index.json",
    ):
        path.unlink(missing_ok=True)

    storage = build_sonantia_storage(root, configuration, local_node)
    storage.initialize(moment=now)
    storage.append_interaction(
        event_type="network_initialized",
        occurred_at=now,
        result="success",
        details={"network_epoch": configuration.network_epoch},
    )
    return publish_active_sonantia_surface(root, generated_at=now)
