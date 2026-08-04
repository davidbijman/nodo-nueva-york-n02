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
