"""Valida y resume el catálogo factorado sin generar contenido duplicado."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from distributed_node.config import load_message_catalog  # noqa: E402


def main() -> None:
    catalog = load_message_catalog(ROOT / "config")
    definition = catalog.definition
    template_count = (
        len(definition.openings)
        + len(definition.declarations)
        + len(definition.fallback_messages)
        + sum(len(source.templates) for source in definition.source_templates)
    )
    print(f"Catálogo válido: {len(catalog.phrases)} frases únicas")
    print(f"Plantillas factoradas: {template_count}")
    print(f"Versión: {catalog.catalog_version}")
    print(f"Hash: {catalog.catalog_hash}")


if __name__ == "__main__":
    main()
