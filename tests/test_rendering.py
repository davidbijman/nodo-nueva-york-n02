from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from distributed_node.models import Weather
from distributed_node.rendering import (
    _weather_composite_chart,
    _weather_icon,
    _weather_measurements,
)


def message(*, observed_at: str | None, created_at: str, value: float) -> dict:
    return {
        "origin_node_id": "N99",
        "created_at": created_at,
        "context": {
            "weather": {
                "provider": "test-weather",
                "observed_at": observed_at,
                "requested_at": "",
                "status": "available",
                "measurement_source_count": 1,
                "data": {
                    "condition_code": 0,
                    "temperature_c": value,
                    "relative_humidity_percent": 60.0 + value,
                    "precipitation_mm": 0.0,
                    "pressure_hpa": 1010.0 + value,
                    "solar_radiation_wm2": 100.0 + value,
                },
            }
        },
    }


def node():
    return SimpleNamespace(
        node_id="N99",
        logical_location=SimpleNamespace(timezone="UTC"),
    )


def test_weather_history_uses_valid_timestamps_and_full_width_chart() -> None:
    messages = [
        message(
            observed_at="2026-08-01T12:00:00Z",
            created_at="2026-08-01T12:01:00Z",
            value=12.0,
        ),
        message(
            observed_at="",
            created_at="2026-08-01T11:00:00Z",
            value=11.0,
        ),
        message(observed_at="fecha-invalida", created_at="", value=10.0),
    ]

    measurements = _weather_measurements(messages, node())
    chart = _weather_composite_chart(measurements)

    assert len(measurements) == 2
    expected = datetime.fromisoformat("2026-08-01T11:00:00+00:00").astimezone(ZoneInfo("UTC"))
    assert measurements[1]["local_time"] == expected.isoformat(timespec="minutes")
    assert chart is not None
    assert chart["viewbox_width"] == 1600
    assert chart["viewbox_height"] == 310
    assert chart["plot_left"] < 60
    assert chart["plot_right"] > 1390
    assert chart["points"][0]["x"] < chart["points"][-1]["x"]
    assert chart["latest"]["temperature"] == 12.0


def test_weather_icons_prioritize_the_observed_condition() -> None:
    assert _weather_icon(3, is_day=False, available=True) == ("☁️", "Cielo cubierto")
    assert _weather_icon(61, is_day=False, available=True)[0] == "🌧️"
    assert _weather_icon(71, is_day=False, available=True)[0] == "🌨️"
    assert _weather_icon(45, is_day=True, available=True)[0] == "🌫️"
    assert _weather_icon(0, is_day=False, available=True)[0] == "🌙"


def test_current_weather_is_added_ahead_of_stale_message_history() -> None:
    messages = [
        message(
            observed_at="2026-08-03T20:59:00Z",
            created_at="2026-08-03T21:09:00Z",
            value=12.0,
        )
    ]
    current = Weather.model_validate(
        {
            "status": "available",
            "provider": "test-current",
            "requested_at": "2026-08-04T00:16:00Z",
            "observed_at": "2026-08-04T00:15:00Z",
            "measurement_source_count": 1,
            "measurement_source_codes": ["test-current"],
            "data": {
                "condition_code": 3,
                "temperature_c": 9.8,
                "relative_humidity_percent": 87.0,
                "precipitation_mm": 0.0,
                "pressure_hpa": 1024.0,
                "solar_radiation_wm2": 0.0,
            },
        }
    )

    measurements = _weather_measurements(
        messages,
        node(),
        current_weather=current,
    )

    assert measurements[0]["local_time"] == "2026-08-04T00:15+00:00"
    assert measurements[0]["temperature_c"] == 9.8
    assert measurements[1]["local_time"] == "2026-08-03T20:59+00:00"
