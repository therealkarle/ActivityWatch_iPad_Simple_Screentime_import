from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlparse

from aw_client import ActivityWatchClient


ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = {
    "activitywatch_base_url": "http://127.0.0.1:5600",
    "activitywatch_hostname": "Florian_IPad_SimpleScreentime",
    "sync_status_file": "sync_status.json",
}


def load_config() -> dict[str, object]:
    config = DEFAULT_CONFIG.copy()
    config_path = ROOT_DIR / "config.json"

    if config_path.exists():
        loaded = json.loads(config_path.read_text(encoding="utf-8-sig"))
        if not isinstance(loaded, dict):
            raise ValueError("config.json must contain a JSON object.")
        config.update(loaded)

    return config


def resolve_path(raw_value: object) -> Path:
    path = Path(str(raw_value))
    if path.is_absolute():
        return path
    return (ROOT_DIR / path).resolve()


def create_client(base_url: str) -> tuple[ActivityWatchClient, str]:
    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 5600
    protocol = parsed.scheme or "http"
    client = ActivityWatchClient("reset-script", host=host, port=port, protocol=protocol)
    return client, f"{protocol}://{host}:{port}"


def delete_bucket_if_present(client: ActivityWatchClient, bucket_id: str) -> bool:
    try:
        client.delete_bucket(bucket_id, force=True)
        print(f"Deleted bucket: {bucket_id}")
        return True
    except Exception as exc:  # noqa: BLE001 - want to continue resetting other targets
        status_code = getattr(exc, "status_code", None)
        message = str(exc).strip() or exc.__class__.__name__
        if status_code == 404 or "404" in message or "not found" in message.lower():
            print(f"Bucket already missing: {bucket_id}")
            return True
        print(f"Warning: could not delete bucket {bucket_id}: {message}")
        return False


def main() -> int:
    try:
        config = load_config()
    except Exception as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    base_url = str(config.get("activitywatch_base_url", DEFAULT_CONFIG["activitywatch_base_url"]))
    client_name = str(
        config.get("activitywatch_hostname")
        or config.get("aw_client_hostname")
        or DEFAULT_CONFIG["activitywatch_hostname"]
    )
    sync_status_path = resolve_path(config.get("sync_status_file", DEFAULT_CONFIG["sync_status_file"]))
    bucket_ids = [
        f"aw-watcher-window_{client_name}",
        f"aw-watcher-afk_{client_name}",
    ]

    try:
        client, server_label = create_client(base_url)
    except Exception as exc:
        print(f"Failed to initialize ActivityWatch client: {exc}", file=sys.stderr)
        return 1

    had_errors = False
    try:
        print(f"Using ActivityWatch server: {server_label}")
        print(f"Using bucket prefix: {client_name}")

        for bucket_id in bucket_ids:
            if not delete_bucket_if_present(client, bucket_id):
                had_errors = True

        if sync_status_path.exists():
            sync_status_path.unlink()
            print(f"Deleted sync status file: {sync_status_path}")
        else:
            print(f"Sync status file not found: {sync_status_path}")
    finally:
        close_method = getattr(client, "close", None) or getattr(client, "shutdown", None)
        if callable(close_method):
            try:
                close_method()
            except Exception:
                pass

    return 1 if had_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
