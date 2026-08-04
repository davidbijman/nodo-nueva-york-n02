from datetime import UTC, datetime

import httpx

from distributed_node.models import NetworkNodeConfig
from distributed_node.sonantia_peers import poll_sonantia_peers
from distributed_node.sonantia_protocol import build_sonantia_message, isoformat_utc
from distributed_node.sonantia_storage import SonantiaStorage

MOMENT = datetime(2026, 8, 1, 16, 6, tzinfo=UTC)


def peer() -> NetworkNodeConfig:
    return NetworkNodeConfig(
        node_id="N02",
        display_name="Nodo remoto",
        platform="github",
        enabled=True,
        public_url="https://example.invalid/",
        feed_url="https://example.invalid/feed.json",
        status_url="https://example.invalid/sonantia-status.json",
    )


def feed(message: dict) -> dict:
    return {
        "network_id": "sonantia-network",
        "protocol_version": "1.0",
        "network_epoch": "SN1-2026-08-02",
        "document_type": "feed",
        "node_id": "N02",
        "generated_at": isoformat_utc(MOMENT),
        "feed_limit": 48,
        "message_count": 1,
        "oldest_message_at": message["created_at"],
        "newest_message_at": message["created_at"],
        "messages": [message],
    }


def client_for(payload: dict) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    )


def test_native_peer_polling_imports_deduplicates_and_rejects_invalid_messages(tmp_path) -> None:
    storage = SonantiaStorage(tmp_path, node_id="N01")
    message = build_sonantia_message(
        node_id="N02",
        moment=MOMENT,
        sequence=1,
        text="Mensaje remoto Sonantia.",
    )

    first = poll_sonantia_peers(
        [peer()], storage, MOMENT, local_node_id="N01", client=client_for(feed(message))
    )
    duplicate = poll_sonantia_peers(
        [peer()], storage, MOMENT, local_node_id="N01", client=client_for(feed(message))
    )
    altered = dict(message)
    altered["text"] = "Mensaje alterado sin recalcular hash."
    rejected = poll_sonantia_peers(
        [peer()], storage, MOMENT, local_node_id="N01", client=client_for(feed(altered))
    )

    assert first.imported == 1
    assert duplicate.duplicates == 1
    assert rejected.rejected == 1
    relay = storage.load_relay("N02", moment=MOMENT)
    assert relay["message_count"] == 1
    assert relay["messages"][0] == message
