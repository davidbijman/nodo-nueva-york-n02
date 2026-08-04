import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from distributed_node.cycle import cycle_due_status, run_cycle, run_cycle_if_due
from distributed_node.validation import validate_message_flow, validate_public
from tests.factories import write_project_profile

ROOT = Path(__file__).resolve().parents[1]
MOMENT = datetime(2026, 8, 1, 12, 6, tzinfo=UTC)


def make_project(tmp_path: Path) -> Path:
    for name in ("config", "schemas", "src"):
        shutil.copytree(ROOT / name, tmp_path / name)
    (tmp_path / "public").mkdir()
    return tmp_path


def condition_client() -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "latitude": 40.7789,
                    "longitude": -73.9692,
                    "current": {
                        "time": "2026-08-01T08:00:00-04:00",
                        "temperature_2m": 15.5,
                        "relative_humidity_2m": 74.0,
                        "precipitation": 0.0,
                        "pressure_msl": 1016.0,
                        "wind_speed_10m": 7.2,
                        "shortwave_radiation": 250.0,
                        "weather_code": 2,
                    },
                },
            )
        )
    )


def station_client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        code = request.url.path.rsplit("/", 1)[-1].removesuffix(".json")
        first = code == "RMCL0098"
        return httpx.Response(
            200,
            json={
                "metadatos": {
                    "id_estacion": code,
                    "nombre": f"Fuente {code}",
                    "ultima_actualizacion": "2026-08-01T12:00:00Z",
                },
                "t": [14.0 if first else 16.0],
                "rh": [70.0 if first else 80.0],
                "slp": [1014.0 if first else 1018.0],
                "vv": [1.0],
                "vd": [180],
                "ppd": [0.2 if first else 0.6],
                "sw": [200.0 if first else 300.0],
                "efemeride": {},
            },
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def single_weather_client() -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "latitude": 40.7789,
                    "longitude": -73.9692,
                    "current": {
                        "time": "2026-08-01T08:00:00-04:00",
                        "temperature_2m": 24.4,
                        "relative_humidity_2m": 66.0,
                        "precipitation": 0.2,
                        "pressure_msl": 1014.8,
                        "wind_speed_10m": 12.6,
                        "shortwave_radiation": 315.0,
                        "weather_code": 2,
                    },
                },
            )
        )
    )


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_two_native_cycles_publish_newest_messages_and_valid_surface(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    (root / "data/messages").mkdir(parents=True)
    (root / "data/messages/deprecated.json").write_text("{}", encoding="utf-8")
    (root / "data/state.json").write_text("{}", encoding="utf-8")
    (root / "public/interactions").mkdir(parents=True)
    (root / "public/interactions/2026-07.json").write_text("{}", encoding="utf-8")
    results = []
    for offset in (0, 1):
        results.append(
            run_cycle(
                root,
                moment=MOMENT + timedelta(hours=offset),
                weather_client=condition_client(),
                redmeteo_client=station_client(),
                fetch_economy=False,
                fetch_geology=False,
                fetch_astronomy=False,
            )
        )

    feed = load_json(root / "public/feed.json")
    status = load_json(root / "public/sonantia-status.json")
    assert [item["sequence"] for item in feed["messages"][:2]] == [2, 1]
    assert results[1]["message_id"] == feed["messages"][0]["message_id"]
    assert status["archive_message_count"] == 2
    assert validate_message_flow(root)["last_sequence"] == 2
    assert validate_public(root)
    assert not (root / "public/status.json").exists()
    assert not (root / "data/messages").exists()
    assert not (root / "data/state.json").exists()
    assert not (root / "public/interactions/2026-07.json").exists()

    index_html = (root / "public/index.html").read_text(encoding="utf-8")
    assert 'data-local-node-id="N02"' in index_html
    assert 'data-weather-composite' in index_html
    assert 'id="weather-evolution-title"' in index_html
    assert 'preserveAspectRatio="xMinYMin meet"' in index_html
    assert "Última actualización:" not in index_html


def test_single_source_profile_uses_same_native_cycle(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    node = write_project_profile(root / "config", "n02")

    result = run_cycle(
        root,
        moment=MOMENT,
        weather_client=single_weather_client(),
        fetch_astronomy=False,
        fetch_economy=False,
        fetch_geology=False,
    )
    feed = load_json(root / "public/feed.json")
    weather = feed["messages"][0]["context"]["weather"]
    index_html = (root / "public/index.html").read_text(encoding="utf-8")

    assert result["sequence"] == 1
    assert feed["node_id"] == node.node_id
    assert weather["provider"] == "open-meteo-current"
    assert weather["measurement_source_count"] == 1
    assert weather["data"]["temperature_c"] == 24.4
    assert (root / "data/sonantia/own" / node.node_id).is_dir()
    assert f'data-local-node-id="{node.node_id}"' in index_html
    assert "Open-Meteo" in index_html
    assert "RMCL0098" not in index_html
    assert validate_public(root)


def test_push_fallback_generates_only_when_message_flow_is_stale(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    first = run_cycle_if_due(
        root,
        moment=MOMENT,
        max_age_minutes=90,
        weather_client=condition_client(),
        redmeteo_client=station_client(),
        fetch_economy=False,
        fetch_geology=False,
        fetch_astronomy=False,
    )
    assert first["action"] == "cycle"
    assert first["sequence"] == 1

    fresh = cycle_due_status(
        root,
        moment=MOMENT + timedelta(minutes=30),
        max_age_minutes=90,
    )
    assert fresh["due"] is False

    recovered = run_cycle_if_due(
        root,
        moment=MOMENT + timedelta(minutes=91),
        max_age_minutes=90,
        weather_client=condition_client(),
        redmeteo_client=station_client(),
        fetch_economy=False,
        fetch_geology=False,
        fetch_astronomy=False,
    )
    assert recovered["action"] == "cycle"
    assert recovered["sequence"] == 2
    assert validate_message_flow(
        root,
        max_age_minutes=90,
        moment=MOMENT + timedelta(minutes=91),
    )["last_sequence"] == 2
