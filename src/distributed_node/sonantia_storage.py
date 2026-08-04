"""Persistencia incremental de Red Sonantia Network v1.0.

La implementación mantiene una única copia canónica permanente de cada mensaje
propio dentro de archivos diarios. ``feed.json``, los inventarios y los índices
son vistas derivadas. Los mensajes remotos se conservan únicamente en relays
acotados por origen.

Este módulo es la única persistencia operativa del nodo. Los feeds, índices,
relays e interacciones se derivan directamente del estado Sonantia.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .sonantia_protocol import (
    NETWORK_EPOCH,
    NETWORK_ID,
    PROTOCOL_VERSION,
    isoformat_utc,
    parse_sonantia_message_id,
    validate_sonantia_message,
)

NODE_ID_PATTERN = re.compile(r"^N\d{2}$")
PERIOD_PATTERN = re.compile(r"^\d{4}-\d{2}$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
INTERACTION_RESERVED_DETAIL_KEYS = {
    "message",
    "messages",
    "payload",
    "text",
    "context",
}


class SonantiaStorageError(RuntimeError):
    """Error base de persistencia Sonantia."""


class SonantiaMessageConflictError(SonantiaStorageError):
    """Un identificador o secuencia ya existe con otro contenido."""


class SonantiaSequenceError(SonantiaStorageError):
    """La secuencia propia no continúa el estado confirmado del nodo."""


@dataclass(frozen=True, slots=True)
class SonantiaStorageSettings:
    """Límites operativos del almacenamiento v1.0."""

    own_feed_limit: int = 48
    relay_retention_hours: int = 168
    relay_limit_per_origin: int = 168
    interaction_limit: int = 200

    def __post_init__(self) -> None:
        for field_name, value in (
            ("own_feed_limit", self.own_feed_limit),
            ("relay_retention_hours", self.relay_retention_hours),
            ("relay_limit_per_origin", self.relay_limit_per_origin),
            ("interaction_limit", self.interaction_limit),
        ):
            if value < 1:
                raise ValueError(f"{field_name} debe ser mayor que cero")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> SonantiaStorageSettings:
        return cls(
            own_feed_limit=int(value.get("own_feed_limit", 48)),
            relay_retention_hours=int(value.get("relay_retention_hours", 168)),
            relay_limit_per_origin=int(value.get("relay_limit_per_origin", 168)),
            interaction_limit=int(value.get("interaction_limit", 200)),
        )


def _parse_iso_utc(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("La fecha debe usar ISO 8601 UTC terminado en Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("Fecha ISO 8601 inválida") from exc
    return parsed.astimezone(UTC)


def _message_sort_key(message: Mapping[str, Any]) -> tuple[datetime, int, str]:
    return (
        _parse_iso_utc(str(message["created_at"])),
        int(message["sequence"]),
        str(message["message_id"]),
    )


def _read_json(path: Path, *, default: Any = None) -> Any:
    if not path.exists():
        return deepcopy(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SonantiaStorageError(f"No se pudo leer JSON válido: {path}") from exc


def atomic_write_json(path: Path, value: Mapping[str, Any] | Sequence[Any]) -> None:
    """Escribe JSON mediante reemplazo atómico dentro del mismo directorio."""
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        sort_keys=False,
    ) + "\n"
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(serialized)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        os.replace(temporary_name, path)
    except (OSError, TypeError, ValueError) as exc:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
        raise SonantiaStorageError(f"No se pudo escribir {path}") from exc


def atomic_copy_file(source: Path, destination: Path) -> None:
    """Copia un recurso mediante reemplazo atómico y evita reescrituras iguales."""
    try:
        payload = source.read_bytes()
        if destination.exists() and destination.read_bytes() == payload:
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_name: str | None = None
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        os.replace(temporary_name, destination)
    except OSError as exc:
        if "temporary_name" in locals() and temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
        raise SonantiaStorageError(
            f"No se pudo copiar {source} hacia {destination}"
        ) from exc


class SonantiaStorage:
    """Almacenamiento diario, feed propio, relay e inventario de un nodo."""

    def __init__(
        self,
        repository_root: Path,
        *,
        node_id: str,
        known_node_ids: Iterable[str] = ("N01", "N02", "N03", "N04"),
        network_epoch: str = NETWORK_EPOCH,
        settings: SonantiaStorageSettings | None = None,
        data_relative_path: str = "data/sonantia",
    ) -> None:
        if not NODE_ID_PATTERN.fullmatch(node_id):
            raise ValueError("node_id debe usar el formato N00")
        normalized_nodes = tuple(dict.fromkeys(known_node_ids))
        if node_id not in normalized_nodes:
            normalized_nodes = (node_id, *normalized_nodes)
        if any(not NODE_ID_PATTERN.fullmatch(item) for item in normalized_nodes):
            raise ValueError("Todos los nodos conocidos deben usar el formato N00")

        self.repository_root = Path(repository_root)
        self.node_id = node_id
        self.known_node_ids = normalized_nodes
        self.network_epoch = network_epoch
        self.settings = settings or SonantiaStorageSettings()
        self.data_root = self.repository_root / data_relative_path
        self.own_root = self.data_root / "own" / self.node_id
        self.relay_root = self.data_root / "relay"
        self.interactions_root = self.data_root / "interactions"
        self.core_path = self.data_root / "core.json"

    def initialize(self, *, moment: datetime | None = None) -> dict[str, Any]:
        """Crea el estado mínimo sin generar mensajes."""
        now = (moment or datetime.now(UTC)).astimezone(UTC)
        self.own_root.mkdir(parents=True, exist_ok=True)
        self.relay_root.mkdir(parents=True, exist_ok=True)
        self.interactions_root.mkdir(parents=True, exist_ok=True)
        if not self.core_path.exists():
            atomic_write_json(self.core_path, self._empty_core(now))
        return self.load_core()

    def _empty_core(self, moment: datetime) -> dict[str, Any]:
        return {
            "network_id": NETWORK_ID,
            "protocol_version": PROTOCOL_VERSION,
            "network_epoch": self.network_epoch,
            "document_type": "core-state",
            "node_id": self.node_id,
            "last_own_sequence": 0,
            "last_message_id": None,
            "last_event_sequence": 0,
            "generator_state": {},
            "updated_at": isoformat_utc(moment),
        }

    def load_core(self) -> dict[str, Any]:
        core = _read_json(self.core_path)
        if not isinstance(core, dict):
            raise SonantiaStorageError("core.json no contiene un objeto")
        required = {
            "network_id": NETWORK_ID,
            "protocol_version": PROTOCOL_VERSION,
            "network_epoch": self.network_epoch,
            "document_type": "core-state",
            "node_id": self.node_id,
        }
        for field, expected in required.items():
            if core.get(field) != expected:
                raise SonantiaStorageError(f"core.json tiene {field} incompatible")
        for field in ("last_own_sequence", "last_event_sequence"):
            value = core.get(field)
            if not isinstance(value, int) or value < 0:
                raise SonantiaStorageError(f"core.json tiene {field} inválido")
        generator_state = core.get("generator_state", {})
        if not isinstance(generator_state, dict):
            raise SonantiaStorageError("core.json tiene generator_state inválido")
        core.setdefault("generator_state", {})
        return core

    def ensure_core_consistency(self, *, moment: datetime) -> dict[str, Any]:
        """Recupera ``core.json`` si no coincide con el último archivo propio."""
        self.initialize(moment=moment)
        core = self.load_core()
        latest_messages = self.load_recent_own_messages(limit=1)
        latest = latest_messages[0] if latest_messages else None
        expected_sequence = int(latest["sequence"]) if latest else 0
        expected_message_id = latest["message_id"] if latest else None
        if (
            int(core.get("last_own_sequence", 0)) != expected_sequence
            or core.get("last_message_id") != expected_message_id
        ):
            return self.recover_core_from_archives(moment=moment)
        return core

    def load_generator_state(self) -> dict[str, Any]:
        """Devuelve el estado compacto del generador de texto."""
        return deepcopy(self.load_core().get("generator_state") or {})

    def save_generator_state(
        self,
        state: Mapping[str, Any],
        *,
        moment: datetime,
    ) -> None:
        """Actualiza el cursor del catálogo en el estado canónico del nodo."""
        core = self.load_core()
        core["generator_state"] = deepcopy(dict(state))
        core["updated_at"] = isoformat_utc(moment)
        atomic_write_json(self.core_path, core)

    def next_own_sequence(self, *, moment: datetime | None = None) -> int:
        now = (moment or datetime.now(UTC)).astimezone(UTC)
        core = self.ensure_core_consistency(moment=now)
        return int(core["last_own_sequence"]) + 1

    def _daily_archive_path(self, created_at: datetime) -> Path:
        return (
            self.own_root
            / f"{created_at.year:04d}"
            / f"{created_at.month:02d}"
            / f"{created_at.day:02d}.json"
        )

    def _empty_daily_archive(self, *, date_value: str) -> dict[str, Any]:
        return {
            "network_id": NETWORK_ID,
            "protocol_version": PROTOCOL_VERSION,
            "network_epoch": self.network_epoch,
            "document_type": "daily-archive",
            "origin_node_id": self.node_id,
            "date": date_value,
            "message_count": 0,
            "first_sequence": None,
            "last_sequence": None,
            "messages": [],
        }

    def append_own_message(
        self,
        message: Mapping[str, Any],
        *,
        stored_at: datetime | None = None,
    ) -> str:
        """Agrega un mensaje propio de forma idempotente al archivo diario.

        Devuelve ``"stored"`` o ``"duplicate"``. Una secuencia con huecos, un
        identificador reutilizado con otro hash o un mensaje de otro origen se
        rechazan explícitamente.
        """
        now = (stored_at or datetime.now(UTC)).astimezone(UTC)
        validated = validate_sonantia_message(
            message,
            expected_epoch=self.network_epoch,
        )
        if validated["origin_node_id"] != self.node_id:
            raise SonantiaStorageError(
                "Un mensaje remoto no puede archivarse como propio"
            )

        self.initialize(moment=now)
        created_at = _parse_iso_utc(validated["created_at"])
        archive_path = self._daily_archive_path(created_at)
        archive = _read_json(
            archive_path,
            default=self._empty_daily_archive(date_value=created_at.date().isoformat()),
        )
        messages = list(archive.get("messages") or [])

        for existing in messages:
            if existing.get("message_id") != validated["message_id"]:
                continue
            if existing.get("content_hash") == validated["content_hash"]:
                core = self.load_core()
                if int(validated["sequence"]) > int(core["last_own_sequence"]):
                    self.recover_core_from_archives(moment=now)
                return "duplicate"
            raise SonantiaMessageConflictError(
                "message_id propio ya existe con otro content_hash"
            )

        for existing in messages:
            if int(existing.get("sequence", -1)) == int(validated["sequence"]):
                raise SonantiaMessageConflictError(
                    "La secuencia propia ya pertenece a otro message_id"
                )

        core = self.load_core()
        expected_sequence = int(core["last_own_sequence"]) + 1
        if int(validated["sequence"]) != expected_sequence:
            raise SonantiaSequenceError(
                f"Se esperaba la secuencia {expected_sequence} y se recibió "
                f"{validated['sequence']}"
            )

        messages.append(validated)
        messages.sort(key=_message_sort_key)
        archive["messages"] = messages
        archive["message_count"] = len(messages)
        archive["first_sequence"] = int(messages[0]["sequence"])
        archive["last_sequence"] = int(messages[-1]["sequence"])
        atomic_write_json(archive_path, archive)

        core["last_own_sequence"] = int(validated["sequence"])
        core["last_message_id"] = validated["message_id"]
        core["updated_at"] = isoformat_utc(now)
        atomic_write_json(self.core_path, core)
        self._refresh_archive_indexes(created_at.year, created_at.month, now)
        return "stored"

    def _iter_daily_archive_paths(self) -> list[Path]:
        if not self.own_root.exists():
            return []
        result: list[Path] = []
        for path in self.own_root.glob("????/??/[0-3][0-9].json"):
            if path.name != "index.json":
                result.append(path)
        return sorted(result)

    def load_recent_own_messages(
        self,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        wanted = self.settings.own_feed_limit if limit is None else limit
        if wanted < 1:
            return []
        collected: list[dict[str, Any]] = []
        for path in reversed(self._iter_daily_archive_paths()):
            archive = _read_json(path, default={})
            messages = archive.get("messages") or []
            for message in reversed(messages):
                collected.append(validate_sonantia_message(
                    message,
                    expected_epoch=self.network_epoch,
                ))
                if len(collected) >= wanted:
                    return collected
        return collected

    def latest_own_message(self) -> dict[str, Any] | None:
        """Devuelve el mensaje propio más reciente, si existe."""
        messages = self.load_recent_own_messages(limit=1)
        return messages[0] if messages else None

    def own_message_age_minutes(self, *, moment: datetime) -> float | None:
        """Calcula la antigüedad del último mensaje propio respecto de ``moment``."""
        latest = self.latest_own_message()
        if latest is None:
            return None
        created_at = _parse_iso_utc(str(latest["created_at"]))
        delta = moment.astimezone(UTC) - created_at
        return max(0.0, delta.total_seconds() / 60.0)

    def is_own_message_due(self, *, moment: datetime, max_age_minutes: int) -> bool:
        """Indica si falta un mensaje o si el último superó la edad permitida."""
        if max_age_minutes < 1:
            raise ValueError("max_age_minutes debe ser mayor que cero")
        age = self.own_message_age_minutes(moment=moment)
        return age is None or age >= max_age_minutes

    def build_feed(self, *, generated_at: datetime) -> dict[str, Any]:
        messages = self.load_recent_own_messages(limit=self.settings.own_feed_limit)
        if any(message["origin_node_id"] != self.node_id for message in messages):
            raise SonantiaStorageError("El feed propio contiene un origen remoto")
        return {
            "network_id": NETWORK_ID,
            "protocol_version": PROTOCOL_VERSION,
            "network_epoch": self.network_epoch,
            "document_type": "feed",
            "node_id": self.node_id,
            "generated_at": isoformat_utc(generated_at),
            "feed_limit": self.settings.own_feed_limit,
            "message_count": len(messages),
            "messages": messages,
        }

    def _relay_path(self, origin_node_id: str) -> Path:
        if not NODE_ID_PATTERN.fullmatch(origin_node_id):
            raise ValueError("origin_node_id debe usar el formato N00")
        return self.relay_root / f"{origin_node_id}.json"

    def _empty_relay(self, origin_node_id: str, moment: datetime) -> dict[str, Any]:
        return {
            "network_id": NETWORK_ID,
            "protocol_version": PROTOCOL_VERSION,
            "network_epoch": self.network_epoch,
            "document_type": "relay",
            "relay_node_id": self.node_id,
            "origin_node_id": origin_node_id,
            "generated_at": isoformat_utc(moment),
            "retention_hours": self.settings.relay_retention_hours,
            "message_count": 0,
            "messages": [],
        }

    def _prune_relay_messages(
        self,
        messages: Iterable[Mapping[str, Any]],
        *,
        origin_node_id: str,
        moment: datetime,
    ) -> list[dict[str, Any]]:
        cutoff = moment.astimezone(UTC) - timedelta(
            hours=self.settings.relay_retention_hours
        )
        unique: dict[str, dict[str, Any]] = {}
        for candidate in messages:
            validated = validate_sonantia_message(
                candidate,
                expected_epoch=self.network_epoch,
            )
            if validated["origin_node_id"] != origin_node_id:
                raise SonantiaStorageError(
                    "Un relay mezcla mensajes de distintos orígenes"
                )
            if _parse_iso_utc(validated["created_at"]) < cutoff:
                continue
            existing = unique.get(validated["message_id"])
            if (
                existing is not None
                and existing["content_hash"] != validated["content_hash"]
            ):
                raise SonantiaMessageConflictError(
                    "Un relay contiene el mismo message_id con hashes distintos"
                )
            unique[validated["message_id"]] = validated
        ordered = sorted(unique.values(), key=_message_sort_key, reverse=True)
        return ordered[: self.settings.relay_limit_per_origin]

    def upsert_relay_message(
        self,
        message: Mapping[str, Any],
        *,
        received_at: datetime,
    ) -> str:
        """Inserta un mensaje remoto sin alterar su identidad canónica."""
        now = received_at.astimezone(UTC)
        validated = validate_sonantia_message(
            message,
            expected_epoch=self.network_epoch,
        )
        origin_node_id = str(validated["origin_node_id"])
        if origin_node_id == self.node_id:
            raise SonantiaStorageError("Un mensaje propio no debe guardarse en relay")

        self.initialize(moment=now)
        path = self._relay_path(origin_node_id)
        document = _read_json(
            path,
            default=self._empty_relay(origin_node_id, now),
        )
        current = list(document.get("messages") or [])
        for existing in current:
            if existing.get("message_id") != validated["message_id"]:
                continue
            if existing.get("content_hash") == validated["content_hash"]:
                pruned = self._prune_relay_messages(
                    current,
                    origin_node_id=origin_node_id,
                    moment=now,
                )
                document["generated_at"] = isoformat_utc(now)
                document["message_count"] = len(pruned)
                document["messages"] = pruned
                atomic_write_json(path, document)
                return "duplicate"
            raise SonantiaMessageConflictError(
                "message_id remoto ya existe con otro content_hash"
            )

        current.append(validated)
        pruned = self._prune_relay_messages(
            current,
            origin_node_id=origin_node_id,
            moment=now,
        )
        document.update(
            {
                "generated_at": isoformat_utc(now),
                "retention_hours": self.settings.relay_retention_hours,
                "message_count": len(pruned),
                "messages": pruned,
            }
        )
        atomic_write_json(path, document)
        return "stored"

    def load_relay(self, origin_node_id: str, *, moment: datetime) -> dict[str, Any]:
        now = moment.astimezone(UTC)
        path = self._relay_path(origin_node_id)
        document = _read_json(
            path,
            default=self._empty_relay(origin_node_id, now),
        )
        pruned = self._prune_relay_messages(
            document.get("messages") or [],
            origin_node_id=origin_node_id,
            moment=now,
        )
        document.update(
            {
                "generated_at": isoformat_utc(now),
                "retention_hours": self.settings.relay_retention_hours,
                "message_count": len(pruned),
                "messages": pruned,
            }
        )
        return document

    def prune_relays(self, *, moment: datetime) -> None:
        for path in sorted(self.relay_root.glob("N[0-9][0-9].json")):
            origin_node_id = path.stem
            atomic_write_json(path, self.load_relay(origin_node_id, moment=moment))

    @staticmethod
    def _sequence_window(
        messages: Sequence[Mapping[str, Any]],
    ) -> tuple[int | None, int | None, list[int]]:
        sequences = sorted({int(message["sequence"]) for message in messages})
        if not sequences:
            return None, None, []
        available_from = sequences[0]
        available_through = sequences[-1]
        known = set(sequences)
        gaps = [
            sequence
            for sequence in range(available_from, available_through + 1)
            if sequence not in known
        ]
        return available_from, available_through, gaps

    def _latest_closed_archive_date(self, *, moment: datetime) -> str | None:
        current_date = moment.astimezone(UTC).date()
        dates: list[str] = []
        for path in self._iter_daily_archive_paths():
            date_value = f"{path.parent.parent.name}-{path.parent.name}-{path.stem}"
            if (
                DATE_PATTERN.fullmatch(date_value)
                and datetime.fromisoformat(date_value).date() < current_date
            ):
                dates.append(date_value)
        return max(dates, default=None)

    def build_inventory(self, *, generated_at: datetime) -> dict[str, Any]:
        now = generated_at.astimezone(UTC)
        self.initialize(moment=now)
        core = self.load_core()
        last_own = int(core["last_own_sequence"])
        origins: dict[str, dict[str, Any]] = {}
        for origin_node_id in self.known_node_ids:
            if origin_node_id == self.node_id:
                origins[origin_node_id] = {
                    "role": "origin",
                    "available_from_sequence": 1 if last_own else None,
                    "available_through_sequence": last_own or None,
                    "gaps": [],
                    "archive_through": self._latest_closed_archive_date(moment=now),
                }
                continue
            relay = self.load_relay(origin_node_id, moment=now)
            available_from, available_through, gaps = self._sequence_window(
                relay["messages"]
            )
            origins[origin_node_id] = {
                "role": "relay",
                "available_from_sequence": available_from,
                "available_through_sequence": available_through,
                "gaps": gaps,
                "archive_through": None,
            }
        return {
            "network_id": NETWORK_ID,
            "protocol_version": PROTOCOL_VERSION,
            "network_epoch": self.network_epoch,
            "document_type": "inventory",
            "node_id": self.node_id,
            "generated_at": isoformat_utc(now),
            "origins": origins,
        }

    def load_interactions(self, *, moment: datetime) -> dict[str, Any]:
        """Carga la ventana actual de interacciones compactas."""
        return self._interaction_document(moment)

    def load_archive_index(self, *, moment: datetime) -> dict[str, Any]:
        """Carga el índice propio o entrega un documento vacío compatible."""
        self.initialize(moment=moment)
        return _read_json(
            self.own_root / "index.json",
            default={
                "network_id": NETWORK_ID,
                "protocol_version": PROTOCOL_VERSION,
                "network_epoch": self.network_epoch,
                "document_type": "archive-index",
                "origin_node_id": self.node_id,
                "generated_at": isoformat_utc(moment),
                "message_count": 0,
                "first_sequence": None,
                "last_sequence": None,
                "months": [],
            },
        )

    def _interaction_document(self, moment: datetime) -> dict[str, Any]:
        return _read_json(
            self.interactions_root / "current.json",
            default={
                "network_id": NETWORK_ID,
                "protocol_version": PROTOCOL_VERSION,
                "network_epoch": self.network_epoch,
                "document_type": "interactions",
                "node_id": self.node_id,
                "generated_at": isoformat_utc(moment),
                "limit": self.settings.interaction_limit,
                "event_count": 0,
                "events": [],
            },
        )

    def append_interaction(
        self,
        *,
        event_type: str,
        occurred_at: datetime,
        result: str,
        message_id: str | None = None,
        peer_node_id: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Registra un evento compacto sin duplicar el payload del mensaje."""
        now = occurred_at.astimezone(UTC)
        if not event_type or not re.fullmatch(r"^[a-z][a-z0-9_]{1,63}$", event_type):
            raise ValueError("event_type debe usar snake_case")
        if result not in {"success", "duplicate", "rejected", "degraded", "failed"}:
            raise ValueError("result no pertenece al conjunto permitido")
        if peer_node_id is not None and not NODE_ID_PATTERN.fullmatch(peer_node_id):
            raise ValueError("peer_node_id debe usar el formato N00")
        compact_details = deepcopy(dict(details or {}))
        forbidden = INTERACTION_RESERVED_DETAIL_KEYS.intersection(compact_details)
        if forbidden:
            raise ValueError(
                "details no puede duplicar payloads: " + ", ".join(sorted(forbidden))
            )

        self.initialize(moment=now)
        core = self.load_core()
        event_sequence = int(core["last_event_sequence"]) + 1
        event_id = (
            f"SN1-{self.node_id}-EVT-"
            f"{now.strftime('%Y-%m-%dT%H-%M-%SZ')}-{event_sequence:06d}"
        )
        event: dict[str, Any] = {
            "event_id": event_id,
            "event_type": event_type,
            "occurred_at": isoformat_utc(now),
            "result": result,
        }
        if message_id is not None:
            parse_sonantia_message_id(message_id)
            event["message_id"] = message_id
        if peer_node_id is not None:
            event["peer_node_id"] = peer_node_id
        if compact_details:
            event["details"] = compact_details

        document = self._interaction_document(now)
        events = [event, *(document.get("events") or [])]
        events = events[: self.settings.interaction_limit]
        document.update(
            {
                "generated_at": isoformat_utc(now),
                "limit": self.settings.interaction_limit,
                "event_count": len(events),
                "events": events,
            }
        )
        atomic_write_json(self.interactions_root / "current.json", document)
        core["last_event_sequence"] = event_sequence
        core["updated_at"] = isoformat_utc(now)
        atomic_write_json(self.core_path, core)
        return event

    def _refresh_archive_indexes(self, year: int, month: int, moment: datetime) -> None:
        month_directory = self.own_root / f"{year:04d}" / f"{month:02d}"
        day_entries: list[dict[str, Any]] = []
        for path in sorted(month_directory.glob("[0-3][0-9].json")):
            archive = _read_json(path, default={})
            if archive.get("document_type") != "daily-archive":
                continue
            day_entries.append(
                {
                    "date": archive["date"],
                    "path": path.name,
                    "message_count": int(archive["message_count"]),
                    "first_sequence": archive["first_sequence"],
                    "last_sequence": archive["last_sequence"],
                }
            )
        message_count = sum(entry["message_count"] for entry in day_entries)
        non_empty = [entry for entry in day_entries if entry["message_count"]]
        monthly = {
            "network_id": NETWORK_ID,
            "protocol_version": PROTOCOL_VERSION,
            "network_epoch": self.network_epoch,
            "document_type": "monthly-archive-index",
            "origin_node_id": self.node_id,
            "generated_at": isoformat_utc(moment),
            "period": f"{year:04d}-{month:02d}",
            "message_count": message_count,
            "first_sequence": non_empty[0]["first_sequence"] if non_empty else None,
            "last_sequence": non_empty[-1]["last_sequence"] if non_empty else None,
            "days": day_entries,
        }
        atomic_write_json(month_directory / "index.json", monthly)
        self._refresh_global_archive_index(moment)

    def _refresh_global_archive_index(self, moment: datetime) -> None:
        months: list[dict[str, Any]] = []
        for path in sorted(self.own_root.glob("????/??/index.json")):
            monthly = _read_json(path, default={})
            period = monthly.get("period")
            if not isinstance(period, str) or not PERIOD_PATTERN.fullmatch(period):
                continue
            months.append(
                {
                    "period": period,
                    "path": f"{path.parent.parent.name}/{path.parent.name}/index.json",
                    "message_count": int(monthly.get("message_count", 0)),
                    "first_sequence": monthly.get("first_sequence"),
                    "last_sequence": monthly.get("last_sequence"),
                }
            )
        non_empty = [month for month in months if month["message_count"]]
        index = {
            "network_id": NETWORK_ID,
            "protocol_version": PROTOCOL_VERSION,
            "network_epoch": self.network_epoch,
            "document_type": "archive-index",
            "origin_node_id": self.node_id,
            "generated_at": isoformat_utc(moment),
            "message_count": sum(month["message_count"] for month in months),
            "first_sequence": non_empty[0]["first_sequence"] if non_empty else None,
            "last_sequence": non_empty[-1]["last_sequence"] if non_empty else None,
            "months": months,
        }
        atomic_write_json(self.own_root / "index.json", index)

    def recover_core_from_archives(self, *, moment: datetime) -> dict[str, Any]:
        """Reconstruye contadores propios tras una interrupción parcial."""
        latest: dict[str, Any] | None = None
        for path in self._iter_daily_archive_paths():
            archive = _read_json(path, default={})
            for message in archive.get("messages") or []:
                validated = validate_sonantia_message(
                    message,
                    expected_epoch=self.network_epoch,
                )
                if validated["origin_node_id"] != self.node_id:
                    raise SonantiaStorageError(
                        "El archivo propio contiene un origen remoto"
                    )
                if (
                    latest is None
                    or int(validated["sequence"]) > int(latest["sequence"])
                ):
                    latest = validated
        previous = (
            self.load_core()
            if self.core_path.exists()
            else self._empty_core(moment)
        )
        recovered = self._empty_core(moment)
        recovered["last_event_sequence"] = int(previous.get("last_event_sequence", 0))
        recovered["generator_state"] = deepcopy(previous.get("generator_state") or {})
        if latest is not None:
            recovered["last_own_sequence"] = int(latest["sequence"])
            recovered["last_message_id"] = latest["message_id"]
        atomic_write_json(self.core_path, recovered)
        return recovered

    def publish_surface(
        self,
        *,
        generated_at: datetime,
        destination: Path | None = None,
    ) -> list[Path]:
        """Materializa feed, inventario, relays, interacciones y archivo público."""
        now = generated_at.astimezone(UTC)
        target = destination or (self.repository_root / "public")
        written: list[Path] = []

        feed_path = target / "feed.json"
        atomic_write_json(feed_path, self.build_feed(generated_at=now))
        written.append(feed_path)

        inventory_path = target / "inventory.json"
        atomic_write_json(inventory_path, self.build_inventory(generated_at=now))
        written.append(inventory_path)

        for origin_node_id in self.known_node_ids:
            if origin_node_id == self.node_id:
                continue
            relay_path = target / "relay" / f"{origin_node_id}.json"
            atomic_write_json(
                relay_path,
                self.load_relay(origin_node_id, moment=now),
            )
            written.append(relay_path)

        interactions = self._interaction_document(now)
        interactions_path = target / "interactions" / "current.json"
        atomic_write_json(interactions_path, interactions)
        written.append(interactions_path)

        archive_target = target / "archive" / self.node_id
        if self.own_root.exists():
            for source in sorted(self.own_root.rglob("*.json")):
                destination = archive_target / source.relative_to(self.own_root)
                atomic_copy_file(source, destination)
                written.append(destination)
        return sorted(set(written))
