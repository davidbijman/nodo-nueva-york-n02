import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from distributed_node.config import load_configuration
from distributed_node.sonantia_cycle import generate_and_store_sonantia_message
from tests.factories import ROOT, available_weather

MOMENT = datetime(2026, 8, 2, 6, 0, tzinfo=UTC)


def test_native_cycle_builds_canonical_message_without_intermediate_protocol(
    tmp_path: Path,
) -> None:
    shutil.copytree(ROOT / "config", tmp_path / "config")
    node, network, catalog, _ = load_configuration(tmp_path / "config")
    result = generate_and_store_sonantia_message(
        tmp_path,
        node=node,
        network=network,
        catalog=catalog,
        weather=available_weather(sources=2),
        moment=MOMENT,
        astronomy_snapshot={
            "status": "available",
            "provider": "nasa-jpl-horizons",
            "generated_at": "2026-08-02T06:00:00Z",
            "observer": {"name": node.logical_location.city},
            "targets": [
                {
                    "status": "available",
                    "name": "Sol",
                    "kind": "estrella",
                    "azimuth_deg": 80.0,
                    "elevation_deg": 12.0,
                    "visibility": "sobre el horizonte",
                }
            ],
        },
        geology_snapshot={
            "status": "available",
            "provider": "chile-csn",
            "provider_label": "CSN",
            "region_label": "Chile",
            "country_code": "CL",
            "generated_at": "2026-08-02T06:00:00Z",
            "events": [],
        },
        economy_snapshot={
            "status": "available",
            "provider": "chile-bcentral",
            "provider_label": "Banco Central de Chile",
            "region_label": "Chile",
            "country_code": "CL",
            "generated_at": "2026-08-02T06:00:00Z",
            "indicators": [],
            "inflation": [],
        },
    )

    archive_path = tmp_path / "data/sonantia/own" / node.node_id / "2026/08/02.json"
    message = json.loads(archive_path.read_text(encoding="utf-8"))["messages"][0]
    assert result.message_id.startswith(f"SN1-{node.node_id}-")
    assert message["protocol_version"] == "1.0"
    assert message["context"]["weather"]["measurement_source_count"] == 2
    assert message["context"]["astronomy"]["reference_target"]["name"] == "Sol"
    assert "source_message_id" not in message["generator"]
