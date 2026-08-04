"""Contrato canónico y validaciones de Red Sonantia Network 1.0."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

NETWORK_ID = "sonantia-network"
NETWORK_NAME = "Sonantia Network"
PROTOCOL_NAME = "Red Sonantia Network"
PROTOCOL_VERSION = "1.0"
NETWORK_EPOCH = "SN1-2026-08-02"
MESSAGE_ID_PREFIX = "SN1"

NODE_ID_PATTERN = re.compile(r"^N\d{2}$")
NETWORK_EPOCH_PATTERN = re.compile(r"^SN1-\d{4}-\d{2}-\d{2}$")
MESSAGE_ID_PATTERN = re.compile(
    r"^(?P<prefix>SN1)-(?P<node_id>N\d{2})-"
    r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})T"
    r"(?P<hour>\d{2})-(?P<minute>\d{2})-(?P<second>\d{2})Z-"
    r"(?P<sequence>\d{6})$"
)
ISO_UTC_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
)
CONTENT_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")

CANONICAL_MESSAGE_FIELDS = (
    "network_id",
    "protocol_version",
    "network_epoch",
    "message_id",
    "origin_node_id",
    "sequence",
    "created_at",
    "visibility",
    "language",
    "text",
    "context",
    "generator",
)


@dataclass(frozen=True, slots=True)
class SonantiaMessageIdentity:
    """Identidad derivada de un identificador público Sonantia."""

    prefix: str
    node_id: str
    created_at: datetime
    sequence: int


def ensure_utc(moment: datetime) -> datetime:
    """Normaliza un instante a UTC y rechaza fechas sin zona horaria."""
    if moment.tzinfo is None:
        raise ValueError("Los instantes deben incluir zona horaria")
    return moment.astimezone(UTC)


def isoformat_utc(moment: datetime) -> str:
    """Devuelve una fecha ISO 8601 UTC sin fracciones de segundo."""
    return ensure_utc(moment).isoformat(timespec="seconds").replace("+00:00", "Z")


def readable_protocol_timestamp(moment: datetime) -> str:
    """Timestamp legible y seguro para nombres de archivo y URLs."""
    return ensure_utc(moment).strftime("%Y-%m-%dT%H-%M-%SZ")


def build_sonantia_message_id(
    node_id: str,
    moment: datetime,
    sequence: int,
    *,
    prefix: str = MESSAGE_ID_PREFIX,
) -> str:
    """Construye un identificador legible de Red Sonantia Network v1.0.

    Formato: ``SN1-N02-2026-08-02T06-00-00Z-000001``.
    """
    if not NODE_ID_PATTERN.fullmatch(node_id):
        raise ValueError("node_id debe usar el formato N00")
    if prefix != MESSAGE_ID_PREFIX:
        raise ValueError(f"Prefijo de protocolo no soportado: {prefix}")
    if not 1 <= sequence <= 999_999:
        raise ValueError("La secuencia debe estar entre 1 y 999999")
    return (
        f"{prefix}-{node_id}-{readable_protocol_timestamp(moment)}-"
        f"{sequence:06d}"
    )


def parse_sonantia_message_id(message_id: str) -> SonantiaMessageIdentity:
    """Analiza y valida un identificador Sonantia v1.0."""
    match = MESSAGE_ID_PATTERN.fullmatch(message_id)
    if match is None:
        raise ValueError("message_id no cumple el formato Sonantia v1.0")
    values = match.groupdict()
    created_at = datetime(
        int(values["year"]),
        int(values["month"]),
        int(values["day"]),
        int(values["hour"]),
        int(values["minute"]),
        int(values["second"]),
        tzinfo=UTC,
    )
    return SonantiaMessageIdentity(
        prefix=values["prefix"],
        node_id=values["node_id"],
        created_at=created_at,
        sequence=int(values["sequence"]),
    )


def canonical_message_payload(message: Mapping[str, Any]) -> dict[str, Any]:
    """Extrae exclusivamente el contenido inmutable que forma el mensaje.

    Metadatos locales de transporte, recepción o custodia quedan fuera del hash.
    """
    missing = [field for field in CANONICAL_MESSAGE_FIELDS if field not in message]
    if missing:
        raise ValueError(f"Faltan campos canónicos: {', '.join(missing)}")
    return {
        field: deepcopy(message[field])
        for field in CANONICAL_MESSAGE_FIELDS
    }


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    """Serializa JSON canónico compatible con implementaciones cruzadas."""
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("El mensaje contiene valores no serializables") from exc
    return serialized.encode("utf-8")


def calculate_content_hash(message: Mapping[str, Any]) -> str:
    """Calcula SHA-256 sobre el contenido canónico del mensaje."""
    digest = hashlib.sha256(
        canonical_json_bytes(canonical_message_payload(message))
    ).hexdigest()
    return f"sha256:{digest}"


def attach_content_hash(message: Mapping[str, Any]) -> dict[str, Any]:
    """Devuelve una copia del mensaje con su hash canónico."""
    result = deepcopy(dict(message))
    result["content_hash"] = calculate_content_hash(result)
    return result


def validate_content_hash(message: Mapping[str, Any]) -> None:
    """Comprueba que el hash declarado corresponda al contenido canónico."""
    declared = str(message.get("content_hash") or "")
    if not CONTENT_HASH_PATTERN.fullmatch(declared):
        raise ValueError("content_hash ausente o inválido")
    expected = calculate_content_hash(message)
    if not hmac.compare_digest(declared, expected):
        raise ValueError("content_hash no corresponde al contenido del mensaje")


def validate_sonantia_message(
    message: Mapping[str, Any],
    *,
    expected_epoch: str | None = NETWORK_EPOCH,
) -> dict[str, Any]:
    """Valida identidad, contenido y hash de un mensaje Sonantia v1.0."""
    if not isinstance(message, Mapping):
        raise ValueError("El mensaje debe ser un objeto JSON")

    canonical = canonical_message_payload(message)
    if canonical["network_id"] != NETWORK_ID:
        raise ValueError("network_id no corresponde a Sonantia Network")
    if canonical["protocol_version"] != PROTOCOL_VERSION:
        raise ValueError("protocol_version no corresponde a la versión 1.0")

    network_epoch = canonical["network_epoch"]
    if not isinstance(network_epoch, str) or not NETWORK_EPOCH_PATTERN.fullmatch(
        network_epoch
    ):
        raise ValueError("network_epoch no cumple el formato Sonantia")
    if expected_epoch is not None and network_epoch != expected_epoch:
        raise ValueError("network_epoch pertenece a otra época de red")

    identity = parse_sonantia_message_id(str(canonical["message_id"]))
    if canonical["origin_node_id"] != identity.node_id:
        raise ValueError("origin_node_id no coincide con message_id")
    if canonical["sequence"] != identity.sequence:
        raise ValueError("sequence no coincide con message_id")
    if canonical["created_at"] != isoformat_utc(identity.created_at):
        raise ValueError("created_at no coincide con message_id")

    if canonical["visibility"] != "public":
        raise ValueError("La versión 1.0 solo admite mensajes públicos")
    language = canonical["language"]
    if not isinstance(language, str) or not re.fullmatch(r"^[a-z]{2}$", language):
        raise ValueError("language debe usar un código de dos letras")
    text = canonical["text"]
    if not isinstance(text, str) or not text.strip() or len(text) > 10_000:
        raise ValueError("text debe contener entre 1 y 10000 caracteres")
    if not isinstance(canonical["context"], dict):
        raise ValueError("context debe ser un objeto JSON")
    if canonical["generator"] is not None and not isinstance(
        canonical["generator"], dict
    ):
        raise ValueError("generator debe ser un objeto JSON o null")

    validate_content_hash(message)
    return deepcopy(dict(message))


def build_sonantia_message(
    *,
    node_id: str,
    moment: datetime,
    sequence: int,
    text: str,
    context: Mapping[str, Any] | None = None,
    generator: Mapping[str, Any] | None = None,
    language: str = "es",
    network_epoch: str = NETWORK_EPOCH,
) -> dict[str, Any]:
    """Construye un mensaje canónico listo para persistencia y publicación."""
    created_at = isoformat_utc(moment)
    message = {
        "network_id": NETWORK_ID,
        "protocol_version": PROTOCOL_VERSION,
        "network_epoch": network_epoch,
        "message_id": build_sonantia_message_id(node_id, moment, sequence),
        "origin_node_id": node_id,
        "sequence": sequence,
        "created_at": created_at,
        "visibility": "public",
        "language": language,
        "text": text,
        "context": deepcopy(dict(context or {})),
        "generator": deepcopy(dict(generator)) if generator is not None else None,
    }
    result = attach_content_hash(message)
    return validate_sonantia_message(result, expected_epoch=network_epoch)
