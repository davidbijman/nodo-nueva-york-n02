"""Composición determinista y factorada de mensajes públicos."""

from __future__ import annotations

import hashlib
import math
import random
import re
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations
from typing import Any
from zoneinfo import ZoneInfo

from .message_catalog import PLACEHOLDER_PATTERN, CatalogPhrase, CompiledMessageCatalog
from .models import (
    CatalogValue,
    GeneratorSelectedValue,
    NodeConfig,
    Weather,
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
            "sequence": sequence,
            "salutation": salutation,
            "day_period": day_period,
        },
        "weather": {
            "status": weather.status,
            "provider": weather.provider,
            "data": weather_data,
        },
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
    same_catalog = (
        cursor.get("catalog_hash") == catalog.catalog_hash
        and cursor.get("phrase_count") == len(catalog.phrases)
    )
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
    material = (
        f"{catalog.catalog_hash}|{node.node_id}|"
        f"{isoformat_utc(moment)}|{sequence}"
    )
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


def _select_fact_sentence(
    contribution: SourceContribution,
    templates: list[CatalogValue],
    sequence: int,
    context: dict[str, Any],
    generator: random.Random,
) -> tuple[str, str, CatalogValue]:
    count = min(contribution.fact_count, len(contribution.facts))
    choices = list(combinations(contribution.facts, count))
    if not choices:
        raise CatalogGenerationError(
            f"La fuente {contribution.source_id} no contiene hechos suficientes"
        )
    selected = choices[(sequence - 1) % len(choices)]
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
        source.source_id: source
        for source in definition.source_templates
        if source.enabled
    }
    contribution_by_id = {source.source_id: source for source in contributions}
    observations: list[str] = []
    selected_values: list[GeneratorSelectedValue] = []
    for source_id in definition.policy.source_order:
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

    opening = _choose_value(definition.openings, context, generator)
    declaration = _choose_value(definition.declarations, context, generator)
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
        GeneratorSelectedValue(part_id="opening", value_id=opening.value_id)
    ]
    selected_values.extend(
        [
            GeneratorSelectedValue(
                part_id="declaration",
                value_id=declaration.value_id,
            ),
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
        selected_values=[
            GeneratorSelectedValue(part_id="fallback", value_id=fallback.value_id)
        ],
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
        try:
            composition = compose_catalog_message(
                catalog,
                context,
                contributions,
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
        {"part_id": item.part_id, "value_id": item.value_id}
        for item in composition.selected_values
    ]
    if content_text != composition.text:
        selected_values.append(
            {"part_id": "reference-note", "value_id": "astronomy-seismic"}
        )
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
