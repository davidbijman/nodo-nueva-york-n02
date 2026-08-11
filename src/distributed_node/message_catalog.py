"""Carga y compilación eficiente del catálogo interno de mensajes."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .message_quality import normalize_phrase_template
from .models import CatalogValue, MessageCatalog

PLACEHOLDER_PATTERN = re.compile(r"\{\{([a-z_][a-z0-9_.]*)\}\}")
PHRASE_LINE_PATTERN = re.compile(r"^\s*(\d+)\.\s+(.+?)\s*$")
ALLOWED_TEMPLATE_PATHS = {
    "node.node_id",
    "node.display_name",
    "node.city",
    "node.country",
    "node.timezone",
    "time.local",
    "time.salutation",
    "time.day_period",
    "recipient.name",
    "source.id",
    "source.provider",
    "source.facts",
}


@dataclass(frozen=True)
class CatalogPhrase:
    value_id: str
    collection_id: str
    text: str


@dataclass(frozen=True)
class CompiledMessageCatalog:
    definition: MessageCatalog
    phrases: tuple[CatalogPhrase, ...]
    catalog_hash: str

    @property
    def catalog_version(self) -> str:
        return self.definition.catalog_version

    @property
    def generator_id(self) -> str:
        return self.definition.generator_id

    @property
    def selection_policy(self) -> str:
        return self.definition.selection_policy

    @property
    def recipient_names(self) -> list[str]:
        return self.definition.recipient_names


def _validate_value_paths(value: CatalogValue) -> None:
    referenced = set(value.requires) | set(PLACEHOLDER_PATTERN.findall(value.text))
    unknown = referenced - ALLOWED_TEMPLATE_PATHS
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ValueError(f"Rutas de contexto desconocidas en {value.value_id}: {names}")


def _load_phrase_file(
    config_dir: Path,
    collection_id: str,
    relative_path: str,
) -> list[CatalogPhrase]:
    path = (config_dir / relative_path).resolve()
    if config_dir.resolve() not in path.parents:
        raise ValueError(f"Ruta de frases fuera de config: {relative_path}")
    phrases: list[CatalogPhrase] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        text = line.strip()
        if not text:
            continue
        match = PHRASE_LINE_PATTERN.fullmatch(text)
        if match is not None:
            text = match.group(2)
        if "{{NOMBRE}}" not in text:
            raise ValueError(f"Falta {{{{NOMBRE}}}} en {relative_path}, línea {line_number}")
        normalized = normalize_phrase_template(text)
        if normalized != text:
            raise ValueError(
                f"La frase requiere normalización en {relative_path}, línea {line_number}"
            )
        phrase_number = len(phrases) + 1
        phrases.append(
            CatalogPhrase(
                value_id=f"{collection_id}-frase-{phrase_number:06d}",
                collection_id=collection_id,
                text=text.replace("{{NOMBRE}}", "{{recipient.name}}"),
            )
        )
    return phrases


def compile_message_catalog(
    definition: MessageCatalog,
    config_dir: Path,
) -> CompiledMessageCatalog:
    values = [
        *definition.openings,
        *definition.declarations,
        *definition.fallback_messages,
        *(value for source in definition.source_templates for value in source.templates),
    ]
    for value in values:
        _validate_value_paths(value)

    phrases = tuple(
        phrase
        for collection in definition.phrase_collections
        if collection.enabled
        for phrase in _load_phrase_file(
            config_dir,
            collection.collection_id,
            collection.path,
        )
    )
    if not phrases:
        raise ValueError("El catálogo requiere al menos una frase habilitada")
    phrase_ids = [phrase.value_id for phrase in phrases]
    phrase_texts = [phrase.text for phrase in phrases]
    if len(phrase_ids) != len(set(phrase_ids)):
        raise ValueError("Las colecciones contienen value_id duplicados")
    if len(phrase_texts) != len(set(phrase_texts)):
        raise ValueError("Las colecciones contienen frases duplicadas")

    canonical = {
        "definition": definition.model_dump(mode="json"),
        "phrases": [
            {
                "value_id": phrase.value_id,
                "collection_id": phrase.collection_id,
                "text": phrase.text,
            }
            for phrase in phrases
        ],
    }
    serialized = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    catalog_hash = f"sha256:{hashlib.sha256(serialized.encode('utf-8')).hexdigest()}"
    return CompiledMessageCatalog(
        definition=definition,
        phrases=phrases,
        catalog_hash=catalog_hash,
    )
