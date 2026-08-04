"""Interfaz de línea de comandos para operar Sonantia Network 1.0."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from .cycle import rebuild_archive, render_existing, run_cycle, run_cycle_if_due
from .sonantia_activation import initialize_sonantia_v1
from .validation import validate_config, validate_message_flow, validate_public


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="distributed_node")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Raíz del repositorio (por defecto, el directorio actual)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate-config", help="Valida la configuración del nodo")
    subparsers.add_parser("validate-public", help="Valida los recursos públicos")
    flow_parser = subparsers.add_parser(
        "validate-message-flow",
        help="Valida continuidad y frescura entre core, archivo y feed Sonantia",
    )
    flow_parser.add_argument(
        "--max-age-minutes",
        type=int,
        default=None,
        help="Falla si el último mensaje supera esta antigüedad",
    )
    subparsers.add_parser("run-cycle", help="Ejecuta un ciclo completo")
    due_parser = subparsers.add_parser(
        "run-cycle-if-due",
        help="Ejecuta un ciclo solo si el último mensaje está ausente o atrasado",
    )
    due_parser.add_argument(
        "--max-age-minutes",
        type=int,
        default=90,
        help="Antigüedad máxima antes de generar un mensaje de recuperación",
    )
    subparsers.add_parser("render", help="Regenera las vistas HTML sin crear un mensaje")
    subparsers.add_parser("rebuild-archive", help="Reconstruye feed e índice histórico")
    initialize_parser = subparsers.add_parser(
        "initialize-sonantia",
        help="Reinicia y publica el estado activo de Red Sonantia Network v1.0",
    )
    initialize_parser.add_argument(
        "--force",
        action="store_true",
        help="Elimina un estado Sonantia existente antes de reinicializar",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    root = arguments.root.resolve()
    try:
        if arguments.command == "validate-config":
            validated = validate_config(root)
            print(f"Configuración válida: {len(validated)} documento(s)")
        elif arguments.command == "validate-public":
            validated = validate_public(root)
            print(f"Recursos públicos válidos: {len(validated)} documento(s)")
        elif arguments.command == "validate-message-flow":
            flow = validate_message_flow(
                root,
                max_age_minutes=arguments.max_age_minutes,
            )
            print(
                "Flujo Sonantia válido: "
                f"{flow['message_count']} mensaje(s) · "
                f"secuencia {flow['last_sequence']}"
            )
        elif arguments.command == "run-cycle":
            result = run_cycle(root)
            print(f"Ciclo completado: {result['status']} · mensaje {result['message_id']}")
        elif arguments.command == "run-cycle-if-due":
            result = run_cycle_if_due(
                root,
                max_age_minutes=arguments.max_age_minutes,
            )
            if result["action"] == "cycle":
                print(
                    "Ciclo de recuperación completado: "
                    f"{result['status']} · mensaje {result['message_id']}"
                )
            else:
                age = result.get("message_age_minutes")
                age_text = "sin edad" if age is None else f"{age:.1f} min"
                print(f"Flujo vigente; solo render: {age_text}")
        elif arguments.command == "render":
            pages = render_existing(root)
            print(f"HTML generado: {len(pages)} página(s)")
        elif arguments.command == "rebuild-archive":
            index = rebuild_archive(root)
            print(f"Archivo reconstruido: {index['message_count']} mensaje(s)")
        elif arguments.command == "initialize-sonantia":
            written = initialize_sonantia_v1(root, force=arguments.force)
            print(f"Red Sonantia inicializada: {len(written)} recurso(s) público(s)")
    except Exception as exc:
        print(f"Error: {type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
