"""Consulta USGS con la estrategia geológica de N02 y resume el resultado."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from distributed_node.config import load_node_configuration
from distributed_node.providers.geology.usgs_earthquakes import fetch_usgs_snapshot


def main() -> int:
    root = Path.cwd()
    node = load_node_configuration(root / "config")
    snapshot = fetch_usgs_snapshot(node, datetime.now(UTC))

    print(f"status: {snapshot['status']}")
    print(f"provider: {snapshot['provider']}")
    print(f"search_stage: {snapshot.get('search_stage')}")
    print(f"window_hours: {snapshot.get('window_hours')}")
    print(f"region_label: {snapshot.get('region_label')}")
    print(f"count: {snapshot.get('count')}")
    print(f"source_url: {snapshot.get('source_url')}")

    for event in snapshot.get("events", [])[:10]:
        print(
            "- "
            f"M{event.get('magnitude_text')} · "
            f"{event.get('local_time')} · "
            f"{event.get('location')}"
        )

    if snapshot.get("error"):
        print(f"error: {snapshot['error']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
