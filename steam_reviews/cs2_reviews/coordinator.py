"""Independent game schedules sharing one outbound Steam request gate."""
from __future__ import annotations

import threading
import time

from .collector import BackgroundCollector
from .config import Settings
from .storage import Store


class RequestGate:
    def __init__(self, interval: float, *, clock=time.monotonic, sleep=time.sleep):
        self.interval, self.clock, self.sleep = interval, clock, sleep
        self.lock = threading.Lock()
        self.next_at = 0.0

    def __call__(self):
        with self.lock:
            remaining = self.next_at - self.clock()
            if remaining > 0:
                self.sleep(remaining)
            self.next_at = self.clock() + self.interval


class MultiGameCollector:
    def __init__(self, stores: dict[int, Store], settings: Settings):
        self.request_gate = RequestGate(settings.request_delay_seconds)
        self.runners = {app_id: BackgroundCollector(store, settings, request_gate=self.request_gate)
                        for app_id, store in stores.items()}

    def start(self):
        for runner in self.runners.values():
            runner.start()

    def stop(self):
        # Signal every game before waiting for any in-flight request to finish.
        for runner in self.runners.values():
            runner.stop_event.set()
        for runner in self.runners.values():
            runner.stop()

    def state_for(self, app_id: int) -> str:
        return self.runners[app_id].state
