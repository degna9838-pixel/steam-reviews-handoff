
"""Synchronize reviews using the existing upstream API consumer."""

import argparse
import re
import subprocess
import sys
from pathlib import Path

from .cloud import resolve_base_url


APP_IDS = (730, 570, 4115450)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

UPSTREAM_CONSUMER = (
    PROJECT_ROOT / "examples" / "consume_api.py"
)


def sync_one(
    app_id,
    consumer_name,
    base_url=None,
):
    """Synchronize one game's available change events."""

    if type(app_id) is not int or app_id not in APP_IDS:
        raise ValueError(f"Unsupported app_id: {app_id}")

    if not isinstance(consumer_name, str) or not re.fullmatch(
        r"[A-Za-z0-9_-]+", consumer_name
    ):
        raise ValueError("Invalid consumer name")

    if not UPSTREAM_CONSUMER.is_file():
        raise FileNotFoundError(
            f"Upstream consumer not found: {UPSTREAM_CONSUMER}"
        )

    command = [
        sys.executable,
        str(UPSTREAM_CONSUMER),
        "--consumer", consumer_name,
        "--app-id", str(app_id),
        "--base-url", resolve_base_url(base_url),
        "--once",
    ]

    print(
        f"\nSyncing app_id={app_id}, "
        f"consumer={consumer_name}",
        flush=True,
    )

    # Delegate cursor, revision, and transaction management
    # to the existing consumer implementation.
    subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=True,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Synchronize Steam review data."
    )

    parser.add_argument("--consumer", required=True)

    parser.add_argument(
        "--app-id",
        type=int,
        choices=APP_IDS,
        default=None,
        help="Omit to synchronize all three games.",
    )

    parser.add_argument(
        "--base-url",
        default=None,
        help="Collection API. Defaults to the shared cloud server.",
    )

    args = parser.parse_args()

    selected_games = (
        (args.app_id,)
        if args.app_id is not None
        else APP_IDS
    )

    for app_id in selected_games:
        sync_one(
            app_id=app_id,
            consumer_name=args.consumer,
            base_url=args.base_url,
        )


if __name__ == "__main__":
    main()
