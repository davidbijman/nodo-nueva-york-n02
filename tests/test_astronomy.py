from datetime import UTC, datetime

import httpx

from distributed_node.astronomy import fetch_astronomy_snapshot
from tests.factories import node_from_profile


def test_horizons_observer_uses_node_location() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(503, request=request)

    node = node_from_profile("n02")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        snapshot = fetch_astronomy_snapshot(
            node,
            datetime(2026, 8, 3, 12, 0, tzinfo=UTC),
            client=client,
        )

    observer_request = next(
        request for request in requests if request.url.params.get("EPHEM_TYPE") == "OBSERVER"
    )
    assert observer_request.url.params["SITE_COORD"].strip("'") == "-73.9692,40.7789,0.043"
    assert snapshot["observer"]["name"] == "Nueva York · coordenadas N02"
    assert snapshot["observer"]["timezone"] == "America/New_York"
