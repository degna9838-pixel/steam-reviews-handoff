from fastapi.testclient import TestClient

from cs2_reviews.api import create_app
from cs2_reviews.config import GAMES, Settings
from cs2_reviews.coordinator import MultiGameCollector, RequestGate
from cs2_reviews.storage import Store


def test_game_selection_isolated_and_default_legacy_compatible(tmp_path):
    stores = {}
    for app_id in GAMES:
        store = Store(tmp_path / str(app_id) / "reviews.sqlite3", app_id=app_id)
        store.apply_page([{"recommendationid": "123", "review": "A fun game", "language": "english",
                           "timestamp_created": 10, "timestamp_updated": 10, "voted_up": True}])
        stores[app_id] = store
    with TestClient(create_app(stores[730], stores=stores)) as client:
        assert client.get("/v1/reviews").json()["items"][0]["app_id"] == 730
        assert len(client.get("/v1/games").json()["games"]) == 3
        streams = set()
        for app_id in GAMES:
            page = client.get("/v1/reviews", params={"app_id": app_id}).json()
            assert page["items"][0]["app_id"] == app_id
            streams.add(page["stream_id"])
            assert client.get("/v1/reviews/123", params={"app_id": app_id}).json()["app_id"] == app_id
            assert client.get("/v1/reviews/changes", params={"app_id": app_id}).json()["items"][0]["review"]["app_id"] == app_id
            assert client.get("/v1/health", params={"app_id": app_id}).json()["app_id"] == app_id
        assert len(streams) == 3
        assert client.get("/v1/reviews?app_id=999").status_code == 422
    with TestClient(create_app(stores[730])) as client:
        assert client.get("/v1/health?app_id=570").status_code == 503


def test_shared_gate_spaces_calls_without_real_sleep():
    now, waits = [0.0], []
    def sleep(delay):
        waits.append(delay)
        now[0] += delay
    gate = RequestGate(1, clock=lambda: now[0], sleep=sleep)
    gate(); gate(); gate()
    assert waits == [1.0, 1.0]


def test_all_games_signalled_before_waiting_for_shutdown(monkeypatch, tmp_path):
    runners = []
    class Runner:
        def __init__(self, store, settings, request_gate=None):
            import threading
            self.stop_event = threading.Event()
            self.state = "not_started"
            runners.append(self)
        def start(self):
            self.state = "running"
        def stop(self):
            assert all(r.stop_event.is_set() for r in runners)
    monkeypatch.setattr("cs2_reviews.coordinator.BackgroundCollector", Runner)
    stores = {i: Store(tmp_path / str(i) / "reviews.sqlite3", app_id=i) for i in GAMES}
    coordinator = MultiGameCollector(stores, Settings())
    coordinator.start()
    assert all(coordinator.state_for(i) == "running" for i in GAMES)
    coordinator.stop()
