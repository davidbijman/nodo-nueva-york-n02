import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from distributed_node.sonantia_activation import (
    SonantiaActivationError,
    initialize_sonantia_v1,
    publish_active_sonantia_surface,
)
from distributed_node.sonantia_protocol import build_sonantia_message
from distributed_node.sonantia_storage import SonantiaStorage
from distributed_node.validation import validate_with_schema

ROOT = Path(__file__).resolve().parents[1]
MOMENT = datetime(2026, 8, 2, 8, 0, tzinfo=UTC)


def _project(tmp_path: Path) -> Path:
    for name in ("config", "schemas"):
        shutil.copytree(ROOT / name, tmp_path / name)
    (tmp_path / "public").mkdir()
    return tmp_path


def test_active_initialization_and_publication_replace_protocol_surface(tmp_path) -> None:
    root = _project(tmp_path)
    node = json.loads((root / "config/node.json").read_text(encoding="utf-8"))
    node_id = node["node_id"]
    written = initialize_sonantia_v1(root, moment=MOMENT)
    assert written
    empty_feed = json.loads((root / "public/feed.json").read_text(encoding="utf-8"))
    assert empty_feed["protocol_version"] == "1.0"
    assert empty_feed["message_count"] == 0

    storage = SonantiaStorage(
        root,
        node_id=node_id,
        network_epoch="SN1-2026-08-02",
    )
    message = build_sonantia_message(
        node_id=node_id,
        moment=MOMENT,
        sequence=1,
        text="Primer mensaje activo de Sonantia.",
        network_epoch="SN1-2026-08-02",
    )
    storage.append_own_message(message, stored_at=MOMENT)
    publish_active_sonantia_surface(
        root,
        generated_at=MOMENT,
        cycle_result="success",
        pages=["index.html"],
    )

    feed = json.loads((root / "public/feed.json").read_text(encoding="utf-8"))
    interactions = json.loads(
        (root / "public/interactions/current.json").read_text(encoding="utf-8")
    )
    assert feed["messages"][0]["message_id"].startswith(f"SN1-{node_id}-")
    assert {item["event_type"] for item in interactions["events"]} >= {
        "network_initialized",
        "site_generated",
        "cycle_completed",
    }
    assert (root / "public/network.json").is_file()
    assert (root / "public/sonantia-status.json").is_file()
    assert (root / "public/archive/index.json").is_file()

    schema_dir = root / "schemas"
    for document, schema in (
        ("public/feed.json", "sonantia-feed.schema.json"),
        ("public/inventory.json", "sonantia-inventory.schema.json"),
        ("public/archive/index.json", "sonantia-archive-index.schema.json"),
        ("public/interactions/current.json", "sonantia-interactions.schema.json"),
        ("public/network.json", "sonantia-network-public.schema.json"),
        ("public/sonantia-status.json", "sonantia-status.schema.json"),
    ):
        validate_with_schema(root / document, schema_dir / schema, schema_dir)

    with pytest.raises(SonantiaActivationError, match="Ya existe estado"):
        initialize_sonantia_v1(root, moment=MOMENT)


def test_local_identity_is_derived_only_from_node_json(tmp_path) -> None:
    root = _project(tmp_path)
    node_path = root / "config/node.json"
    node = json.loads(node_path.read_text(encoding="utf-8"))
    node.update(
        {
            "node_id": "N02",
            "display_name": "Nodo Nueva York",
            "public_url": "https://example.invalid/n02/",
        }
    )
    node["logical_location"].update(
        {
            "zone": "north-america",
            "country": "Estados Unidos",
            "country_code": "US",
            "city": "Nueva York",
            "timezone": "America/New_York",
            "latitude": 40.7128,
            "longitude": -74.0060,
            "elevation_m": 10,
        }
    )
    node["infrastructure"].update(
        {
            "provider": "github",
            "automation": "github-actions",
            "hosting": "github-pages",
            "region_description": "GitHub-managed infrastructure",
        }
    )
    node_path.write_text(json.dumps(node, ensure_ascii=False, indent=2), encoding="utf-8")

    initialize_sonantia_v1(root, moment=MOMENT)

    feed = json.loads((root / "public/feed.json").read_text(encoding="utf-8"))
    network = json.loads((root / "public/network.json").read_text(encoding="utf-8"))
    status = json.loads((root / "public/sonantia-status.json").read_text(encoding="utf-8"))

    assert feed["node_id"] == "N02"
    assert network["reference_node_id"] == "N02"
    assert network["reference_node_name"] == "Nodo Nueva York"
    assert network["platform"] == "github"
    assert status["node_id"] == "N02"
    assert (root / "data/sonantia/own/N02").is_dir()
