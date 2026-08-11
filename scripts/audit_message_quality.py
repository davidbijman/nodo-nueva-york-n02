"""Audita el corpus activo y los mensajes Sonantia almacenados localmente."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from distributed_node.config import (  # noqa: E402
    load_core_configuration,
    load_message_catalog,
)
from distributed_node.message_quality import phrase_quality_issues  # noqa: E402
from distributed_node.messages import generate_sonantia_text  # noqa: E402
from distributed_node.models import Weather  # noqa: E402


def _daily_archives(data_root: Path) -> list[Path]:
    paths: list[Path] = []
    if not data_root.exists():
        return paths
    for path in data_root.rglob("*.json"):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(document, dict) and document.get("document_type") == "daily-archive":
            paths.append(path)
    return sorted(paths)


def _message_text_for_audit(message: dict[str, Any]) -> str:
    generator = message.get("generator")
    if isinstance(generator, dict) and generator.get("affirmation_text"):
        return str(generator["affirmation_text"])
    text = str(message.get("text") or "").strip()
    return re.sub(
        r"\s*\((?:referencias?|referencia ambiental|movimiento telúrico)[\s\S]*?\)\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()


def audit_history(data_root: Path) -> tuple[int, list[tuple[str, list[str]]]]:
    total = 0
    findings: list[tuple[str, list[str]]] = []
    for path in _daily_archives(data_root):
        document = json.loads(path.read_text(encoding="utf-8"))
        messages = document.get("messages") or []
        for message in messages:
            if not isinstance(message, dict):
                continue
            total += 1
            issues = phrase_quality_issues(_message_text_for_audit(message))
            if issues:
                findings.append((str(message.get("message_id") or path), issues))
    return total, findings


def simulate_catalog() -> tuple[int, int, list[tuple[int, list[str]]]]:
    node, _, _ = load_core_configuration(ROOT / "config")
    catalog = load_message_catalog(ROOT / "config")
    weather = Weather.model_validate(
        {
            "status": "available",
            "provider": "audit-weather",
            "requested_at": "2026-08-04T12:00:00Z",
            "observed_at": "2026-08-04T12:00:00Z",
            "measurement_source_count": 1,
            "measurement_source_codes": ["audit-weather"],
            "data": {
                "temperature_c": 18.2,
                "relative_humidity_percent": 61.0,
                "precipitation_mm": 0.0,
                "pressure_hpa": 1017.4,
                "solar_radiation_wm2": 120.0,
                "condition_code": 2,
            },
        }
    )
    cursor = None
    generated_texts: list[str] = []
    findings: list[tuple[int, list[str]]] = []
    base_moment = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
    for sequence in range(1, len(catalog.phrases) + 1):
        generated = generate_sonantia_text(
            node,
            catalog,
            weather,
            base_moment + timedelta(minutes=sequence),
            sequence,
            phrase_cursor=cursor,
        )
        cursor = generated.next_phrase_cursor
        affirmation = str(generated.generator.get("affirmation_text") or "")
        issues = phrase_quality_issues(affirmation)
        if len(generated.text) > catalog.definition.policy.max_message_characters:
            issues.append("mensaje-excede-limite")
        if issues:
            findings.append((sequence, issues))
        generated_texts.append(generated.text)
    return len(generated_texts), len(set(generated_texts)), findings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fallar si el catálogo activo tiene errores",
    )
    parser.add_argument(
        "--fail-on-history",
        action="store_true",
        help="Fallar también por mensajes históricos, que son inmutables",
    )
    args = parser.parse_args()

    catalog = load_message_catalog(ROOT / "config")
    catalog_findings = [
        (phrase.value_id, phrase_quality_issues(phrase.text))
        for phrase in catalog.phrases
        if phrase_quality_issues(phrase.text)
    ]
    history_count, history_findings = audit_history(ROOT / "data" / "sonantia" / "own")
    simulated_count, simulated_unique, simulation_findings = simulate_catalog()

    print(f"Catálogo activo: {len(catalog.phrases)} frases")
    print(f"Problemas en catálogo: {len(catalog_findings)}")
    print(
        f"Mensajes simulados: {simulated_count}; "
        f"únicos: {simulated_unique}; problemas: {len(simulation_findings)}"
    )
    print(f"Mensajes históricos revisados: {history_count}")
    print(f"Mensajes históricos con patrones conocidos: {len(history_findings)}")
    for message_id, issues in history_findings[:20]:
        print(f"- {message_id}: {', '.join(issues)}")
    if len(history_findings) > 20:
        print(f"- ... y {len(history_findings) - 20} hallazgo(s) adicionales")

    if args.strict and (catalog_findings or simulation_findings):
        raise SystemExit(1)
    if args.fail_on_history and history_findings:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
