"""Run cloud-backed consumers and a local dashboard API (no credentials in code)."""
import argparse
from dataclasses import dataclass
import json
import os
import subprocess
import sys
import time

from .settings import ROOT, SUPPORTED_APP_IDS, validate_consumer_name


@dataclass
class ManagedService:
    name: str
    command: list[str]
    process: subprocess.Popen | None = None
    started_at: float = 0
    retry_at: float = 0
    failures: int = 0

    def start(self, now):
        self.process = subprocess.Popen(self.command, cwd=ROOT)
        self.started_at = now

    def schedule_retry(self, now, code):
        self.failures += 1
        delay = min(60, 2 ** min(self.failures, 6))
        self.retry_at = now + delay
        print(json.dumps({"event": "service_retry", "service": self.name,
                          "exit_code": code, "retry_in_seconds": delay}), flush=True)

    def tick(self, now):
        if self.process is not None:
            code = self.process.poll()
            if code is None:
                return
            if now - self.started_at >= 300:
                self.failures = 0
            self.process = None
            self.schedule_retry(now, code)
        if now >= self.retry_at:
            try:
                self.start(now)
            except OSError:
                self.schedule_retry(now, None)

    def stop(self):
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://43.161.240.54")
    parser.add_argument("--consumer", default="tencent_cloud_live")
    parser.add_argument("--port", type=int, default=8220)
    args = parser.parse_args()
    validate_consumer_name(args.consumer)
    if "demo" in args.consumer:
        parser.error("Cloud live data requires a separate consumer without demo in its name")
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if not os.environ.get("STEAM_API_USER") or not os.environ.get("STEAM_API_PASSWORD"):
        parser.error("Set STEAM_API_USER and STEAM_API_PASSWORD in this terminal first")
    if not args.base_url.startswith("https://"):
        parser.error("Cloud collection requires HTTPS")

    services = []
    try:
        for app_id in SUPPORTED_APP_IDS:
            command = [sys.executable, "-u", "-m", "sentiment_analysis", "run", "--app-id", str(app_id),
                       "--consumer", args.consumer, "--base-url", args.base_url,
                       "--limit", "1000", "--poll-seconds", "60", "--no-summary"]
            service = ManagedService(f"consumer_{app_id}", command)
            services.append(service)
            service.start(time.monotonic())
        command = [sys.executable, "-u", "-m", "sentiment_analysis", "serve", "--consumer", args.consumer,
                   "--collector-url", args.base_url, "--port", str(args.port)]
        service = ManagedService("dashboard_api", command)
        services.append(service)
        service.start(time.monotonic())
        print(f"Cloud consumers running. Local dashboard API: http://127.0.0.1:{args.port}/docs", flush=True)
        while True:
            for service in services:
                service.tick(time.monotonic())
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for service in services:
            service.stop()


if __name__ == "__main__":
    main()
