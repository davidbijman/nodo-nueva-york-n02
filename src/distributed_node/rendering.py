"""Renderizado estático, accesible y con autoescape de las vistas públicas."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .messages import CONDITION_LABELS
from .models import NodeConfig, OperatorMessage, SonantiaNetworkConfig, Weather
from .sonantia_protocol import isoformat_utc
from .sonantia_storage import atomic_write_json
from .weather import OPEN_METEO_PUBLIC_URL, condition_key


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _to_local_datetime(value: str, timezone_name: str) -> datetime:
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    timestamp = datetime.fromisoformat(normalized)
    if timestamp.tzinfo is None:
        raise ValueError("La fecha debe incluir una zona horaria")
    return timestamp.astimezone(ZoneInfo(timezone_name))


def _try_local_datetime(value: str | None, timezone_name: str) -> datetime | None:
    """Convierte una fecha válida sin interrumpir el renderizado por una fecha externa inválida."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _to_local_datetime(value.strip(), timezone_name)
    except (TypeError, ValueError):
        return None


def _local_datetime(value: str | None, timezone_name: str) -> str:
    local = _try_local_datetime(value, timezone_name)
    if local is None:
        return "—"
    return f"{local:%Y-%m-%d %H:%M:%S} ({timezone_name})"


def _local_datetime_short(value: str | None, timezone_name: str) -> str:
    local = _try_local_datetime(value, timezone_name)
    if local is None:
        return "—"
    return f"{local:%Y-%m-%d %H:%M:%S}"


def _local_iso(value: str | None, timezone_name: str) -> str:
    local = _try_local_datetime(value, timezone_name)
    if local is None:
        return ""
    return local.isoformat(timespec="seconds")


def template_environment(template_dir: Path) -> Environment:
    environment = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(enabled_extensions=("html", "j2"), default=True),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    environment.filters["local_datetime"] = _local_datetime
    environment.filters["local_datetime_short"] = _local_datetime_short
    environment.filters["local_iso"] = _local_iso
    return environment


def _last_received_message_time(
    relay: dict[str, Any] | None,
    timezone_name: str,
) -> str | None:
    messages = (relay or {}).get("messages") or []
    latest = max(
        (message for message in messages if isinstance(message, dict)),
        key=lambda message: str(message.get("created_at") or ""),
        default=None,
    )
    if latest is None:
        return None
    return _local_datetime_short(str(latest.get("created_at") or ""), timezone_name)


def _peer_cards(
    network: SonantiaNetworkConfig,
    local_node_id: str,
    peer_status: list[dict[str, Any]],
    relays: dict[str, dict[str, Any]],
    timezone_name: str,
) -> list[dict[str, Any]]:
    observed_by_id = {
        item.get("node_id"): item
        for item in peer_status
        if isinstance(item, dict)
    }
    cards: list[dict[str, Any]] = []
    for peer in network.nodes:
        if peer.node_id == local_node_id:
            continue
        observed = observed_by_id.get(peer.node_id)
        if peer.enabled and observed and observed.get("status") == "reachable":
            signal, label = "green", "Verde: accesible"
        elif peer.enabled and observed and observed.get("status") == "unreachable":
            signal, label = "red", "Rojo: no disponible"
        else:
            signal, label = "yellow", "Amarillo: pendiente o sin datos"
        cards.append(
            {
                "peer": peer,
                "signal": signal,
                "label": label,
                "last_received_at": _last_received_message_time(
                    relays.get(peer.node_id),
                    timezone_name,
                ),
            }
        )
    return cards


def _weather_icon(
    condition: int | str | None,
    *,
    is_day: bool,
    available: bool,
) -> tuple[str, str]:
    """Representa el fenómeno observado sin mezclar iconos incompatibles."""
    if not available:
        return "◌", "Medición no disponible"

    normalized = condition_key(condition) or ""
    if "thunderstorm" in normalized:
        return "⛈️", "Tormenta"
    if "snow" in normalized:
        return "🌨️", "Nieve"
    if "freezing" in normalized and (
        "rain" in normalized or "drizzle" in normalized
    ):
        return "🌧️", "Precipitación helada"
    if "rain" in normalized or "drizzle" in normalized:
        return "🌧️", "Lluvia o llovizna"
    if "fog" in normalized:
        return "🌫️", "Niebla"
    if normalized == "overcast":
        return "☁️", "Cielo cubierto"
    if normalized == "partly-cloudy":
        return "⛅", "Cielo parcialmente nublado"
    if normalized == "mainly-clear":
        return "🌤️", "Cielo mayormente despejado"
    if normalized == "clear-sky":
        return (
            ("☀️", "Cielo despejado")
            if is_day
            else ("🌙", "Noche despejada")
        )
    if "cloud" in normalized:
        return "☁️", "Nubosidad"
    return "🌡️", "Condición meteorológica"


def _condition_label(condition: int | str | None) -> str:
    normalized = condition_key(condition)
    if not normalized:
        return "no informada"
    return CONDITION_LABELS.get(normalized, normalized.replace("-", " "))


def _current_condition_snapshot(weather: Weather, node: NodeConfig) -> dict[str, Any]:
    reference_time = (
        weather.condition_observed_at
        or weather.observed_at
        or weather.requested_at
    )
    reference_datetime = _try_local_datetime(
        reference_time,
        node.logical_location.timezone,
    )
    local_time = (
        f"{reference_datetime:%Y-%m-%d %H:%M:%S}"
        if reference_datetime is not None
        else "—"
    )
    is_day = (
        7 <= reference_datetime.hour < 20
        if reference_datetime is not None
        else True
    )
    icon, icon_label = _weather_icon(
        weather.data.condition_code,
        is_day=is_day,
        available=weather.data.condition_code is not None,
    )
    return {
        "status": weather.status,
        "icon": icon,
        "icon_label": icon_label,
        "condition": _condition_label(weather.data.condition_code),
        "updated_at_local": local_time,
        "source_label": "Open-Meteo",
        "source_url": OPEN_METEO_PUBLIC_URL,
    }


def _weather_measurements(
    messages: list[dict[str, Any]],
    node: NodeConfig,
    *,
    current_weather: Weather | None = None,
    hours: int = 72,
) -> list[dict[str, Any]]:
    """Combina el historial canónico con la observación actual y elimina duplicados."""
    candidates: list[tuple[dict[str, Any], str | None]] = []
    if current_weather is not None:
        current_document = current_weather.model_dump(mode="json")
        current_reference = (
            current_document.get("observed_at")
            or current_document.get("requested_at")
        )
        candidates.append((current_document, current_reference))

    own_messages = sorted(
        (
            message
            for message in messages
            if isinstance(message, dict)
            and message.get("origin_node_id") == node.node_id
        ),
        key=lambda message: str(message.get("created_at") or ""),
        reverse=True,
    )
    for message in own_messages:
        context = message.get("context") if isinstance(message.get("context"), dict) else {}
        weather = context.get("weather") if isinstance(context.get("weather"), dict) else {}
        reference_time = (
            weather.get("observed_at")
            or weather.get("requested_at")
            or message.get("created_at")
        )
        candidates.append((weather, str(reference_time or "")))

    measurements: list[dict[str, Any]] = []
    seen_observations: set[str] = set()
    for weather, reference_time in candidates:
        data = weather.get("data") if isinstance(weather.get("data"), dict) else {}
        if weather.get("status") != "available":
            continue
        local_time = _try_local_datetime(
            str(reference_time or ""),
            node.logical_location.timezone,
        )
        if local_time is None:
            continue
        local_key = local_time.isoformat(timespec="minutes")
        if local_key in seen_observations:
            continue
        seen_observations.add(local_key)
        condition = data.get("condition_code")
        icon, icon_label = _weather_icon(
            condition,
            is_day=7 <= local_time.hour < 20,
            available=condition is not None,
        )
        measurements.append(
            {
                "icon": icon,
                "icon_label": icon_label,
                "local_time": local_key,
                "status": weather.get("status"),
                "condition": _condition_label(condition),
                "temperature_c": data.get("temperature_c"),
                "relative_humidity_percent": data.get("relative_humidity_percent"),
                "precipitation_mm": data.get("precipitation_mm"),
                "pressure_hpa": data.get("pressure_hpa"),
                "solar_radiation_wm2": data.get("solar_radiation_wm2"),
                "source_count": int(weather.get("measurement_source_count") or 0),
            }
        )

    if not measurements:
        return []
    measurements.sort(
        key=lambda item: datetime.fromisoformat(item["local_time"]),
        reverse=True,
    )
    latest = datetime.fromisoformat(measurements[0]["local_time"])
    cutoff = latest - timedelta(hours=hours)
    return [
        item
        for item in measurements
        if datetime.fromisoformat(item["local_time"]) >= cutoff
    ]


def _numeric_range(
    values: list[float | None],
    *,
    minimum_padding: float,
    clamp: tuple[float, float] | None = None,
) -> tuple[float, float]:
    available = [float(value) for value in values if value is not None]
    if not available:
        return (0.0, 1.0)
    minimum = min(available)
    maximum = max(available)
    span = maximum - minimum
    padding = max(minimum_padding, span * 0.08)
    lower = minimum - padding
    upper = maximum + padding
    if clamp is not None:
        lower = max(clamp[0], lower)
        upper = min(clamp[1], upper)
    if upper <= lower:
        upper = lower + max(minimum_padding, 1.0)
    return lower, upper


def _scaled_y(
    value: float,
    value_range: tuple[float, float],
    top: float,
    bottom: float,
) -> float:
    lower, upper = value_range
    ratio = (float(value) - lower) / (upper - lower)
    return round(bottom - min(1.0, max(0.0, ratio)) * (bottom - top), 2)


def _line_segments(
    points: list[dict[str, Any]],
    field: str,
    value_range: tuple[float, float],
    top: float,
    bottom: float,
) -> list[str]:
    segments: list[list[str]] = []
    current: list[str] = []
    for point in points:
        value = point.get(field)
        if value is None:
            if current:
                segments.append(current)
                current = []
            continue
        current.append(f"{point['x']},{_scaled_y(float(value), value_range, top, bottom)}")
    if current:
        segments.append(current)
    return [" ".join(segment) for segment in segments if segment]


def _area_paths(
    points: list[dict[str, Any]],
    field: str,
    value_range: tuple[float, float],
    top: float,
    bottom: float,
) -> list[str]:
    paths: list[str] = []
    current: list[tuple[float, float]] = []
    for point in points:
        value = point.get(field)
        if value is None:
            if current:
                start_x = current[0][0]
                end_x = current[-1][0]
                path = [f"M {start_x} {bottom}"]
                path.extend(f"L {x} {y}" for x, y in current)
                path.append(f"L {end_x} {bottom} Z")
                paths.append(" ".join(path))
                current = []
            continue
        current.append(
            (float(point["x"]), _scaled_y(float(value), value_range, top, bottom))
        )
    if current:
        start_x = current[0][0]
        end_x = current[-1][0]
        path = [f"M {start_x} {bottom}"]
        path.extend(f"L {x} {y}" for x, y in current)
        path.append(f"L {end_x} {bottom} Z")
        paths.append(" ".join(path))
    return paths


def _precipitation_increments(points: list[dict[str, Any]]) -> list[float | None]:
    increments: list[float | None] = []
    previous_value: float | None = None
    previous_date = None
    for point in points:
        value = point.get("precipitation_mm")
        current_date = datetime.fromisoformat(point["local_time"]).date()
        if value is None:
            increments.append(None)
            previous_value = None
            previous_date = current_date
            continue
        current_value = float(value)
        if previous_value is None:
            increment = None
        elif previous_date != current_date:
            increment = max(0.0, current_value)
        elif current_value >= previous_value:
            increment = current_value - previous_value
        else:
            increment = None
        increments.append(round(increment, 3) if increment is not None else None)
        previous_value = current_value
        previous_date = current_date
    return increments


def _weather_composite_chart(
    measurements: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not measurements:
        return None

    chronological = [dict(item) for item in reversed(measurements)]
    parsed_times = [datetime.fromisoformat(item["local_time"]) for item in chronological]
    start = min(parsed_times)
    end = max(parsed_times)
    duration = max((end - start).total_seconds(), 1.0)
    plot_left = 34.0
    plot_right = 1582.0
    plot_width = plot_right - plot_left

    for item, parsed in zip(chronological, parsed_times, strict=True):
        item["x"] = round(
            plot_right - ((parsed - start).total_seconds() / duration) * plot_width,
            2,
        )

    increments = _precipitation_increments(chronological)
    for item, increment in zip(chronological, increments, strict=True):
        item["precipitation_interval_mm"] = increment

    temperature_values = [item.get("temperature_c") for item in chronological]
    humidity_values = [item.get("relative_humidity_percent") for item in chronological]
    pressure_values = [item.get("pressure_hpa") for item in chronological]
    precipitation_values = [item.get("precipitation_interval_mm") for item in chronological]
    radiation_values = [item.get("solar_radiation_wm2") for item in chronological]

    temperature_range = _numeric_range(temperature_values, minimum_padding=1.0)
    humidity_range = _numeric_range(
        humidity_values,
        minimum_padding=5.0,
        clamp=(0.0, 100.0),
    )
    pressure_range = _numeric_range(pressure_values, minimum_padding=0.6)
    precipitation_max = max(
        [float(value) for value in precipitation_values if value is not None] or [0.0]
    )
    radiation_max = max(
        [float(value) for value in radiation_values if value is not None] or [0.0]
    )
    precipitation_range = (0.0, max(0.5, precipitation_max * 1.15))
    radiation_range = (0.0, max(100.0, radiation_max * 1.1))

    bands = {
        "temperature_humidity": (26.0, 102.0),
        "pressure": (123.0, 177.0),
        "precipitation_radiation": (199.0, 265.0),
    }
    top_1, bottom_1 = bands["temperature_humidity"]
    top_2, bottom_2 = bands["pressure"]
    top_3, bottom_3 = bands["precipitation_radiation"]

    bar_width = max(2.5, min(11.0, plot_width / max(len(chronological), 1) * 0.62))
    precipitation_bars: list[dict[str, Any]] = []
    for item in chronological:
        value = item.get("precipitation_interval_mm")
        if value is None or float(value) <= 0:
            continue
        y = _scaled_y(float(value), precipitation_range, top_3, bottom_3)
        precipitation_bars.append(
            {
                "x": round(float(item["x"]) - bar_width / 2, 2),
                "y": y,
                "width": round(bar_width, 2),
                "height": round(bottom_3 - y, 2),
            }
        )

    tick_count = min(9, max(2, len(chronological)))
    ticks: list[dict[str, Any]] = []
    for index in range(tick_count):
        ratio = index / (tick_count - 1) if tick_count > 1 else 0.0
        tick_time = end - (end - start) * ratio
        ticks.append(
            {
                "x": round(plot_left + ratio * plot_width, 2),
                "label": f"{tick_time:%d/%m}",
                "time": f"{tick_time:%H:%M}",
            }
        )

    day_markers: list[dict[str, Any]] = []
    current_day = start.date() + timedelta(days=1)
    while current_day <= end.date():
        marker_time = datetime.combine(current_day, time.min, tzinfo=start.tzinfo)
        if start < marker_time < end:
            ratio = (marker_time - start).total_seconds() / duration
            day_markers.append(
                {
                    "x": round(plot_right - ratio * plot_width, 2),
                    "label": f"{marker_time:%d/%m}",
                }
            )
        current_day += timedelta(days=1)

    def latest_value(field: str) -> float | None:
        return next(
            (
                float(item[field])
                for item in reversed(chronological)
                if item.get(field) is not None
            ),
            None,
        )

    js_points = [
        {
            "x": item["x"],
            "time": datetime.fromisoformat(item["local_time"]).strftime("%d/%m/%Y %H:%M"),
            "temperature": item.get("temperature_c"),
            "humidity": item.get("relative_humidity_percent"),
            "pressure": item.get("pressure_hpa"),
            "precipitation": item.get("precipitation_interval_mm"),
            "radiation": item.get("solar_radiation_wm2"),
            "source_count": item.get("source_count", 0),
        }
        for item in reversed(chronological)
    ]

    return {
        "viewbox_width": 1600,
        "viewbox_height": 310,
        "grid_top": 18,
        "grid_bottom": 265,
        "separator_y": (112, 188, 265),
        "tick_label_y": 286,
        "tick_time_y": 299,
        "plot_left": plot_left,
        "plot_right": plot_right,
        "ticks": ticks,
        "day_markers": day_markers,
        "bands": bands,
        "temperature_segments": _line_segments(
            chronological,
            "temperature_c",
            temperature_range,
            top_1,
            bottom_1,
        ),
        "humidity_segments": _line_segments(
            chronological,
            "relative_humidity_percent",
            humidity_range,
            top_1,
            bottom_1,
        ),
        "pressure_segments": _line_segments(
            chronological,
            "pressure_hpa",
            pressure_range,
            top_2,
            bottom_2,
        ),
        "radiation_segments": _line_segments(
            chronological,
            "solar_radiation_wm2",
            radiation_range,
            top_3,
            bottom_3,
        ),
        "radiation_areas": _area_paths(
            chronological,
            "solar_radiation_wm2",
            radiation_range,
            top_3,
            bottom_3,
        ),
        "precipitation_bars": precipitation_bars,
        "latest": {
            "temperature": latest_value("temperature_c"),
            "humidity": latest_value("relative_humidity_percent"),
            "pressure": latest_value("pressure_hpa"),
            "precipitation": latest_value("precipitation_interval_mm"),
            "radiation": latest_value("solar_radiation_wm2"),
        },
        "ranges": {
            "temperature": temperature_range,
            "humidity": humidity_range,
            "pressure": pressure_range,
            "precipitation": precipitation_range,
            "radiation": radiation_range,
        },
        "points": js_points,
        "start_label": f"{end:%Y-%m-%d %H:%M}",
        "end_label": f"{start:%Y-%m-%d %H:%M}",
    }

def render_public_site(
    *,
    node: NodeConfig,
    network: SonantiaNetworkConfig,
    operator_message: OperatorMessage,
    weather: Weather,
    current_weather: Weather | None = None,
    feed: dict[str, Any],
    own_messages: list[dict[str, Any]],
    archive_index: dict[str, Any],
    interactions: dict[str, Any],
    relays: dict[str, dict[str, Any]],
    peer_status: list[dict[str, Any]],
    economy_snapshot: dict[str, Any] | None = None,
    weather_source_snapshots: dict[str, dict[str, Any]] | None = None,
    geology_snapshot: dict[str, Any] | None = None,
    astronomy_snapshot: dict[str, Any] | None = None,
    status: dict[str, Any],
    public_dir: Path,
    template_dir: Path,
    moment: datetime,
) -> list[str]:
    environment = template_environment(template_dir)
    weather_measurements = _weather_measurements(
        own_messages,
        node,
        current_weather=current_weather or weather,
    )
    source_snapshots = weather_source_snapshots or {}
    weather_source_cards = [
        snapshot
        for snapshot in source_snapshots.values()
        if isinstance(snapshot, dict)
    ]

    common = {
        "node": node,
        "network": network,
        "peers": [item for item in network.nodes if item.node_id != node.node_id],
        "weather": weather,
        "feed": feed,
        "status": status,
        "archive_index": archive_index,
        "interactions": interactions,
        "economy": economy_snapshot,
        "weather_source_cards": weather_source_cards,
        "current_condition": _current_condition_snapshot(weather, node),
        "geology": geology_snapshot,
        "astronomy": astronomy_snapshot,
        "updated_at": isoformat_utc(moment),
        "root_prefix": "",
        "peer_cards": _peer_cards(
            network,
            node.node_id,
            peer_status,
            relays,
            node.logical_location.timezone,
        ),
    }
    pages = {
        "index.html": (
            "index.html.j2",
            {
                **common,
                "weather_measurements": weather_measurements,
                "weather_table_measurements": weather_measurements[:12],
                "weather_chart": _weather_composite_chart(weather_measurements),
            },
        ),
        "sonantia.html": ("sonantia.html.j2", common),
    }
    for relative_path, (template_name, context) in pages.items():
        rendered = environment.get_template(template_name).render(**context)
        _write_text(public_dir / relative_path, rendered)

    for stale_page in (
        public_dir / "month.html",
        public_dir / "interactions.html",
        public_dir / "archive" / "index.html",
        public_dir / "status.json",
    ):
        stale_page.unlink(missing_ok=True)

    atomic_write_json(public_dir / "node.json", node.model_dump(mode="json"))
    atomic_write_json(
        public_dir / "operator-message.json",
        operator_message.model_dump(mode="json"),
    )
    return list(pages)
