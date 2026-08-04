import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from distributed_node.sonantia_protocol import (
    attach_content_hash,
    build_sonantia_message,
)
from distributed_node.sonantia_storage import (
    SonantiaMessageConflictError,
    SonantiaSequenceError,
    SonantiaStorage,
    SonantiaStorageSettings,
)


def _schema_registry(root: Path) -> tuple[Registry, dict[str, dict]]:
    registry = Registry()
    schemas: dict[str, dict] = {}
    for path in sorted((root / "schemas").glob("sonantia-*.schema.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        schemas[path.name] = schema
        registry = registry.with_resource(
            schema["$id"],
            Resource.from_contents(schema),
        )
    return registry, schemas


def _validate(
    value: dict,
    schema_name: str,
    *,
    schemas: dict[str, dict],
    registry: Registry,
) -> None:
    Draft202012Validator(
        schemas[schema_name],
        registry=registry,
    ).validate(value)


def test_sonantia_storage_keeps_daily_own_messages_and_bounded_relays(
    tmp_path: Path,
) -> None:
    settings = SonantiaStorageSettings(
        own_feed_limit=2,
        relay_retention_hours=72,
        relay_limit_per_origin=2,
        interaction_limit=2,
    )
    storage = SonantiaStorage(
        tmp_path,
        node_id="N01",
        settings=settings,
    )
    first = datetime(2026, 8, 2, 6, 0, tzinfo=UTC)
    second = datetime(2026, 8, 2, 8, 0, tzinfo=UTC)
    third = datetime(2026, 8, 3, 0, 0, tzinfo=UTC)

    own_messages = [
        build_sonantia_message(
            node_id="N01",
            moment=moment,
            sequence=sequence,
            text=f"Mensaje propio {sequence}",
        )
        for sequence, moment in enumerate((first, second, third), start=1)
    ]
    assert storage.next_own_sequence() == 1
    for message in own_messages:
        assert storage.append_own_message(message, stored_at=third) == "stored"
    assert storage.append_own_message(own_messages[-1], stored_at=third) == "duplicate"
    assert storage.next_own_sequence() == 4
    assert storage.load_recent_own_messages(limit=0) == []

    core_path = tmp_path / "data" / "sonantia" / "core.json"
    interrupted_core = json.loads(core_path.read_text(encoding="utf-8"))
    interrupted_core["last_own_sequence"] = 2
    interrupted_core["last_message_id"] = own_messages[1]["message_id"]
    core_path.write_text(
        json.dumps(interrupted_core, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    assert storage.append_own_message(own_messages[-1], stored_at=third) == "duplicate"
    assert storage.next_own_sequence() == 4

    day_one = json.loads(
        (
            tmp_path
            / "data"
            / "sonantia"
            / "own"
            / "N01"
            / "2026"
            / "08"
            / "02.json"
        ).read_text(encoding="utf-8")
    )
    day_two = json.loads(
        (
            tmp_path
            / "data"
            / "sonantia"
            / "own"
            / "N01"
            / "2026"
            / "08"
            / "03.json"
        ).read_text(encoding="utf-8")
    )
    assert [item["sequence"] for item in day_one["messages"]] == [1, 2]
    assert [item["sequence"] for item in day_two["messages"]] == [3]

    feed = storage.build_feed(generated_at=third)
    assert [item["sequence"] for item in feed["messages"]] == [3, 2]
    assert {item["origin_node_id"] for item in feed["messages"]} == {"N01"}

    skipped = build_sonantia_message(
        node_id="N01",
        moment=third,
        sequence=5,
        text="Secuencia con hueco",
    )
    with pytest.raises(SonantiaSequenceError):
        storage.append_own_message(skipped, stored_at=third)

    relay_messages = [
        build_sonantia_message(
            node_id="N02",
            moment=datetime(2026, 8, 2, hour, 0, tzinfo=UTC),
            sequence=sequence,
            text=f"Mensaje remoto {sequence}",
        )
        for sequence, hour in ((5, 7), (7, 9), (8, 10))
    ]
    for message in relay_messages:
        assert storage.upsert_relay_message(message, received_at=third) == "stored"
    assert storage.upsert_relay_message(
        relay_messages[-1],
        received_at=third,
    ) == "duplicate"

    relay = storage.load_relay("N02", moment=third)
    assert [item["sequence"] for item in relay["messages"]] == [8, 7]
    assert all(item["origin_node_id"] == "N02" for item in relay["messages"])

    conflicting = dict(relay_messages[-1])
    conflicting["text"] = "Contenido alterado"
    conflicting = attach_content_hash(conflicting)
    with pytest.raises(SonantiaMessageConflictError):
        storage.upsert_relay_message(conflicting, received_at=third)

    event = storage.append_interaction(
        event_type="message_received",
        occurred_at=third,
        result="success",
        message_id=relay_messages[-1]["message_id"],
        peer_node_id="N02",
        details={"source": "relay"},
    )
    assert event["message_id"] == relay_messages[-1]["message_id"]
    with pytest.raises(ValueError, match="payloads"):
        storage.append_interaction(
            event_type="message_received",
            occurred_at=third,
            result="success",
            details={"text": "No debe duplicarse"},
        )

    inventory = storage.build_inventory(generated_at=third)
    assert inventory["origins"]["N01"]["available_through_sequence"] == 3
    assert inventory["origins"]["N02"] == {
        "role": "relay",
        "available_from_sequence": 7,
        "available_through_sequence": 8,
        "gaps": [],
        "archive_through": None,
    }

    preview_paths = storage.publish_surface(generated_at=third)
    assert tmp_path / "public" / "feed.json" in preview_paths
    preview_archive = (
        tmp_path
        / "public"
        / "archive"
        / "N01"
        / "2026"
        / "08"
        / "03.json"
    )
    assert preview_archive.exists()
    archive_mtime = preview_archive.stat().st_mtime_ns
    storage.publish_surface(generated_at=third)
    assert preview_archive.stat().st_mtime_ns == archive_mtime
    assert not list(tmp_path.rglob("*.tmp"))

    root = Path(__file__).resolve().parents[1]
    registry, schemas = _schema_registry(root)
    _validate(
        storage.load_core(),
        "sonantia-core.schema.json",
        schemas=schemas,
        registry=registry,
    )
    _validate(
        day_one,
        "sonantia-daily-archive.schema.json",
        schemas=schemas,
        registry=registry,
    )
    _validate(
        feed,
        "sonantia-feed.schema.json",
        schemas=schemas,
        registry=registry,
    )
    _validate(
        relay,
        "sonantia-relay.schema.json",
        schemas=schemas,
        registry=registry,
    )
    _validate(
        inventory,
        "sonantia-inventory.schema.json",
        schemas=schemas,
        registry=registry,
    )
    _validate(
        json.loads(
            (
                tmp_path
                / "data"
                / "sonantia"
                / "interactions"
                / "current.json"
            ).read_text(encoding="utf-8")
        ),
        "sonantia-interactions.schema.json",
        schemas=schemas,
        registry=registry,
    )

    archive_index = json.loads(
        (
            tmp_path
            / "data"
            / "sonantia"
            / "own"
            / "N01"
            / "index.json"
        ).read_text(encoding="utf-8")
    )
    monthly_index = json.loads(
        (
            tmp_path
            / "data"
            / "sonantia"
            / "own"
            / "N01"
            / "2026"
            / "08"
            / "index.json"
        ).read_text(encoding="utf-8")
    )
    _validate(
        archive_index,
        "sonantia-archive-index.schema.json",
        schemas=schemas,
        registry=registry,
    )
    _validate(
        monthly_index,
        "sonantia-archive-index.schema.json",
        schemas=schemas,
        registry=registry,
    )
