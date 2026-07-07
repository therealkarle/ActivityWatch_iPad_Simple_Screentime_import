from __future__ import annotations

import inspect
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, DefaultDict, Iterable

try:
    from aw_client import ActivityWatchClient
    from aw_core.models import Event
except ImportError as exc:  # pragma: no cover - handled at runtime
    raise SystemExit(
        "Missing dependencies. Install aw-client and aw-core before running this importer."
    ) from exc


BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
DEFAULT_CONFIG = {
    "log_file_path": r"C:\Users\flori\iCloudDrive\IPadUsageLogs\Florian_IPad_Daily-Usage-Time.txt",
    "aw_hostname": "127.0.0.1",
    "aw_port": 5600,
    "aw_client_hostname": "Florian_IPad_SimpleScreentime",
    "sync_status_file": "sync_status.json",
    "debug": False,
}

BLOCK_PATTERN = re.compile(
    # Match repeated blocks like `2026-07-06:{ ... },` anywhere in the file.
    r"(?ms)(\d{4}-\d{2}-\d{2})\s*:\s*\{\s*(.*?)\s*\}\s*,?"
)
ENTRY_PATTERN = re.compile(r"^\s*(?P<app>.+)\s*\((?P<duration>[^()]*)\)\s*$")
DURATION_PATTERN = re.compile(
    r"(\d+)\s*(hours?|hrs?|hr|h|minutes?|mins?|min|m|seconds?|secs?|sec|s)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedEntry:
    app_name: str
    duration_seconds: int


def log_debug(enabled: bool, message: str) -> None:
    if enabled:
        print(f"[debug] {message}")


def resolve_path(raw_value: str | Path, base_dir: Path) -> Path:
    path = Path(raw_value)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def load_json_file(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def save_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def load_config() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(
            f"Missing config.json at {CONFIG_FILE}. Copy config.example.json to config.json and customize it."
        )

    config = DEFAULT_CONFIG.copy()
    loaded = load_json_file(CONFIG_FILE)
    if not isinstance(loaded, dict):
        raise ValueError("config.json must contain a JSON object.")

    config.update(loaded)
    return config


def get_debug_flag(config: dict[str, Any]) -> bool:
    value = config.get("debug", False)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def parse_duration_to_seconds(duration_text: str) -> int | None:
    total_seconds = 0
    matched = False

    for amount_text, unit_text in DURATION_PATTERN.findall(duration_text):
        matched = True
        amount = int(amount_text)
        unit = unit_text.lower()

        if unit.startswith(("hour", "hr")) or unit == "h":
            total_seconds += amount * 3600
        elif unit.startswith(("minute", "min")) or unit == "m":
            total_seconds += amount * 60
        else:
            total_seconds += amount

    if not matched or total_seconds <= 0:
        return None

    return total_seconds


def parse_block_entries(block_body: str) -> list[ParsedEntry]:
    entries: list[ParsedEntry] = []

    for raw_line in block_body.splitlines():
        line = raw_line.strip().rstrip("},")
        if not line:
            continue

        match = ENTRY_PATTERN.match(line)
        if not match:
            continue

        app_name = match.group("app").strip()
        duration_text = match.group("duration").strip()
        if not app_name or not duration_text:
            continue

        duration_seconds = parse_duration_to_seconds(duration_text)
        if duration_seconds is None:
            continue

        entries.append(ParsedEntry(app_name=app_name, duration_seconds=duration_seconds))

    return entries


def parse_log_file(log_path: Path, debug: bool = False) -> dict[str, list[ParsedEntry]]:
    try:
        content = log_path.read_text(encoding="utf-8-sig", errors="replace")
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Log file not found: {log_path}") from exc

    log_debug(debug, f"Reading log file: {log_path}")
    log_debug(debug, f"Log file size: {len(content)} characters")

    grouped_entries: DefaultDict[str, list[ParsedEntry]] = defaultdict(list)
    matches = list(BLOCK_PATTERN.findall(content))
    log_debug(debug, f"Found {len(matches)} daily block candidate(s)")

    for date_text, block_body in matches:
        try:
            datetime.strptime(date_text, "%Y-%m-%d")
        except ValueError:
            log_debug(debug, f"Skipping invalid date block: {date_text}")
            continue

        entries = parse_block_entries(block_body)
        if entries:
            grouped_entries[date_text].extend(entries)
            log_debug(debug, f"Parsed {date_text}: {len(entries)} valid entrie(s)")
        else:
            log_debug(debug, f"Parsed {date_text}: no valid entries")

    return dict(grouped_entries)


def load_last_synced_date(sync_status_path: Path, debug: bool = False) -> str | None:
    if not sync_status_path.exists():
        log_debug(debug, f"Sync status file not found: {sync_status_path}")
        return None

    try:
        payload = load_json_file(sync_status_path)
    except (OSError, json.JSONDecodeError, ValueError):
        log_debug(debug, f"Could not read sync status file: {sync_status_path}")
        return None

    if not isinstance(payload, dict):
        log_debug(debug, "Sync status file does not contain a JSON object")
        return None

    value = payload.get("last_synced_date")
    if not isinstance(value, str):
        log_debug(debug, "Sync status file is missing last_synced_date")
        return None

    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        log_debug(debug, f"Sync status has invalid date: {value}")
        return None

    log_debug(debug, f"Last synced date: {value}")
    return value


def save_last_synced_date(sync_status_path: Path, date_text: str, debug: bool = False) -> None:
    save_json_file(sync_status_path, {"last_synced_date": date_text})
    log_debug(debug, f"Updated sync status file: {sync_status_path} -> {date_text}")


def build_activitywatch_client(
    client_name: str,
    hostname: str,
    port: int,
    debug: bool = False,
) -> ActivityWatchClient:
    attempts: list[dict[str, Any]] = [
        {"host": hostname, "port": port},
        {"hostname": hostname, "port": port},
        {"server": hostname, "port": port},
        {"base_url": f"http://{hostname}:{port}"},
        {"url": f"http://{hostname}:{port}"},
        {"host": hostname},
        {"hostname": hostname},
        {},
    ]

    last_error: Exception | None = None
    for extra_kwargs in attempts:
        try:
            client = ActivityWatchClient(client_name, **extra_kwargs)
            log_debug(debug, f"Initialized ActivityWatch client with args: {extra_kwargs or '{}'}")
            return client
        except TypeError as exc:
            last_error = exc

    if last_error is not None:
        raise last_error

    return ActivityWatchClient(client_name)


def ensure_bucket(client: Any, bucket_id: str, bucket_type: str, debug: bool = False) -> None:
    method = getattr(client, "create_bucket", None) or getattr(client, "create", None)
    if method is None:
        raise RuntimeError("The installed ActivityWatch client does not expose a bucket creation method.")

    log_debug(debug, f"Ensuring bucket exists: {bucket_id} ({bucket_type})")
    signature = inspect.signature(method)
    kwargs: dict[str, Any] = {}

    if "event_type" in signature.parameters:
        kwargs["event_type"] = bucket_type
    elif "bucket_type" in signature.parameters:
        kwargs["bucket_type"] = bucket_type
    elif "type" in signature.parameters:
        kwargs["type"] = bucket_type

    try:
        if kwargs:
            try:
                method(bucket_id, **kwargs)
                return
            except TypeError:
                method(bucket_id, bucket_type)
                return
        else:
            method(bucket_id, bucket_type)
    except Exception as exc:  # Bucket may already exist; ignore that case.
        message = str(exc).lower()
        status_code = getattr(exc, "status_code", None)
        if status_code == 409 or "already exists" in message or "exists" in message:
            log_debug(debug, f"Bucket already exists: {bucket_id}")
            return
        raise


def insert_events(client: Any, bucket_id: str, events: list[Event], debug: bool = False) -> None:
    method = (
        getattr(client, "insert_events", None)
        or getattr(client, "insert", None)
        or getattr(client, "post_events", None)
    )
    if method is None:
        raise RuntimeError("The installed ActivityWatch client does not expose an event insertion method.")

    log_debug(debug, f"Inserting {len(events)} event(s) into {bucket_id}")
    signature = inspect.signature(method)
    parameters = [
        name for name in signature.parameters if name not in {"self", "cls"}
    ]
    call_errors: list[Exception] = []

    if parameters[:2] == ["bucket_id", "events"]:
        try:
            method(bucket_id, events)
            return
        except TypeError as exc:
            call_errors.append(exc)

    if parameters[:2] == ["events", "bucket_id"]:
        try:
            method(events, bucket_id)
            return
        except TypeError as exc:
            call_errors.append(exc)

    if "bucket_id" in signature.parameters and "events" in signature.parameters:
        try:
            method(bucket_id=bucket_id, events=events)
            return
        except TypeError as exc:
            call_errors.append(exc)

    if len(parameters) >= 2:
        try:
            method(bucket_id, events)
            return
        except TypeError as exc:
            call_errors.append(exc)

    if call_errors:
        raise call_errors[-1]

    raise RuntimeError("The installed ActivityWatch client does not accept event insertion arguments.")


def create_events_for_day(day_text: str, entries: Iterable[ParsedEntry]) -> tuple[list[Event], list[Event]]:
    day_start = datetime.strptime(day_text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    current_start = day_start

    app_events: list[Event] = []
    afk_events: list[Event] = []

    for entry in entries:
        duration = timedelta(seconds=entry.duration_seconds)
        app_events.append(
            Event(
                timestamp=current_start,
                duration=duration,
                data={
                    "app": entry.app_name,
                    "title": entry.app_name,
                    "usage_seconds": entry.duration_seconds,
                },
            )
        )
        afk_events.append(
            Event(
                timestamp=current_start,
                duration=duration,
                data={"status": "not-afk"},
            )
        )
        current_start += duration

    return app_events, afk_events


def sync_days(
    client: Any,
    grouped_entries: dict[str, list[ParsedEntry]],
    last_synced_date: str | None,
    client_hostname: str,
    sync_status_path: Path,
    debug: bool = False,
) -> tuple[int, str | None]:
    app_bucket_id = f"aw-watcher-window_{client_hostname}"
    afk_bucket_id = f"aw-watcher-afk_{client_hostname}"

    log_debug(debug, f"Target bucket IDs: {app_bucket_id}, {afk_bucket_id}")
    ensure_bucket(client, app_bucket_id, "currentwindow", debug=debug)
    ensure_bucket(client, afk_bucket_id, "afkstatus", debug=debug)

    processed_dates = 0
    last_synced_value = last_synced_date

    for date_text in sorted(grouped_entries):
        if last_synced_value is not None and date_text <= last_synced_value:
            log_debug(debug, f"Skipping already-synced day: {date_text}")
            continue

        app_events, afk_events = create_events_for_day(date_text, grouped_entries[date_text])
        if not app_events:
            log_debug(debug, f"Skipping day with no app events: {date_text}")
            continue

        log_debug(debug, f"Importing {date_text}: {len(app_events)} app event(s), {len(afk_events)} afk event(s)")
        insert_events(client, app_bucket_id, app_events, debug=debug)
        insert_events(client, afk_bucket_id, afk_events, debug=debug)

        last_synced_value = date_text
        save_last_synced_date(sync_status_path, last_synced_value, debug=debug)
        processed_dates += 1

    return processed_dates, last_synced_value


def main() -> int:
    try:
        config = load_config()
    except Exception as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    debug = get_debug_flag(config)

    log_file_path = resolve_path(config["log_file_path"], BASE_DIR)
    sync_status_path = resolve_path(config["sync_status_file"], BASE_DIR)
    aw_hostname = str(config["aw_hostname"])
    log_debug(debug, f"Resolved log path: {log_file_path}")
    log_debug(debug, f"Resolved sync status path: {sync_status_path}")
    log_debug(debug, f"ActivityWatch host: {aw_hostname}")

    try:
        aw_port = int(config["aw_port"])
    except (TypeError, ValueError):
        print("Configuration error: aw_port must be an integer.", file=sys.stderr)
        return 1

    aw_client_hostname = str(config["aw_client_hostname"])
    log_debug(debug, f"ActivityWatch port: {aw_port}")
    log_debug(debug, f"ActivityWatch client hostname: {aw_client_hostname}")

    try:
        grouped_entries = parse_log_file(log_file_path, debug=debug)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Failed to parse log file: {exc}", file=sys.stderr)
        return 1

    if not grouped_entries:
        print("No valid daily usage blocks were found.")
        return 0

    log_debug(debug, f"Parsed {len(grouped_entries)} day block(s) from log")
    last_synced_date = load_last_synced_date(sync_status_path, debug=debug)

    try:
        client = build_activitywatch_client(aw_client_hostname, aw_hostname, aw_port, debug=debug)
    except Exception as exc:
        print(f"Failed to initialize ActivityWatch client: {exc}", file=sys.stderr)
        return 1

    try:
        processed_dates, _ = sync_days(
            client=client,
            grouped_entries=grouped_entries,
            last_synced_date=last_synced_date,
            client_hostname=aw_client_hostname,
            sync_status_path=sync_status_path,
            debug=debug,
        )
    except Exception as exc:
        print(f"Failed to synchronize with ActivityWatch: {exc}", file=sys.stderr)
        return 1
    finally:
        close_method = getattr(client, "close", None) or getattr(client, "shutdown", None)
        if callable(close_method):
            try:
                close_method()
            except Exception:
                pass

    print(f"Imported {processed_dates} day(s) into ActivityWatch.")
    log_debug(debug, "Import run completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
