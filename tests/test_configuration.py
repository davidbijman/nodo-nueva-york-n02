import json
import shutil
from pathlib import Path

import pytest

from distributed_node.config import load_configuration, load_message_catalog

ROOT = Path(__file__).resolve().parents[1]


def test_configuration_and_catalog_are_structurally_valid(tmp_path: Path) -> None:
    node, network, catalog, operator_message = load_configuration(ROOT / "config")

    assert node.protocol_version == "1.0"
    assert node.node_id in {item.node_id for item in network.nodes}
    assert node.endpoints.status == "/sonantia-status.json"
    assert all(item.feed_url.startswith("https://") for item in network.nodes)
    assert catalog.phrases
    assert len({phrase.value_id for phrase in catalog.phrases}) == len(catalog.phrases)
    assert operator_message.status in {"active", "inactive"}
    assert not (ROOT / "config/peers.json").exists()

    raw_network = json.loads((ROOT / "config/sonantia-network.json").read_text(encoding="utf-8"))
    assert "cycle_integration" not in raw_network
    assert "preview_public_directory" not in raw_network["storage"]

    shutil.copytree(ROOT / "config", tmp_path / "config")
    path = tmp_path / "config/message-catalog.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["openings"][0]["requires"].append("unknown.value")
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="Rutas de contexto desconocidas"):
        load_message_catalog(tmp_path / "config")
