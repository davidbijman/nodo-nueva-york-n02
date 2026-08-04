"""Validación de configuraciones y recursos públicos contra JSON Schema."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from .config import load_configuration, load_node_configuration
from .context_providers import validate_context_provider_configuration
from .sonantia_activation import build_sonantia_storage, load_sonantia_configuration


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def schema_registry(schema_dir: Path) -> Registry:
    resources: list[tuple[str, Resource[Any]]] = []
    for path in schema_dir.glob("*.schema.json"):
        contents = load_json(path)
        resource = Resource.from_contents(contents)
        resources.append((contents["$id"], resource))
        resources.append((path.name, resource))
    return Registry().with_resources(resources)


def validate_with_schema(document_path: Path, schema_path: Path, schema_dir: Path) -> None:
    schema = load_json(schema_path)
    validator = Draft202012Validator(
        schema,
        registry=schema_registry(schema_dir),
        format_checker=FormatChecker(),
    )
    validator.validate(load_json(document_path))


def validate_config(root: Path) -> list[Path]:
    node, _, _, _ = load_configuration(root / "config")
    validate_context_provider_configuration(node)
    node_path = root / "config/node.json"
    catalog_path = root / "config/message-catalog.json"
    sonantia_path = root / "config/sonantia-network.json"
    validate_with_schema(
        node_path,
        root / "schemas/node.schema.json",
        root / "schemas",
    )
    validate_with_schema(
        catalog_path,
        root / "schemas/message-catalog.schema.json",
        root / "schemas",
    )
    validate_with_schema(
        sonantia_path,
        root / "schemas/sonantia-network.schema.json",
        root / "schemas",
    )
    return [node_path, catalog_path, sonantia_path]


def validate_public(root: Path) -> list[Path]:
    """Valida exclusivamente la superficie pública Sonantia 1.0."""
    public_dir = root / "public"
    schema_dir = root / "schemas"
    targets = [
        (public_dir / "node.json", schema_dir / "node.schema.json"),
        (public_dir / "network.json", schema_dir / "sonantia-network-public.schema.json"),
        (public_dir / "sonantia-status.json", schema_dir / "sonantia-status.schema.json"),
        (public_dir / "feed.json", schema_dir / "sonantia-feed.schema.json"),
        (public_dir / "inventory.json", schema_dir / "sonantia-inventory.schema.json"),
        (public_dir / "archive/index.json", schema_dir / "sonantia-archive-index.schema.json"),
        (
            public_dir / "interactions/current.json",
            schema_dir / "sonantia-interactions.schema.json",
        ),
    ]
    targets.extend(
        (path, schema_dir / "sonantia-relay.schema.json")
        for path in public_dir.glob("relay/N[0-9][0-9].json")
    )
    targets.extend(
        (path, schema_dir / "sonantia-archive-index.schema.json")
        for path in public_dir.glob("archive/N[0-9][0-9]/**/index.json")
    )
    daily_pattern = (
        "archive/N[0-9][0-9]/[0-9][0-9][0-9][0-9]/"
        "[0-9][0-9]/[0-3][0-9].json"
    )
    targets.extend(
        (path, schema_dir / "sonantia-daily-archive.schema.json")
        for path in public_dir.glob(daily_pattern)
    )
    validated: list[Path] = []
    for document_path, schema_path in targets:
        if not document_path.exists():
            raise FileNotFoundError(f"Falta el recurso público requerido: {document_path}")
        validate_with_schema(document_path, schema_path, schema_dir)
        validated.append(document_path)

    stale_resources = [
        public_dir / "status.json",
        public_dir / "month.html",
        public_dir / "interactions.html",
        public_dir / "archive" / "index.html",
    ]
    existing = [path for path in stale_resources if path.exists()]
    if existing:
        raise ValueError(
            "Persisten recursos operativos retirados: "
            + ", ".join(str(path.relative_to(root)) for path in existing)
        )
    return validated


def validate_message_flow(
    root: Path,
    *,
    expected_message_id: str | None = None,
    require_message: bool = True,
    max_age_minutes: int | None = None,
    moment: datetime | None = None,
) -> dict[str, Any]:
    """Comprueba que estado, archivo y feed Sonantia apunten al mismo mensaje."""
    node = load_node_configuration(root / "config")
    configuration = load_sonantia_configuration(root)
    storage = build_sonantia_storage(root, configuration, node)
    core = storage.load_core()
    feed = load_json(root / "public/feed.json")
    status = load_json(root / "public/sonantia-status.json")
    archive = load_json(root / "public/archive/index.json")

    messages = feed.get("messages") or []
    if require_message and not messages:
        raise ValueError("El feed Sonantia no contiene mensajes propios")
    if feed.get("node_id") != node.node_id:
        raise ValueError("feed.json no pertenece al nodo local")
    if status.get("node_id") != node.node_id:
        raise ValueError("sonantia-status.json no pertenece al nodo local")

    last_sequence = int(core.get("last_own_sequence", 0))
    last_message_id = core.get("last_message_id")
    if messages:
        newest = messages[0]
        if newest.get("message_id") != last_message_id:
            raise ValueError("El mensaje más reciente del feed no coincide con core.json")
        if int(newest.get("sequence", 0)) != last_sequence:
            raise ValueError("La secuencia más reciente del feed no coincide con core.json")
        if expected_message_id and newest.get("message_id") != expected_message_id:
            raise ValueError("El mensaje generado por el ciclo no llegó al feed público")
    elif last_sequence or last_message_id:
        raise ValueError("core.json informa mensajes que no aparecen en el feed")

    if int(status.get("archive_message_count", 0)) != last_sequence:
        raise ValueError("sonantia-status.json no refleja la última secuencia propia")
    if int(archive.get("message_count", 0)) != last_sequence:
        raise ValueError("archive/index.json no refleja la última secuencia propia")
    if last_sequence and int(archive.get("last_sequence", 0)) != last_sequence:
        raise ValueError("archive/index.json no apunta a la última secuencia propia")

    age_minutes: float | None = None
    if messages:
        latest_created_at = str(messages[0].get("created_at") or "")
        try:
            parsed_latest = datetime.fromisoformat(
                latest_created_at.removesuffix("Z") + "+00:00"
            ).astimezone(UTC)
        except ValueError as exc:
            raise ValueError("El último mensaje no tiene una fecha UTC válida") from exc
        reference_moment = (moment or datetime.now(UTC)).astimezone(UTC)
        age_minutes = max(
            0.0,
            (reference_moment - parsed_latest).total_seconds() / 60.0,
        )
        if max_age_minutes is not None:
            if max_age_minutes < 1:
                raise ValueError("max_age_minutes debe ser mayor que cero")
            if age_minutes > max_age_minutes:
                raise ValueError(
                    "El flujo Sonantia está atrasado: "
                    f"{age_minutes:.1f} min sin un mensaje nuevo"
                )

    return {
        "node_id": node.node_id,
        "message_count": len(messages),
        "last_sequence": last_sequence,
        "last_message_id": last_message_id,
        "age_minutes": age_minutes,
    }
