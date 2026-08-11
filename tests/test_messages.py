from datetime import UTC, datetime

from distributed_node.config import load_message_catalog
from distributed_node.messages import generate_sonantia_text
from distributed_node.models import Weather
from tests.factories import ROOT, available_weather, make_node, node_from_profile


def test_native_message_generation_is_deterministic_and_advances_phrase_cursor() -> None:
    node = node_from_profile("n02")
    catalog = load_message_catalog(ROOT / "config")
    moment = datetime(2026, 8, 1, 18, 0, tzinfo=UTC)
    weather = available_weather()

    first = generate_sonantia_text(node, catalog, weather, moment, 1)
    repeated = generate_sonantia_text(node, catalog, weather, moment, 1)
    second = generate_sonantia_text(
        node,
        catalog,
        weather,
        moment,
        2,
        phrase_cursor=first.next_phrase_cursor,
    )

    assert first.text == repeated.text
    assert "Nueva York" in first.text
    assert first.generator["generator_id"] == catalog.generator_id
    assert first.next_phrase_cursor is not None
    assert second.next_phrase_cursor != first.next_phrase_cursor


def test_native_message_generation_has_safe_fallback_without_catalog() -> None:
    generated = generate_sonantia_text(
        make_node(),
        None,
        Weather(),
        datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
        1,
    )
    assert generated.fallback is True
    assert generated.text.strip()
    assert generated.generator["generator_id"] == "sonantia-fallback"


def test_generator_exposes_clean_standalone_affirmation() -> None:
    node = node_from_profile("n01")
    catalog = load_message_catalog(ROOT / "config")
    generated = generate_sonantia_text(
        node,
        catalog,
        available_weather(),
        datetime(2026, 8, 4, 14, 0, tzinfo=UTC),
        1,
    )
    affirmation = generated.generator.get("affirmation_text")
    assert isinstance(affirmation, str)
    assert affirmation.endswith((".", "!", "?"))
    assert "{{" not in affirmation
    assert generated.generator["generator_id"] == catalog.generator_id


def test_extended_catalog_components_are_present_and_unique() -> None:
    catalog = load_message_catalog(ROOT / "config")

    assert catalog.definition.openings
    assert catalog.definition.declarations
    assert catalog.definition.weather_pair_profiles
    assert catalog.phrases

    assert len({item.value_id for item in catalog.definition.openings}) == len(
        catalog.definition.openings
    )
    assert len({item.value_id for item in catalog.definition.declarations}) == len(
        catalog.definition.declarations
    )
    assert len({phrase.value_id for phrase in catalog.phrases}) == len(catalog.phrases)

    weather_templates = next(
        source.templates
        for source in catalog.definition.source_templates
        if source.source_id == "weather"
    )
    assert weather_templates
    pair_ids = [profile.pair_profile_id for profile in catalog.definition.weather_pair_profiles]
    assert len(set(pair_ids)) == len(pair_ids)


def test_weather_generation_records_selected_pair_profile() -> None:
    node = node_from_profile("n01")
    catalog = load_message_catalog(ROOT / "config")
    generated = generate_sonantia_text(
        node,
        catalog,
        available_weather(),
        datetime(2026, 8, 4, 14, 0, tzinfo=UTC),
        1,
    )

    weather_selection = next(
        item
        for item in generated.generator["selected_values"]
        if item["part_id"] == "source-weather"
    )
    assert "meteo-pair-" in weather_selection["value_id"]


def test_delivery2_contextual_families_are_loaded_and_executed() -> None:
    node = node_from_profile("n01")
    catalog = load_message_catalog(ROOT / "config")

    assert catalog.definition.opening_families
    assert catalog.definition.declaration_families
    assert any(family.family_id == "general" for family in catalog.definition.opening_families)
    assert any(family.family_id == "neutral" for family in catalog.definition.declaration_families)

    generated = generate_sonantia_text(
        node,
        catalog,
        available_weather(),
        datetime(2026, 8, 4, 18, 0, tzinfo=UTC),
        7,
    )
    selected = generated.generator["selected_values"]
    assert any(item["part_id"] == "opening-family" for item in selected)
    assert any(item["part_id"] == "declaration-family" for item in selected)


def test_delivery2_contextual_selection_remains_deterministic() -> None:
    node = node_from_profile("n01")
    catalog = load_message_catalog(ROOT / "config")
    weather = available_weather()
    moment = datetime(2026, 8, 4, 23, 0, tzinfo=UTC)

    first = generate_sonantia_text(node, catalog, weather, moment, 33)
    second = generate_sonantia_text(node, catalog, weather, moment, 33)
    assert first.text == second.text
    assert first.generator["selected_values"] == second.generator["selected_values"]


def _astronomy_snapshot(*, sun_altitude: float, moon_altitude: float = -20.0) -> dict:
    return {
        "status": "available",
        "provider": "nasa-horizons",
        "targets": [
            {"name": "Sol", "status": "available", "elevation_deg": sun_altitude},
            {"name": "Luna", "status": "available", "elevation_deg": moon_altitude},
        ],
    }


def _geology_snapshot(events: list[dict]) -> dict:
    return {"status": "available", "provider": "usgs-earthquakes", "events": events}


def _economy_snapshot() -> dict:
    return {
        "status": "available",
        "provider": "us-economic-data",
        "indicators": [
            {"label": "Euro (USD por EUR)", "value": "1,1650", "group": "general"},
            {"label": "Dólar observado", "value": "$945,30", "group": "general"},
        ],
        "inflation": [],
    }


def test_delivery3_magnitude_five_earthquake_is_mandatory() -> None:
    node = node_from_profile("n01")
    catalog = load_message_catalog(ROOT / "config")
    moment = datetime(2026, 8, 10, 18, 0, tzinfo=UTC)
    geology = _geology_snapshot(
        [
            {
                "event_id": "m5",
                "occurred_at": "2026-08-10T17:40:00Z",
                "location": "Región de Coquimbo",
                "magnitude": 5.2,
                "latitude": -30.0,
                "longitude": -71.0,
            }
        ]
    )
    generated = generate_sonantia_text(
        node,
        catalog,
        available_weather(),
        moment,
        40,
        geology_snapshot=geology,
        astronomy_snapshot=_astronomy_snapshot(sun_altitude=25),
        economy_snapshot=_economy_snapshot(),
    )
    selected = generated.generator["selected_values"]
    assert any(item["part_id"] == "source-geology" for item in selected)
    assert {"part_id": "source-selection", "value_id": "geology-mandatory"} in selected
    assert "5,2" in generated.text


def test_delivery3_seismic_priority_prefers_magnitude_over_recency() -> None:
    from distributed_node.messages import build_cycle_context

    node = node_from_profile("n01")
    moment = datetime(2026, 8, 10, 18, 0, tzinfo=UTC)
    geology = _geology_snapshot(
        [
            {
                "event_id": "recent-smaller",
                "occurred_at": "2026-08-10T17:58:00Z",
                "location": "cerca",
                "magnitude": 4.3,
                "latitude": -33.5,
                "longitude": -70.7,
            },
            {
                "event_id": "older-larger",
                "occurred_at": "2026-08-10T10:00:00Z",
                "location": "más lejos",
                "magnitude": 4.9,
                "latitude": -35.0,
                "longitude": -72.0,
            },
        ]
    )
    context = build_cycle_context(
        node,
        available_weather(),
        moment,
        1,
        ["David"],
        geology_snapshot=geology,
    )
    assert context["geology"]["priority_event"]["event_id"] == "older-larger"
    assert context["geology"]["relevance"] > 0


def test_delivery3_context_exposes_multisource_data() -> None:
    from distributed_node.messages import build_cycle_context

    node = node_from_profile("n01")
    context = build_cycle_context(
        node,
        available_weather(),
        datetime(2026, 8, 10, 22, 0, tzinfo=UTC),
        1,
        ["David"],
        astronomy_snapshot=_astronomy_snapshot(sun_altitude=-1.0, moon_altitude=35.0),
        geology_snapshot=_geology_snapshot([]),
        economy_snapshot=_economy_snapshot(),
    )

    assert context["weather"]["status"] == "available"
    assert context["astronomy"]["status"] == "available"
    assert context["economy"]["status"] == "available"
    assert context["astronomy"]["phase"] in {"sunrise", "sunset", "day", "night"}
    assert context["economy"]["indicator_count"] > 0


def test_delivery3_multisource_catalog_has_all_source_templates() -> None:
    catalog = load_message_catalog(ROOT / "config")
    source_ids = {source.source_id for source in catalog.definition.source_templates}
    assert {"weather", "astronomy", "geology", "economy"} <= source_ids
    assert catalog.definition.policy.max_source_sections >= 1


def test_delivery4_external_source_degradation_keeps_weather_available() -> None:
    node = node_from_profile("n01")
    catalog = load_message_catalog(ROOT / "config")
    unavailable_astronomy = {"status": "unavailable", "provider": "nasa-horizons", "targets": []}
    unavailable_geology = {"status": "unavailable", "provider": "usgs-earthquakes", "events": []}
    unavailable_economy = {
        "status": "unavailable",
        "provider": "us-economic-data",
        "indicators": [],
        "inflation": [],
    }

    generated = generate_sonantia_text(
        node,
        catalog,
        available_weather(),
        datetime(2026, 8, 10, 18, 0, tzinfo=UTC),
        91,
        astronomy_snapshot=unavailable_astronomy,
        geology_snapshot=unavailable_geology,
        economy_snapshot=unavailable_economy,
    )

    assert generated.fallback is False
    selected = generated.generator["selected_values"]
    assert any(item["part_id"] == "source-weather" for item in selected)
    assert {"part_id": "source-selection", "value_id": "weather"} in selected


def test_delivery4_magnitude_five_is_mandatory_across_sequences() -> None:
    node = node_from_profile("n01")
    catalog = load_message_catalog(ROOT / "config")
    moment = datetime(2026, 8, 10, 18, 0, tzinfo=UTC)
    geology = _geology_snapshot(
        [
            {
                "event_id": "m5-persistent",
                "occurred_at": "2026-08-10T17:45:00Z",
                "location": "Región de Coquimbo",
                "magnitude": 5.4,
                "latitude": -30.0,
                "longitude": -71.0,
            }
        ]
    )

    for sequence in range(1, 9):
        generated = generate_sonantia_text(
            node,
            catalog,
            available_weather(),
            moment,
            sequence,
            astronomy_snapshot=_astronomy_snapshot(sun_altitude=-1.0, moon_altitude=35.0),
            geology_snapshot=geology,
            economy_snapshot=_economy_snapshot(),
        )
        selected = generated.generator["selected_values"]
        assert {"part_id": "source-selection", "value_id": "geology-mandatory"} in selected
        assert any(item["part_id"] == "source-geology" for item in selected)
        assert "5,4" in generated.text


def test_delivery4_multisource_never_exceeds_two_factual_sections() -> None:
    node = node_from_profile("n01")
    catalog = load_message_catalog(ROOT / "config")
    moment = datetime(2026, 8, 10, 22, 0, tzinfo=UTC)
    geology = _geology_snapshot(
        [
            {
                "event_id": "m4",
                "occurred_at": "2026-08-10T21:30:00Z",
                "location": "Región cercana a Nueva York",
                "magnitude": 4.7,
                "latitude": -33.5,
                "longitude": -70.7,
            }
        ]
    )

    for sequence in range(1, 17):
        generated = generate_sonantia_text(
            node,
            catalog,
            available_weather(),
            moment,
            sequence,
            astronomy_snapshot=_astronomy_snapshot(sun_altitude=-1.0, moon_altitude=35.0),
            geology_snapshot=geology,
            economy_snapshot=_economy_snapshot(),
        )
        factual_parts = [
            item
            for item in generated.generator["selected_values"]
            if item["part_id"]
            in {
                "source-weather",
                "source-astronomy",
                "source-geology",
                "source-economy",
            }
        ]
        assert 1 <= len(factual_parts) <= catalog.definition.policy.max_source_sections


def test_seismic_priority_keeps_magnitude_strictly_dominant() -> None:
    """Una diferencia de magnitud no debe ser anulada por recencia o cercanía."""
    from distributed_node.messages import build_cycle_context

    node = node_from_profile("n01")
    moment = datetime(2026, 8, 10, 18, 0, tzinfo=UTC)
    geology = _geology_snapshot(
        [
            {
                "event_id": "recent-smaller",
                "occurred_at": "2026-08-10T17:59:00Z",
                "location": "muy cerca",
                "magnitude": 4.4,
                "latitude": -33.45,
                "longitude": -70.66,
            },
            {
                "event_id": "older-larger",
                "occurred_at": "2026-08-10T02:00:00Z",
                "location": "más lejos",
                "magnitude": 4.5,
                "latitude": -38.0,
                "longitude": -73.0,
            },
        ]
    )
    context = build_cycle_context(
        node,
        available_weather(),
        moment,
        1,
        geology_snapshot=geology,
    )
    assert context["geology"]["priority_event"]["event_id"] == "older-larger"


def test_non_weather_fact_selection_is_not_locked_to_first_provider_item() -> None:
    """
    Astronomía y economía pueden usar más de un hecho disponible
    sin depender del orden del proveedor.
    """
    import random

    from distributed_node.messages import (
        _astronomy_contribution,
        _economy_contribution,
        _select_fact_sentence,
        build_cycle_context,
    )

    node = node_from_profile("n01")
    catalog = load_message_catalog(ROOT / "config")
    moment = datetime(2026, 8, 10, 21, 0, tzinfo=UTC)
    astronomy = _astronomy_snapshot(sun_altitude=-2.0, moon_altitude=30.0)
    economy = {
        "status": "available",
        "provider": "us-economic-data",
        "indicators": [
            {"label": "Euro (USD por EUR)", "value": "1,1650"},
            {"label": "Dólar observado", "value": "$945,30"},
        ],
        "inflation": [],
    }
    context = build_cycle_context(
        node,
        available_weather(),
        moment,
        1,
        astronomy_snapshot=astronomy,
        economy_snapshot=economy,
    )
    template_map = {
        source.source_id: source.templates for source in catalog.definition.source_templates
    }
    astronomy_contribution = _astronomy_contribution(astronomy, context)
    economy_contribution = _economy_contribution(economy)
    assert astronomy_contribution is not None
    assert economy_contribution is not None

    astronomy_fact_ids = set()
    economy_fact_ids = set()
    for seed in range(24):
        _, fact_id, _ = _select_fact_sentence(
            astronomy_contribution,
            template_map["astronomy"],
            seed + 1,
            context,
            random.Random(seed),
        )
        astronomy_fact_ids.add(fact_id)
        _, fact_id, _ = _select_fact_sentence(
            economy_contribution,
            template_map["economy"],
            seed + 1,
            context,
            random.Random(seed),
        )
        economy_fact_ids.add(fact_id)

    assert len(astronomy_fact_ids) > 1
    assert len(economy_fact_ids) > 1
