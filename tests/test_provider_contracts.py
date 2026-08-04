from datetime import UTC, datetime

import pytest

from distributed_node.context_providers import (
    ProviderConfigurationError,
    _us_economic_provider,
    _usgs_earthquakes_provider,
)
from distributed_node.models import EconomySnapshot, GeologySnapshot
from tests.factories import node_from_profile

MOMENT = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def test_us_adapters_emit_common_normalized_contracts_when_disabled() -> None:
    node = node_from_profile("n02")
    economy = _us_economic_provider(
        node, MOMENT, client=None, enabled=False, settings={}
    )
    geology = _usgs_earthquakes_provider(
        node, MOMENT, client=None, enabled=False, settings={}
    )

    assert EconomySnapshot.model_validate(economy).provider == "us-economic-data"
    assert EconomySnapshot.model_validate(economy).country_code == "US"
    assert GeologySnapshot.model_validate(geology).provider == "usgs-earthquakes"
    assert GeologySnapshot.model_validate(geology).events == []


def test_country_specific_provider_rejects_incompatible_enabled_profile() -> None:
    node = node_from_profile("n03")
    with pytest.raises(ProviderConfigurationError, match="country_code US"):
        _us_economic_provider(node, MOMENT, client=None, enabled=True, settings={})
    with pytest.raises(ProviderConfigurationError, match="country_code US"):
        _usgs_earthquakes_provider(
            node, MOMENT, client=None, enabled=True, settings={}
        )
