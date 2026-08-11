from datetime import UTC, datetime

import httpx

from distributed_node.providers.geology.usgs_earthquakes import fetch_usgs_snapshot
from tests.factories import node_from_profile

MOMENT = datetime(2026, 8, 10, 18, 0, tzinfo=UTC)


def _feature(*, event_id: str = "evt-1", place: str = "Example location") -> dict:
    return {
        "id": event_id,
        "properties": {
            "type": "earthquake",
            "place": place,
            "time": int(datetime(2026, 8, 9, 12, 0, tzinfo=UTC).timestamp() * 1000),
            "mag": 2.4,
            "url": f"https://example.test/{event_id}",
        },
        "geometry": {"coordinates": [-74.2, 43.0, 8.0]},
    }


def test_regional_search_uses_radius_around_node() -> None:
    node = node_from_profile("n02")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            request=request,
            json={"features": [_feature(place="10 km S of Example locality")]},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        snapshot = fetch_usgs_snapshot(node, MOMENT, client=client)

    assert snapshot["status"] == "available"
    assert snapshot["count"] == 1
    assert snapshot["search_stage"] == "regional-7d"
    assert snapshot["window_hours"] == 168
    assert len(requests) == 1
    assert requests[0].url.params["maxradiuskm"] == "550"
    assert requests[0].url.params["latitude"] == str(node.logical_location.latitude)


def test_usgs_search_expands_to_30_days_after_empty_regional_window() -> None:
    node = node_from_profile("n02")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) < 2:
            return httpx.Response(200, request=request, json={"features": []})
        return httpx.Response(
            200,
            request=request,
            json={"features": [_feature(event_id="extended-event")]},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        snapshot = fetch_usgs_snapshot(node, MOMENT, client=client)

    assert len(requests) == 2
    assert snapshot["count"] == 1
    assert snapshot["events"][0]["event_id"] == "extended-event"
    assert snapshot["search_stage"] == "regional-30d"
    assert snapshot["window_hours"] == 720

    assert requests[0].url.params["maxradiuskm"] == "550"
    assert requests[1].url.params["maxradiuskm"] == "550"
    assert requests[0].url.params["starttime"] != requests[1].url.params["starttime"]


def test_usgs_returns_last_extended_empty_snapshot_when_no_events_exist() -> None:
    node = node_from_profile("n02")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, request=request, json={"features": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        snapshot = fetch_usgs_snapshot(node, MOMENT, client=client)

    assert len(requests) == 2
    assert snapshot["status"] == "available"
    assert snapshot["count"] == 0
    assert snapshot["events"] == []
    assert snapshot["search_stage"] == "regional-30d"
    assert snapshot["window_hours"] == 720
