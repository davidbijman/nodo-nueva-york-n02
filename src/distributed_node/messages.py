"""Composición determinista y factorada de mensajes públicos."""

from __future__ import annotations

import hashlib
import math
import random
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from .message_catalog import PLACEHOLDER_PATTERN, CatalogPhrase, CompiledMessageCatalog
from .models import (
    CatalogValue,
    DeclarationFamily,
    GeneratorSelectedValue,
    NodeConfig,
    OpeningFamily,
    Weather,
    WeatherPairProfile,
)
from .sonantia_protocol import isoformat_utc
from .weather import condition_key

TERMINAL_PUNCTUATION = (".", "!", "?")
CONDITION_LABELS = {
    "clear-sky": "cielo despejado",
    "mainly-clear": "cielo mayormente despejado",
    "partly-cloudy": "cielo parcialmente nublado",
    "overcast": "cielo cubierto",
    "fog": "niebla",
    "rime-fog": "niebla con escarcha",
    "light-drizzle": "llovizna ligera",
    "moderate-drizzle": "llovizna moderada",
    "dense-drizzle": "llovizna intensa",
    "light-freezing-drizzle": "llovizna helada ligera",
    "dense-freezing-drizzle": "llovizna helada intensa",
    "slight-rain": "lluvia ligera",
    "moderate-rain": "lluvia moderada",
    "heavy-rain": "lluvia intensa",
    "light-freezing-rain": "lluvia helada ligera",
    "heavy-freezing-rain": "lluvia helada intensa",
    "slight-rain-showers": "chubascos ligeros",
    "moderate-rain-showers": "chubascos moderados",
    "violent-rain-showers": "chubascos intensos",
    "slight-snowfall": "nevada ligera",
    "moderate-snowfall": "nevada moderada",
    "heavy-snowfall": "nevada intensa",
    "snow-grains": "granos de nieve",
    "slight-snow-showers": "chubascos de nieve ligeros",
    "moderate-snow-showers": "chubascos de nieve moderados",
    "heavy-snow-showers": "chubascos de nieve intensos",
    "thunderstorm": "tormenta",
    "thunderstorm-with-slight-hail": "tormenta con granizo ligero",
    "thunderstorm-with-heavy-hail": "tormenta con granizo intenso",
}


RAIN_CONDITIONS = {
    key
    for key in CONDITION_LABELS
    if any(token in key for token in ("rain", "drizzle", "thunderstorm"))
}
CLEAR_CONDITIONS = {"clear-sky", "mainly-clear"}
CLOUDY_CONDITIONS = {"partly-cloudy", "overcast", "fog", "rime-fog"}


def _weather_condition_group(condition: str | None) -> str | None:
    if condition in RAIN_CONDITIONS:
        return "rain"
    if condition in CLEAR_CONDITIONS:
        return "clear"
    if condition in CLOUDY_CONDITIONS:
        return "cloudy"
    return None


def _temperature_band(value: Any) -> str | None:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return None
    if value < 10:
        return "cold"
    if value < 18:
        return "mild"
    if value < 27:
        return "warm"
    return "hot"


def _humidity_band(value: Any) -> str | None:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return None
    if value < 40:
        return "dry"
    if value >= 75:
        return "humid"
    return None


def _solar_radiation_band(value: Any) -> str | None:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return None
    return "high" if value >= 600 else None


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1 - a)))


def _astronomy_features(
    snapshot: dict[str, Any] | None,
    local_datetime: datetime,
) -> dict[str, Any]:
    document = snapshot or {}
    targets = document.get("targets") if isinstance(document.get("targets"), list) else []
    available = [
        item for item in targets if isinstance(item, dict) and item.get("status") == "available"
    ]
    by_name = {str(item.get("name")): item for item in available}
    sun = by_name.get("Sol", {})
    moon = by_name.get("Luna", {})
    sun_alt = (
        sun.get("elevation_deg") if isinstance(sun.get("elevation_deg"), int | float) else None
    )
    moon_alt = (
        moon.get("elevation_deg") if isinstance(moon.get("elevation_deg"), int | float) else None
    )
    phase = None
    if sun_alt is not None and -6 <= sun_alt < 6:
        phase = "sunrise" if local_datetime.hour < 12 else "sunset"
    elif sun_alt is not None and sun_alt < -6:
        phase = "night"
    elif sun_alt is not None:
        phase = "day"
    return {
        "status": document.get("status", "unavailable"),
        "provider": document.get("provider", "nasa-horizons"),
        "phase": phase,
        "sun_altitude_deg": sun_alt,
        "moon_altitude_deg": moon_alt,
        "moon_visible": moon_alt is not None and moon_alt >= 0,
        "available_count": len(available),
    }


def _geology_features(
    node: NodeConfig,
    snapshot: dict[str, Any] | None,
    moment: datetime,
) -> dict[str, Any]:
    document = snapshot or {}
    events = document.get("events") if isinstance(document.get("events"), list) else []
    ranked: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        magnitude = event.get("magnitude")
        if not isinstance(magnitude, int | float) or isinstance(magnitude, bool):
            continue
        occurred = _parse_iso_datetime(event.get("occurred_at"))
        age_hours = (
            max(
                0.0,
                (moment.astimezone(UTC) - occurred.astimezone(UTC)).total_seconds() / 3600,
            )
            if occurred
            else 24.0
        )
        lat, lon = event.get("latitude"), event.get("longitude")
        distance_km = None
        if isinstance(lat, int | float) and isinstance(lon, int | float):
            distance_km = _haversine_km(
                node.logical_location.latitude,
                node.logical_location.longitude,
                float(lat),
                float(lon),
            )
        # Magnitud domina el ranking; recencia y distancia sólo desempatan/modulan.
        recency_bonus = max(0.0, 3.0 * (1.0 - min(age_hours, 24.0) / 24.0))
        distance_bonus = (
            0.0
            if distance_km is None
            else max(0.0, 2.0 * (1.0 - min(distance_km, 1000.0) / 1000.0))
        )
        priority = float(magnitude) * 100.0 + recency_bonus + distance_bonus
        ranked.append(
            {
                **event,
                "age_hours": age_hours,
                "distance_km": distance_km,
                "priority": priority,
            }
        )
    ranked.sort(key=lambda item: item["priority"], reverse=True)
    event = ranked[0] if ranked else None
    magnitude = float(event["magnitude"]) if event else None
    mandatory = magnitude is not None and magnitude >= 5.0
    if magnitude is None:
        relevance = 0.0
    elif magnitude >= 5.0:
        relevance = 100.0 + magnitude
    elif magnitude >= 4.0:
        # Crecimiento fuerte y continuo dentro de M4.x.
        relevance = 3.0 + (magnitude - 4.0) * 6.0
        relevance += max(0.0, 1.5 * (1.0 - min(float(event["age_hours"]), 24.0) / 24.0))
        if event.get("distance_km") is not None:
            relevance += max(0.0, 1.0 * (1.0 - min(float(event["distance_km"]), 1000.0) / 1000.0))
    else:
        relevance = max(0.2, magnitude / 5.0)
    return {
        "status": document.get("status", "unavailable"),
        "provider": document.get("provider", "geology"),
        "event_count": len(ranked),
        "priority_event": event,
        "magnitude": magnitude,
        "mandatory": mandatory,
        "relevance": relevance,
    }


def _economy_features(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    document = snapshot or {}
    indicators = document.get("indicators") if isinstance(document.get("indicators"), list) else []
    inflation = document.get("inflation") if isinstance(document.get("inflation"), list) else []
    usable = [
        item
        for item in [*indicators, *inflation]
        if isinstance(item, dict) and item.get("value") not in {None, "", "—"}
    ]
    return {
        "status": document.get("status", "unavailable"),
        "provider": document.get("provider", "economy"),
        "indicator_count": len(usable),
        "date": document.get("date"),
    }


class CatalogGenerationError(ValueError):
    """La configuración es válida, pero no permite construir un mensaje."""


@dataclass(frozen=True)
class ObservationFact:
    fact_id: str
    text: str


@dataclass(frozen=True)
class SourceContribution:
    """Aporte factual ya normalizado por un adaptador confiable."""

    source_id: str
    provider: str
    facts: tuple[ObservationFact, ...]
    fact_count: int = 2


@dataclass(frozen=True)
class Composition:
    text: str
    group_id: str
    selected_values: list[GeneratorSelectedValue]
    next_phrase_cursor: dict[str, Any] | None = None
    affirmation_text: str | None = None


def build_cycle_context(
    node: NodeConfig,
    weather: Weather,
    moment: datetime,
    sequence: int,
    recipient_names: list[str] | None = None,
    *,
    astronomy_snapshot: dict[str, Any] | None = None,
    geology_snapshot: dict[str, Any] | None = None,
    economy_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    local_datetime = moment.astimezone(ZoneInfo(node.logical_location.timezone))
    if 5 <= local_datetime.hour < 12:
        salutation, day_period = "Buenos días", "mañana"
    elif 12 <= local_datetime.hour < 20:
        salutation, day_period = "Buenas tardes", "tarde"
    else:
        salutation, day_period = "Buenas noches", "noche"
    names = recipient_names or []
    recipient_name = names[(sequence - 1) % len(names)] if names else None
    weather_data = weather.data.model_dump(mode="json")
    condition_code = condition_key(weather_data.get("condition_code"))
    weather_data["condition_label"] = (
        CONDITION_LABELS.get(condition_code, condition_code.replace("-", " "))
        if condition_code
        else None
    )
    return {
        "node": {
            "node_id": node.node_id,
            "display_name": node.display_name,
            "city": node.logical_location.city,
            "country": node.logical_location.country,
            "timezone": node.logical_location.timezone,
        },
        "time": {
            "utc": isoformat_utc(moment),
            "local": local_datetime.isoformat(timespec="seconds"),
            "local_hour": local_datetime.hour,
            "sequence": sequence,
            "salutation": salutation,
            "day_period": day_period,
        },
        "weather": {
            "status": weather.status,
            "provider": weather.provider,
            "data": weather_data,
            "precipitation_mm": weather_data.get("precipitation_mm"),
            "condition_group": _weather_condition_group(condition_code),
            "temperature_band": _temperature_band(weather_data.get("temperature_c")),
            "humidity_band": _humidity_band(weather_data.get("relative_humidity_percent")),
            "solar_radiation_band": _solar_radiation_band(weather_data.get("solar_radiation_wm2")),
        },
        "astronomy": _astronomy_features(astronomy_snapshot, local_datetime),
        "geology": _geology_features(node, geology_snapshot, moment),
        "economy": _economy_features(economy_snapshot),
        "recipient": {"name": recipient_name},
    }


def resolve_context_path(context: dict[str, Any], path: str) -> Any:
    current: Any = context
    for segment in path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return None
        current = current[segment]
    return current


def _render_value(value: Any) -> str:
    if isinstance(value, float):
        return str(value).replace(".", ",")
    return str(value)


def _render_fragment(template: str, context: dict[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        value = resolve_context_path(context, match.group(1))
        if value is None:
            raise CatalogGenerationError(f"Falta la ruta {match.group(1)}")
        return _render_value(value)

    rendered = PLACEHOLDER_PATTERN.sub(replace, template)
    rendered = re.sub(r"\s+", " ", rendered).strip()
    return re.sub(r"\s+([,.;:!?])", r"\1", rendered)


def _as_sentence(text: str) -> str:
    cleaned = text.strip()
    if cleaned and not cleaned.endswith(TERMINAL_PUNCTUATION):
        return f"{cleaned}."
    return cleaned


def render_catalog_text(template: str, context: dict[str, Any]) -> str:
    """Renderiza una plantilla segura como oración completa."""
    return _as_sentence(_render_fragment(template, context))


def _is_compatible(value: CatalogValue, context: dict[str, Any]) -> bool:
    referenced = set(value.requires) | set(PLACEHOLDER_PATTERN.findall(value.text))
    return value.enabled and all(
        resolve_context_path(context, path) is not None for path in referenced
    )


def _weighted_choice[T](
    values: list[T],
    weights: list[int],
    generator: random.Random,
) -> T:
    if not values:
        raise CatalogGenerationError("No existen alternativas compatibles")
    return generator.choices(values, weights=weights, k=1)[0]


def _choose_value(
    values: list[CatalogValue],
    context: dict[str, Any],
    generator: random.Random,
) -> CatalogValue:
    compatible = [value for value in values if _is_compatible(value, context)]
    return _weighted_choice(
        compatible,
        [value.weight for value in compatible],
        generator,
    )


def _matches_condition(context: dict[str, Any], condition: dict[str, Any]) -> bool:
    if not condition:
        return False
    if condition.get("always") is True:
        return True
    alternatives = condition.get("any")
    if isinstance(alternatives, list):
        return any(
            _matches_condition(context, item) for item in alternatives if isinstance(item, dict)
        )
    for path, expected in condition.items():
        if path in {"always", "any"}:
            continue
        actual = resolve_context_path(context, path)
        if isinstance(expected, dict):
            if actual is None:
                return False
            for operator, threshold in expected.items():
                if operator == "gte" and not actual >= threshold:
                    return False
                if operator == "gt" and not actual > threshold:
                    return False
                if operator == "lte" and not actual <= threshold:
                    return False
                if operator == "lt" and not actual < threshold:
                    return False
        elif actual != expected:
            return False
    return True


def _choose_contextual_opening(
    values: list[CatalogValue],
    families: list[OpeningFamily],
    context: dict[str, Any],
    generator: random.Random,
) -> tuple[CatalogValue, list[str]]:
    if not families:
        return _choose_value(values, context, generator), []
    value_by_id = {value.value_id: value for value in values}
    eligible_families = [
        family for family in families if _matches_condition(context, family.eligibility)
    ]
    candidates: list[CatalogValue] = []
    weights: list[int] = []
    candidate_family: dict[str, list[str]] = {}
    for family in eligible_families:
        multiplier = max(1.0, 1.0 + family.priority / 2.0)
        for value_id in family.opening_ids:
            value = value_by_id.get(value_id)
            if value is None or not _is_compatible(value, context):
                continue
            candidate_family.setdefault(value_id, []).append(family.family_id)
            if value not in candidates:
                candidates.append(value)
                weights.append(max(1, round(value.weight * multiplier)))
            else:
                idx = candidates.index(value)
                weights[idx] = max(weights[idx], max(1, round(value.weight * multiplier)))
    if not candidates:
        return _choose_value(values, context, generator), []
    chosen = _weighted_choice(candidates, weights, generator)
    return chosen, candidate_family.get(chosen.value_id, [])


def _active_declaration_family_ids(context: dict[str, Any]) -> set[str]:
    active = {"neutral", "bridge", "present-moment", "observation", "data-driven"}
    hour = resolve_context_path(context, "time.local_hour")
    condition = resolve_context_path(context, "weather.condition_group")
    temp = resolve_context_path(context, "weather.temperature_band")
    humidity = resolve_context_path(context, "weather.humidity_band")
    radiation = resolve_context_path(context, "weather.solar_radiation_band")
    if isinstance(hour, int) and 6 <= hour < 12:
        active |= {"beginning", "renewal", "possibility"}
    if isinstance(hour, int) and (hour >= 22 or hour < 6):
        active |= {"reflective", "calm", "serenity", "memory"}
    if condition == "rain":
        active |= {"change", "curiosity", "adaptation", "present-moment"}
    elif condition == "clear":
        active |= {"calm", "balance", "steadiness"}
    elif condition == "cloudy":
        active |= {"reflective", "observation", "present-moment"}
    if temp in {"cold", "hot"}:
        active |= {"change", "observation", "focus"}
    elif temp == "mild":
        active |= {"calm", "balance"}
    elif temp == "warm":
        active |= {"present-moment", "steadiness"}
    if humidity in {"dry", "humid"} or radiation == "high":
        active |= {"observation", "curiosity", "data-driven"}
    primary_source = resolve_context_path(context, "source_selection.primary")
    secondary_source = resolve_context_path(context, "source_selection.secondary")
    selected_sources = {source for source in (primary_source, secondary_source) if source}
    astronomy_phase = resolve_context_path(context, "astronomy.phase")
    geology_magnitude = resolve_context_path(context, "geology.magnitude")
    if "astronomy" in selected_sources:
        active |= {"reflective", "wonder", "curiosity", "data-to-language"}
        if astronomy_phase == "sunrise":
            active |= {"beginning", "renewal", "hope", "possibility"}
        elif astronomy_phase in {"sunset", "night"}:
            active |= {"calm", "serenity", "memory"}
    if "geology" in selected_sources:
        active |= {"change", "observation", "present-moment", "data-driven"}
        if isinstance(geology_magnitude, int | float) and geology_magnitude >= 5:
            active |= {"focus", "clarity", "resilience"}
    if "economy" in selected_sources:
        active |= {"data-driven", "data-to-language", "change", "reflective"}
    return active


def _choose_contextual_declaration(
    values: list[CatalogValue],
    families: list[DeclarationFamily],
    context: dict[str, Any],
    generator: random.Random,
) -> tuple[CatalogValue, list[str]]:
    if not families:
        return _choose_value(values, context, generator), []
    active = _active_declaration_family_ids(context)
    family_by_id = {family.family_id: family for family in families}
    memberships: dict[str, list[str]] = {}
    biases: dict[str, float] = {}
    for family_id in active:
        family = family_by_id.get(family_id)
        if family is None:
            continue
        for value_id in family.declaration_ids:
            memberships.setdefault(value_id, []).append(family_id)
            current = biases.get(value_id, 1.0)
            # Los matices contextuales se acumulan suavemente. El tope evita
            # que una declaración muy etiquetada monopolice el pool.
            biases[value_id] = min(2.0, current + max(0.0, family.selection_bias - 1.0))
    compatible = [value for value in values if _is_compatible(value, context)]
    weights = [
        max(1, round(value.weight * biases.get(value.value_id, 1.0) * 10)) for value in compatible
    ]
    chosen = _weighted_choice(compatible, weights, generator)
    return chosen, sorted(memberships.get(chosen.value_id, []))


def _weather_contribution(
    weather: Weather,
    context: dict[str, Any],
    fact_count: int,
) -> SourceContribution | None:
    if weather.status != "available":
        return None
    data = context["weather"]["data"]
    definitions = (
        ("temperatura", "temperature_c", "una temperatura de {value} °C"),
        (
            "humedad",
            "relative_humidity_percent",
            "una humedad relativa de {value} %",
        ),
        (
            "lluvia-diaria",
            "precipitation_mm",
            "una lluvia acumulada durante el día de {value} mm",
        ),
        ("presion", "pressure_hpa", "una presión atmosférica de {value} hPa"),
        (
            "radiacion-solar",
            "solar_radiation_wm2",
            "una radiación solar de {value} W/m²",
        ),
    )
    facts = tuple(
        ObservationFact(fact_id, template.format(value=_render_value(data[field])))
        for fact_id, field, template in definitions
        if data.get(field) is not None
    )
    if not facts:
        return None
    return SourceContribution(
        source_id="weather",
        provider=weather.provider,
        facts=facts,
        fact_count=min(fact_count, len(facts)),
    )


def _astronomy_contribution(
    snapshot: dict[str, Any] | None,
    context: dict[str, Any],
) -> SourceContribution | None:
    document = snapshot or {}
    if document.get("status") != "available":
        return None
    targets = document.get("targets") if isinstance(document.get("targets"), list) else []
    by_name = {
        str(item.get("name")): item
        for item in targets
        if isinstance(item, dict) and item.get("status") == "available"
    }
    facts: list[ObservationFact] = []
    sun = by_name.get("Sol")
    moon = by_name.get("Luna")
    if sun and isinstance(sun.get("elevation_deg"), int | float):
        sun_altitude = float(sun["elevation_deg"])
        sun_position = "sobre" if sun_altitude >= 0 else "bajo"
        facts.append(
            ObservationFact(
                "sol-altura",
                f"el Sol a {_render_value(abs(sun_altitude))}° {sun_position} el horizonte local",
            )
        )
    if moon and isinstance(moon.get("elevation_deg"), int | float):
        moon_altitude = float(moon["elevation_deg"])
        visibility = "sobre" if moon_altitude >= 0 else "bajo"
        facts.append(
            ObservationFact(
                "luna-altura",
                f"la Luna a {_render_value(abs(moon_altitude))}° {visibility} el horizonte local",
            )
        )
    if not facts:
        visible = [
            item
            for item in targets
            if isinstance(item, dict)
            and item.get("status") == "available"
            and isinstance(item.get("elevation_deg"), int | float)
            and float(item["elevation_deg"]) >= 0
        ]
        if visible:
            item = visible[0]
            facts.append(
                ObservationFact(
                    "astro-visible",
                    f"{item.get('name', 'un astro')} visible sobre el horizonte",
                )
            )
    if not facts:
        return None
    return SourceContribution(
        "astronomy",
        str(document.get("provider") or "nasa-horizons"),
        tuple(facts),
        1,
    )


def _geology_contribution(
    snapshot: dict[str, Any] | None,
    context: dict[str, Any],
) -> SourceContribution | None:
    event = resolve_context_path(context, "geology.priority_event")
    if not isinstance(event, dict):
        return None
    magnitude = event.get("magnitude")
    if not isinstance(magnitude, int | float):
        return None
    location = str(event.get("location") or "una zona informada por la fuente")
    age = event.get("age_hours")
    distance = event.get("distance_km")
    facts = [
        ObservationFact(
            "sismo-magnitud",
            (
                f"un sismo de magnitud {_render_value(float(magnitude))} "
                f"con ubicación reportada como {location}"
            ),
        )
    ]
    if isinstance(age, int | float):
        facts.append(
            ObservationFact(
                "sismo-antiguedad",
                f"ocurrido hace aproximadamente {_render_value(round(float(age), 1))} horas",
            )
        )
    if isinstance(distance, int | float):
        facts.append(
            ObservationFact(
                "sismo-distancia",
                f"a unos {_render_value(round(float(distance)))} km del nodo",
            )
        )
    return SourceContribution(
        "geology",
        str((snapshot or {}).get("provider") or "geology"),
        tuple(facts),
        min(2, len(facts)),
    )


def _economy_contribution(snapshot: dict[str, Any] | None) -> SourceContribution | None:
    document = snapshot or {}
    if document.get("status") != "available":
        return None
    raw = []
    for key in ("indicators", "inflation"):
        values = document.get(key)
        if isinstance(values, list):
            raw.extend(item for item in values if isinstance(item, dict))
    facts = tuple(
        ObservationFact(
            f"indicador-{index + 1}",
            f"{item.get('label')} con un valor de {item.get('value')}",
        )
        for index, item in enumerate(raw)
        if item.get("label") and item.get("value") not in {None, "", "—"}
    )
    return (
        SourceContribution(
            "economy",
            str(document.get("provider") or "economy"),
            facts,
            1,
        )
        if facts
        else None
    )


@dataclass(frozen=True)
class SourceSelection:
    contributions: list[SourceContribution]
    primary: str
    secondary: str | None
    reason: str
    mandatory: bool
    scores: dict[str, float]


def _source_selection(
    contributions: list[SourceContribution],
    context: dict[str, Any],
    generator: random.Random,
) -> SourceSelection:
    by_id = {item.source_id: item for item in contributions}
    scores: dict[str, float] = {}
    if "weather" in by_id:
        scores["weather"] = 1.0
        if resolve_context_path(context, "weather.condition_group") == "rain":
            scores["weather"] += 0.9
        if resolve_context_path(context, "weather.temperature_band") in {"cold", "hot"}:
            scores["weather"] += 0.5
        if resolve_context_path(context, "weather.solar_radiation_band") == "high":
            scores["weather"] += 0.35
    if "astronomy" in by_id:
        phase = resolve_context_path(context, "astronomy.phase")
        if phase in {"sunrise", "sunset"}:
            scores["astronomy"] = 2.2
        elif phase == "night" and resolve_context_path(context, "astronomy.moon_visible"):
            scores["astronomy"] = 1.45
        else:
            scores["astronomy"] = 0.65
    if "economy" in by_id:
        indicator_count = int(resolve_context_path(context, "economy.indicator_count") or 0)
        scores["economy"] = 0.55 + min(0.45, 0.05 * indicator_count)
    if "geology" in by_id:
        scores["geology"] = float(resolve_context_path(context, "geology.relevance") or 0.0)

    mandatory_geology = (
        bool(resolve_context_path(context, "geology.mandatory")) and "geology" in by_id
    )
    if mandatory_geology:
        primary = "geology"
        reason = "mandatory-seismic-event"
    else:
        eligible = [source_id for source_id, score in scores.items() if score > 0]
        if not eligible:
            if "weather" not in by_id:
                raise CatalogGenerationError("No existen fuentes seleccionables")
            primary, reason = "weather", "weather-fallback"
        else:
            primary = _weighted_choice(
                eligible,
                [max(1, round(scores[source_id] * 100)) for source_id in eligible],
                generator,
            )
            reason = "contextual-relevance"

    secondary = None
    remaining = [
        source_id for source_id in scores if source_id != primary and scores[source_id] >= 1.25
    ]
    if remaining and (mandatory_geology or generator.random() < 0.18):
        secondary = max(remaining, key=lambda source_id: scores[source_id])
    selected_ids = [primary] + ([secondary] if secondary else [])
    return SourceSelection(
        contributions=[by_id[source_id] for source_id in selected_ids if source_id in by_id],
        primary=primary,
        secondary=secondary,
        reason=reason,
        mandatory=mandatory_geology,
        scores=scores,
    )


def _phrase_permutation_index(
    size: int,
    catalog_hash: str,
    round_number: int,
    position: int,
) -> int:
    if size == 1:
        return 0
    digest = hashlib.sha256(f"{catalog_hash}:{round_number}".encode()).digest()
    offset = int.from_bytes(digest[:8], "big") % size
    step = int.from_bytes(digest[8:16], "big") % size or 1
    while math.gcd(step, size) != 1:
        step = (step + 1) % size or 1
    return (offset + position * step) % size


def _select_phrase(
    catalog: CompiledMessageCatalog,
    stored_cursor: dict[str, Any] | None,
) -> tuple[CatalogPhrase, dict[str, Any]]:
    cursor = stored_cursor or {}
    same_catalog = cursor.get("catalog_hash") == catalog.catalog_hash and cursor.get(
        "phrase_count"
    ) == len(catalog.phrases)
    round_number = max(1, int(cursor.get("round", 1))) if same_catalog else 1
    position = max(0, int(cursor.get("position", 0))) if same_catalog else 0
    if position >= len(catalog.phrases):
        round_number += position // len(catalog.phrases)
        position %= len(catalog.phrases)
    index = _phrase_permutation_index(
        len(catalog.phrases),
        catalog.catalog_hash,
        round_number,
        position,
    )
    return catalog.phrases[index], {
        "catalog_hash": catalog.catalog_hash,
        "phrase_count": len(catalog.phrases),
        "round": round_number,
        "position": position + 1,
    }


def _deterministic_generator(
    catalog: CompiledMessageCatalog,
    node: NodeConfig,
    moment: datetime,
    sequence: int,
) -> random.Random:
    material = f"{catalog.catalog_hash}|{node.node_id}|{isoformat_utc(moment)}|{sequence}"
    seed = int.from_bytes(hashlib.sha256(material.encode("utf-8")).digest(), "big")
    return random.Random(seed)


def _inline_affirmation(text: str, recipient_name: str | None) -> str:
    if not text or (recipient_name and text.startswith(recipient_name)):
        return text
    return text[0].lower() + text[1:]


def _soften_repeated_recipient_name(text: str, recipient_name: str | None) -> str:
    if not recipient_name or text.count(recipient_name) <= 1:
        return text
    short_name = recipient_name.split()[0]
    first_seen = False

    def replace(match: re.Match[str]) -> str:
        nonlocal first_seen
        if not first_seen:
            first_seen = True
            return match.group(0)
        return short_name

    return re.sub(re.escape(recipient_name), replace, text)


def _select_weather_pair_profile(
    contribution: SourceContribution,
    profiles: list[WeatherPairProfile],
    generator: random.Random,
) -> tuple[tuple[ObservationFact, ObservationFact], WeatherPairProfile] | None:
    fact_by_id = {fact.fact_id: fact for fact in contribution.facts}
    compatible = [
        profile
        for profile in profiles
        if all(fact_id in fact_by_id for fact_id in profile.fact_ids)
    ]
    if not compatible:
        return None
    profile = _weighted_choice(
        compatible,
        [item.weight for item in compatible],
        generator,
    )
    selected = tuple(fact_by_id[fact_id] for fact_id in profile.fact_ids)
    return (selected[0], selected[1]), profile


def _select_fact_sentence(
    contribution: SourceContribution,
    templates: list[CatalogValue],
    sequence: int,
    context: dict[str, Any],
    generator: random.Random,
    weather_pair_profiles: list[WeatherPairProfile] | None = None,
) -> tuple[str, str, CatalogValue]:
    profile: WeatherPairProfile | None = None
    selected: tuple[ObservationFact, ...]
    facts_text: str

    if contribution.source_id == "weather" and weather_pair_profiles:
        selection = _select_weather_pair_profile(
            contribution,
            weather_pair_profiles,
            generator,
        )
        if selection is None:
            raise CatalogGenerationError(
                "No existe un perfil meteorológico compatible con los hechos disponibles"
            )
        selected, profile = selection
        facts_text = profile.joiner.join(fact.text for fact in selected)
    else:
        count = min(contribution.fact_count, len(contribution.facts))
        if count <= 0 or len(contribution.facts) < count:
            raise CatalogGenerationError(
                f"La fuente {contribution.source_id} no contiene hechos suficientes"
            )
        if contribution.source_id == "geology":
            # La magnitud/localización es el núcleo del hecho sísmico y siempre
            # debe conservarse. Recencia o distancia aportan variedad contextual.
            primary = next(
                (fact for fact in contribution.facts if fact.fact_id == "sismo-magnitud"),
                contribution.facts[0],
            )
            extras = [fact for fact in contribution.facts if fact.fact_id != primary.fact_id]
            selected_list = [primary]
            if count > 1 and extras:
                selected_list.append(generator.choice(extras))
            selected = tuple(selected_list)
            facts_text = selected[0].text
            if len(selected) > 1:
                facts_text += f", {selected[1].text}"
        elif contribution.source_id in {"astronomy", "economy"}:
            # Estas fuentes suelen ofrecer varios hechos equivalentes. Elegirlos
            # determinísticamente evita que Sol o el primer indicador monopolicen
            # la salida sólo por ocupar la primera posición del proveedor.
            selected = tuple(generator.sample(list(contribution.facts), k=count))
            facts_text = " y ".join(fact.text for fact in selected)
        else:
            selected = tuple(contribution.facts[:count])
            facts_text = " y ".join(fact.text for fact in selected)

    source_context = {
        **context,
        "source": {
            "id": contribution.source_id,
            "provider": contribution.provider,
            "facts": facts_text,
        },
    }
    template = _choose_value(templates, source_context, generator)
    if profile is not None:
        fact_ids = profile.pair_profile_id
    else:
        fact_ids = "-".join(fact.fact_id for fact in selected)
    return render_catalog_text(template.text, source_context), fact_ids, template


def _compose_contextual(
    catalog: CompiledMessageCatalog,
    context: dict[str, Any],
    contributions: list[SourceContribution],
    sequence: int,
    stored_cursor: dict[str, Any] | None,
    generator: random.Random,
) -> Composition:
    recipient_name = resolve_context_path(context, "recipient.name")
    if not recipient_name:
        raise CatalogGenerationError("No existe un destinatario configurado")

    definition = catalog.definition
    source_config = {
        source.source_id: source for source in definition.source_templates if source.enabled
    }
    contribution_by_id = {source.source_id: source for source in contributions}
    observations: list[str] = []
    selected_values: list[GeneratorSelectedValue] = []
    ordered_source_ids = [
        contribution.source_id
        for contribution in contributions
        if contribution.source_id in definition.policy.source_order
    ]
    for source_id in ordered_source_ids:
        if len(observations) >= definition.policy.max_source_sections:
            break
        contribution = contribution_by_id.get(source_id)
        templates = source_config.get(source_id)
        if contribution is None or templates is None:
            continue
        sentence, fact_ids, template = _select_fact_sentence(
            contribution,
            templates.templates,
            sequence,
            context,
            generator,
            definition.weather_pair_profiles if source_id == "weather" else None,
        )
        observations.append(sentence)
        selected_values.append(
            GeneratorSelectedValue(
                part_id=f"source-{source_id}",
                value_id=f"{template.value_id}-{fact_ids}",
            )
        )
    if not observations:
        raise CatalogGenerationError("No existen fuentes factuales disponibles")

    opening, opening_families = _choose_contextual_opening(
        definition.openings, definition.opening_families, context, generator
    )
    declaration, declaration_families = _choose_contextual_declaration(
        definition.declarations, definition.declaration_families, context, generator
    )
    phrase, next_cursor = _select_phrase(catalog, stored_cursor)
    opening_text = render_catalog_text(opening.text, context)
    declaration_text = _render_fragment(declaration.text, context)
    standalone_affirmation = _as_sentence(
        _soften_repeated_recipient_name(
            _render_fragment(phrase.text, context),
            str(recipient_name),
        )
    )
    affirmation = _inline_affirmation(
        standalone_affirmation,
        str(recipient_name),
    )
    closing = _as_sentence(f"{declaration_text} {affirmation}")
    text = " ".join([opening_text, *observations, closing])
    if len(text) > definition.policy.max_message_characters:
        raise CatalogGenerationError("El mensaje supera el límite configurado")
    selected_values[:0] = [
        GeneratorSelectedValue(part_id="opening", value_id=opening.value_id),
        *[
            GeneratorSelectedValue(part_id="opening-family", value_id=family_id)
            for family_id in opening_families
        ],
    ]
    selected_values.extend(
        [
            GeneratorSelectedValue(
                part_id="declaration",
                value_id=declaration.value_id,
            ),
            *[
                GeneratorSelectedValue(part_id="declaration-family", value_id=family_id)
                for family_id in declaration_families
            ],
            GeneratorSelectedValue(
                part_id="affirmation",
                value_id=phrase.value_id,
            ),
        ]
    )
    return Composition(
        text=text,
        group_id="contextual",
        selected_values=selected_values,
        next_phrase_cursor=next_cursor,
        affirmation_text=standalone_affirmation,
    )


def _compose_fallback(
    catalog: CompiledMessageCatalog,
    context: dict[str, Any],
    generator: random.Random,
) -> Composition:
    fallback = _choose_value(
        catalog.definition.fallback_messages,
        context,
        generator,
    )
    return Composition(
        text=render_catalog_text(fallback.text, context),
        group_id="general-fallback",
        selected_values=[GeneratorSelectedValue(part_id="fallback", value_id=fallback.value_id)],
    )


def compose_catalog_message(
    catalog: CompiledMessageCatalog,
    context: dict[str, Any],
    contributions: list[SourceContribution],
    sequence: int,
    stored_cursor: dict[str, Any] | None,
    generator: random.Random,
) -> Composition:
    try:
        return _compose_contextual(
            catalog,
            context,
            contributions,
            sequence,
            stored_cursor,
            generator,
        )
    except CatalogGenerationError:
        return _compose_fallback(catalog, context, generator)


@dataclass(frozen=True, slots=True)
class GeneratedText:
    """Texto y metadatos listos para un mensaje canónico Sonantia."""

    text: str
    language: str
    generator: dict[str, Any]
    next_phrase_cursor: dict[str, Any] | None
    fallback: bool = False


def _append_trailing_reference(
    text: str,
    trailing_reference: str | None,
    *,
    max_length: int,
) -> str:
    if not trailing_reference:
        return text
    candidate = f"{text.rstrip()} {trailing_reference}"
    if len(candidate) > max_length:
        return text
    return candidate


def generate_sonantia_text(
    node: NodeConfig,
    catalog: CompiledMessageCatalog | None,
    weather: Weather,
    moment: datetime,
    sequence: int,
    *,
    phrase_cursor: dict[str, Any] | None = None,
    rng: random.Random | None = None,
    additional_sources: list[SourceContribution] | None = None,
    astronomy_snapshot: dict[str, Any] | None = None,
    geology_snapshot: dict[str, Any] | None = None,
    economy_snapshot: dict[str, Any] | None = None,
    trailing_reference: str | None = None,
) -> GeneratedText:
    """Compone directamente el contenido de un mensaje Sonantia 1.0.

    La función no reserva secuencias ni escribe estado. Esas responsabilidades
    pertenecen a ``SonantiaStorage`` y al ciclo transaccional del nodo.
    """
    context = build_cycle_context(
        node,
        weather,
        moment,
        sequence,
        catalog.recipient_names if catalog is not None else None,
        astronomy_snapshot=astronomy_snapshot,
        geology_snapshot=geology_snapshot,
        economy_snapshot=economy_snapshot,
    )
    composition: Composition | None = None
    if catalog is not None:
        generator = rng or _deterministic_generator(catalog, node, moment, sequence)
        contributions = list(additional_sources or [])
        weather_contribution = _weather_contribution(
            weather,
            context,
            catalog.definition.policy.weather_fact_count,
        )
        if weather_contribution is not None:
            contributions.append(weather_contribution)
        for contribution in (
            _astronomy_contribution(astronomy_snapshot, context),
            _geology_contribution(geology_snapshot, context),
            _economy_contribution(economy_snapshot),
        ):
            existing_source_ids = {item.source_id for item in contributions}
            if contribution is not None and contribution.source_id not in existing_source_ids:
                contributions.append(contribution)
        try:
            selection = _source_selection(contributions, context, generator)
            context["source_selection"] = {
                "primary": selection.primary,
                "secondary": selection.secondary,
                "reason": selection.reason,
                "mandatory": selection.mandatory,
                "scores": selection.scores,
            }
            composition = compose_catalog_message(
                catalog,
                context,
                selection.contributions,
                sequence,
                phrase_cursor,
                generator,
            )
        except CatalogGenerationError:
            composition = None

    if composition is None:
        fallback_text = (
            f"El nodo {node.node_id} completó su ciclo, "
            "pero no pudo generar un mensaje desde su catálogo."
        )
        content_text = _append_trailing_reference(
            fallback_text,
            trailing_reference,
            max_length=10_000,
        )
        return GeneratedText(
            text=content_text,
            language="es",
            generator={
                "generator_id": "sonantia-fallback",
                "catalog_version": "fallback",
                "catalog_hash": None,
                "group_id": "system-fallback",
                "selection_policy": "fallback",
                "selected_values": [],
            },
            next_phrase_cursor=phrase_cursor,
            fallback=True,
        )

    content_text = _append_trailing_reference(
        composition.text,
        trailing_reference,
        max_length=catalog.definition.policy.max_message_characters,
    )
    selected_values = [
        {"part_id": item.part_id, "value_id": item.value_id} for item in composition.selected_values
    ]
    selection_context = context.get("source_selection", {})
    if selection_context:
        selected_values.append(
            {
                "part_id": "source-selection",
                "value_id": str(selection_context.get("primary") or "weather")
                + ("-mandatory" if selection_context.get("mandatory") else ""),
            }
        )
        if selection_context.get("secondary"):
            selected_values.append(
                {
                    "part_id": "source-secondary",
                    "value_id": str(selection_context["secondary"]),
                }
            )
    if content_text != composition.text:
        selected_values.append({"part_id": "reference-note", "value_id": "astronomy-seismic"})
    return GeneratedText(
        text=content_text,
        language=catalog.definition.default_language,
        generator={
            "generator_id": catalog.generator_id,
            "catalog_version": catalog.catalog_version,
            "catalog_hash": catalog.catalog_hash,
            "group_id": composition.group_id,
            "selection_policy": catalog.definition.selection_policy,
            "selected_values": selected_values,
            "affirmation_text": composition.affirmation_text,
        },
        next_phrase_cursor=composition.next_phrase_cursor,
        fallback=False,
    )
