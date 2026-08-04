"""Consulta pull de pares compatibles con Sonantia Network 1.0."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from .models import NetworkNodeConfig
from .sonantia_protocol import NETWORK_ID, PROTOCOL_VERSION
from .sonantia_storage import (
    SonantiaMessageConflictError,
    SonantiaStorage,
    SonantiaStorageError,
)

MAX_FEED_BYTES = 2 * 1024 * 1024


@dataclass(slots=True)
class PeerPollingResult:
    imported: int = 0
    duplicates: int = 0
    rejected: int = 0
    statuses: list[dict[str, Any]] = field(default_factory=list)

    def components(self) -> list[dict[str, Any]]:
        return [
            {
                "component": item["node_id"],
                "status": item["status"],
                "error": item.get("error"),
            }
            for item in self.statuses
        ]


def _validate_feed_document(
    document: Any,
    *,
    expected_node_id: str,
    expected_epoch: str,
) -> list[dict[str, Any]]:
    if not isinstance(document, dict):
        raise ValueError("El feed debe ser un objeto JSON")
    if document.get("network_id") != NETWORK_ID:
        raise ValueError("network_id incompatible")
    if document.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("protocol_version incompatible")
    if document.get("network_epoch") != expected_epoch:
        raise ValueError("network_epoch incompatible")
    if document.get("document_type") != "feed":
        raise ValueError("document_type incompatible")
    if document.get("node_id") != expected_node_id:
        raise ValueError("node_id del feed no coincide con el par")
    messages = document.get("messages")
    if not isinstance(messages, list):
        raise ValueError("messages debe ser una lista")
    return [message for message in messages if isinstance(message, dict)]


def poll_sonantia_peers(
    nodes: list[NetworkNodeConfig],
    storage: SonantiaStorage,
    moment: datetime,
    *,
    local_node_id: str,
    client: httpx.Client | None = None,
    timeout_seconds: float = 10.0,
) -> PeerPollingResult:
    """Consulta feeds 1.0 habilitados e incorpora mensajes en relays por origen."""
    result = PeerPollingResult()
    owns_client = client is None
    active_client = client or httpx.Client(follow_redirects=True)
    try:
        for peer in nodes:
            if peer.node_id == local_node_id or not peer.enabled:
                continue
            status: dict[str, Any] = {
                "node_id": peer.node_id,
                "status": "unreachable",
                "checked_at": moment.isoformat(),
                "error": None,
            }
            try:
                response = active_client.get(peer.feed_url, timeout=timeout_seconds)
                response.raise_for_status()
                if len(response.content) > MAX_FEED_BYTES:
                    raise ValueError("feed excede 2 MB")
                messages = _validate_feed_document(
                    response.json(),
                    expected_node_id=peer.node_id,
                    expected_epoch=storage.network_epoch,
                )
                imported = duplicates = rejected = 0
                for message in messages:
                    try:
                        stored = storage.upsert_relay_message(
                            message,
                            received_at=moment,
                        )
                    except (SonantiaMessageConflictError, SonantiaStorageError, ValueError):
                        rejected += 1
                        storage.append_interaction(
                            event_type="message_rejected",
                            occurred_at=moment,
                            result="rejected",
                            peer_node_id=peer.node_id,
                            details={"reason": "invalid-or-conflicting-message"},
                        )
                        continue
                    if stored == "stored":
                        imported += 1
                    else:
                        duplicates += 1
                result.imported += imported
                result.duplicates += duplicates
                result.rejected += rejected
                status.update(
                    {
                        "status": "reachable",
                        "message_count": len(messages),
                        "imported": imported,
                        "duplicates": duplicates,
                        "rejected": rejected,
                    }
                )
                storage.append_interaction(
                    event_type="peer_checked",
                    occurred_at=moment,
                    result="success",
                    peer_node_id=peer.node_id,
                    details={
                        "message_count": len(messages),
                        "imported": imported,
                        "duplicates": duplicates,
                        "rejected": rejected,
                    },
                )
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                status["error"] = f"{type(exc).__name__}: {exc}"
                storage.append_interaction(
                    event_type="peer_unreachable",
                    occurred_at=moment,
                    result="degraded",
                    peer_node_id=peer.node_id,
                    details={"error": status["error"]},
                )
            result.statuses.append(status)
        storage.prune_relays(moment=moment)
        return result
    finally:
        if owns_client:
            active_client.close()
