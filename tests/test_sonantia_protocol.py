import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from distributed_node.sonantia_protocol import (
    NETWORK_EPOCH,
    attach_content_hash,
    build_sonantia_message,
    build_sonantia_message_id,
    calculate_content_hash,
    parse_sonantia_message_id,
    validate_sonantia_message,
)


def test_sonantia_v1_contract_is_readable_stable_and_self_consistent() -> None:
    moment = datetime(2026, 8, 2, 6, 0, tzinfo=UTC)
    message_id = build_sonantia_message_id("N01", moment, 1)
    assert message_id == "SN1-N01-2026-08-02T06-00-00Z-000001"

    identity = parse_sonantia_message_id(message_id)
    assert identity.node_id == "N01"
    assert identity.created_at == moment
    assert identity.sequence == 1

    message = build_sonantia_message(
        node_id="N01",
        moment=moment,
        sequence=1,
        text="Primer mensaje de Red Sonantia Network v1.0.",
        context={
            "weather": None,
            "astronomy": None,
            "geology": None,
            "economy": None,
        },
        generator={
            "generator_id": "sonantia-context-composer",
            "catalog_version": "1.0",
        },
    )
    validated = validate_sonantia_message(message)
    assert validated["network_epoch"] == NETWORK_EPOCH
    assert validated["content_hash"] == calculate_content_hash(validated)

    reordered = dict(reversed(list(message.items())))
    assert calculate_content_hash(reordered) == message["content_hash"]

    transported = dict(message)
    transported.update(
        {
            "received_at": "2026-08-02T06:20:00Z",
            "received_from_node_id": "N04",
            "hop_count": 2,
        }
    )
    assert calculate_content_hash(transported) == message["content_hash"]

    conflicting = dict(message)
    conflicting["origin_node_id"] = "N02"
    conflicting = attach_content_hash(conflicting)
    with pytest.raises(ValueError, match="origin_node_id"):
        validate_sonantia_message(conflicting)

    root = Path(__file__).resolve().parents[1]
    fixture = json.loads(
        (root / "tests" / "fixtures" / "sonantia-message-v1.json").read_text(encoding="utf-8")
    )
    schema = json.loads(
        (root / "schemas" / "sonantia-message.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(fixture)
    validate_sonantia_message(fixture)

    network_config = json.loads(
        (root / "config" / "sonantia-network.json").read_text(encoding="utf-8")
    )
    network_schema = json.loads(
        (root / "schemas" / "sonantia-network.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(network_schema)
    Draft202012Validator(network_schema).validate(network_config)

    for schema_path in sorted((root / "schemas").glob("sonantia-*.schema.json")):
        candidate = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(candidate)
